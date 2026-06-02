"""Defensive reader for ``/etc/__APP_SLUG__/update-status.json``.

Requirements:
: Main service reads the status file produced by the root
    updater helper (04) and surfaces the current phase in
    ``/api/health`` and ``/api/update/status`` without ever crashing —
    regardless of whether the file is missing, truncated mid-write, or
    written with an unknown schema version.

Design notes:
    This module is read-only. Writes to the status file happen only in
    ``updater_root/runner.py`` (running as root). The split keeps the
    attack surface clean: the main service never needs write access to
    the status file.

    The read is deliberately silent on missing files — an empty status
    means "no update has ever been triggered", which is the steady state
    on a fresh install. It's not an error.

    Mirrors the defensive pattern in
    :func:`__PKG_NAME__.state_file.load_state`: any read error,
    decode error, wrong-type, or schema mismatch returns a default
    :class:`UpdateStatus` with ``current=None`` and ``history=[]``.
    :func:`load_status` is guaranteed never to raise.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import structlog

log = structlog.get_logger(component="updater.status")

#: Canonical on-disk location. Directory is created by install.sh; this
#: module never creates it.
STATUS_FILE_PATH: Path = Path("/etc/__APP_SLUG__/update-status.json")

#: Known phase names (advisory; defensive reader does not enforce).
#: The root updater is the authoritative producer of this set.
PHASE_IDLE = "idle"
PHASE_TRIGGER_RECEIVED = "trigger_received"
PHASE_BACKUP = "backup"
PHASE_EXTRACT = "extract"
PHASE_PIP_INSTALL = "pip_install"
PHASE_CONFIG_DRYRUN = "config_dryrun"
PHASE_RESTARTING = "restarting"
PHASE_HEALTHCHECK = "healthcheck"
PHASE_DONE = "done"
PHASE_ROLLBACK = "rollback"
PHASE_ROLLBACK_FAILED = "rollback_failed"


@dataclass
class UpdateStatus:
    """Parsed view of ``update-status.json``.

    Fields:
        current: The in-flight update record, or ``None`` when idle. When
            populated, contains at minimum ``phase``, ``nonce``,
            ``target_sha``, ``old_sha``, and ``started_at`` — but this
            reader intentionally does not validate the inner shape beyond
            "is a dict", to stay forward-compatible with the root updater
            adding fields.
        history: Ordered list of phase transition records. Each entry is
            a dict with at least ``phase`` and ``at``; may carry ``error``.
        schema_version: Always 1 in the current protocol. A future bump
            would require coordinated updates in the root updater +
            this reader.
    """

    current: dict | None = None
    history: list[dict] = field(default_factory=list)
    schema_version: int = 1


def load_status(path: Path | None = None) -> UpdateStatus:
    """Read the status file, returning a safe default on any error.

    Returns :class:`UpdateStatus` with ``current=None`` and
    ``history=[]`` on every failure path:

    * File does not exist.
    * :class:`OSError` on read (directory, permission denied, ...).
    * :class:`json.JSONDecodeError` (empty, truncated, garbage).
    * :class:`UnicodeDecodeError` (non-UTF-8 bytes).
    * Top-level is not a dict (list, string, number, null).
    * ``schema_version`` missing or not equal to 1.
    * ``current`` is present but not dict/None.

    Never raises. Callers can rely on this to surface "idle" in the UI
    even when the file is corrupt.

    Args:
        path: Override for :data:`STATUS_FILE_PATH` (used in tests).
    """
    target = path or STATUS_FILE_PATH

    if not target.exists():
        return UpdateStatus()

    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("status_file_read_error", path=str(target), error=str(exc))
        return UpdateStatus()
    except UnicodeDecodeError as exc:
        log.warning("status_file_decode_error", path=str(target), error=str(exc))
        return UpdateStatus()

    if not raw:
        return UpdateStatus()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("status_file_corrupt", path=str(target), error=str(exc))
        return UpdateStatus()

    if not isinstance(data, dict):
        log.warning(
            "status_file_wrong_type",
            path=str(target),
            type=type(data).__name__,
        )
        return UpdateStatus()

    schema = data.get("schema_version")
    if schema != 1:
        log.warning(
            "status_file_unsupported_schema",
            path=str(target),
            schema=schema,
        )
        return UpdateStatus()

    current_raw = data.get("current")
    if current_raw is not None and not isinstance(current_raw, dict):
        log.warning(
            "status_file_current_wrong_type",
            path=str(target),
            type=type(current_raw).__name__,
        )
        return UpdateStatus()

    history_raw = data.get("history", [])
    if not isinstance(history_raw, list):
        log.warning(
            "status_file_history_wrong_type",
            path=str(target),
            type=type(history_raw).__name__,
        )
        history_raw = []
    else:
        # Filter out non-dict entries defensively but keep the valid ones.
        history_raw = [h for h in history_raw if isinstance(h, dict)]

    return UpdateStatus(
        current=current_raw,
        history=history_raw,
        schema_version=1,
    )


def current_phase(status: UpdateStatus) -> str:
    """Return the active phase name, or ``"idle"`` when nothing runs.

    Falls back to ``"idle"`` when:

    * ``status.current`` is ``None``.
    * ``status.current`` is a dict but has no ``phase`` key.
    """
    if status.current is None:
        return PHASE_IDLE
    phase = status.current.get("phase")
    if not isinstance(phase, str) or not phase:
        return PHASE_IDLE
    return phase
