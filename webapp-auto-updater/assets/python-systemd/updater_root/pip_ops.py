"""Async subprocess wrappers for pip + venv operations.

All helpers use ``asyncio.create_subprocess_exec`` with explicit argv —
``shell=True`` is never used, and no argument is ever interpolated into a
command string. Matches the security pattern established in
``updater_root.git_ops.run_git``.

Lifecycle for a new release:

1. :func:`create_venv` — ``python3 -m venv <release>/.venv``
2. :func:`pip_install_dry_run` — ``pip install --dry-run -e .``
3. :func:`pip_install` — real install into the new venv
4. :func:`compileall` — pre-compile .pyc so ``ProtectSystem=strict``
   cannot block runtime writes
5. :func:`smoke_import` — part 1, ``python -c "import __PKG_NAME__"``
6. :func:`config_dryrun` — part 2, runs ``load_config`` against the
   existing ``/etc/__APP_SLUG__/config.yaml`` to catch schema
   incompatibilities in the NEW code

All operations live BEFORE the symlink flip — a failure at any step aborts
the update with zero blast radius on the running release.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(component="updater_root.pip_ops")

# Timeouts (seconds) — generous to accommodate slow LXC I/O and build tools.
PIP_DRY_RUN_TIMEOUT_S: float = 300.0
PIP_INSTALL_TIMEOUT_S: float = 600.0
COMPILEALL_TIMEOUT_S: float = 120.0
SMOKE_IMPORT_TIMEOUT_S: float = 30.0
CONFIG_DRYRUN_TIMEOUT_S: float = 30.0
CREATE_VENV_TIMEOUT_S: float = 120.0


class PipTimeoutError(Exception):
    """Raised when a pip/venv subprocess exceeds its timeout."""


@dataclass
class PipResult:
    """Result of a pip/python subprocess invocation.

    Non-zero ``returncode`` is NOT raised — the caller inspects
    ``.ok`` and surfaces the failure to the state machine.
    """

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


async def _run(
    argv: list[str],
    *,
    timeout_s: float,
    label: str,
) -> PipResult:
    """Run ``argv`` as an async subprocess with a hard timeout.

    Kills the process on timeout and raises :class:`PipTimeoutError`.
    Never uses a shell. argv is passed verbatim via ``*argv``.
    """
    log.info("pip_exec", label=label, argv=argv)
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout_s
        )
    except asyncio.TimeoutError as e:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        raise PipTimeoutError(
            f"{label} timed out after {timeout_s}s"
        ) from e
    return PipResult(
        returncode=proc.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


async def create_venv(venv_dir: Path) -> PipResult:
    """Run ``python3 -m venv <venv_dir>``.

    Uses the system ``python3`` (not the current venv's python) so the
    new venv is independent of the running release's interpreter.
    """
    return await _run(
        ["python3", "-m", "venv", str(venv_dir)],
        timeout_s=CREATE_VENV_TIMEOUT_S,
        label="create_venv",
    )


async def pip_install_dry_run(venv_python: Path, project_dir: Path) -> PipResult:
    """: ``<venv_python> -m pip install --dry-run -e <project_dir>``.

    Surfaces missing dependencies, resolver conflicts, and network
    errors BEFORE the real install writes anything.
    """
    return await _run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--dry-run",
            "-e",
            str(project_dir),
        ],
        timeout_s=PIP_DRY_RUN_TIMEOUT_S,
        label="pip_install_dry_run",
    )


async def pip_install(venv_python: Path, project_dir: Path) -> PipResult:
    """Real ``pip install -e <project_dir>`` into the target venv."""
    return await _run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "-e",
            str(project_dir),
        ],
        timeout_s=PIP_INSTALL_TIMEOUT_S,
        label="pip_install",
    )


async def compileall(venv_python: Path, src_dir: Path) -> PipResult:
    """: ``<venv_python> -m compileall -q <src_dir>``.

    Pre-compiles every .py under ``src_dir`` to .pyc so the runtime
    does not attempt to write .pyc files under ``ProtectSystem=strict``.
    """
    return await _run(
        [
            str(venv_python),
            "-m",
            "compileall",
            "-q",
            str(src_dir),
        ],
        timeout_s=COMPILEALL_TIMEOUT_S,
        label="compileall",
    )


async def smoke_import(venv_python: Path) -> PipResult:
    """ part 1: ``<venv_python> -c "import __PKG_NAME__; print('ok')"``.

    Fails fast on any import-time exception (bad syntax, missing
    module, broken C extension). Runs BEFORE the symlink flip.
    """
    return await _run(
        [
            str(venv_python),
            "-c",
            "import __PKG_NAME__; print('ok')",
        ],
        timeout_s=SMOKE_IMPORT_TIMEOUT_S,
        label="smoke_import",
    )


async def config_dryrun(venv_python: Path, config_path: Path) -> PipResult:
    """ part 2: load_config dryrun against the existing config.

    Runs the NEW code against the EXISTING ``/etc/__APP_SLUG__/config.yaml``
    to catch schema incompatibilities that would crash the main service
    on restart. Runs BEFORE the symlink flip so a failure is safe.

    The config path is embedded into the ``-c`` script via ``repr`` so
    paths with spaces/quotes cannot break the invocation.
    """
    script = (
        "from __PKG_NAME__.config import load_config; "
        f"load_config({str(config_path)!r})"
    )
    return await _run(
        [str(venv_python), "-c", script],
        timeout_s=CONFIG_DRYRUN_TIMEOUT_S,
        label="config_dryrun",
    )
