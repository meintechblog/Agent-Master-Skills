"""GitHub Releases API client with ETag caching.

Requirements:
    aiohttp client with required headers, 10s timeout, and ETag caching to
        keep rate-limit friendly.
    Fault tolerance — network errors, 4xx, 5xx, timeouts, and parse errors
        must NEVER crash the caller. Every failure path returns ``None``
        and emits a structured log warning.

References:
    - src/__PKG_NAME__/state_file.py (mirror the atomic-write pattern)

Design notes:
    The client does NOT own its ``aiohttp.ClientSession``. The caller
    passes in an existing session — the webapp typically already keeps
    one around — so we avoid spawning a second one.

    ETags are persisted to ``/etc/__APP_SLUG__/update-state.json``
    using the exact tempfile + ``os.replace`` pattern from ``state_file.py``
    so a crash mid-write cannot corrupt the cache file. The state file path
    is injectable via the constructor so tests can point it at ``tmp_path``.

    We intentionally swallow EVERY exception in ``fetch_latest_release``,
    including ``Exception`` at the bottom. That looks broad, but the
    contract is explicit: the scheduler must keep running even on
    unexpected errors — an unexpected exception here would bubble up into
    the scheduler loop and the rest of the webapp must remain unaffected.
"""
from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import aiohttp
import structlog

log = structlog.get_logger(component="updater.github_client")

#: Upstream repository. The /releases/latest endpoint automatically
#: excludes drafts and returns the most recent non-draft release.
GITHUB_REPO = "__GITHUB_REPO__"

#: Canonical Releases-API URL. Leading-slash collapsed, no query string.
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

#: User-Agent is REQUIRED by GitHub API (else 403).
USER_AGENT = "__APP_SLUG__ (github.com/__GITHUB_REPO__)"

#: Stable Accept header pins us to the v3 JSON schema.
ACCEPT = "application/vnd.github+json"

#: Explicit API version so GitHub can phase out old schemas
#: without breaking us silently.
API_VERSION = "2022-11-28"

#: 10s total timeout covers connect + TLS + body. GitHub's
#: median P95 for /releases/latest is <300ms, so 10s is an order of
#: magnitude safety margin.
REQUEST_TIMEOUT_SECONDS = 10.0

#: Where the ETag cache lives on disk. Same directory as state.json so
#: install.sh already creates the parent with the right mode.
DEFAULT_STATE_FILE: Path = Path("/etc/__APP_SLUG__/update-state.json")


@dataclass(frozen=True)
class ReleaseInfo:
    """Structured view of the fields we care about from /releases/latest.

    Everything is a plain string / bool so ``asdict()`` round-trips cleanly
    through JSON for the ETag cache file. The GitHub response has many more
    fields; we deliberately ignore them to keep the cache small and the
    parsing surface minimal.
    """

    tag_name: str
    published_at: str
    body: str
    html_url: str
    prerelease: bool


