"""In-app updater routes.

Update trigger producer, rollback, status/version reads, check-now and
the minimal update-config GET/PATCH. CSRF is enforced upstream by
``csrf_middleware`` (wired in :mod:`webapp.app`).
"""
from __future__ import annotations

import dataclasses
import json
from dataclasses import asdict as _asdict_update_config
from datetime import datetime, timezone

import structlog
from aiohttp import web

from __PKG_NAME__.updater.config import (
    UpdateConfig,
    load_update_config,
    save_update_config,
    validate_update_config_patch,
)
from __PKG_NAME__.updater.security import (
    RateLimiter,
    audit_log_append,
    is_update_running,
)
from __PKG_NAME__.updater.status import load_status

log = structlog.get_logger()


#: single module-level sliding-window rate limiter
# Separate rate limiters: install/rollback share one 60s slot, check-now has
# its own. Prevents "Jetzt prüfen" from burning the install rate limit.
_update_rate_limiter = RateLimiter()  # type: RateLimiter
_check_rate_limiter = RateLimiter()   # type: RateLimiter


async def broadcast_update_in_progress(app) -> None:
    """: pre-shutdown WebSocket broadcast.

    Fires the ``update_in_progress`` message to every connected client
    so the UI can show a "reconnect in ~10s" banner before the app
    goes down. Best-effort: dead clients are silently
    dropped, any send failure is logged but does not abort the other
    sends.

    Accepts either an aiohttp ``web.Application`` or a plain dict-like
    object with a ``ws_clients`` entry (tests use the latter).
    """
    if app is None:
        return
    clients = app.get("ws_clients") if hasattr(app, "get") else None
    if not clients:
        return
    payload = json.dumps({
        "type": "update_in_progress",
        "message": "Update starting — reconnect in ~10s",
        "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    })
    for ws in list(clients):
        try:
            await ws.send_str(payload)
        except (ConnectionError, ConnectionResetError, RuntimeError) as exc:
            log.warning("ws_broadcast_update_in_progress_send_failed", error=str(exc))
            try:
                clients.discard(ws)
            except Exception:
                pass
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("ws_broadcast_update_in_progress_unexpected", error=str(exc))


async def _log_and_respond(
    request: web.Request,
    outcome: str | None,
    status: int,
    body: dict,
    *,
    extra_headers: dict | None = None,
) -> web.Response:
    """Emit one audit line (unless ``outcome`` is None) and return ``body``.

: every /api/update/{start,rollback,check} decision
    that falls into the closed outcome enum — ``accepted``, ``409_conflict``,
    ``429_rate_limited``, ``422_invalid_csrf`` — is recorded via
    :func:`audit_log_append`. Outcomes outside this set (e.g. malformed JSON
    body yielding 400) pass ``outcome=None`` to skip audit entirely rather
    than synthesizing a fake code. Audit failures never block the response
    so a disk-full condition on the audit log cannot wedge the UI path.
    """
    if outcome is not None:
        try:
            await audit_log_append(
                ip=request.remote or "unknown",
                user_agent=request.headers.get("User-Agent", ""),
                outcome=outcome,  # type: ignore[arg-type]
            )
        except Exception as exc:  # pragma: no cover - best-effort
            log.warning("audit_log_append_failed", error=str(exc))
    headers = extra_headers or {}
    return web.json_response(body, status=status, headers=headers)


async def update_start_handler(request: web.Request) -> web.Response:
    """POST /api/update/start -- + guards.

    Writes a trigger file atomically to ``/etc/__APP_SLUG__/update-trigger.json``
    and returns HTTP 202. The actual update work happens in the root helper
    (``__APP_SLUG__-updater.service``) triggered by a ``.path`` unit
    on ``PathModified``. shipped the producer half;
    hardens this handler with the full guard belt (rate limit, concurrent
    guard, audit log) and expects CSRF to be enforced upstream by
    :func:`__PKG_NAME__.updater.security.csrf_middleware`.

    Pipeline order (mandatory for <100ms latency):

        1. CSRF — enforced by middleware BEFORE reaching this handler.
        2. Rate limit (module-level sliding 60s window per IP).
        3. Concurrent-update guard (reads update-status.json).
        4. Parse + validate JSON body (existing validation).
        5. Enter maintenance mode + ``update_in_progress`` WS broadcast
           (03) BEFORE the write.
        6. Atomic trigger write (``write_trigger``).
        7. Audit-log ``accepted`` and return 202.

    Request body (JSON):

        {"op": "update", "target_sha": "<40-char lowercase hex>"}

    or for rollback:

        {"op": "rollback", "target_sha": "previous"}

    Response:

        202 — trigger written, updater will pick it up:
            {"update_id": "<nonce>", "status_url": "/api/update/status"}
        400 — body malformed or schema-invalid:
            {"error": "<reason>"}
        500 — disk write failed (ENOSPC, EACCES, ...):
            {"error": "trigger_write_failed: <detail>"}

    Latency budget: the handler must complete in <100ms.
    The write path is sync and tiny — tempfile create, 5-field json.dumps,
    os.replace, chmod — all within the same filesystem as the target.
    The guard prefix is also hot: rate limit is an in-memory dict lookup,
    the concurrent guard is a single file read, and the audit log write
    runs in the thread pool executor. No network I/O, no subprocess.
    """
    # Updater disabled (rsync-deployed hosts) — refuse up front, before
    # any side effect: no maintenance mode entered, no trigger written.
    if not load_update_config(request.app.get("config_path") or "").enabled:
        return web.json_response({"error": "updater_disabled"}, status=403)

    # Local import keeps module-load time flat for webapp.py and avoids a
    # circular-import risk if the updater package ever pulls webapp types.
    from __PKG_NAME__.updater.trigger import (
        TriggerPayload,
        generate_nonce,
        now_iso_utc,
        write_trigger,
    )

    # (a) Rate limit — 60s sliding window per source IP.
    accepted, retry_after = _update_rate_limiter.check(request.remote or "unknown")
    if not accepted:
        return await _log_and_respond(
            request,
            "429_rate_limited",
            429,
            {"error": "rate_limited", "retry_after": retry_after},
            extra_headers={"Retry-After": str(retry_after)},
        )

    # (b) Concurrent-update guard — status file is source of truth
    #. The webapp process never writes update-status.json, so
    # asyncio.Lock would be a false source of authority after a webapp
    # restart during an update.
    running, phase = is_update_running()
    if running:
        return await _log_and_respond(
            request,
            "409_conflict",
            409,
            {"error": "update_in_progress", "phase": phase},
        )

    # (c) Parse body. Malformed JSON is a client bug; not an auditable
    # outcome per the closed enum, so we skip the audit write.
    try:
        body = await request.json()
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        return await _log_and_respond(
            request, None, 400, {"error": f"invalid_json: {exc}"}
        )

    if not isinstance(body, dict):
        return await _log_and_respond(
            request, None, 400, {"error": "body_must_be_json_object"}
        )

    op = body.get("op", "update")
    target_sha = body.get("target_sha")

    if not isinstance(op, str) or not isinstance(target_sha, str):
        return await _log_and_respond(
            request,
            None,
            400,
            {"error": "op and target_sha required as strings"},
        )

    payload = TriggerPayload(
        op=op,
        target_sha=target_sha,
        requested_at=now_iso_utc(),
        requested_by="webapp",
        nonce=generate_nonce(),
    )

    try:
        payload.validate()
    except ValueError as exc:
        return await _log_and_respond(
            request, None, 400, {"error": f"invalid_payload: {exc}"}
        )

    # Enter maintenance mode + broadcast BEFORE writing the trigger.
    # Clients will get a retryable busy response on their next write
    # during the drain window; connected web UI clients receive the
    # update_in_progress banner.
    try:
        from __PKG_NAME__.updater.maintenance import enter_maintenance_mode

        app_ctx = request.app.get("app_ctx") if hasattr(request.app, "get") else None
        if app_ctx is not None:
            await enter_maintenance_mode(app_ctx, reason="update_requested")
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("maintenance_mode_enter_failed", error=str(exc))

    try:
        await broadcast_update_in_progress(request.app)
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("ws_broadcast_update_in_progress_failed", error=str(exc))

    try:
        write_trigger(payload)
    except OSError as exc:
        log.error("update_start_write_failed", error=str(exc))
        return await _log_and_respond(
            request, "500_trigger_write_failed", 500, {"error": f"trigger_write_failed: {exc}"}
        )

    log.info(
        "update_start_accepted",
        op=payload.op,
        target_sha=payload.target_sha,
        nonce=payload.nonce,
    )
    return await _log_and_respond(
        request,
        "accepted",
        202,
        {
            "update_id": payload.nonce,
            "status_url": "/api/update/status",
        },
    )


async def update_rollback_handler(request: web.Request) -> web.Response:
    """POST /api/update/rollback —.

    Writes a rollback trigger with the sentinel ``target_sha="previous"``.
    The root updater resolves "previous" against the
    update-status.json history to find the prior version. Same 3-guard
    pipeline as :func:`update_start_handler`; CSRF is enforced by
    :func:`csrf_middleware`.
    """
    if not load_update_config(request.app.get("config_path") or "").enabled:
        return web.json_response({"error": "updater_disabled"}, status=403)

    from __PKG_NAME__.updater.trigger import (
        TriggerPayload,
        generate_nonce,
        now_iso_utc,
        write_trigger,
    )

    accepted, retry_after = _update_rate_limiter.check(request.remote or "unknown")
    if not accepted:
        return await _log_and_respond(
            request,
            "429_rate_limited",
            429,
            {"error": "rate_limited", "retry_after": retry_after},
            extra_headers={"Retry-After": str(retry_after)},
        )

    running, phase = is_update_running()
    if running:
        return await _log_and_respond(
            request,
            "409_conflict",
            409,
            {"error": "update_in_progress", "phase": phase},
        )

    payload = TriggerPayload(
        op="rollback",
        target_sha="previous", # sentinel
        requested_at=now_iso_utc(),
        requested_by="webapp",
        nonce=generate_nonce(),
    )

    # Best-effort maintenance entry mirrors update_start_handler so the
    # app starts returning a busy response before the root updater starts
    # ripping files.
    try:
        from __PKG_NAME__.updater.maintenance import enter_maintenance_mode

        app_ctx = request.app.get("app_ctx") if hasattr(request.app, "get") else None
        if app_ctx is not None:
            await enter_maintenance_mode(app_ctx, reason="rollback_requested")
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("maintenance_mode_enter_failed_rollback", error=str(exc))

    try:
        await broadcast_update_in_progress(request.app)
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("ws_broadcast_update_in_progress_failed", error=str(exc))

    try:
        write_trigger(payload)
    except OSError as exc:
        log.error("rollback_write_failed", error=str(exc))
        return await _log_and_respond(
            request, "500_trigger_write_failed", 500, {"error": f"trigger_write_failed: {exc}"}
        )

    log.info("update_rollback_accepted", nonce=payload.nonce)
    return await _log_and_respond(
        request,
        "accepted",
        202,
        {"update_id": payload.nonce, "status_url": "/api/update/status"},
    )


async def version_handler(request: web.Request) -> web.Response:
    """GET /api/version —.

    Returns ``{version, commit}`` from the AppContext populated at startup
    by ``__main__`` via :func:`get_current_version` + :func:`get_commit_hash`.
    Frontend stores this on first load and re-fetches on WS
    reconnect to detect a post-update restart and trigger ``location.reload()``.
    """
    app_ctx = request.app.get("app_ctx") if hasattr(request.app, "get") else None
    if app_ctx is None:
        return web.json_response({"version": None, "commit": None})
    return web.json_response(
        {
            "version": getattr(app_ctx, "current_version", None),
            "commit": getattr(app_ctx, "current_commit", None),
        }
    )


async def update_status_handler(request: web.Request) -> web.Response:
    """GET /api/update/status —.

    Returns the full ``{current, history, schema_version}`` payload from
    the defensive status reader. Never raises: :func:`load_status` swallows
    all read errors and returns an empty :class:`UpdateStatus`.
    """
    try:
        status = load_status()
    except Exception as exc:  # pragma: no cover - load_status never raises
        log.warning("update_status_handler_load_failed", error=str(exc))
        return web.json_response({"current": None, "history": [], "schema_version": 1})

    if dataclasses.is_dataclass(status):
        return web.json_response(dataclasses.asdict(status))
    if hasattr(status, "to_dict"):
        return web.json_response(status.to_dict())
    return web.json_response(status)


async def update_check_handler(request: web.Request) -> web.Response:
    """POST /api/update/check — Check-now button.

    Invokes the:class:`UpdateCheckScheduler.check_once` helper
    to force an immediate GitHub Releases poll. Returns
    ``{checked, available, latest_version}``. Rate-limited via the
    dedicated ``_check_rate_limiter`` (separate from the privileged
    start/rollback limiter) so a check never burns the install budget.
    """
    # Updater disabled — report "up to date" so the UI's check button
    # stays quiet instead of surfacing an error.
    if not load_update_config(request.app.get("config_path") or "").enabled:
        return web.json_response(
            {"checked": True, "available": False, "latest_version": None}
        )

    accepted, retry_after = _check_rate_limiter.check(request.remote or "unknown")
    if not accepted:
        return await _log_and_respond(
            request,
            "429_rate_limited",
            429,
            {"error": "rate_limited", "retry_after": retry_after},
            extra_headers={"Retry-After": str(retry_after)},
        )

    scheduler = (
        request.app.get("update_scheduler") if hasattr(request.app, "get") else None
    )
    if scheduler is None:
        return web.json_response({"error": "scheduler_not_running"}, status=503)

    try:
        await scheduler.check_once()
    except Exception as exc:
        log.warning("update_check_once_failed", error=str(exc))
        return web.json_response(
            {"error": "check_failed", "detail": str(exc)}, status=500
        )

    # check_once() ran the scheduler's version-comparison callback, which
    # sets app_ctx.available_update only when a strictly-newer release
    # exists. Trust that verdict: a raw ReleaseInfo merely means GitHub
    # has *a* release, not that it is newer than the installed version.
    app_ctx = request.app.get("app_ctx") if hasattr(request.app, "get") else None
    available_update = getattr(app_ctx, "available_update", None)
    if available_update:
        return web.json_response(
            {
                "checked": True,
                "available": True,
                "latest_version": available_update.get("latest_version"),
            }
        )
    return web.json_response(
        {"checked": True, "available": False, "latest_version": None}
    )


async def update_config_get_handler(request: web.Request) -> web.Response:
    """GET /api/update/config —.

    Returns the minimal 3-field ``UpdateConfig`` dataclass as JSON. Read
    is unauthenticated (no secrets among the 3 fields) and does NOT
    require a CSRF token: :func:`csrf_middleware` only gates mutating
    methods on ``/api/update/*``.
    """
    cfg_path = request.app.get("config_path") or ""
    try:
        uc = load_update_config(cfg_path)
    except Exception as exc:  # pragma: no cover - load_update_config never raises
        log.warning("update_config_load_failed", error=str(exc))
        uc = UpdateConfig()
    return web.json_response(_asdict_update_config(uc))


async def update_config_patch_handler(request: web.Request) -> web.Response:
    """PATCH /api/update/config —.

    Accepts a subset of the 3 allowed keys
    (``github_repo`` / ``check_interval_hours`` / ``auto_install``) and
    merges the patch onto the current config. Unknown keys or bad types
    return 422 with a machine-readable ``detail`` string. CSRF is
    enforced upstream by :func:`csrf_middleware` (PATCH + ``/api/update/``
    prefix → double-submit cookie check).

    Response on success: the merged ``UpdateConfig`` as JSON, 200.
    """
    try:
        patch = await request.json()
    except Exception:
        return web.json_response(
            {"error": "invalid_json", "detail": "body_not_json"}, status=400
        )

    valid, err = validate_update_config_patch(patch)
    if not valid:
        return web.json_response(
            {"error": "validation_failed", "detail": err}, status=422
        )

    cfg_path = request.app.get("config_path") or ""
    try:
        current = load_update_config(cfg_path)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("update_config_load_failed_for_patch", error=str(exc))
        current = UpdateConfig()

    merged = UpdateConfig(
        github_repo=patch.get("github_repo", current.github_repo),
        check_interval_hours=patch.get(
            "check_interval_hours", current.check_interval_hours
        ),
        auto_install=patch.get("auto_install", current.auto_install),
        enabled=patch.get("enabled", current.enabled),
    )

    try:
        save_update_config(cfg_path, merged)
    except Exception as exc:
        log.warning("update_config_save_failed", error=str(exc))
        return web.json_response(
            {"error": "save_failed", "detail": str(exc)}, status=500
        )

    return web.json_response(_asdict_update_config(merged), status=200)


async def update_available_handler(request: web.Request) -> web.Response:
    """GET /api/update/available --.

    Returns the current version, commit, and (if available) the latest GitHub
    release info.: also surfaces last_check_at and last_check_failed_at
    so the UI can show a stale/failed indicator.

    Response shape:
        {
          "current_version": "8.0.0",
          "current_commit": "abc123d",
          "available_update": {
             "latest_version": "v8.1.0",
             "tag_name": "v8.1.0",
             "release_notes": "...",
             "published_at": "2026-04-10T...",
             "html_url": "https://github.com/..."
          } | null,
          "last_check_at": 1712755200.0 | null,
          "last_check_failed_at": null | 1712755200.0
        }
    """
    app_ctx = request.app["app_ctx"]
    return web.json_response({
        "current_version": app_ctx.current_version,
        "current_commit": app_ctx.current_commit,
        "available_update": app_ctx.available_update,
        "last_check_at": app_ctx.update_last_check_at,
        "last_check_failed_at": app_ctx.update_last_check_failed_at,
    })

