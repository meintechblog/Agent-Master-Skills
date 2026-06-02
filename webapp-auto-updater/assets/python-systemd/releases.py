"""Blue-green release layout helpers.

Pure-function module encapsulating the layout anchors and logic for the
blue-green auto-update system. No systemd coupling, no actual symlink
flipping — those are the responsibility of install.sh (migration) and
the privileged updater.

Layout:
    /opt/__APP_SLUG__-releases/
        v7.0-abc1234/       # full checkout + .venv
        v8.0-def5678/       # full checkout + .venv
        current -> v8.0-def5678
    /opt/__APP_SLUG__ -> /opt/__APP_SLUG__-releases/current
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import structlog

log = structlog.get_logger(component="releases")

# -- Anchor constants (see references/architecture.md) --
RELEASES_ROOT: Path = Path("/opt/__APP_SLUG__-releases")
INSTALL_ROOT: Path = Path("/opt/__APP_SLUG__")
CURRENT_SYMLINK_NAME: str = "current"
DEFAULT_KEEP_RELEASES: int = 3
MIN_FREE_BYTES: int = 500 * 1024 * 1024 # 500 MB,


class LayoutKind(str, Enum):
    """On-disk layout state for /opt/__APP_SLUG__.

    FLAT: pre-v8.0 layout (direct git checkout in INSTALL_ROOT).
    BLUE_GREEN: v8.0+ layout (INSTALL_ROOT is a symlink into RELEASES_ROOT).
    MISSING: INSTALL_ROOT does not exist (fresh install).
    UNKNOWN: anything else (foreign symlink, dangling link, dir without .git).
    """

    FLAT = "flat"
    BLUE_GREEN = "blue_green"
    MISSING = "missing"
    UNKNOWN = "unknown"


def detect_layout(
    install_root: Path | None = None,
    releases_root: Path | None = None,
) -> LayoutKind:
    """Classify the on-disk layout at ``install_root``.

    Arguments default to the module-level constants and can be overridden
    for testing.  See :class:`LayoutKind` for possible return values.
    """
    ir = install_root if install_root is not None else INSTALL_ROOT
    rr = releases_root if releases_root is not None else RELEASES_ROOT

    # Symlink? Resolve and check destination
    if ir.is_symlink():
        try:
            resolved = ir.resolve(strict=False)
        except OSError:
            return LayoutKind.UNKNOWN
        if not resolved.exists():
            log.warning("install_root_dangling_symlink", path=str(ir))
            return LayoutKind.UNKNOWN
        try:
            resolved.relative_to(rr.resolve(strict=False))
            return LayoutKind.BLUE_GREEN
        except ValueError:
            # symlink points outside releases root — foreign layout
            return LayoutKind.UNKNOWN

    if not ir.exists():
        return LayoutKind.MISSING

    if ir.is_dir() and (ir / ".git").exists():
        return LayoutKind.FLAT

    return LayoutKind.UNKNOWN


def current_release_dir(releases_root: Path | None = None) -> Path | None:
    """Resolve the ``current`` symlink under ``releases_root``.

    Returns the resolved target path if the symlink exists and points at
    a real directory, otherwise ``None``.  Never raises — a dangling or
    missing symlink is reported as ``None``.
    """
    rr = releases_root if releases_root is not None else RELEASES_ROOT
    link = rr / CURRENT_SYMLINK_NAME
    if not link.is_symlink():
        return None
    try:
        target = link.resolve(strict=False)
    except OSError:
        return None
    if not target.exists():
        return None
    return target


def list_release_dirs(releases_root: Path | None = None) -> list[Path]:
    """Return release subdirectories under ``releases_root``, newest first.

    Only real directories are returned.  The ``current`` symlink and any
    other symlinks, files, or stray entries are skipped (not errors).
    Returns an empty list if ``releases_root`` does not exist.
    """
    rr = releases_root if releases_root is not None else RELEASES_ROOT
    if not rr.exists() or not rr.is_dir():
        return []
    dirs: list[Path] = []
    for entry in rr.iterdir():
        if entry.name == CURRENT_SYMLINK_NAME:
            continue
        if entry.is_symlink():
            # Defensive: skip any symlink sibling (could point anywhere).
            continue
        if not entry.is_dir():
            continue
        dirs.append(entry)
    dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return dirs


def select_releases_to_delete(
    releases_root: Path | None = None,
    keep: int = DEFAULT_KEEP_RELEASES,
    protect: set[Path] | None = None,
) -> list[Path]:
    """Return the release directories that are safe to delete.

    Implements retention with defense-in-depth:

    * Retains the ``keep`` newest directories by mtime.
    * Additionally always protects the directory pointed at by the
      ``current`` symlink, even if it is not among the newest ``keep``
      (e.g. just after a rollback where the older release is live).
    * Additionally always protects every path in ``protect`` (
      will pass the previous-release directory recorded in
      update-status.json).
    * ``keep < 1`` is coerced to ``1`` with a warning (never delete all).
    * The retained set is the UNION of "top N newest" and all protected
      paths.  This can exceed ``keep`` when protected dirs fall outside
      the newest window — the safety posture is "never delete current
      or previous", which takes precedence over the target retention
      count.

    The caller is responsible for the actual ``shutil.rmtree`` — this
    function never touches the filesystem beyond stat/readlink.
    """
    rr = releases_root if releases_root is not None else RELEASES_ROOT
    all_dirs = list_release_dirs(rr)
    if not all_dirs:
        return []

    effective_keep = keep
    if effective_keep < 1:
        log.warning(
            "retention_keep_coerced",
            requested=keep,
            coerced=1,
            reason="refusing to delete all releases",
        )
        effective_keep = 1

    # Build protected set: explicit caller set + current symlink target.
    # These are ALWAYS retained regardless of position in the mtime order.
    protected: set[Path] = set()
    for p in (protect or set()):
        try:
            protected.add(p.resolve(strict=False))
        except OSError:
            protected.add(p)
    current = current_release_dir(rr)
    if current is not None:
        try:
            protected.add(current.resolve(strict=False))
        except OSError:
            protected.add(current)

    # Retained = {top N newest by mtime} ∪ {protected}.  Union semantics
    # mean protected dirs outside the newest window increase the total
    # retained count beyond `effective_keep` — this is intentional.
    retained: set[Path] = set(protected)
    for d in all_dirs[:effective_keep]:
        try:
            retained.add(d.resolve(strict=False))
        except OSError:
            retained.add(d)

    # Result: dirs in all_dirs not in retained, preserving newest-first order
    to_delete: list[Path] = []
    for d in all_dirs:
        try:
            resolved = d.resolve(strict=False)
        except OSError:
            resolved = d
        if resolved not in retained:
            to_delete.append(d)
    return to_delete


@dataclass
class DiskSpaceReport:
    """Result of :func:`check_disk_space`.

    ``ok`` is ``True`` iff BOTH monitored paths have at least
    ``min_free_bytes`` free.  ``message`` is an empty string on success
    and a human-readable reason joined by ``"; "`` on failure.
    """

    opt_free_bytes: int
    var_cache_free_bytes: int
    ok: bool
    message: str


def _free_bytes(path: Path) -> tuple[int, str | None]:
    """Return ``(free_bytes, error_message_or_None)`` for ``path``.

    Defensive: never raises.  Missing path or ``OSError`` from
    ``shutil.disk_usage`` is reported as an error string.
    """
    if not path.exists():
        return 0, f"{path} does not exist"
    try:
        usage = shutil.disk_usage(str(path))
        return usage.free, None
    except OSError as e:
        return 0, f"disk_usage({path}) failed: {e}"


def check_disk_space(
    min_free_bytes: int = MIN_FREE_BYTES,
    opt_path: Path = Path("/opt"),
    var_cache_path: Path = Path("/var/cache"),
) -> DiskSpaceReport:
    """Pre-flight disk space check for update operations.

    Verifies that BOTH ``opt_path`` and ``var_cache_path`` have at
    least ``min_free_bytes`` of free space.  Missing paths and
    ``OSError`` are reported as ``ok=False`` — this helper never raises.
    """
    opt_free, opt_err = _free_bytes(opt_path)
    vc_free, vc_err = _free_bytes(var_cache_path)

    problems: list[str] = []
    if opt_err:
        problems.append(opt_err)
    elif opt_free < min_free_bytes:
        problems.append(
            f"{opt_path} has {opt_free // (1024 * 1024)} MB free "
            f"(need {min_free_bytes // (1024 * 1024)} MB)"
        )
    if vc_err:
        problems.append(vc_err)
    elif vc_free < min_free_bytes:
        problems.append(
            f"{var_cache_path} has {vc_free // (1024 * 1024)} MB free "
            f"(need {min_free_bytes // (1024 * 1024)} MB)"
        )

    return DiskSpaceReport(
        opt_free_bytes=opt_free,
        var_cache_free_bytes=vc_free,
        ok=not problems,
        message="; ".join(problems),
    )
