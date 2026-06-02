"""Atomic phase-progression writer for ``/etc/__APP_SLUG__/update-status.json``.

: the updater calls:meth:`StatusFileWriter.write_phase` at every
state transition so the main service (reader lives in
``updater.status.load_status``) can surface phase info in ``/api/health``
and can build a progress UI.

Mode is ``0o644`` so the service user can read but only root (this module)
writes. The directory ``/etc/__APP_SLUG__/`` is created by install.sh;
this module never creates it.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import structlog

log = structlog.get_logger(component="updater_root.status_writer")

#: Canonical on-disk location. Written by root updater only.
STATUS_FILE_PATH: Path = Path("/etc/__APP_SLUG__/update-status.json")

#: World-readable; only root writes.
STATUS_FILE_MODE: int = 0o644

#: Documented phase allowlist. Writing a phase not in this set logs a
#: warning but is NOT blocked — the state machine is the authority, and
#: a typo should not silently drop a phase transition in a safety-critical
#: update path.
PHASES: frozenset[str] = frozenset(
    {
        "trigger_received",
        "backup",
        "extract",
        "pip_install_dryrun",
        "pip_install",
        "compileall",
        "smoke_import",
        "config_dryrun",
        "pending_marker_written",
        "symlink_flipped",
        "restarting",
        "healthcheck",
        "done",
        # Rollback branch phases
        "rollback_starting",
        "rollback_symlink_flipped",
        "rollback_restarting",
        "rollback_healthcheck",
        "rollback_done",
        "rollback_failed",
    }
)


def _iso_utc(t: float) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ`` — second-precision UTC."""
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StatusFileWriter:
    """Atomic phase-progression writer for the update-status.json file.

    Not thread-safe — designed for a single updater_root process running
    one update attempt at a time.

    Lifecycle:
        1. ``begin(nonce, target_sha, old_sha)`` — seeds current + history
           with a ``trigger_received`` entry and flushes.
        2. Repeated ``write_phase(phase)`` — appends each transition.
        3. ``finalize(outcome)`` — writes the terminal phase. ``current``
           stays populated so the UI can show the last result.

    The writer defers creating ``self._path.parent`` — if the directory
    is missing (shouldn't happen on a properly installed host), the
    ``os.replace`` will surface the error loudly.
    """

    def __init__(
        self,
        path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = path or STATUS_FILE_PATH
        self._clock = clock
        self._state: dict = {
            "schema_version": 1,
            "current": None,
            "history": [],
        }

    def begin(self, nonce: str, target_sha: str, old_sha: str) -> None:
        """Initialize current + history with the trigger_received entry."""
        now = self._clock()
        self._state["current"] = {
            "nonce": nonce,
            "phase": "trigger_received",
            "target_sha": target_sha,
            "old_sha": old_sha,
            "started_at": _iso_utc(now),
        }
        self._state["history"] = [
            {"phase": "trigger_received", "at": _iso_utc(now)},
        ]
        self._flush()

    def write_phase(self, phase: str, *, error: str | None = None) -> None:
        """Append a phase transition and update current.phase.

        If :meth:`begin` has not been called, this is a no-op with a
        warning — a state machine shouldn't reach here, but the updater
        must never crash on a defensive path.
        """
        if phase not in PHASES:
            log.warning("status_unknown_phase", phase=phase)
        if self._state["current"] is None:
            log.warning("status_write_phase_without_begin", phase=phase)
            return
        now = self._clock()
        self._state["current"]["phase"] = phase
        entry: dict = {"phase": phase, "at": _iso_utc(now)}
        if error is not None:
            entry["error"] = error
        self._state["history"].append(entry)
        self._flush()

    def finalize(self, outcome: str) -> None:
        """Write the terminal phase. Thin alias for ``write_phase``."""
        self.write_phase(outcome)

    def load_existing(self) -> dict | None:
        """Defensive reader for the current on-disk state.

        Returns the parsed dict, or ``None`` if the file is missing,
        unreadable, corrupt, or not a JSON object. Never raises.
        """
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.warning("status_load_failed", path=str(self._path), error=str(e))
            return None
        if not isinstance(data, dict):
            log.warning(
                "status_load_wrong_type",
                path=str(self._path),
                type=type(data).__name__,
            )
            return None
        return data

    # ------------------------------------------------------------------
    # Internal

    def _flush(self) -> None:
        """Atomic tempfile + os.replace write at mode 0o644."""
        tmp = self._path.with_name(self._path.name + ".tmp")
        blob = json.dumps(self._state, indent=2, sort_keys=True)
        try:
            tmp.write_text(blob)
            os.replace(tmp, self._path)
            os.chmod(self._path, STATUS_FILE_MODE)
        except OSError as e:
            log.error(
                "status_flush_failed",
                path=str(self._path),
                error=str(e),
            )
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise
