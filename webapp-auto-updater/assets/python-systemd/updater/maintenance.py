"""Maintenance mode for the update flow.

Encapsulates the three-state lifecycle that protects downstream clients
while the updater is restarting the main service:

1. ``enter_maintenance_mode`` flips ``app_ctx.maintenance_mode`` to True
   and records ``maintenance_entered_at``. The app's write path observes
   the flag and rejects client writes per ``MAINTENANCE_STRATEGY``
   (see below).

2. ``drain_inflight_writes`` waits up to ``timeout_s`` seconds for the
   in-flight write counter to reach zero. The counter is maintained on
   the app's request context — this helper reads it via
   ``app_ctx._client_ctx``.

3. ``exit_maintenance_mode`` clears the flag. Normally the process is
   going away (systemd restart), so this is only called when an error
   caused the update trigger to be aborted before the restart actually
   happened.

MAINTENANCE_STRATEGY
====================

Two strategies, chosen empirically for your protocol:

- ``"busy"``  (default): reject writes with a retryable "busy" response
  your clients can re-try. Most client libraries follow a back-off +
  retry convention, so an in-flight write simply lands once the restart
  completes.

- ``"silent_drop"`` (fallback): swallow the write, return success without
  forwarding it downstream. Use only if your clients drop the connection
  on a busy response instead of retrying — flip this constant as a
  one-line rollback.
"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:  # pragma: no cover
    from __PKG_NAME__.context import AppContext

log = structlog.get_logger(component="updater.maintenance")

# Default rejection strategy: return a retryable busy response.
# Rollback by changing this constant to "silent_drop" and redeploying.
MAINTENANCE_STRATEGY = "busy"


async def enter_maintenance_mode(
    app_ctx: "AppContext",
    reason: str = "update",
) -> None:
    """Set the maintenance flag and log a structured event.

    Idempotent: calling twice in a row does not re-log or reset the
    entered_at timestamp on the second call. This matters because the
    POST /api/update/start handler and _graceful_shutdown_maintenance
    may both call this path on different code legs.
    """
    if app_ctx.maintenance_mode:
        log.debug("maintenance_mode_already_active", reason=reason)
        return
    app_ctx.maintenance_mode = True
    app_ctx.maintenance_entered_at = time.time()
    log.info(
        "maintenance_mode_entered",
        reason=reason,
        entered_at=app_ctx.maintenance_entered_at,
        strategy=MAINTENANCE_STRATEGY,
    )


async def exit_maintenance_mode(app_ctx: "AppContext") -> None:
    """Clear the maintenance flag.

    Only meaningful in failure-recovery paths — under normal operation
    the process exits during shutdown and the flag goes away with it.
    """
    if not app_ctx.maintenance_mode:
        log.debug("maintenance_mode_already_inactive")
        return
    app_ctx.maintenance_mode = False
    log.info("maintenance_mode_exited")


def is_write_allowed(app_ctx: "AppContext") -> bool:
    """Return True iff client writes should be forwarded downstream.

    Used by tests and by the app's write gate (which calls this via a
    simpler attribute check on the hot path).
    """
    return not getattr(app_ctx, "maintenance_mode", False)


async def drain_inflight_writes(
    app_ctx: "AppContext",
    timeout_s: float = 2.0,
) -> bool:
    """Wait up to ``timeout_s`` seconds for in-flight writes to finish.

    Drain time should be longer than your clients' poll/retry cycle so
    in-flight transactions settle before the process is terminated.

    Returns True if the counter reached zero within the timeout, False
    if the wait timed out. Also returns True if the client context has
    not been wired yet (nothing to drain).
    """
    client_ctx = getattr(app_ctx, "_client_ctx", None)
    if client_ctx is None:
        log.debug("drain_inflight_no_client_ctx")
        return True
    inflight = getattr(client_ctx, "_inflight_count", 0)
    event = getattr(client_ctx, "_inflight_drained", None)
    if inflight == 0 or event is None:
        return True
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout_s)
        log.info(
            "write_drain_complete",
            timeout_s=timeout_s,
            final_inflight=getattr(client_ctx, "_inflight_count", -1),
        )
        return True
    except asyncio.TimeoutError:
        log.warning(
            "write_drain_timeout",
            timeout_s=timeout_s,
            stuck_inflight=getattr(client_ctx, "_inflight_count", -1),
        )
        return False
