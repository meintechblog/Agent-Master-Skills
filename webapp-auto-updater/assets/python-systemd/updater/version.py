"""Version parsing + current-version resolution for the updater subsystem.

Requirements:
    Determine the running build's version and expose it as a comparable
    tuple.

References:
    - pyproject.toml [project].name / version

Design notes:
    This module is intentionally hand-rolled to avoid pulling in `packaging`
    or `semver` as new dependencies. The version grammar we need is narrow:
    two- or three-field dotted integers with an optional leading `v`. A
    single anchored regex is enough and gives us a free ReDoS-safe parser.

    `Version` is a NamedTuple, so ordering/equality are free via tuple
    comparison — we never need a separate `<`/`>` implementation.

    `get_current_version()` reads from `importlib.metadata` (stdlib since
    3.8) rather than importing any project module, so it keeps working in
    an editable install and never crashes if the package metadata is
    missing — it just returns `"unknown"`.
"""
from __future__ import annotations

import re
import subprocess
from importlib import metadata
from pathlib import Path
from typing import NamedTuple

import structlog

log = structlog.get_logger(component="updater.version")

#: Name as it appears in pyproject.toml's ``[project].name``. Keep in sync.
PACKAGE_NAME = "__APP_SLUG__"

#: Default install directory used by get_commit_hash when caller passes None.
DEFAULT_INSTALL_DIR = Path("/opt/__APP_SLUG__")

#: Anchored, no nested quantifiers → bounded linear time, ReDoS-safe.
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")


class Version(NamedTuple):
    """Semver-ish triple (major, minor, patch).

    Tuple comparison gives free <, <=, ==, >=, > ordering. Use :meth:`parse`
    to build one from a string; use :meth:`__str__` to get the canonical
    ``vX.Y.Z`` form back.
    """

    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, raw: str) -> "Version":
        """Parse ``vX.Y`` or ``vX.Y.Z`` (leading ``v`` optional).

        Whitespace is stripped. Missing patch defaults to 0. Anything else
        (single-field, 4-field, pre-release suffix, empty, etc.) raises
        :class:`ValueError`.

        Args:
            raw: The version string to parse.

        Returns:
            A :class:`Version` tuple.

        Raises:
            ValueError: If ``raw`` is not a recognizable version string.
        """
        if not isinstance(raw, str):
            raise ValueError(f"Unparseable version: {raw!r}")
        stripped = raw.strip()
        match = _VERSION_RE.match(stripped)
        if match is None:
            raise ValueError(f"Unparseable version: {raw!r}")
        return cls(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3) or 0),
        )

    def __str__(self) -> str:
        return f"v{self.major}.{self.minor}.{self.patch}"


def get_current_version() -> str:
    """Return the running build's version string, or ``"unknown"``.

    Reads ``importlib.metadata.version(PACKAGE_NAME)``. Catches
    ``PackageNotFoundError`` (editable install / broken pyproject) and any
    other exception defensively. **Never raises.**

    The returned string may or may not be parseable by :meth:`Version.parse`
    — callers that need a structured comparison must wrap the call in a
    try/except.
    """
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        log.debug("package_metadata_not_found", package=PACKAGE_NAME)
        return "unknown"
    except Exception as exc:  # pragma: no cover - defensive
        log.warning(
            "package_metadata_unexpected_error",
            package=PACKAGE_NAME,
            error=str(exc),
        )
        return "unknown"


def _read_commit_file_fallback() -> str | None:
    """Read the short SHA from a packaged ``COMMIT`` file next to this module.

      fallback: on production LXC installs, ``.git/`` is
    excluded from the rsync deploy, so ``git rev-parse`` cannot run. The
    deploy script writes the committed short SHA into
    ``src/__PKG_NAME__/COMMIT`` right before the sync, and this
    function reads it back at startup. Returns ``None`` when the file is
    missing, empty, contains "unknown", or is unreadable. **Never raises.**
    """
    try:
        commit_file = Path(__file__).resolve().parent.parent / "COMMIT"
        if not commit_file.is_file():
            return None
        content = commit_file.read_text(encoding="utf-8").strip()
        if not content or content == "unknown":
            return None
        # Accept only short-SHA-ish content (hex up to 40 chars).
        if not all(c in "0123456789abcdefABCDEF" for c in content):
            return None
        return content[:7].lower()
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("commit_file_read_error", error=str(exc))
        return None


