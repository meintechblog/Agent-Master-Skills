"""Entry point for ``__APP_SLUG__-updater.service``.

Runs as ``root`` via the systemd ``Type=oneshot`` unit. Wires the real
primitives from :mod:`__PKG_NAME__.updater_root` into
:class:`UpdateRunner`, executes one update attempt, writes the status
file, and exits with a systemd-visible return code:

* 0 — update succeeded
* 1 — pre-flight or install failure (no symlink flip, safe abort)
* 2 — healthcheck failed, rollback succeeded
* 3 — rollback failed (CRITICAL, manual SSH required)

Structured logs flow to journald via
``StandardOutput=journal`` + ``SyslogIdentifier=__APP_SLUG__-updater``.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import aiohttp
import structlog

from __PKG_NAME__.recovery import clear_pending_marker
from __PKG_NAME__.releases import check_disk_space
from __PKG_NAME__.updater_root import backup as backup_mod
from __PKG_NAME__.updater_root import git_ops, pip_ops, trigger_reader
from __PKG_NAME__.updater_root.healthcheck import (
    HealthCheckConfig,
    HealthChecker,
    systemctl_restart,
)
from __PKG_NAME__.updater_root.runner import (
    UpdateRunner,
    UpdateRunnerConfig,
    UpdateRunnerPrimitives,
    atomic_symlink_flip,
    write_pending_marker,
)
from __PKG_NAME__.updater_root.status_writer import StatusFileWriter


def _configure_logging() -> None:
    """JSON structlog to stdout -> journald.

    Deliberately bypasses :mod:`__PKG_NAME__.logging_config` because
    that module expects ``config.yaml`` to be loadable, which may fail
    if a previous broken update left the config in a bad state.
    """
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
    )


def _make_production_primitives(
    config: UpdateRunnerConfig,
) -> UpdateRunnerPrimitives:
    """Wire real subprocess/HTTP/filesystem helpers into the runner."""

    def _make_health_checker(
        *,
        expected_version: str | None = None,
        expected_commit: str | None = None,
    ) -> HealthChecker:
        hc_cfg = HealthCheckConfig()

        async def session_factory() -> Any:
            return aiohttp.ClientSession()

        return HealthChecker(
            config=hc_cfg,
            expected_version=expected_version,
            expected_commit=expected_commit,
            session_factory=session_factory,
        )

    def _status_writer_factory() -> StatusFileWriter:
        return StatusFileWriter(path=config.status_path)

    def _make_dedup_store(path: Path) -> trigger_reader.NonceDedupStore:
        return trigger_reader.NonceDedupStore(path)

    return UpdateRunnerPrimitives(
        # Git
        is_sha_on_main=git_ops.is_sha_on_main,
        git_rev_parse=git_ops.git_rev_parse,
        git_clone_shared=git_ops.git_clone_shared,
        git_get_origin_url=git_ops.git_get_origin_url,
        git_set_origin_url=git_ops.git_set_origin_url,
        git_checkout_detach=git_ops.git_checkout_detach,
        git_fetch=git_ops.git_fetch,
        # Disk
        check_disk_space=check_disk_space,
        # Backup
        create_backup=backup_mod.create_backup,
        apply_release_retention=backup_mod.apply_release_retention,
        apply_backup_retention=backup_mod.apply_backup_retention,
        # Pip
        create_venv=pip_ops.create_venv,
        pip_install_dry_run=pip_ops.pip_install_dry_run,
        pip_install=pip_ops.pip_install,
        compileall=pip_ops.compileall,
        smoke_import=pip_ops.smoke_import,
        config_dryrun=pip_ops.config_dryrun,
        # Systemd
        systemctl_restart=systemctl_restart,
        # Symlink
        atomic_symlink_flip=atomic_symlink_flip,
        # Pending marker
        write_pending_marker=write_pending_marker,
        clear_pending_marker=clear_pending_marker,
        # Status
        status_writer_factory=_status_writer_factory,
        # Trigger
        read_trigger=trigger_reader.read_and_validate_trigger,
        make_dedup_store=_make_dedup_store,
        # Healthcheck
        make_health_checker=_make_health_checker,
    )


async def _async_main() -> int:
    log = structlog.get_logger(component="updater_root.__main__")
    log.info("updater_starting")
    config = UpdateRunnerConfig.default()
    primitives = _make_production_primitives(config)
    runner = UpdateRunner(config, primitives)
    try:
        rc = await runner.run()
    except Exception as e:  # noqa: BLE001 - last-resort safety net
        log.critical(
            "updater_unhandled_exception",
            error=str(e),
            error_type=type(e).__name__,
        )
        return 1
    log.info("updater_complete", returncode=rc)
    return rc


def main() -> int:
    _configure_logging()
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())
