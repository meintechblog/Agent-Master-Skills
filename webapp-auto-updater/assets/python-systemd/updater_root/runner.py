"""Orchestrator state machine for the privileged updater.

:class:`UpdateRunner` composes the git/backup/release primitives +
healthcheck + status writer + pip ops into an end-to-end update cycle:

    trigger_received
      -> validate SHA on main
      -> disk space preflight
      -> backup
      -> git clone + checkout --detach
      -> create new venv
      -> pip install --dry-run
      -> pip install (real)
      -> compileall
      -> smoke import
      -> config dryrun
      -> write PENDING marker (point of no return)
      -> atomic symlink flip
      -> systemctl restart main service
      -> healthcheck
      -> done  OR  _rollback()

Single-rollback cap: ``_rollback_count`` is checked at the
top of :meth:`UpdateRunner._rollback`. A second call writes
``rollback_failed`` and returns immediately.

PENDING marker is written BEFORE the symlink flip so
``recovery.py`` can flip back on the next boot if the updater dies
mid-flip. Order is enforced by ``test_pending_marker_written_before_flip``.

The runner takes an :class:`UpdateRunnerPrimitives` bag of injectable
callables so the entire state machine is unit-testable without real
subprocesses, filesystems, or systemd.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import structlog

from __PKG_NAME__.recovery import (
    PENDING_MARKER_PATH,
    UPDATER_ACTIVE_FLAG,
    PendingMarker,
)
from __PKG_NAME__.releases import (
    CURRENT_SYMLINK_NAME,
    DEFAULT_KEEP_RELEASES,
    INSTALL_ROOT,
    RELEASES_ROOT,
)
from __PKG_NAME__.updater_root.healthcheck import (
    HealthCheckConfig,
    HealthChecker,
    HealthCheckOutcome,
)
from __PKG_NAME__.updater_root.status_writer import StatusFileWriter
from __PKG_NAME__.updater_root.trigger_reader import (
    NonceDedupStore,
    NonceReplayError,
    TriggerValidationError,
    ValidatedTrigger,
)

log = structlog.get_logger(component="updater_root.runner")

# Exit codes returned by UpdateRunner.run()
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_ROLLBACK_DONE = 2
EXIT_ROLLBACK_FAILED = 3


# ---------------------------------------------------------------------
# Filesystem helpers (exported so __main__.py can reuse them)

def atomic_symlink_flip(current_link: Path, new_target: Path) -> None:
    """POSIX-atomic symlink replacement via tmp + os.replace.

    Mirrors the pattern used by ``recovery._atomic_symlink_flip``.
    Either the old or the new symlink exists on disk — never neither.
    """
    tmp = current_link.with_name(current_link.name + ".new")
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    tmp.symlink_to(new_target)
    os.replace(tmp, current_link)


def write_pending_marker(
    pending_path: Path,
    previous_release: Path,
    target_release: Path,
    created_at: float,
    reason: str = "update",
) -> None:
    """Write the PendingMarker schema atomically.

    Importing :class:`PendingMarker` from ``__PKG_NAME__.recovery``
    is allowed by the trust boundary (recovery is on the allowlist).
    """
    marker = PendingMarker(
        previous_release=str(previous_release),
        target_release=str(target_release),
        created_at=created_at,
        reason=reason,
    )
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pending_path.with_name(pending_path.name + ".tmp")
    tmp.write_text(json.dumps(asdict(marker), indent=2, sort_keys=True))
    os.replace(tmp, pending_path)
    os.chmod(pending_path, 0o644)


# ---------------------------------------------------------------------
# Config + primitives bag

@dataclass
class UpdateRunnerConfig:
    """Static paths + tuning for :class:`UpdateRunner`.

    ``default()`` returns production values; tests construct this
    directly with tmp_path-backed paths.
    """

    releases_root: Path
    install_root: Path
    current_symlink: Path
    backups_root: Path
    trigger_path: Path
    status_path: Path
    config_path: Path
    dedup_path: Path
    pending_marker_path: Path
    updater_active_flag: Path
    main_service_unit: str = "__APP_SLUG__.service"
    keep_releases: int = DEFAULT_KEEP_RELEASES
    release_name_fallback: str = "release"

    @classmethod
    def default(cls) -> "UpdateRunnerConfig":
        return cls(
            releases_root=RELEASES_ROOT,
            install_root=INSTALL_ROOT,
            current_symlink=RELEASES_ROOT / CURRENT_SYMLINK_NAME,
            backups_root=Path("/var/lib/__APP_SLUG__/backups"),
            trigger_path=Path("/etc/__APP_SLUG__/update-trigger.json"),
            status_path=Path("/etc/__APP_SLUG__/update-status.json"),
            config_path=Path("/etc/__APP_SLUG__/config.yaml"),
            dedup_path=Path("/var/lib/__APP_SLUG__/processed-nonces.json"),
            pending_marker_path=PENDING_MARKER_PATH,
            updater_active_flag=UPDATER_ACTIVE_FLAG,
        )


@dataclass
class UpdateRunnerPrimitives:
    """Injectable callables — production values wired in ``__main__.py``.

    Every side-effecting primitive is passed in so tests can construct
    fakes with captured call sequences and controlled return values.
    """

    # Git
    is_sha_on_main: Callable[[Path, str], Awaitable[bool]]
    git_rev_parse: Callable[[Path, str], Awaitable[str | None]]
    git_clone_shared: Callable[[Path, Path], Awaitable[Any]]
    git_checkout_detach: Callable[[Path, str], Awaitable[Any]]
    git_fetch: Callable[[Path], Awaitable[Any]]
    # Disk
    check_disk_space: Callable[[], Any]
    # Backup
    create_backup: Callable[[Path, Path, Path], Any]
    apply_release_retention: Callable[..., list[Path]]
    apply_backup_retention: Callable[..., list[Path]]
    # Pip
    create_venv: Callable[[Path], Awaitable[Any]]
    pip_install_dry_run: Callable[[Path, Path], Awaitable[Any]]
    pip_install: Callable[[Path, Path], Awaitable[Any]]
    compileall: Callable[[Path, Path], Awaitable[Any]]
    smoke_import: Callable[[Path], Awaitable[Any]]
    config_dryrun: Callable[[Path, Path], Awaitable[Any]]
    # Systemd
    systemctl_restart: Callable[[str], Awaitable[bool]]
    # Symlink
    atomic_symlink_flip: Callable[[Path, Path], None]
    # Pending marker
    write_pending_marker: Callable[..., None]
    clear_pending_marker: Callable[..., None]
    # Status
    status_writer_factory: Callable[[], StatusFileWriter]
    # Trigger
    read_trigger: Callable[[Path, NonceDedupStore], ValidatedTrigger]
    make_dedup_store: Callable[[Path], NonceDedupStore]
    # Healthcheck
    make_health_checker: Callable[..., HealthChecker]
    # Origin propagation (optional, backward-compatible for existing fakes/tests):
    # git clone --shared points origin at the LOCAL predecessor release path —
    # without propagating the real remote URL the ancestor security check
    # freezes on the predecessor's state and every follow-up update dies with
    # sha_not_on_main (verified on a private-repo deployment, 2026-06-12).
    git_get_origin_url: "Callable[[Path], Awaitable[str | None]] | None" = None
    git_set_origin_url: "Callable[[Path, str], Awaitable[Any]] | None" = None


# ---------------------------------------------------------------------
# UpdateRunner state machine

class UpdateRunner:
    """Orchestrates one update attempt. Returns a systemd exit code.

    Lifecycle is single-shot: construct, ``await run()``, inspect
    return code, discard. The single-rollback cap is
    enforced by an instance counter.
    """

    def __init__(
        self,
        config: UpdateRunnerConfig,
        primitives: UpdateRunnerPrimitives,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._cfg = config
        self._p = primitives
        self._clock = clock
        self._rollback_count = 0
        self._status: StatusFileWriter | None = None

    async def run(self) -> int:
        """Execute one update attempt end-to-end.

        Returns:
            0: success
            1: pre-flight or install failure (no symlink flip, no rollback)
            2: healthcheck failed, rollback succeeded
            3: rollback failed — CRITICAL, manual SSH required
        """
        self._status = self._p.status_writer_factory()
        status = self._status
        #: raise the updater-active flag (on tmpfs) so the
        # recovery hook skips rollback during the in-flight
        # systemctl restart we are about to issue. The flag is cleared in
        # the finally block below, regardless of outcome.
        self._raise_updater_active_flag()
        try:
            # 1. Read + validate trigger
            dedup = self._p.make_dedup_store(self._cfg.dedup_path)
            try:
                trigger = self._p.read_trigger(self._cfg.trigger_path, dedup)
            except NonceReplayError as e:
                log.warning("nonce_replay", error=str(e))
                return EXIT_FAILURE
            except TriggerValidationError as e:
                log.error("trigger_invalid", error=str(e))
                return EXIT_FAILURE

            # Resolve current release dir (via symlink target)
            current_release = self._resolve_current_release()
            if current_release is None:
                log.critical(
                    "no_current_release",
                    symlink=str(self._cfg.current_symlink),
                )
                return EXIT_FAILURE

            old_sha = await self._p.git_rev_parse(current_release, "HEAD")
            status.begin(
                nonce=trigger.nonce,
                target_sha=trigger.target_sha,
                old_sha=old_sha or "unknown",
            )

            # 2. SHA must be an ancestor of origin/main
            # Refresh remote refs first so a newly-pushed SHA is reachable.
            try:
                await self._p.git_fetch(current_release)
            except Exception as e:  # noqa: BLE001
                log.warning("git_fetch_failed", error=str(e))
            if not await self._p.is_sha_on_main(
                current_release, trigger.target_sha
            ):
                status.finalize("rollback_failed")
                log.error(
                    "sha_not_on_main",
                    target_sha=trigger.target_sha,
                )
                return EXIT_FAILURE

            # 3. disk space preflight
            disk = self._p.check_disk_space()
            if not disk.ok:
                status.write_phase("backup", error=f"disk_space: {disk.message}")
                status.finalize("rollback_failed")
                log.error("disk_preflight_failed", message=disk.message)
                return EXIT_FAILURE

            # 4. backup
            status.write_phase("backup")
            self._p.create_backup(
                current_release,
                self._cfg.config_path,
                self._cfg.backups_root,
            )

            # 5. extract new release
            status.write_phase("extract")
            new_release = self._new_release_dir(trigger.target_sha)
            await self._p.git_clone_shared(current_release, new_release)
            # 5b. Origin propagation: --shared points origin at the local
            # predecessor path -> carry the running release's real remote URL
            # onto the new release. ONLY propagate real remote URLs
            # (http/https/ssh/git@) — a local path means the chain was already
            # broken; don't forward it, just warn.
            if self._p.git_get_origin_url and self._p.git_set_origin_url:
                try:
                    cur_origin = await self._p.git_get_origin_url(current_release)
                    if cur_origin and not cur_origin.startswith(("/", ".")):
                        await self._p.git_set_origin_url(new_release, cur_origin)
                    elif cur_origin:
                        log.warning("origin_is_local_path", origin=cur_origin)
                except Exception as e:  # noqa: BLE001
                    log.warning("origin_propagation_failed", error=str(e))
            await self._p.git_checkout_detach(new_release, trigger.target_sha)

            # 6. pip dry-run (inside new venv)
            status.write_phase("pip_install_dryrun")
            new_venv = new_release / ".venv"
            new_python = new_venv / "bin" / "python3"
            venv_result = await self._p.create_venv(new_venv)
            if hasattr(venv_result, "ok") and not venv_result.ok:
                status.finalize("rollback_failed")
                log.error("create_venv_failed")
                return EXIT_FAILURE
            dry = await self._p.pip_install_dry_run(new_python, new_release)
            if hasattr(dry, "ok") and not dry.ok:
                status.write_phase(
                    "pip_install_dryrun",
                    error=getattr(dry, "stderr", "dry_run_failed")[:500],
                )
                status.finalize("rollback_failed")
                return EXIT_FAILURE

            # 7. Real install
            status.write_phase("pip_install")
            inst = await self._p.pip_install(new_python, new_release)
            if hasattr(inst, "ok") and not inst.ok:
                status.write_phase(
                    "pip_install",
                    error=getattr(inst, "stderr", "install_failed")[:500],
                )
                status.finalize("rollback_failed")
                return EXIT_FAILURE

            # 8. compileall
            status.write_phase("compileall")
            ca = await self._p.compileall(new_python, new_release / "src")
            if hasattr(ca, "ok") and not ca.ok:
                status.write_phase(
                    "compileall",
                    error=getattr(ca, "stderr", "compileall_failed")[:500],
                )
                status.finalize("rollback_failed")
                return EXIT_FAILURE

            # 9. part 1: smoke import
            status.write_phase("smoke_import")
            smoke = await self._p.smoke_import(new_python)
            if hasattr(smoke, "ok") and not smoke.ok:
                status.write_phase(
                    "smoke_import",
                    error=getattr(smoke, "stderr", "smoke_failed")[:500],
                )
                status.finalize("rollback_failed")
                return EXIT_FAILURE

            # 10. part 2: config dryrun
            status.write_phase("config_dryrun")
            cfg = await self._p.config_dryrun(new_python, self._cfg.config_path)
            if hasattr(cfg, "ok") and not cfg.ok:
                status.write_phase(
                    "config_dryrun",
                    error=getattr(cfg, "stderr", "config_dryrun_failed")[:500],
                )
                status.finalize("rollback_failed")
                return EXIT_FAILURE

            # 11. POINT OF NO RETURN — pending marker BEFORE flip
            status.write_phase("pending_marker_written")
            self._p.write_pending_marker(
                self._cfg.pending_marker_path,
                current_release,
                new_release,
                self._clock(),
                "update",
            )

            # 12. atomic symlink flip
            status.write_phase("symlink_flipped")
            self._p.atomic_symlink_flip(self._cfg.current_symlink, new_release)

            # 13. Restart main service
            status.write_phase("restarting")
            restart_ok = await self._p.systemctl_restart(
                self._cfg.main_service_unit
            )
            if not restart_ok:
                log.error("systemctl_restart_failed_initial")
                return await self._rollback(
                    current_release, old_sha, "restart_failed"
                )

            # 14. post-restart healthcheck
            status.write_phase("healthcheck")
            new_commit = await self._p.git_rev_parse(new_release, "HEAD")
            checker = self._p.make_health_checker(
                expected_version=None,
                expected_commit=new_commit,
            )
            outcome: HealthCheckOutcome = await checker.wait_for_healthy()
            if outcome.success:
                # SUCCESS: clear pending marker, retain current + previous
                self._p.clear_pending_marker(self._cfg.pending_marker_path)
                self._p.apply_release_retention(
                    releases_root=self._cfg.releases_root,
                    keep=self._cfg.keep_releases,
                    protect={current_release},
                )
                self._p.apply_backup_retention(
                    backups_root=self._cfg.backups_root,
                    keep=self._cfg.keep_releases,
                )
                status.finalize("done")
                log.info("update_done", new_commit=new_commit)
                return EXIT_SUCCESS

            #: rollback
            log.warning(
                "healthcheck_failed",
                reason=outcome.reason,
                probes=outcome.probes,
            )
            return await self._rollback(
                current_release, old_sha, outcome.reason
            )

        except Exception as e:  # noqa: BLE001
            log.critical(
                "runner_unhandled_exception",
                error=str(e),
                error_type=type(e).__name__,
            )
            if self._status is not None:
                try:
                    self._status.finalize("rollback_failed")
                except Exception:  # noqa: BLE001
                    pass
            return EXIT_FAILURE
        finally:
            # Always drop the updater-active flag so a subsequent real
            # boot can engage recovery normally.
            self._drop_updater_active_flag()

    # ------------------------------------------------------------------
    # Rollback (single-rollback cap)

    async def _rollback(
        self,
        previous_release: Path,
        old_sha: str | None,
        reason: str,
    ) -> int:
        """Flip symlink back + restart + second healthcheck.

        Called AT MOST ONCE per :meth:`run` invocation. The counter is
        checked on entry — a second call writes ``rollback_failed``
        and returns immediately, satisfying.
        """
        assert self._status is not None
        status = self._status

        if self._rollback_count >= 1:
            log.critical(
                "rollback_refused_already_attempted", reason=reason
            )
            status.finalize("rollback_failed")
            return EXIT_ROLLBACK_FAILED
        self._rollback_count += 1

        status.write_phase("rollback_starting", error=reason)
        try:
            self._p.atomic_symlink_flip(
                self._cfg.current_symlink, previous_release
            )
        except Exception as e:  # noqa: BLE001
            log.critical("rollback_symlink_flip_failed", error=str(e))
            status.finalize("rollback_failed")
            return EXIT_ROLLBACK_FAILED
        status.write_phase("rollback_symlink_flipped")

        restart_ok = await self._p.systemctl_restart(
            self._cfg.main_service_unit
        )
        status.write_phase("rollback_restarting")
        if not restart_ok:
            log.critical("rollback_restart_failed")
            status.finalize("rollback_failed")
            return EXIT_ROLLBACK_FAILED

        checker = self._p.make_health_checker(
            expected_version=None,
            expected_commit=old_sha,
        )
        outcome2: HealthCheckOutcome = await checker.wait_for_healthy()
        status.write_phase("rollback_healthcheck")
        if outcome2.success:
            self._p.clear_pending_marker(self._cfg.pending_marker_path)
            status.finalize("rollback_done")
            log.warning("rollback_done", previous=str(previous_release))
            return EXIT_ROLLBACK_DONE

        status.finalize("rollback_failed")
        log.critical(
            "rollback_failed_unhealthy",
            reason=outcome2.reason,
            hint="manual SSH intervention required",
        )
        return EXIT_ROLLBACK_FAILED

    # ------------------------------------------------------------------
    # Helpers

    def _resolve_current_release(self) -> Path | None:
        """Follow the current symlink to the real release directory."""
        link = self._cfg.current_symlink
        if not link.exists():
            return None
        try:
            target = link.resolve(strict=False)
        except OSError:
            return None
        if not target.exists() or not target.is_dir():
            return None
        return target

    def _new_release_dir(self, target_sha: str) -> Path:
        """Construct the new release dir path.

        Name format: ``<fallback>-<short_sha>`` — the fallback is the
        configured ``release_name_fallback`` (default ``"release"``).
        A timestamp suffix guarantees uniqueness if the same SHA is
        re-deployed (no-op dry-run testing), to avoid colliding with
        any existing directory.
        """
        short = target_sha[:7]
        base = self._cfg.releases_root / f"{self._cfg.release_name_fallback}-{short}"
        if not base.exists():
            return base
        # Append clock epoch to distinguish replays (same-SHA smoke test)
        return self._cfg.releases_root / f"{self._cfg.release_name_fallback}-{short}-{int(self._clock())}"

    def _raise_updater_active_flag(self) -> None:
        """Create the updater-active tmpfs flag.

        Best-effort: if the parent directory doesn't exist (unusual — the
        main service creates /run/__APP_SLUG__ via RuntimeDirectory),
        we fall back to creating it. Any OSError is logged but not raised
        so the main update flow is never blocked by tmpfs issues.
        """
        try:
            flag = self._cfg.updater_active_flag
            flag.parent.mkdir(parents=True, exist_ok=True)
            flag.touch(exist_ok=True)
            log.info("updater_active_flag_raised", path=str(flag))
        except OSError as e:
            log.warning(
                "updater_active_flag_raise_failed",
                error=str(e),
                path=str(self._cfg.updater_active_flag),
            )

    def _drop_updater_active_flag(self) -> None:
        """Remove the updater-active tmpfs flag. Silent on missing."""
        try:
            flag = self._cfg.updater_active_flag
            if flag.exists():
                flag.unlink()
            log.info("updater_active_flag_dropped", path=str(flag))
        except OSError as e:
            log.warning(
                "updater_active_flag_drop_failed",
                error=str(e),
                path=str(self._cfg.updater_active_flag),
            )
