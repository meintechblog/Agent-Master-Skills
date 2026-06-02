"""UpdateCheckScheduler — asyncio task that polls GitHub Releases.

Requirements:
    Background scheduler as asyncio Task in main loop, polling GitHub
        Releases hourly by default.
    Fault tolerant — any error logs a warning and the loop continues.
        CancelledError is the only exception allowed to propagate (that's
        the asyncio contract for clean shutdown).
    If a WebSocket client is connected (user is live in the dashboard),
        defer the check to the next interval so we don't poll during an
        active session.

References:
    - github_client.GithubReleaseClient for the fetch contract

Design notes:
    The scheduler is intentionally minimal — no cron, no drift correction,
    no jitter. The interval is "approximately every N seconds" and that's
    exactly what we need. A user-active probe happens at the top of
    every iteration; probe failures DO NOT skip the check, they fall
    through (log a warning and try anyway) so a broken probe can't lock
    out updates forever.

    The scheduler's ``last_check_failed_at`` tracks SCHEDULER-level
    failures only — i.e., exceptions bubbling out of fetch_latest_release
    or the user callback. Network errors swallowed inside the client
    itself are not reflected here (the client's own logs surface those).
    A future revision may introduce a richer FetchResult to distinguish
    "no release" from "network failed" at this layer.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Awaitable, Callable, Optional

import structlog

from __PKG_NAME__.updater.github_client import (
    GithubReleaseClient,
    ReleaseInfo,
)

log = structlog.get_logger(component="updater.scheduler")

#: 1h default interval. Tunable via config.
DEFAULT_INTERVAL_SECONDS = 3600.0

#: Initial delay after service start — avoids hammering GitHub on
#: restart loops (DoS mitigation).
DEFAULT_INITIAL_DELAY_SECONDS = 60.0


OnUpdateCallback = Callable[[Optional[ReleaseInfo]], "Awaitable[None] | None"]
IsActiveCallback = Callable[[], bool]


class UpdateCheckScheduler:
    """Periodic GitHub Releases poll loop.

    Lifecycle:
        scheduler = UpdateCheckScheduler(client, on_update, is_active)
        scheduler.start()   # fires and forgets an asyncio.Task
        ...
        await scheduler.stop()   # clean cancellation

    Args:
        github_client: The :class:`GithubReleaseClient` to poll with.
        on_update_available: Callback invoked with each fetch result
            (``ReleaseInfo`` or ``None``). May be sync or async.
        has_active_websocket_client: Sync predicate that returns True when
            a user is actively connected — the iteration will be deferred.
            Probe failures are logged and treated as "not active".
        interval_seconds: Seconds between iterations. Default 3600 (1h).
        initial_delay_seconds: Seconds to wait before the first check.
            Default 60.
    """

    def __init__(
        self,
        github_client: GithubReleaseClient,
        on_update_available: OnUpdateCallback,
        has_active_websocket_client: IsActiveCallback,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        initial_delay_seconds: float = DEFAULT_INITIAL_DELAY_SECONDS,
    ) -> None:
        self._client = github_client
        self._on_update = on_update_available
        self._has_active_client = has_active_websocket_client
        self._interval = float(interval_seconds)
        self._initial_delay = float(initial_delay_seconds)
        self._task: Optional[asyncio.Task[None]] = None
        self._last_check_at: Optional[float] = None
        self._last_check_failed_at: Optional[float] = None

    # ------------------------------------------------------------------
    # Public state (read by the webapp for UI surfacing)
    # ------------------------------------------------------------------

    @property
    def last_check_at(self) -> Optional[float]:
        """UNIX timestamp of the last SUCCESSFUL iteration (sched-level)."""
        return self._last_check_at

    @property
    def last_check_failed_at(self) -> Optional[float]:
        """UNIX timestamp of the last iteration that raised at scheduler level."""
        return self._last_check_failed_at

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> asyncio.Task[None]:
        """Start the background loop; idempotent if already running."""
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(
            self._run(), name="update_check_scheduler"
        )
        return self._task

    async def stop(self) -> None:
        """Cancel the background loop and await clean shutdown.

        Idempotent: safe to call multiple times. Swallows CancelledError
        from the task — callers do not see shutdown as an error.
        """
        if self._task is None:
            return
        if self._task.done():
            self._task = None
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "update_scheduler_stop_unexpected",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        finally:
            self._task = None

    # ------------------------------------------------------------------
    # Loop internals
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        log.info(
            "update_scheduler_starting",
            initial_delay_s=self._initial_delay,
            interval_s=self._interval,
        )
        try:
            if self._initial_delay > 0:
                await asyncio.sleep(self._initial_delay)
            while True:
                await self._run_one_iteration()
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            log.info("update_scheduler_cancelled")
            raise

    async def check_once(self) -> Optional[ReleaseInfo]:
        """Force a single check cycle without waiting for the loop timer.

        Wired into ``POST /api/update/check`` so the user
        can click "Check for updates" and get an immediate answer rather
        than waiting up to an hour for the next scheduler tick.

        This bypasses the active-user probe (the user is clearly active
        if they just clicked the button) but still runs through the real
        GitHub client + callback so the result flows back into
        ``app_ctx.available_update`` via the normal pipeline.

        Returns the :class:`ReleaseInfo` if a release is surfaced, or
        ``None`` when the repo has no releases / the latest matches the
        current version. Exceptions from the GitHub client or the callback
        propagate to the caller — the webapp handler translates them into
        a 500 response.
        """
        release = await self._client.fetch_latest_release()
        try:
            await self._invoke_callback(release)
        except Exception as exc:
            log.warning(
                "update_check_once_callback_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._last_check_failed_at = time.time()
            raise
        self._last_check_at = time.time()
        return release

    async def _run_one_iteration(self) -> None:
        """Execute one poll iteration.

        Order of operations:
        1. Active-user probe. If active, skip and return early.
           Probe failures are logged but fall through to step 2.
        2. Fetch latest release. Exceptions are caught and recorded.
        3. Invoke the on_update_available callback. Exceptions are caught
           and recorded.
        4. Update last_check_at on success.
        """
        # 1. User-active probe
        try:
            if self._has_active_client():
                log.info("update_check_deferred_user_active")
                return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "update_check_active_probe_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # Fall through — a broken probe must not permanently block checks.

        # 2 + 3. Fetch + callback (wrapped together: either failing means
        # the iteration failed)
        try:
            log.info("update_check_started")
            release = await self._client.fetch_latest_release()
            await self._invoke_callback(release)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(
                "update_check_iteration_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._last_check_failed_at = time.time()
            return

        # 4. Success bookkeeping
        self._last_check_at = time.time()
        if release is None:
            log.info("update_not_available")
        else:
            log.info(
                "update_available",
                tag=release.tag_name,
                published_at=release.published_at,
            )

    async def _invoke_callback(
        self, release: Optional[ReleaseInfo]
    ) -> None:
        """Invoke on_update_available, awaiting if async."""
        result = self._on_update(release)
        if inspect.isawaitable(result):
            await result