class GithubReleaseClient:
    """Fetches /releases/latest with ETag-aware caching.

    Not thread-safe — intended for single-loop asyncio use. Re-entering
    ``fetch_latest_release`` concurrently from the same instance is undefined.

    Args:
        session: An existing ``aiohttp.ClientSession`` owned by the caller.
            The client will NOT close it.
        state_file: Path to the on-disk ETag cache. Defaults to
            :data:`DEFAULT_STATE_FILE`. Tests pass a ``tmp_path`` here.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        state_file: Path = DEFAULT_STATE_FILE,
    ) -> None:
        self._session = session
        self._state_file = state_file
        self._etag: Optional[str] = None
        self._cached_release: Optional[ReleaseInfo] = None
        self._load_state()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_state(self) -> None:
        """Load any previously saved ETag + release from disk.

        Silent on every failure path: missing file, permission error,
        corrupt JSON, unexpected shape. State stays as ``None`` in that
        case, which is indistinguishable from first-boot behavior.
        """
        if not self._state_file.exists():
            return
        try:
            raw = self._state_file.read_text()
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning(
                "update_state_file_unreadable",
                path=str(self._state_file),
                error=str(exc),
            )
            return
        if not isinstance(data, dict):
            log.warning(
                "update_state_file_wrong_type",
                path=str(self._state_file),
                type=type(data).__name__,
            )
            return

        etag = data.get("etag")
        if isinstance(etag, str) and etag:
            self._etag = etag

        release_raw = data.get("release")
        if isinstance(release_raw, dict):
            try:
                self._cached_release = ReleaseInfo(
                    tag_name=str(release_raw.get("tag_name", "")),
                    published_at=str(release_raw.get("published_at", "")),
                    body=str(release_raw.get("body", "")),
                    html_url=str(release_raw.get("html_url", "")),
                    prerelease=bool(release_raw.get("prerelease", False)),
                )
            except Exception as exc:  # pragma: no cover - defensive
                log.warning(
                    "update_state_release_shape_bad",
                    path=str(self._state_file),
                    error=str(exc),
                )
                self._cached_release = None

    def _save_state(
        self, etag: Optional[str], release: Optional[ReleaseInfo]
    ) -> None:
        """Atomically persist the ETag + release.

        Best-effort: failures are logged but NOT raised — a write failure
        must not corrupt the fetch return value. Mirrors the pattern in
        ``state_file.save_state()`` except this one swallows errors, since
        the ETag cache is an optimization, not correctness.
        """
        target = self._state_file
        tmp = target.with_suffix(target.suffix + ".tmp")
        try:
            payload = json.dumps(
                {
                    "etag": etag,
                    "release": asdict(release) if release is not None else None,
                },
                indent=2,
                sort_keys=True,
            )
            tmp.write_text(payload)
            os.replace(tmp, target)
            os.chmod(target, 0o644)
        except OSError as exc:
            log.warning(
                "update_state_write_failed",
                path=str(target),
                error=str(exc),
            )
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        except Exception as exc:  # pragma: no cover - defensive
            log.warning(
                "update_state_write_unexpected",
                path=str(target),
                error=str(exc),
            )

    # ------------------------------------------------------------------
    # Main fetch
    # ------------------------------------------------------------------

    def _build_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {
            "User-Agent": USER_AGENT,
            "Accept": ACCEPT,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if self._etag:
            headers["If-None-Match"] = self._etag
        return headers

    async def fetch_latest_release(self) -> Optional[ReleaseInfo]:
        """Fetch the latest release; return None on any failure.

         contract: this method NEVER raises. Every exception is
        logged as a warning and results in ``None``. Callers cannot
        distinguish "network failed" from "no release exists" from the
        return value alone — use the log stream for that. may
        introduce a richer return type if we need finer fault reporting
        in the UI.
        """
        headers = self._build_headers()
        try:
            async with self._session.get(
                GITHUB_API_URL,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as resp:
                status = resp.status

                if status == 304:
                    log.info(
                        "github_release_not_modified",
                        etag=self._etag,
                        has_cache=self._cached_release is not None,
                    )
                    return self._cached_release

                if status in (403, 429):
                    snippet = ""
                    try:
                        body = await resp.text()
                        snippet = (body or "")[:200]
                    except Exception:  # pragma: no cover - best-effort
                        snippet = ""
                    log.warning(
                        "github_rate_limit_or_forbidden",
                        status=status,
                        body=snippet,
                    )
                    return None

                if status >= 500:
                    log.warning(
                        "github_server_error",
                        status=status,
                    )
                    return None

                if status != 200:
                    log.warning(
                        "github_unexpected_status",
                        status=status,
                    )
                    return None

                data = await resp.json()
                if not isinstance(data, dict):
                    log.warning(
                        "github_response_wrong_type",
                        type=type(data).__name__,
                    )
                    return None

                prerelease = bool(data.get("prerelease", False))
                new_etag = resp.headers.get("ETag")

                if prerelease:
                    log.info(
                        "github_release_is_prerelease_skipped",
                        tag=data.get("tag_name"),
                    )
                    # Still persist the ETag so we don't re-fetch the
                    # same prerelease body on the next iteration.
                    self._etag = new_etag or self._etag
                    self._cached_release = None
                    self._save_state(self._etag, None)
                    return None

                release = ReleaseInfo(
                    tag_name=str(data.get("tag_name", "") or ""),
                    published_at=str(data.get("published_at", "") or ""),
                    body=str(data.get("body", "") or ""),
                    html_url=str(data.get("html_url", "") or ""),
                    prerelease=False,
                )

                self._etag = new_etag or self._etag
                self._cached_release = release
                self._save_state(self._etag, release)

                log.info(
                    "github_release_fetched",
                    tag=release.tag_name,
                    published_at=release.published_at,
                    etag=self._etag,
                )
                return release

        except asyncio.TimeoutError:
            log.warning(
                "github_request_timeout",
                timeout_s=REQUEST_TIMEOUT_SECONDS,
            )
            return None
        except aiohttp.ClientError as exc:
            log.warning(
                "github_client_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.warning(
                "github_parse_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        except Exception as exc: # catch-all
            log.warning(
                "github_unexpected_error",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
