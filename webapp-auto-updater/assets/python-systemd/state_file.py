"""Persistent state file (example support module).

Writes to /etc/__APP_SLUG__/state.json atomically via os.replace.
Reads are defensive: missing, corrupt, or wrong-schema files return
safe defaults, never raise. The main service writes on state changes,
reads on boot and restores if the timestamp is still fresh enough.

This is an example of an app-owned support module that the updater can
import. The generator only copies it when the target repo does not
already provide its own ``state_file`` module — adapt the fields to
whatever runtime state your app needs to survive a restart.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(component="state_file")

STATE_FILE_PATH: Path = Path("/etc/__APP_SLUG__/state.json")


@dataclass
class PersistedState:
    """Schema for /etc/__APP_SLUG__/state.json.

    Fields (example — replace with your app's runtime state):
        value: Last persisted value. None = not set.
        value_set_at: UNIX timestamp when ``value`` was set. Used for staleness.
        active: Generic boolean toggle.
        active_set_at: UNIX timestamp when ``active`` was last toggled.
        schema_version: For future migrations. Current schema = 1.
    """
    value: float | None = None
    value_set_at: float | None = None
    active: bool = False
    active_set_at: float | None = None
    schema_version: int = 1


def load_state(path: Path | None = None) -> PersistedState:
    """Load persisted state from disk. Never raises.

    Returns PersistedState() on any error path:
    - missing file
    - unreadable file (OSError)
    - corrupt JSON
    - JSON that is not a top-level dict
    - schema_version missing or not 1

    Unknown fields in the JSON are ignored (forward-compat with future
    schema additions in the same major version).
    """
    target = path or STATE_FILE_PATH
    if not target.exists():
        return PersistedState()
    try:
        raw = target.read_text()
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        log.warning("state_file_corrupt", path=str(target), error=str(e))
        return PersistedState()
    except OSError as e:
        log.warning("state_file_read_error", path=str(target), error=str(e))
        return PersistedState()

    if not isinstance(data, dict):
        log.warning(
            "state_file_wrong_type",
            path=str(target),
            type=type(data).__name__,
        )
        return PersistedState()

    schema = data.get("schema_version")
    if schema != 1:
        log.warning(
            "state_file_unsupported_schema",
            path=str(target),
            schema=schema,
        )
        return PersistedState()

    try:
        return PersistedState(**{
            k: v for k, v in data.items()
            if k in PersistedState.__dataclass_fields__
        })
    except Exception as e:  # pragma: no cover - defensive
        log.error("state_file_construct_failed", path=str(target), error=str(e))
        return PersistedState()


def save_state(state: PersistedState, path: Path | None = None) -> None:
    """Atomically write state to disk via tempfile + os.replace.

    Temp file lives in the same directory as the target (required for
    atomicity on POSIX — cross-device rename is not atomic). On success,
    file mode is set to 0o644 (world-readable, owner-writable).

    Raises:
        FileNotFoundError: if parent directory does not exist (install bug).
        OSError: on any other write failure (EACCES, ENOSPC, ...).

    The caller decides whether to swallow or propagate these; this module
    re-raises intentionally so silent install/permission bugs surface
    loudly rather than being masked as runtime state loss.
    """
    target = path or STATE_FILE_PATH
    tmp = target.with_suffix(".json.tmp")
    payload = json.dumps(asdict(state), indent=2, sort_keys=True)
    try:
        tmp.write_text(payload)
        os.replace(tmp, target)
        os.chmod(target, 0o644)
    except FileNotFoundError:
        log.error(
            "state_file_parent_missing",
            path=str(target),
            hint="run install.sh to create /etc/__APP_SLUG__",
        )
        raise
    except OSError as e:
        log.error("state_file_write_failed", path=str(target), error=str(e))
        # best-effort cleanup of the .tmp if it exists
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def is_value_fresh(
    state: PersistedState,
    max_age_s: float,
    now: float | None = None,
) -> bool:
    """Staleness gate used at boot.

    Returns True iff a value is recorded AND its age is strictly less than
    ``max_age_s``. Use this on the boot restore path to decide whether the
    persisted value is still recent enough to re-apply.

    Args:
        state: The loaded PersistedState.
        max_age_s: Maximum acceptable age in seconds.
        now: Optional override for time.time(); used for deterministic tests.
    """
    if state.value is None or state.value_set_at is None:
        return False
    current = now if now is not None else time.time()
    age = current - state.value_set_at
    return age < max_age_s
