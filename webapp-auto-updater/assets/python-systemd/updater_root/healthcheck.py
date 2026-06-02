"""Post-restart health poller with rollback triggers.

The runner calls :meth:`HealthChecker.wait_for_healthy` after restarting
the main service. The checker:

* Polls ``/api/health`` on a fixed interval.
* Demands ``consecutive_ok_required`` successive "good" responses.
* Fails fast on ``systemctl is-active`` reporting failed.
* Fails fast on version mismatch (old code still running).
* Fails after a 5xx/unreachable streak longer than
  ``degraded_5xx_timeout_s``.
* Hard-bounds the entire wait at ``hard_timeout_s``.
* Requires the main service's tmpfs ``/run/__APP_SLUG__/healthy``
  flag to exist at the moment of declared success.

"Good" response is whatever your ``/api/health`` returns on success.
The example schema below treats a payload as healthy when:

* ``status == "ok"``
* ``webapp == "ok"``

Adapt :meth:`HealthChecker._is_required_ok` to match your own health
payload (e.g. add per-component checks for downstream dependencies).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import structlog

log = structlog.get_logger(component="updater_root.healthcheck")


@dataclass
class HealthCheckConfig:
    """Runtime tuning for :class:`HealthChecker`.

    Defaults match (3 consecutive ok) and (60s hard
    timeout, 45s 5xx tolerance).
    """

    health_url: str = "http://127.0.0.1/api/health"
    healthy_flag_path: Path = Path("/run/__APP_SLUG__/healthy")
    hard_timeout_s: float = 60.0
    consecutive_ok_required: int = 3
    poll_interval_s: float = 5.0
    degraded_5xx_timeout_s: float = 45.0
    http_request_timeout_s: float = 5.0


@dataclass
class HealthCheckOutcome:
    """Result of :meth:`HealthChecker.wait_for_healthy`.

    ``reason`` is one of:

    * ``stable_ok`` — success
    * ``systemctl_failed`` — ``systemctl is-active`` returned non-zero
    * ``version_mismatch`` — response version != expected
    * ``degraded_5xx_timeout`` — continuous 5xx/unreachable > threshold
    * ``no_healthy_flag`` — 3 consecutive oks but runtime flag missing
    * ``timeout`` — hard timeout reached without stability
    """

    success: bool
    reason: str
    last_response: dict | None
    probes: int
    consecutive_ok: int


# ---------------------------------------------------------------------
# systemctl wrappers (module-level so tests can monkeypatch)

async def check_systemctl_active(
    unit: str = "__APP_SLUG__.service",
) -> bool:
    """``systemctl is-active --quiet <unit>`` -> True iff exit 0."""
    proc = await asyncio.create_subprocess_exec(
        "systemctl",
        "is-active",
        "--quiet",
        unit,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await proc.wait()
    return rc == 0


async def systemctl_restart(
    unit: str = "__APP_SLUG__.service",
) -> bool:
    """``systemctl restart <unit>`` -> True iff exit 0."""
    proc = await asyncio.create_subprocess_exec(
        "systemctl",
        "restart",
        unit,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning(
            "systemctl_restart_failed",
            unit=unit,
            returncode=proc.returncode,
            stderr=stderr.decode("utf-8", errors="replace"),
        )
        return False
    return True


# ---------------------------------------------------------------------
# HealthChecker

class HealthChecker:
    """Post-restart poller. Constructed once per restart cycle."""

    def __init__(
        self,
        config: HealthCheckConfig,
        expected_version: str | None,
        expected_commit: str | None,
        session_factory: Callable[[], Awaitable[Any]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._cfg = config
        self._expected_version = expected_version
        self._expected_commit = expected_commit
        self._session_factory = session_factory
        self._clock = clock

    @staticmethod
    def _is_required_ok(body: dict) -> bool:
        """Check required-for-success components of the health payload.

        Adapt this to your own ``/api/health`` schema. The example here
        treats the app as healthy when the top-level ``status`` and the
        ``webapp`` component both report ``"ok"``. Add further per-component
        checks (downstream dependencies, queues, etc.) as needed.
        """
        if body.get("status") != "ok":
            return False
        if body.get("webapp") != "ok":
            return False
        return True

    async def wait_for_healthy(self) -> HealthCheckOutcome:
        """Poll ``/api/health`` until stable-ok, timeout, or hard fail.

        Always closes the aiohttp session before returning.
        """
        t0 = self._clock()
        consecutive_ok = 0
        probes = 0
        last_response: dict | None = None
        # Track streak of non-OK HTTP responses for degraded_5xx_timeout.
        first_degraded_at: float | None = None

        session = await self._session_factory()
        try:
            while True:
                now = self._clock()
                elapsed = now - t0
                if (
                    consecutive_ok >= self._cfg.consecutive_ok_required
                    and self._cfg.healthy_flag_path.exists()
                ):
                    return HealthCheckOutcome(
                        success=True,
                        reason="stable_ok",
                        last_response=last_response,
                        probes=probes,
                        consecutive_ok=consecutive_ok,
                    )
                if elapsed >= self._cfg.hard_timeout_s:
                    break

                await asyncio.sleep(self._cfg.poll_interval_s)
                probes += 1

                #: systemctl is-active failed == rollback trigger
                if not await check_systemctl_active():
                    return HealthCheckOutcome(
                        success=False,
                        reason="systemctl_failed",
                        last_response=last_response,
                        probes=probes,
                        consecutive_ok=0,
                    )

                try:
                    ctx = session.get(
                        self._cfg.health_url,
                        timeout=self._cfg.http_request_timeout_s,
                    )
                    async with ctx as resp:
                        status = resp.status
                        if status >= 500:
                            consecutive_ok = 0
                            if first_degraded_at is None:
                                first_degraded_at = now
                            elif (
                                now - first_degraded_at
                                >= self._cfg.degraded_5xx_timeout_s
                            ):
                                return HealthCheckOutcome(
                                    success=False,
                                    reason="degraded_5xx_timeout",
                                    last_response=last_response,
                                    probes=probes,
                                    consecutive_ok=0,
                                )
                            continue
                        # 2xx/3xx/4xx
                        try:
                            body = await resp.json()
                        except Exception as e:  # noqa: BLE001
                            log.warning("healthcheck_json_error", error=str(e))
                            consecutive_ok = 0
                            continue
                        last_response = body
                        first_degraded_at = None  # reset streak
                        # version mismatch -> immediate fail.
                        # Only checked when expected_version is set (the
                        # UI path with real version strings). expected_commit
                        # is advisory only — the main service's commit field
                        # may be a short SHA derived from a COMMIT file that
                        # does not match the update target's git SHA format,
                        # so we cannot reliably compare the two.
                        # The primary guarantee against "old code still
                        # running" comes from systemctl restart force-killing
                        # the previous process before the new one binds.
                        if (
                            self._expected_version
                            and body.get("version")
                            != self._expected_version
                        ):
                            return HealthCheckOutcome(
                                success=False,
                                reason="version_mismatch",
                                last_response=body,
                                probes=probes,
                                consecutive_ok=consecutive_ok,
                            )
                        if self._is_required_ok(body):
                            consecutive_ok += 1
                        else:
                            consecutive_ok = 0
                except Exception as e:  # noqa: BLE001
                    log.warning("healthcheck_request_error", error=str(e))
                    consecutive_ok = 0
                    if first_degraded_at is None:
                        first_degraded_at = now
                    elif (
                        now - first_degraded_at
                        >= self._cfg.degraded_5xx_timeout_s
                    ):
                        return HealthCheckOutcome(
                            success=False,
                            reason="degraded_5xx_timeout",
                            last_response=last_response,
                            probes=probes,
                            consecutive_ok=0,
                        )
                    last_response = {"error": str(e)}
        finally:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                pass

        # Hard-timeout exit
        if consecutive_ok >= self._cfg.consecutive_ok_required:
            if not self._cfg.healthy_flag_path.exists():
                return HealthCheckOutcome(
                    success=False,
                    reason="no_healthy_flag",
                    last_response=last_response,
                    probes=probes,
                    consecutive_ok=consecutive_ok,
                )
            return HealthCheckOutcome(
                success=True,
                reason="stable_ok",
                last_response=last_response,
                probes=probes,
                consecutive_ok=consecutive_ok,
            )
        # Never reached required consecutive count
        if not self._cfg.healthy_flag_path.exists():
            return HealthCheckOutcome(
                success=False,
                reason="no_healthy_flag",
                last_response=last_response,
                probes=probes,
                consecutive_ok=consecutive_ok,
            )
        return HealthCheckOutcome(
            success=False,
            reason="timeout",
            last_response=last_response,
            probes=probes,
            consecutive_ok=consecutive_ok,
        )
