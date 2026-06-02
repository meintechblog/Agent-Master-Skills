"""Boot-time recovery hook.

Runs as root via __APP_SLUG__-recovery.service BEFORE the main service
on every boot. If a PENDING update marker is found WITHOUT a corresponding
post-update LAST_BOOT_SUCCESS, the previous release symlink is restored.

Critical design rules:
1. NEVER exit non-zero. If recovery cannot help, it logs CRITICAL and exits 0
   so the main service still attempts to start. A failing recovery unit
   would block boot, which is strictly worse than a no-op.
2. Only flip the symlink when we have a VALID previous_release directory.
   Bogus markers are ignored with a CRITICAL log, not acted upon.
3. The symlink flip is atomic via os.replace — if we crash mid-flip the
   old symlink is still intact.
4. Once we successfully flip, delete the PENDING marker so we don't loop
   on the next boot.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import structlog

from __PKG_NAME__.releases import (
    CURRENT_SYMLINK_NAME,
    RELEASES_ROOT,
)

PENDING_MARKER_PATH: Path = Path("/var/lib/__APP_SLUG__/update-pending.marker")
LAST_BOOT_SUCCESS_PATH: Path = Path("/var/lib/__APP_SLUG__/last-boot-success.marker")

#: — tmpfs flag touched by ``updater_root.runner`` while an
#: update is in flight. When this file exists, the recovery hook skips
#: rollback because the updater is actively managing the restart cycle
#: and will handle rollback itself via HealthChecker. Because the file
#: lives on ``/run`` (tmpfs), it vanishes automatically on every reboot,
#: which preserves the recovery guarantee at actual boot time.
#:
#: NOT placed inside /run/__APP_SLUG__/ because that is the
#: main service's RuntimeDirectory, which systemd destroys whenever
#: the main service stops — including the in-flight restart the
#: updater is issuing — defeating the purpose of the flag. Using the
#: top-level /run directory means the flag survives the main service
#: restart but still vanishes on actual reboot.
UPDATER_ACTIVE_FLAG: Path = Path("/run/__APP_SLUG__-updater-active")


def _configure_logging() -> None:
    """Minimal structlog configuration for standalone boot-time use.

    Does NOT import logging_config — that module expects config.yaml to be
    loadable, which may fail if the recovery unit runs before config is in
    place. We use a bare structlog setup with JSON output to stdout; systemd
    captures it into journald via StandardOutput=journal.
    """
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )


log = structlog.get_logger(component="recovery")


@dataclass
class PendingMarker:
    """Schema v1 representation of the update-pending marker.

    Written by the privileged updater BEFORE the symlink flip, and
    consumed by recovery.py on the subsequent boot to decide whether to roll
    back.
    """

    previous_release: str
    target_release: str
    created_at: float
    reason: str = "update"
    schema_version: int = 1


def load_pending_marker(path: Path | None = None) -> PendingMarker | None:
    """Load and validate the PENDING marker.

    Returns None on any failure (missing file, corrupt JSON, wrong schema,
    invalid paths). Never raises — the recovery path must be bullet-proof.
    """
    target = path or PENDING_MARKER_PATH
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("pending_marker_unreadable", path=str(target), error=str(e))
        return None
    if not isinstance(data, dict):
        log.warning("pending_marker_wrong_type", path=str(target))
        return None
    if data.get("schema_version") != 1:
        log.warning(
            "pending_marker_unsupported_schema",
            schema=data.get("schema_version"),
        )
        return None
    prev = data.get("previous_release")
    tgt = data.get("target_release")
    created = data.get("created_at")
    if not isinstance(prev, str) or not prev.startswith("/"):
        log.warning("pending_marker_bad_previous", previous=prev)
        return None
    if not isinstance(tgt, str) or not tgt.startswith("/"):
        log.warning("pending_marker_bad_target", target=tgt)
        return None
    if not isinstance(created, (int, float)):
        log.warning("pending_marker_bad_created_at", created_at=created)
        return None
    return PendingMarker(
        previous_release=prev,
        target_release=tgt,
        created_at=float(created),
        reason=str(data.get("reason", "update")),
    )


def clear_pending_marker(path: Path | None = None) -> None:
    """Unlink the PENDING marker. Silently succeeds if missing.

    Does not raise on OSError — the caller has already decided the rollback
    is complete; a failure to clear the marker means the next boot will
    re-attempt the rollback against the now-current release, which is
    detected as a no-op by is_dir() checks.
    """
    target = path or PENDING_MARKER_PATH
    try:
        target.unlink(missing_ok=True)
    except OSError as e:
        log.warning("pending_marker_unlink_failed", path=str(target), error=str(e))


def _atomic_symlink_flip(current_link: Path, new_target: Path) -> None:
    """Atomically replace ``current_link`` to point at ``new_target``.

    Uses the standard ``ln -sfn`` + ``mv -T`` pattern via ``os.replace``,
    which POSIX guarantees is atomic: either the old or new symlink exists,
    never neither. Raises OSError on failure (caller must handle).
    """
    tmp = current_link.with_name(current_link.name + ".new")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(new_target)
    os.replace(tmp, current_link)


def recover_if_needed(
    pending_path: Path | None = None,
    last_success_path: Path | None = None,
    releases_root: Path | None = None,
    updater_active_flag: Path | None = None,
) -> str:
    """Core recovery decision logic.

    Returns one of:
    - "no_pending": no PENDING marker (normal boot)
    - "updater_active": PENDING present but updater-active tmpfs flag is set
    - "stale_pending_cleaned": PENDING present but last-boot-success is newer
    - "rolled_back": symlink flipped back to previous_release
    - "target_missing": previous_release does not exist on disk
    - "flip_failed": OSError during symlink flip
    """
    p_path = pending_path or PENDING_MARKER_PATH
    s_path = last_success_path or LAST_BOOT_SUCCESS_PATH
    rr = releases_root or RELEASES_ROOT
    ua_path = updater_active_flag or UPDATER_ACTIVE_FLAG

    marker = load_pending_marker(p_path)
    if marker is None:
        log.info("no_pending_marker")
        return "no_pending"

    #: the updater manages its own restart + health + rollback
    # cycle. While an update is in flight the main service restarts twice
    # (once for the new release, possibly once for rollback) and recovery
    # must NOT interfere. The updater creates
    # /run/__APP_SLUG__/updater-active before issuing systemctl restart
    # and removes it on exit. Because /run is tmpfs the flag is gone after
    # a real reboot, so boot-time recovery still engages for genuine
    # failed-update boots (where the machine power-cycled mid-update).
    if ua_path.exists():
        log.info(
            "updater_active_skip_recovery",
            flag=str(ua_path),
            pending_target=marker.target_release,
        )
        return "updater_active"

    log.info(
        "pending_marker_found",
        previous=marker.previous_release,
        target=marker.target_release,
        created_at=marker.created_at,
    )

    # Stale marker check: last_success newer than marker = previous boot
    # completed successfully post-update. Marker was orphaned.
    if s_path.exists():
        try:
            last_success_mtime = s_path.stat().st_mtime
        except OSError as e:
            log.warning("last_success_stat_failed", error=str(e))
            last_success_mtime = 0.0
        if last_success_mtime > marker.created_at:
            log.info(
                "stale_pending_marker_cleaning",
                last_success_mtime=last_success_mtime,
                marker_created_at=marker.created_at,
            )
            clear_pending_marker(p_path)
            return "stale_pending_cleaned"

    # Genuine rollback needed.
    previous = Path(marker.previous_release)
    if not previous.is_dir():
        log.critical(
            "recovery_target_missing",
            previous_release=marker.previous_release,
            hint="manual SSH intervention required",
        )
        return "target_missing"

    current_link = rr / CURRENT_SYMLINK_NAME
    try:
        _atomic_symlink_flip(current_link, previous)
    except OSError as e:
        log.critical(
            "recovery_symlink_flip_failed",
            error=str(e),
            current_link=str(current_link),
            target=str(previous),
            hint="manual SSH intervention required",
        )
        return "flip_failed"

    log.warning(
        "recovery_rolled_back",
        previous_release=marker.previous_release,
        failed_target=marker.target_release,
    )
    clear_pending_marker(p_path)
    return "rolled_back"


def main() -> int:
    """Entry point for `python -m __PKG_NAME__.recovery`.

    ALWAYS returns 0. The recovery unit must never block boot; any non-zero
    exit would cause systemd to mark __APP_SLUG__-recovery.service as
    failed and — because the main service is RequiredBy it — prevent the
    main service from starting. That is strictly worse than a no-op: the
    user would lose the web UI used to diagnose the problem.
    """
    _configure_logging()
    try:
        outcome = recover_if_needed()
    except Exception as e:  # noqa: BLE001 - last-resort safety net
        log.critical("recovery_unexpected_exception", error=str(e))
        outcome = "exception"
    log.info("recovery_complete", outcome=outcome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
