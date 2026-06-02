"""Progress broadcaster: polls update-status.json and pushes
`update_progress` WS messages.

Design:
    * A single module-level asyncio.Task (one per webapp process) reads
      the status file every :data:`ACTIVE_POLL_INTERVAL_S` seconds while
      `current_phase()` is not in :data:`IDLE_PHASES`, and backs off to
      :data:`IDLE_POLL_INTERVAL_S` seconds when idle.
    * Each new `history[]` entry is emitted as exactly one
      `{"type": "update_progress", "data": {...}}` WS message to every
      registered ws client.
    * Dedupe uses the monotonic `sequence` field on history entries if
      present. If `updater_root/status_writer.py` does NOT write
      `sequence`, the broadcaster falls back to the entry's index in
      `history[]`. This keeps the broadcaster working with the updater's
      output without a status-writer bump.
    * The task is wrapped in belt-and-braces exception handling — a
      malformed status file, a load_status() crash, or a dead WS client
      must never take the task down. A downed progress broadcaster would
      freeze the UI's phase checklist without any error signal.

No coupling to webapp.py — the module talks to the aiohttp `app` dict
contract only (`app["ws_clients"]`). Your app wiring connects
:func:`start_broadcaster` into `create_webapp` on_startup and
:func:`stop_broadcaster` into on_cleanup.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import structlog
from aiohttp import web

from __PKG_NAME__.updater.status import current_phase, load_status

#: Active polling interval while an update is in progress.
ACTIVE_POLL_INTERVAL_S: float = 0.5

#: Idle polling interval when current_phase is in:data:`IDLE_PHASES`.
IDLE_POLL_INTERVAL_S: float = 5.0

#: Terminal/quiescent phases. The updater keeps `current` populated after
#: `done` so that the UI can show the last outcome; a correct guard must
#: check the phase name, not `current is not None`.
IDLE_PHASES: frozenset[str] = frozenset(
    {"idle", "done", "rollback_done", "rollback_failed"}
)

#: WS message type for progress updates.
WS_MESSAGE_TYPE: str = "update_progress"

#: aiohttp app dict key used by :func:`start_broadcaster` /
#: :func:`stop_broadcaster` to stash the singleton instance.
APP_KEY: str = "progress_broadcaster"

_logger = structlog.get_logger(component="updater.progress")


class ProgressBroadcaster:
    """Polls the update-status.json file and broadcasts new history entries.

    Lifecycle:
        1. :meth:`start` — spawns the internal asyncio task.
        2. Task loops: poll, broadcast new entries, sleep for the
           interval dictated by the observed phase.
        3. :meth:`stop` — signals the task to stop and awaits its exit.
    """

    def __init__(
        self,
        app: web.Application | dict,
        *,
        status_path: Path | None = None,
        active_interval: float = ACTIVE_POLL_INTERVAL_S,
        idle_interval: float = IDLE_POLL_INTERVAL_S,
    ) -> None:
        self._app = app
        self._status_path = status_path
        self._active = active_interval
        self._idle = idle_interval
        self._last_sequence: int = -1
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------
    # Interval selection

    def _next_interval(self, phase: str) -> float:
        """Return the poll interval for the given phase name."""
        return self._idle if phase in IDLE_PHASES else self._active

    # ------------------------------------------------------------------
    # Single poll iteration (exposed for unit tests)

    async def _poll_once(self) -> str:
        """Poll the status file once, emit new history entries.

        Returns the observed current phase name, or ``"unknown"`` on any
        load error. Never raises.
        """
        try:
            status = (
                load_status(self._status_path)
                if self._status_path is not None
                else load_status()
            )
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning("progress_load_status_failed", error=str(exc))
            return "unknown"

        history = self._extract_history(status)
        if history:
            await self._emit_new_entries(history)

        try:
            return current_phase(status)
        except Exception as exc:  # pragma: no cover - defensive
            _logger.warning("progress_current_phase_failed", error=str(exc))
            return "unknown"

    @staticmethod
    def _extract_history(status: Any) -> list[Any]:
        """Pull `history` off the status object defensively.

        The real :class:`UpdateStatus` is a dataclass, but fakes and future
        schema shifts may hand us a dict.
        """
        history = getattr(status, "history", None)
        if history is None and isinstance(status, dict):
            history = status.get("history")
        if not isinstance(history, list):
            return []
        return history

    async def _emit_new_entries(self, history: list[Any]) -> None:
        """Broadcast history entries with sequence > last_sequence_sent.

        If an entry lacks a `sequence` field, falls back to its index in
        `history[]` (see module docstring).
        """
        new_entries: list[tuple[int, Any]] = []
        for idx, entry in enumerate(history):
            seq = self._entry_sequence(entry, idx)
            if seq is None:
                continue
            if seq > self._last_sequence:
                new_entries.append((seq, entry))

        if not new_entries:
            return

        new_entries.sort(key=lambda t: t[0])
        for seq, entry in new_entries:
            payload = self._envelope(entry, seq)
            await self._broadcast(payload)
            self._last_sequence = seq

    @staticmethod
    def _entry_sequence(entry: Any, fallback_index: int) -> int | None:
        """Return the monotonic sequence for an entry.

        Prefers an explicit ``sequence`` field (dict or attribute), falls
        back to the entry's index in ``history[]`` when absent (
        status_writer does not currently write the field). Returns
        ``None`` for malformed (non-int) sequence values — those entries
        are silently dropped rather than crashing the broadcaster.
        """
        raw: Any
        if isinstance(entry, dict):
            raw = entry.get("sequence")
        else:
            raw = getattr(entry, "sequence", None)

        if raw is None:
            return fallback_index
        if isinstance(raw, bool):
            # bool is a subclass of int — reject it explicitly.
            return None
        if isinstance(raw, int):
            return raw
        return None

    def _envelope(self, entry: Any, sequence: int) -> str:
        """Build the JSON envelope for one history entry."""
        if isinstance(entry, dict):
            data = dict(entry)
        else:
            data = {
                "phase": getattr(entry, "phase", None),
                "at": getattr(entry, "at", None),
                "sequence": getattr(entry, "sequence", None),
                "error": getattr(entry, "error", None),
            }
        # Ensure `sequence` is always present in the emitted data, even
        # when the status file omits it — the client dedupes on this.
        data.setdefault("sequence", sequence)
        if data.get("sequence") is None:
            data["sequence"] = sequence
        data.setdefault("error", None)
        return json.dumps(
            {"type": WS_MESSAGE_TYPE, "data": data},
            separators=(",", ":"),
        )

    async def _broadcast(self, payload: str) -> None:
        """Send `payload` to every ws client; evict dead ones.

        Mirrors the `broadcast_available_update` pattern so ws
        lifecycle semantics stay consistent across all push paths.
        """
        clients = None
        if hasattr(self._app, "get"):
            try:
                clients = self._app.get("ws_clients")
            except Exception:  # pragma: no cover - defensive
                clients = None
        if not clients:
            return

        dead: list[Any] = []
        for ws in list(clients):
            try:
                await ws.send_str(payload)
            except (ConnectionError, RuntimeError, ConnectionResetError):
                dead.append(ws)
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warning("progress_ws_send_failed", error=str(exc))
                dead.append(ws)
        for ws in dead:
            try:
                clients.discard(ws)
            except AttributeError:  # pragma: no cover - defensive
                pass

    # ------------------------------------------------------------------
    # Task loop + lifecycle

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                phase = await self._poll_once()
            except Exception as exc:  # pragma: no cover - defensive
                _logger.warning("progress_poll_loop_error", error=str(exc))
                phase = "unknown"
            interval = self._next_interval(phase)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
            else:
                break

    async def start(self) -> None:
        """Start the polling task. Idempotent."""
        if self._task is not None:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self._loop(), name="progress_broadcaster"
        )

    async def stop(self) -> None:
        """Stop the polling task and await its exit. Idempotent."""
        if self._task is None:
            return
        self._stop.set()
        try:
            await asyncio.wait_for(self._task, timeout=2.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            pass
        self._task = None


# ---------------------------------------------------------------------------
# App lifecycle helpers (called by create_webapp on_startup / on_cleanup
# in).


async def start_broadcaster(app: web.Application) -> None:
    """on_startup hook: instantiate and start the singleton broadcaster."""
    broadcaster = ProgressBroadcaster(app)
    app[APP_KEY] = broadcaster
    await broadcaster.start()


async def stop_broadcaster(app: web.Application) -> None:
    """on_cleanup hook: stop the singleton broadcaster if present."""
    broadcaster = app.get(APP_KEY) if hasattr(app, "get") else None
    if broadcaster is not None:
        await broadcaster.stop()
