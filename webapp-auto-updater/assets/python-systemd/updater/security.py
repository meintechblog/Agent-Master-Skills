"""Security belt: CSRF double-submit cookie, rate limiter,
concurrent-update guard, and JSONL audit log.

This module is a pure primitive library. It has ZERO coupling to
``webapp.py`` — your app wiring is responsible for wiring ``csrf_middleware``,
constructing a module-level ``RateLimiter`` instance, and calling
:func:`is_update_running` + :func:`audit_log_append` inside the new
``/api/update/*`` handlers.

Primitives:
    * :func:`csrf_middleware` — aiohttp middleware implementing the
      double-submit cookie pattern. Rejects POST/PUT/
      PATCH/DELETE to ``/api/update/*`` with 422 when cookie and header
      do not match (timing-safe via :func:`secrets.compare_digest`).
      Lazily seeds ``app_csrf`` cookie with ``SameSite=Strict``,
      ``Path=/``, ``Max-Age=86400`` on any request that lacks it.
    * :class:`RateLimiter` — in-memory sliding 60-second window keyed on
      ``request.remote``. Returns
      ``(accepted, retry_after_seconds)`` tuples. Uses injectable clock
      for hermetic testing. Normalizes IPv6-mapped IPv4 addresses.
    * :func:`is_update_running` — reads ``update-status.json`` via the
      existing :mod:`__PKG_NAME__.updater.status` defensive reader.
      Returns ``(running, phase)``. Fails **open** on
      status-load errors to avoid wedging the UI.
    * :func:`audit_log_append` — async, concurrent-safe JSONL append to
      ``/var/lib/__APP_SLUG__/update-audit.log``.
      Creates the parent directory lazily at mode ``0o750`` and the file
      at mode ``0o640``. Serializes writes through a module-level
      :class:`asyncio.Lock` so 10 concurrent ``asyncio.gather`` calls
      produce 10 intact lines.

Rationale for NOT using ``asyncio.Lock`` as the source of truth for
``is_update_running``: the webapp process does not own the updater; the
root updater daemon is the exclusive writer of ``update-status.json``.
Using a process-local ``asyncio.Lock`` would give the wrong answer after
a webapp restart during an update.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

import structlog
from aiohttp import web

from __PKG_NAME__.updater.status import current_phase, load_status

_logger = structlog.get_logger(component="updater.security")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Name of the double-submit cookie set by :func:`csrf_middleware`.
CSRF_COOKIE_NAME = "app_csrf"

#: Header clients must echo back on mutating requests to ``/api/update/*``.
CSRF_HEADER_NAME = "X-CSRF-Token"

#: Cookie lifetime in seconds. 86400 = 24 hours.
CSRF_COOKIE_MAX_AGE: int = 86400

#: Sliding-window length for the rate limiter in seconds.
RATE_LIMIT_WINDOW_SECONDS: int = 60

#: Phases in which accepting a new /api/update/start POST is legal.
#: Everything else returns 409 Conflict. These four names are the
#: complete idle set.
IDLE_PHASES: frozenset[str] = frozenset(
    {"idle", "done", "rollback_done", "rollback_failed"}
)

#: Canonical audit log path. Directory is created lazily on
#: the first successful ``audit_log_append`` call; never pre-created by
#: install.sh so the deploy script stays simple.
AUDIT_LOG_PATH: Path = Path("/var/lib/__APP_SLUG__/update-audit.log")

#: Parent-directory mode for the audit log.
AUDIT_LOG_DIR_MODE: int = 0o750

#: File mode for the audit log. Owner read+write, group read.
AUDIT_LOG_FILE_MODE: int = 0o640

#: Outcome enum used in audit log entries. Every decision taken by
#: the /api/update/* handlers — accepted or rejected — produces exactly
#: one of these values.
AuditOutcome = Literal[
    "accepted", "409_conflict", "429_rate_limited", "422_invalid_csrf"
]

# ---------------------------------------------------------------------------
# Module-level asyncio.Lock for serialized audit-log writes.
#
# NOTE: This lock is NOT the source of truth for "is an update running".
# Its sole purpose is to make ``open(path, 'a')`` atomic w.r.t. other
# coroutines in the same aiohttp event loop. Cross-process safety relies
# on the single-writer contract (only the main webapp process writes
# the audit log).
# ---------------------------------------------------------------------------

_audit_lock: asyncio.Lock | None = None


def _get_audit_lock() -> asyncio.Lock:
    """Lazy-init the audit lock inside a running event loop (Python 3.10+ safe)."""
    global _audit_lock
    if _audit_lock is None:
        _audit_lock = asyncio.Lock()
    return _audit_lock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_ip(raw: str | None) -> str:
    """Return a canonical IP string for rate-limiter and audit-log keys.

    aiohttp's ``request.remote`` may surface IPv6-mapped IPv4 addresses
    (``::ffff:203.0.113.17``) depending on the bind configuration, which
    would make the same client hash to two different keys across
    requests. This helper strips the ``::ffff:`` prefix and maps
    ``None``/empty to the literal ``"unknown"`` so dict lookups never
    hit ``KeyError``.

    (IPv6-mapped-IPv4 mitigation.)
    """
    if not raw:
        return "unknown"
    if raw.startswith("::ffff:"):
        return raw[len("::ffff:") :]
    return raw


# ---------------------------------------------------------------------------
#: CSRF double-submit cookie middleware
# ---------------------------------------------------------------------------


async def _audit_csrf_reject(request: web.Request) -> None:
    """Best-effort audit log for a CSRF rejection.

    Isolated helper so csrf failures produce an audit line without
    polluting every csrf_middleware branch with try/except. Any error
    (PermissionError on the canonical path during tests, disk full,
    interrupted I/O, ...) is swallowed — the rejection response itself
    is the authoritative signal to the client.
    """
    try:
        await audit_log_append(
            ip=request.remote or "unknown",
            user_agent=request.headers.get("User-Agent", ""),
            outcome="422_invalid_csrf",
        )
    except Exception:  # pragma: no cover - best-effort
        pass


@web.middleware
async def csrf_middleware(
    request: web.Request,
    handler: Callable[[web.Request], web.StreamResponse],
) -> web.StreamResponse:
    """Enforce double-submit cookie on mutating /api/update/* requests.

    Incoming check (only for POST/PUT/PATCH/DELETE on ``/api/update/*``):
        1. Both ``app_csrf`` cookie and ``X-CSRF-Token`` header must be
           present. Missing either → 422 ``csrf_missing``.
        2. Compare with :func:`secrets.compare_digest` (timing-safe).
           Mismatch → 422 ``csrf_mismatch``.

    On every 422 rejection the middleware emits one ``422_invalid_csrf``
    line through :func:`audit_log_append` — every outcome in the
    closed enum is logged, including CSRF violations that never reach
    the downstream handler. Audit failures are swallowed so a broken
    audit log cannot wedge the CSRF gate.

    Outgoing cookie seeding (all request methods, all paths):
        If the request arrived **without** the ``app_csrf`` cookie,
        stamp a fresh 32-byte ``secrets.token_urlsafe`` onto the response
        with ``SameSite=Strict``, ``Path=/``, ``Max-Age=86400``, and
        ``HttpOnly=False`` (so the browser's client-side JS can read it
        for the header echo).

    This handles the cold-start case: users who POST before any GET still
    get a 422 on the first attempt AND a seeded cookie in the rejection
    response, so the next attempt after a reload can succeed.
    """
    needs_check = (
        request.method in ("POST", "PUT", "PATCH", "DELETE")
        and request.path.startswith("/api/update/")
    )
    if needs_check:
        cookie_tok = request.cookies.get(CSRF_COOKIE_NAME)
        header_tok = request.headers.get(CSRF_HEADER_NAME)
        if not cookie_tok or not header_tok:
            await _audit_csrf_reject(request)
            response: web.StreamResponse = web.json_response(
                {"error": "csrf_missing"}, status=422
            )
            _maybe_seed_csrf_cookie(request, response)
            return response
        # Timing-safe comparison — never use `==` on secrets.
        if not secrets.compare_digest(cookie_tok, header_tok):
            await _audit_csrf_reject(request)
            response = web.json_response(
                {"error": "csrf_mismatch"}, status=422
            )
            # Do NOT re-seed on mismatch — that would paper over a real
            # attack by handing the attacker a fresh valid token. The
            # legitimate client will keep using the cookie it already
            # has; the mismatch indicates a real protocol violation.
            return response

    response = await handler(request)
    _maybe_seed_csrf_cookie(request, response)
    return response


def _maybe_seed_csrf_cookie(
    request: web.Request, response: web.StreamResponse
) -> None:
    """Set Set-Cookie header on ``response`` iff the request lacked it.

    Isolated helper so the same seeding behavior applies uniformly to
    (a) the normal response path, (b) the 422 ``csrf_missing`` rejection
    path. Missing-header rejections do NOT regenerate a new token;
    they only re-seed if the cookie is entirely absent.
    """
    if CSRF_COOKIE_NAME in request.cookies:
        return
    response.set_cookie(
        CSRF_COOKIE_NAME,
        secrets.token_urlsafe(32),
        max_age=CSRF_COOKIE_MAX_AGE,
        path="/",
        samesite="Strict",
        httponly=False,
        secure=False,
    )


# ---------------------------------------------------------------------------
#: In-memory sliding-window rate limiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Per-IP sliding 60-second window rate limiter.

    Memory model: ``dict[str, float]`` keyed on the normalized source IP,
    value = monotonic timestamp of the most recent accepted request.
    Stale entries (older than the window) are evicted lazily on each
    :meth:`check` call to bound memory on a long-running process.

    Clock injection: pass ``clock=FakeClock()`` in tests to advance time
    deterministically. Defaults to :func:`time.monotonic` in production,
    which is immune to wall-clock jumps.

    This is NOT a token bucket — it is a "one request per IP per window"
    gate, matching the threat model: we only need to stop a single rogue
    tab from flooding Install POSTs, not throttle legitimate polling.
    """

    def __init__(
        self,
        window_seconds: int = RATE_LIMIT_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._window: int = window_seconds
        self._clock: Callable[[], float] = clock
        self._last_seen: dict[str, float] = {}

    def check(self, ip: str) -> tuple[bool, int]:
        """Return ``(accepted, retry_after_seconds)`` for an IP.

        When ``accepted`` is True, ``retry_after_seconds`` is 0. When
        ``accepted`` is False, ``retry_after_seconds`` is a positive
        integer suitable for an HTTP ``Retry-After`` header (RFC 9110
        §10.2.3 — always integer seconds, never HTTP-date).
        The minimum returned value when rejected is 1 to avoid the
        client retrying instantly on a boundary.

        On acceptance the IP's last-seen timestamp is updated to now,
        starting a new 60-second window.
        """
        now = self._clock()
        # Lazy eviction of stale entries (memory-bounded on long uptime).
        stale = [k for k, t in self._last_seen.items() if now - t >= self._window]
        for k in stale:
            del self._last_seen[k]

        normalized = _normalize_ip(ip)
        last = self._last_seen.get(normalized)
        if last is None or now - last >= self._window:
            self._last_seen[normalized] = now
            return True, 0

        remaining = self._window - (now - last)
        retry_after = max(1, int(remaining))
        return False, retry_after


# ---------------------------------------------------------------------------
#: Concurrent-update guard (status-file-driven, NOT asyncio.Lock)
# ---------------------------------------------------------------------------


def is_update_running(status_path: Path | None = None) -> tuple[bool, str]:
    """Return ``(running, current_phase_string)`` by reading the status file.

    Source of truth: :func:`__PKG_NAME__.updater.status.load_status`
    (the defensive reader from). The webapp process never
    writes the status file — only the root updater does — so the file
    gives a cross-process, restart-surviving view of "is an update in
    flight right now".

    ``running`` is True iff the parsed phase is NOT in :data:`IDLE_PHASES`.
    This contract matters because the status file keeps
    ``current`` populated after ``done`` so the UI can show the last
    result, so checking ``current is not None`` would wedge the system
    after the first successful update.

    Fail-open policy: if the status file is unreadable, this function
    returns ``(False, "unknown")``. We prefer an occasional bad "allow"
    over a permanent bad "deny" because the downstream root updater
    performs its own nonce-dedup and locking which catches
    any genuine double-submit at a lower layer.
    """
    try:
        status = (
            load_status(status_path) if status_path is not None else load_status()
        )
    except Exception as exc:  # pragma: no cover - load_status never raises
        _logger.warning(
            "status_load_failed_concurrent_guard_fails_open",
            error=str(exc),
        )
        return False, "unknown"
    phase = current_phase(status)
    return phase not in IDLE_PHASES, phase


# ---------------------------------------------------------------------------
#: JSONL audit log writer
# ---------------------------------------------------------------------------


async def audit_log_append(
    *,
    ip: str,
    user_agent: str,
    outcome: AuditOutcome,
    log_path: Path | None = None,
    clock: Callable[[], str] | None = None,
) -> None:
    """Append a JSONL audit entry and return.

    Each call writes exactly one line containing ``{ts, ip, ua, outcome}``
    . Concurrent callers are serialized through a module-level
:class:`asyncio.Lock` so 10 ``asyncio.gather`` calls produce
    10 intact lines (no interleaving, no truncation).

    The actual file write runs in the default thread pool executor so
    the event loop is not blocked by synchronous I/O. This matters on
    slow disks; the lock is held for the full executor call so total
    throughput is ~bounded by disk seek time, which is fine for the
    expected audit volume (one write per user POST).

    Lazy filesystem setup:
        * Parent directory created at mode :data:`AUDIT_LOG_DIR_MODE`.
        * File created at mode :data:`AUDIT_LOG_FILE_MODE` on first write.
        * No exception if the directory/file already exist.
        * :class:`PermissionError` on chmod is swallowed — the file will
          still work with its default mode, and ops will see it in the
          journal if it's actually unwritable.

    Args:
        ip: Source IP (``request.remote``). Normalized via
            :func:`_normalize_ip` so IPv4-mapped IPv6 addresses hash
            consistently.
        user_agent: Verbatim ``User-Agent`` header or empty string.
        outcome: One of the four :data:`AuditOutcome` literals. Invalid
            values raise :class:`TypeError` at static-analysis time;
            runtime validation is not performed for perf reasons.
        log_path: Override for :data:`AUDIT_LOG_PATH` (used by tests).
        clock: Optional synchronous function returning the timestamp
            string. Defaults to :func:`datetime.now` in UTC, formatted
            as ``YYYY-MM-DDTHH:MM:SSZ`` (same grammar as 's
            ``now_iso_utc``).
    """
    path = log_path if log_path is not None else AUDIT_LOG_PATH
    if clock is not None:
        ts = clock()
    else:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "ts": ts,
        "ip": _normalize_ip(ip),
        "ua": user_agent or "",
        "outcome": outcome,
    }
    line = json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"

    async with _get_audit_lock():
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _append_audit_line, path, line)


def _append_audit_line(path: Path, line: str) -> None:
    """Synchronous helper: lazy-mkdir, append, chmod.

    Runs inside the thread executor under the async lock. Isolated so
    tests can patch the exact unit under lock if ever needed. Never
    logs — the outer async caller owns error reporting.

    CRITICAL ordering:
        1. Create parent dir if missing, enforce mode 0o750.
        2. Remember whether the file existed pre-write.
        3. Open in append mode (``"a"``) and write the single line.
        4. If we just created the file, chmod it to 0o640.

    Why chmod after open: Python's ``open("a")`` respects the process
    ``umask`` when creating the file, which on most systems yields
    ``0o644`` or ``0o664`` — neither of which matches. A dedicated
    ``os.chmod`` after creation guarantees the final mode. Existing
    files keep their mode so an admin who tightens permissions after
    the fact is not overridden.
    """
    parent = path.parent
    if not parent.exists():
        parent.mkdir(parents=True, mode=AUDIT_LOG_DIR_MODE, exist_ok=True)
        # mkdir's ``mode`` is masked by the process umask, so force the
        # real mode afterwards. Best-effort: swallow PermissionError so
        # an unprivileged test runner (no chmod rights on tmp) still
        # works on CI.
        try:
            os.chmod(parent, AUDIT_LOG_DIR_MODE)
        except PermissionError:  # pragma: no cover - test/ops edge
            pass

    existed = path.exists()
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
    if not existed:
        try:
            os.chmod(path, AUDIT_LOG_FILE_MODE)
        except PermissionError:  # pragma: no cover
            pass