def get_commit_hash(install_dir: Path | None = None) -> str | None:
    """Return the 7-char short SHA of ``install_dir``'s current HEAD.

    Shells out to ``git -C <install_dir> rev-parse --short HEAD``. Uses an
    explicit argv list (never ``shell=True``) so there is no injection
    surface. Returns ``None`` on every failure mode — git missing, path
    missing, .git missing, non-zero exit, timeout, permission error —
    **never raises**.

    When git lookup fails (e.g. on production LXC installs where ``.git/``
    is excluded from the rsync deploy), falls back to reading a packaged
    ``COMMIT`` file written by ``deploy.sh``. This ensures the version
    footer can show a real commit SHA in production even without a git
    checkout on the target host.

    Args:
        install_dir: Repository path. Defaults to ``/opt/__APP_SLUG__``
            when ``None``.

    Returns:
        Lowercase hex short SHA (max 7 chars) or ``None``.
    """
    target = install_dir if install_dir is not None else DEFAULT_INSTALL_DIR
    try:
        result = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except FileNotFoundError:
        log.debug("git_binary_missing", install_dir=str(target))
        return _read_commit_file_fallback()
    except subprocess.TimeoutExpired:
        log.debug("git_rev_parse_timeout", install_dir=str(target))
        return _read_commit_file_fallback()
    except OSError as exc:
        log.debug(
            "git_rev_parse_os_error", install_dir=str(target), error=str(exc)
        )
        return _read_commit_file_fallback()
    except Exception as exc:  # pragma: no cover - defensive catch-all
        log.debug(
            "git_rev_parse_unexpected",
            install_dir=str(target),
            error=str(exc),
        )
        return _read_commit_file_fallback()

    if result.returncode != 0:
        log.debug(
            "git_rev_parse_nonzero",
            install_dir=str(target),
            returncode=result.returncode,
            stderr=(result.stderr or "").strip()[:200],
        )
        return _read_commit_file_fallback()

    sha = (result.stdout or "").strip()
    if not sha:
        return _read_commit_file_fallback()
    return sha[:7]


_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def resolve_remote_main_sha(install_dir: Path | None = None) -> str | None:
    """Return the full 40-char SHA of the remote ``origin/main`` HEAD.

    Used to give the one-click updater a concrete, installable target: the
    updater_root only applies SHAs that are ancestors of ``origin/main``,
    and we cut every GitHub release at ``main`` HEAD, so the latest main
    commit IS the release commit. Fetches first so a freshly-pushed release
    is visible, then resolves ``origin/main``.

    Runs as the (non-root) main-service user against its own clone — both
    are read-only-ish git ops. Returns ``None`` on any failure (no git, no
    network, no checkout, bad output) — **never raises**.
    """
    target = install_dir if install_dir is not None else DEFAULT_INSTALL_DIR

    def _git(*args: str, timeout: int = 20) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(
                ["git", "-c", "safe.directory=*", "-C", str(target), *args],
                capture_output=True, text=True, timeout=timeout, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
            log.debug("git_remote_sha_error", install_dir=str(target), error=str(exc))
            return None

    # Best-effort fetch; even if it fails we still try the cached origin/main.
    _git("fetch", "origin", "main", "--quiet")
    result = _git("rev-parse", "origin/main", timeout=5)
    if result is None or result.returncode != 0:
        return None
    sha = (result.stdout or "").strip().lower()
    return sha if _FULL_SHA_RE.match(sha) else None
