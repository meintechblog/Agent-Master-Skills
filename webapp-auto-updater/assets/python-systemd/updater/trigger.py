"""Atomic trigger file writer for the update protocol.

Requirements:
    POST /api/update/start writes a trigger atomically and returns
        HTTP 202 in <100ms.
    Trigger schema is exactly {op, target_sha, requested_at,
        requested_by, nonce}. Nonce is a UUID4.
    Trigger file mode 0o664, owner root:<service-user>. Main service
        writes; root updater reads.

Design notes:
    The atomicity pattern mirrors :mod:`__PKG_NAME__.state_file`: a
    tempfile sibling (``.json.tmp``) is written first, then ``os.replace``
    atomically renames it over the target. POSIX guarantees readers see
    either the old inode or the new one — never a half-written blob. On
    success we chmod the target to 0o664 so the root-owned updater can
    read it.

    No ``fsync`` is issued: a crash before ``os.replace`` means the trigger
    was never issued, which is the correct failure mode (the user can
    retry). A crash after ``os.replace`` but before the next reboot means
    the trigger survives on disk and will be consumed on startup by the
    updater's PathExistsGlob semantics — exactly what we want.

    Producer-side validation is deliberately light: the security root of
    trust is the **consumer** (``trigger_reader``), which
    re-validates against stricter rules (SHA reachability, tag regex).
    This module only catches obvious programmer errors so the wrong shape
    never reaches disk.

    This module MUST remain free of I/O in module-import side effects —
    no directory creation, no permission probing — so it can be imported
    cheaply from ``webapp.update_start_handler`` at request time without
    blocking the aiohttp event loop.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import structlog

log = structlog.get_logger(component="updater.trigger")

#: Canonical on-disk location. Directory (`/etc/__APP_SLUG__/`) is
#: created by install.sh; this module never creates it.
TRIGGER_FILE_PATH: Path = Path("/etc/__APP_SLUG__/update-trigger.json")

#: Service user (owner) + service-user group write, world read.
#: install.sh sets ownership to root:<service-user> so root (the updater) can read.
TRIGGER_FILE_MODE: int = 0o664

#: Anchored 40-char lowercase hex — matches ``git rev-parse HEAD`` output.
#: No nested quantifiers → bounded linear time, ReDoS-safe.
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Allowed ``op`` values. Rollback uses the sentinel ``"previous"`` as
#: target_sha to short-circuit the consumer's SHA-resolution step.
_VALID_OPS: frozenset[str] = frozenset({"update", "rollback"})

#: Rollback target_sha may be this sentinel in addition to a full SHA.
_ROLLBACK_SENTINEL: str = "previous"


@dataclass
class TriggerPayload:
    """v1 schema for ``/etc/__APP_SLUG__/update-trigger.json``.

    Fields (all required, producer writes exactly this set):
        op: ``"update"`` or ``"rollback"``.
        target_sha: 40-char lowercase hex SHA for ``update``. For
            ``rollback`` either a full SHA or the string ``"previous"``.
        requested_at: ISO-8601 UTC timestamp ending with ``"Z"`` (e.g.
            ``"2026-04-10T14:22:00Z"``). Produced by :func:`now_iso_utc`.
        requested_by: Short identifier of the caller (e.g. ``"webapp"``,
            ``"cli"``, ``"self-test"``). hardcodes ``"webapp"``;
             will widen this with audit metadata.
        nonce: UUID4 string. The consumer dedupes against the last 50
            processed nonces to make retries idempotent.

    The schema is **closed** — extra fields at the producer side are a
    programming bug. Any schema evolution MUST introduce an explicit
    ``schema_version`` field in a future release and bump the consumer's
    acceptable-version set in lockstep.
    """

    op: str
    target_sha: str
    requested_at: str
    requested_by: str
    nonce: str

    def validate(self) -> None:
        """Raise :class:`ValueError` on any obvious schema violation.

        This is defense-in-depth; the security boundary lives in the
        consumer. We validate only what a well-behaved
        producer can check without external I/O:

        * ``op`` is in the closed set.
        * ``target_sha`` matches the SHA regex (with rollback sentinel).
        * ``nonce`` is non-empty (full UUID4 shape is not enforced —
          :func:`generate_nonce` always produces a valid one).
        * ``requested_at`` ends with ``"Z"`` (UTC).
        * ``requested_by`` is non-empty.
        """
        if self.op not in _VALID_OPS:
            raise ValueError(f"invalid op: {self.op!r}")
        if self.op == "update":
            if not _SHA_RE.match(self.target_sha):
                raise ValueError(
                    "update requires full 40-char lowercase hex SHA, "
                    f"got {self.target_sha!r}"
                )
        else:  # rollback
            if self.target_sha != _ROLLBACK_SENTINEL and not _SHA_RE.match(
                self.target_sha
            ):
                raise ValueError(
                    "rollback target_sha must be 'previous' or full 40-char "
                    f"lowercase hex SHA, got {self.target_sha!r}"
                )
        if not self.nonce:
            raise ValueError("nonce must not be empty")
        if not self.requested_at.endswith("Z"):
            raise ValueError(
                f"requested_at must end with Z (UTC), got {self.requested_at!r}"
            )
        if not self.requested_by:
            raise ValueError("requested_by must not be empty")


def generate_nonce() -> str:
    """Return a fresh UUID4 string (36 chars including dashes)."""
    return str(uuid.uuid4())


def now_iso_utc() -> str:
    """Return current UTC time as ``YYYY-MM-DDTHH:MM:SSZ`` (second precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_trigger(
    payload: TriggerPayload,
    path: Path | None = None,
) -> None:
    """Atomically write ``payload`` to the trigger file.

    Sequence:

    1. :meth:`TriggerPayload.validate` — fail fast before any I/O.
    2. ``json.dumps`` with ``indent=2`` and ``sort_keys=True`` so diffs
       across consecutive writes are stable and the file is human-readable
       during incident response.
    3. Write to ``<target>.tmp``.
    4. ``os.replace(tmp, target)`` — atomic rename on POSIX.
    5. ``os.chmod(target, 0o664)`` —.

    On any :class:`OSError` during steps 3-5 the tempfile is best-effort
    unlinked so repeated failures don't leak ``.json.tmp`` siblings, and
    the exception is re-raised so the caller surfaces it as HTTP 500.

    Args:
        payload: The :class:`TriggerPayload` to persist.
        path: Override for :data:`TRIGGER_FILE_PATH` (used by unit tests
            to write into ``tmp_path``). Production callers pass ``None``.

    Raises:
        ValueError: If ``payload.validate()`` fails.
        OSError: If the write, replace, or chmod step fails.
    """
    payload.validate()
    target = path or TRIGGER_FILE_PATH
    tmp = target.with_suffix(".json.tmp")
    blob = json.dumps(asdict(payload), indent=2, sort_keys=True)
    try:
        tmp.write_text(blob)
        os.replace(tmp, target)
        os.chmod(target, TRIGGER_FILE_MODE)
    except OSError as exc:
        log.error(
            "trigger_write_failed",
            path=str(target),
            error=str(exc),
        )
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:  # pragma: no cover - best-effort cleanup
            pass
        raise
