"""Pre-update backup management + retention.

Creates a venv tarball + config + pyproject snapshot before every update,
and enforces retention to prevent ``/var/lib/__APP_SLUG__/backups/``
from filling up.

``apply_release_retention`` is the FIRST place in the codebase that
actually deletes release directories — ``releases.py`` is read-only per
the decision (planning vs. acting). Deletion lives behind the
trust boundary so a compromised main service cannot wipe release history.
"""
from __future__ import annotations

import os
import shutil
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path

import structlog

from __PKG_NAME__.releases import (
    DEFAULT_KEEP_RELEASES,
    select_releases_to_delete,
)

log = structlog.get_logger(component="updater_root.backup")

BACKUPS_ROOT_DEFAULT: Path = Path("/var/lib/__APP_SLUG__/backups")
BACKUP_FILE_MODE: int = 0o640


@dataclass
class BackupResult:
    """Paths of the three artifacts created by :func:`create_backup`."""

    venv_tarball: Path
    config_copy: Path
    pyproject_copy: Path
    created_at: float
    timestamp_str: str


def _ts_str(now: float) -> str:
    """``gmtime`` -> ``YYYYMMDDTHHMMSSZ`` (sortable, UTC)."""
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now))


def create_backup(
    release_dir: Path,
    config_path: Path,
    backups_root: Path | None = None,
    now: float | None = None,
) -> BackupResult:
    """Create the three pre-update backup artifacts.

    * ``<backups_root>/venv-<ts>.tar.gz``: gzipped tar of ``release_dir/.venv``.
      If the venv is missing, an empty tarball is written (the caller
      still gets a sibling file for retention symmetry).
    * ``<backups_root>/config-<ts>.yaml``: literal copy of ``config_path``.
    * ``<backups_root>/pyproject-<ts>.toml``: copy of
      ``release_dir/pyproject.toml``, or a placeholder line if missing.

    All outputs are chmod'd to ``0o640`` (root:root, group-readable).
    install.sh created ``/var/lib/__APP_SLUG__/backups/`` with mode
    ``2775`` root:<service-user>; the setgid bit means new files inherit
    the service-user group when the updater runs as root.
    """
    root = backups_root or BACKUPS_ROOT_DEFAULT
    root.mkdir(parents=True, exist_ok=True)
    t = now if now is not None else time.time()
    ts = _ts_str(t)

    venv_src = release_dir / ".venv"
    venv_dst = root / f"venv-{ts}.tar.gz"
    config_dst = root / f"config-{ts}.yaml"
    pyproject_src = release_dir / "pyproject.toml"
    pyproject_dst = root / f"pyproject-{ts}.toml"

    log.info("backup_starting", ts=ts, release=str(release_dir))
    with tarfile.open(venv_dst, "w:gz") as tar:
        if venv_src.exists():
            tar.add(str(venv_src), arcname=".venv")
        else:
            log.warning("venv_missing_skipping", path=str(venv_src))
    os.chmod(venv_dst, BACKUP_FILE_MODE)

    shutil.copy2(config_path, config_dst)
    os.chmod(config_dst, BACKUP_FILE_MODE)

    if pyproject_src.exists():
        shutil.copy2(pyproject_src, pyproject_dst)
    else:
        pyproject_dst.write_text("# pyproject.toml missing at backup time\n")
    os.chmod(pyproject_dst, BACKUP_FILE_MODE)

    log.info(
        "backup_complete",
        ts=ts,
        venv=str(venv_dst),
        config=str(config_dst),
        pyproject=str(pyproject_dst),
    )
    return BackupResult(
        venv_tarball=venv_dst,
        config_copy=config_dst,
        pyproject_copy=pyproject_dst,
        created_at=t,
        timestamp_str=ts,
    )


def apply_backup_retention(
    backups_root: Path | None = None,
    keep: int = 3,
) -> list[Path]:
    """Delete old backup sets, keeping the ``keep`` newest.

    A "set" is the triple ``(venv-<ts>.tar.gz, config-<ts>.yaml,
    pyproject-<ts>.toml)`` sharing a timestamp. We sort venv tarballs
    by mtime (newest first), keep the top ``keep``, and delete all
    three siblings for each older set. Missing siblings are silently
    tolerated. ``keep < 1`` is coerced to 1 to prevent wiping history.

    Returns the list of files actually deleted (not the sets).
    """
    root = backups_root or BACKUPS_ROOT_DEFAULT
    if not root.exists():
        return []
    venvs = sorted(
        root.glob("venv-*.tar.gz"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    effective_keep = max(1, keep)
    to_delete_venvs = venvs[effective_keep:]
    deleted: list[Path] = []
    for v in to_delete_venvs:
        ts = v.name[len("venv-") : -len(".tar.gz")]
        for sibling in (
            v,
            root / f"config-{ts}.yaml",
            root / f"pyproject-{ts}.toml",
        ):
            if not sibling.exists():
                continue
            try:
                sibling.unlink()
                deleted.append(sibling)
            except OSError as e:
                log.warning(
                    "backup_delete_failed", path=str(sibling), error=str(e)
                )
    return deleted


def apply_release_retention(
    releases_root: Path | None = None,
    keep: int = DEFAULT_KEEP_RELEASES,
    protect: set[Path] | None = None,
) -> list[Path]:
    """Delete old release directories using ``select_releases_to_delete``.

    Thin wrapper that actually calls ``shutil.rmtree`` on the directories
    ``releases.select_releases_to_delete`` returns. The current symlink
    target and any paths in ``protect`` are preserved (enforced inside
    ``select_releases_to_delete``).

    This is the ONLY place in the codebase that deletes release
    directories. Living behind the trust boundary means a compromised
    main service cannot invoke it.
    """
    to_delete = select_releases_to_delete(
        releases_root=releases_root, keep=keep, protect=protect
    )
    deleted: list[Path] = []
    for d in to_delete:
        try:
            shutil.rmtree(d)
            deleted.append(d)
            log.info("release_deleted", path=str(d))
        except OSError as e:
            log.warning("release_delete_failed", path=str(d), error=str(e))
    return deleted
