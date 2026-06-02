"""Optional GPG SHA256SUMS.asc verification + SHA256SUMS file check.

By default ``allow_unsigned=True`` — the GPG verifier is a no-op and
every release is accepted as long as its ``SHA256SUMS`` file matches the
computed hashes. Flip the default to require a maintainer key on the box.

NOTE on runtime status: the default execution path uses a git-based
install (``git clone --shared`` + ``git checkout --detach``) where
integrity is delivered by the git SHA content-hash. The SHA256SUMS
primitives in this module are therefore DORMANT in that path, reserved
for a potential tarball-alternative install. They are still thoroughly
unit-tested.
"""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(component="updater_root.gpg_verify")

#: Upper bound on the ``gpg --verify`` subprocess run time.
GPG_VERIFY_TIMEOUT_S: float = 15.0


@dataclass
class GpgConfig:
    """Runtime GPG policy.

    ``allow_unsigned=True`` (the default) short-circuits
    :func:`verify_sha256sums_signature` to a no-op. ``keyring_path``
    is passed to ``gpg --no-default-keyring --keyring <path>`` when
    present, so the updater doesn't rely on root's default keyring.
    """

    allow_unsigned: bool = True
    keyring_path: Path | None = None


@dataclass
class GpgResult:
    """Result of :func:`verify_sha256sums_signature`."""

    ok: bool
    reason: str
    verified_uid: str | None = None


def compute_sha256(path: Path) -> str:
    """Return hex SHA-256 of ``path`` using a 64 KiB streaming buffer."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_sha256sums_file(
    sums_path: Path,
    files_dir: Path,
) -> list[tuple[str, bool, str]]:
    """Verify every file listed in a ``SHA256SUMS`` manifest.

    Parses the standard ``sha256sum`` output format::

        <64-hex>  <filename>
        <64-hex> *<filename>   # binary mode prefix

    Blank lines and ``#`` comments are skipped. Malformed lines (fewer
    than 2 whitespace-separated tokens) are silently ignored — the
    caller evaluates the list of results, so malformed lines don't
    break the whole check.

    Returns a list of ``(filename, matches, expected_hash)`` tuples.
    Missing files count as ``matches=False``.
    """
    results: list[tuple[str, bool, str]] = []
    for line in sums_path.read_text().splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split()
        if len(parts) < 2:
            continue
        expected_hash = parts[0].lower()
        # Rejoin remaining parts so filenames with spaces survive; strip
        # the sha256sum "*" binary-mode marker if present.
        filename = " ".join(parts[1:])
        if filename.startswith("*"):
            filename = filename[1:]
        target = files_dir / filename
        if not target.exists():
            results.append((filename, False, expected_hash))
            continue
        actual = compute_sha256(target)
        results.append((filename, actual == expected_hash, expected_hash))
    return results


async def verify_sha256sums_signature(
    sums_path: Path,
    sig_path: Path,
    config: GpgConfig,
) -> GpgResult:
    """Verify ``sig_path`` is a good GPG signature over ``sums_path``.

    Short-circuits to ``GpgResult(ok=True, reason="unsigned_allowed")``
    when ``config.allow_unsigned`` is true (the default). Otherwise:

    1. Missing ``sig_path`` -> ``ok=False, reason="signature_file_missing"``.
    2. Run ``gpg --status-fd 1 --verify <sig_path> <sums_path>`` (optionally
       with ``--no-default-keyring --keyring <config.keyring_path>``).
    3. Parse the status output for:
       - ``BADSIG`` -> ``ok=False, reason="bad_signature"``
       - ``EXPSIG`` / ``EXPKEYSIG`` -> ``ok=False, reason="expired_signature"``
       - ``GOODSIG`` + ``VALIDSIG`` -> ``ok=True, reason="valid_signature"``
    4. Any other shape -> ``ok=False`` with a truncated status in the reason.

    On timeout, the subprocess is killed and ``ok=False,
    reason="gpg_timeout"`` is returned. This function NEVER raises.
    """
    if config.allow_unsigned:
        log.info("gpg_verify_skipped", reason="allow_unsigned")
        return GpgResult(ok=True, reason="unsigned_allowed")
    if not sig_path.exists():
        return GpgResult(ok=False, reason="signature_file_missing")

    if config.keyring_path is not None:
        args = [
            "gpg",
            "--no-default-keyring",
            "--keyring",
            str(config.keyring_path),
            "--status-fd",
            "1",
            "--verify",
            str(sig_path),
            str(sums_path),
        ]
    else:
        args = [
            "gpg",
            "--status-fd",
            "1",
            "--verify",
            str(sig_path),
            str(sums_path),
        ]

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(
            proc.communicate(), timeout=GPG_VERIFY_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except ProcessLookupError:
            pass
        return GpgResult(ok=False, reason="gpg_timeout")

    status = stdout.decode("utf-8", errors="replace")
    if "BADSIG" in status:
        return GpgResult(ok=False, reason="bad_signature")
    if "EXPSIG" in status or "EXPKEYSIG" in status:
        return GpgResult(ok=False, reason="expired_signature")
    if "GOODSIG" in status and "VALIDSIG" in status:
        return GpgResult(ok=True, reason="valid_signature")
    return GpgResult(
        ok=False, reason=f"unexpected_status: {status[:200]}"
    )
