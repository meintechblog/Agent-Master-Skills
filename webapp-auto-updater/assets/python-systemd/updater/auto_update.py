"""Nightly unattended auto-update.

Once a day at the configured local hour (default 04:00 __TZ__), if
the in-app updater is enabled AND ``auto_install`` is on AND a newer GitHub
release is available, write an update trigger so the privileged updater
(``__APP_SLUG__-updater.service``) installs it unattended — same
mechanism as the manual "Installieren" button, just driven by a timer.

Why a dedicated nightly loop instead of the existing interval scheduler:
the scheduler only *detects* releases (sets ``available_update``); it never
installs. Auto-install at a fixed quiet hour is the behaviour the operator
asked for, so the action lives here and the install window is predictable.

The box clock is UTC; the operator means 04:00 local, so we resolve the
target in __TZ__ (DST-aware via zoneinfo) and fall back to naive
system time only if tz data is unavailable.
"""
from __future__ import annotations

import asyncio
import datetime

import structlog

from __PKG_NAME__.updater.config import load_update_config
from __PKG_NAME__.updater.trigger import (
    TriggerPayload,
    generate_nonce,
    now_iso_utc,
    write_trigger,
)

log = structlog.get_logger(component="updater.auto_update")

#: Local hour (0-23) the nightly auto-update fires at.
AUTO_UPDATE_HOUR = 4
#: Timezone the operator thinks in. The container itself runs UTC.
AUTO_UPDATE_TZ = "__TZ__"


def _local_now(tz_name: str = AUTO_UPDATE_TZ) -> datetime.datetime:
    """Current time in ``tz_name``; naive system time if tz data is missing."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.datetime.now(ZoneInfo(tz_name))
    except Exception:  # pragma: no cover - tzdata missing
        return datetime.datetime.now()


def seconds_until_next(hour: int, now: datetime.datetime | None = None) -> float:
    """Seconds from ``now`` until the next occurrence of ``hour:00`` local."""
    current = now if now is not None else _local_now()
    target = current.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= current:
        target += datetime.timedelta(days=1)
    return max(0.0, (target - current).total_seconds())


async def run_auto_update_once(app_ctx, config_path, scheduler=None) -> bool:
    """One auto-update pass. Returns True iff an install trigger was written.

    Best-effort and exception-safe at the edges; the loop wraps it too.
    """
    cfg = load_update_config(config_path)
    if not (cfg.enabled and cfg.auto_install):
        log.info(
            "auto_update_skipped_disabled",
            enabled=cfg.enabled,
            auto_install=cfg.auto_install,
        )
        return False

    # Refresh availability through the normal pipeline so available_update
    # (incl. latest_commit) is current.
    if scheduler is not None:
        try:
            await scheduler.check_once()
        except Exception as exc:
            log.warning("auto_update_check_failed", error=str(exc))

    avail = getattr(app_ctx, "available_update", None)
    if not avail:
        log.info("auto_update_no_update")
        return False

    sha = avail.get("latest_commit")
    if not sha:
        log.warning("auto_update_no_target_sha", version=avail.get("latest_version"))
        return False

    payload = TriggerPayload(
        op="update",
        target_sha=sha,
        requested_at=now_iso_utc(),
        requested_by="auto-update",
        nonce=generate_nonce(),
    )
    try:
        payload.validate()
    except ValueError as exc:
        log.warning("auto_update_invalid_payload", error=str(exc))
        return False

    # Mirror the manual path: enter maintenance mode (busy response to
    # clients) before the trigger so the swap is graceful. Best-effort.
    try:
        from __PKG_NAME__.updater.maintenance import enter_maintenance_mode

        await enter_maintenance_mode(app_ctx, reason="auto_update")
    except Exception as exc:  # pragma: no cover - best effort
        log.warning("auto_update_maintenance_failed", error=str(exc))

    write_trigger(payload)
    log.info(
        "auto_update_triggered",
        target_sha=sha,
        version=avail.get("latest_version"),
    )
    return True


async def nightly_auto_update_loop(
    app_ctx, config_path, *, scheduler=None, hour: int = AUTO_UPDATE_HOUR
) -> None:
    """Sleep until the next ``hour:00`` local, run one pass, repeat daily."""
    log.info("auto_update_loop_started", hour=hour, tz=AUTO_UPDATE_TZ)
    while True:
        delay = seconds_until_next(hour)
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            raise
        try:
            await run_auto_update_once(app_ctx, config_path, scheduler)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("auto_update_iteration_failed", error=str(exc))
        # Guard against a fast re-fire if the clock lands exactly on the hour.
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
