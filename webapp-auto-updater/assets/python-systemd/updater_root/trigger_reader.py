"""Trigger file consumer + nonce replay guard.

This module is the security root of trust for the trigger protocol: it
re-validates every field the producer (``__PKG_NAME__.updater.trigger``)
wrote, enforces strict schema equality (no extra keys), and persists
processed nonces to disk so a retry of the same trigger is idempotent.

The schema is MIRRORED from ``updater.trigger`` on purpose — importing
``updater.trigger`` from here would violate the trust boundary (main-service
code must never be loaded by the root helper). The mirror is sparse and
independently validated against the on-disk bytes.

Schema (see ``updater.trigger.TriggerPayload``):

    {
        "op": "update" | "rollback",
        "target_sha": "<40-char hex>" | "previous",
        "requested_at": "<ISO-8601 Z>",
        "requested_by": "<string>",
        "nonce": "<uuid>"
    }

Extra keys are REJECTED (: "exactly these 5 fields").
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(component="updater_root.trigger_reader")

#: Canonical dedup store path (install.sh created /var/lib/__APP_SLUG__
#: mode 2775 root:<service-user>). Only the updater (root) writes here.
PROCESSED_NONCES_PATH: Path = Path(
    "/var/lib/__APP_SLUG__/processed-nonces.json"
)

#: Maximum number of nonces retained in the dedup store.
DEFAULT_MAX_NONCES: int = 50

#: The producer writes EXACTLY these 5 keys. Any extra or missing key is
#: a schema violation. See ``updater.trigger.TriggerPayload``.
ALLOWED_KEYS: frozenset[str] = frozenset(
    {"op", "target_sha", "requested_at", "requested_by", "nonce"}
)

#: Valid ``op`` values.
VALID_OPS: frozenset[str] = frozenset({"update", "rollback"})

#: Rollback sentinel — allows the producer to request rollback without
#: knowing the previous SHA.
ROLLBACK_SENTINEL: str = "previous"

#: 40-char lowercase hex SHA, anchored; ReDoS-safe bounded quantifier.
SHA_RE: re.Pattern[str] = re.compile(r"^[0-9a-f]{40}$")

#: tag regex: ``v<major>.<minor>`` with optional ``.<patch>``.
#: No pre-release / rc / build metadata allowed.
TAG_RE: re.Pattern[str] = re.compile(r"^v\d+\.\d+(\.\d+)?$")


class TriggerValidationError(Exception):
    """Raised for any trigger schema or content violation."""


class NonceReplayError(TriggerValidationError):
    """Raised when a trigger nonce has already been processed."""


@dataclass
class ValidatedTrigger:
    """Successfully validated trigger payload.

    ``raw_body`` preserves the original parsed dict for audit logging.
    """

    op: str
    target_sha: str
    requested_at: str
    requested_by: str
    nonce: str
    raw_body: dict[str, Any] = field(default_factory=dict)


def validate_tag_regex(tag: str) -> bool:
    """: ``True`` iff ``tag`` matches ``^v\\d+\\.\\d+(\\.\\d+)?$``.

     will call this against the ``tag_name`` returned by the
    GitHub releases API before accepting any tag->SHA mapping. The
    trigger itself carries a SHA, not a tag, so this function is exposed
    here but not called by :func:`read_and_validate_trigger`.
    """
    if not isinstance(tag, str):
        return False
    return bool(TAG_RE.match(tag))


def _parse_iso_utc(value: str) -> bool:
    """Return True iff ``value`` is a valid ISO-8601 UTC string ending in ``Z``.

    Accepts the exact format produced by
    ``updater.trigger.now_iso_utc`` (``YYYY-MM-DDTHH:MM:SSZ``).
    """
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        # strptime handles the second-precision shape strictly.
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


class NonceDedupStore:
    """Persistent last-N nonce set backed by a JSON file.

    File schema (v1):

        {"nonces": [{"nonce": "<uuid>", "seen_at": <float>}, ...]}

    * Missing file: treated as empty (first boot / fresh install).
    * Corrupt JSON / wrong top-level type: treated as empty with a
      warning. The failure mode is "reprocess one trigger", which is
      strictly better than "lock out updates forever".
    * Writes are atomic via tempfile + ``os.replace`` (same pattern as
      ``state_file.save_state``).
    * Trimmed to ``max_entries`` newest on every write.
    """

    def __init__(
        self,
        path: Path | None = None,
        max_entries: int = DEFAULT_MAX_NONCES,
    ) -> None:
        self.path = path or PROCESSED_NONCES_PATH
        self.max_entries = max_entries

    def _load(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            body = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            log.warning(
                "dedup_store_corrupt",
                path=str(self.path),
                error=str(e),
                action="treating_as_empty",
            )
            return []
        if not isinstance(body, dict):
            log.warning(
                "dedup_store_wrong_type",
                path=str(self.path),
                type=type(body).__name__,
            )
            return []
        nonces = body.get("nonces")
        if not isinstance(nonces, list):
            log.warning("dedup_store_bad_nonces_field", path=str(self.path))
            return []
        # Filter out malformed entries defensively.
        clean: list[dict[str, Any]] = []
        for entry in nonces:
            if (
                isinstance(entry, dict)
                and isinstance(entry.get("nonce"), str)
                and isinstance(entry.get("seen_at"), (int, float))
            ):
                clean.append(entry)
        return clean

    def _save(self, entries: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Trim to newest max_entries by seen_at desc.
        entries_sorted = sorted(
            entries, key=lambda e: e["seen_at"], reverse=True
        )[: self.max_entries]
        payload = json.dumps({"nonces": entries_sorted}, indent=2, sort_keys=True)
        tmp = self.path.with_suffix(".json.tmp")
        try:
            tmp.write_text(payload)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o644)
        except OSError as e:
            log.error(
                "dedup_store_write_failed", path=str(self.path), error=str(e)
            )
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
            raise

    def has_seen(self, nonce: str) -> bool:
        entries = self._load()
        return any(e.get("nonce") == nonce for e in entries)

    def mark_seen(self, nonce: str, now: float | None = None) -> None:
        entries = self._load()
        # If already present, no-op (mark_seen is idempotent in practice,
        # though has_seen is the gate used by read_and_validate_trigger).
        if any(e.get("nonce") == nonce for e in entries):
            return
        t = now if now is not None else time.time()
        entries.append({"nonce": nonce, "seen_at": t})
        self._save(entries)


def read_and_validate_trigger(
    path: Path,
    dedup_store: NonceDedupStore,
) -> ValidatedTrigger:
    """Read, validate, and dedup a trigger file.

    Sequence:

    1. Read ``path`` (FileNotFoundError -> TriggerValidationError).
    2. Parse JSON (errors -> TriggerValidationError).
    3. Must be a dict with EXACTLY ``ALLOWED_KEYS`` (no extras, no missing).
    4. ``op`` in ``VALID_OPS``.
    5. ``target_sha`` matches ``SHA_RE``, or equals ``"previous"`` for rollback.
    6. ``requested_at`` parseable as ``YYYY-MM-DDTHH:MM:SSZ``.
    7. ``requested_by`` non-empty string.
    8. ``nonce`` non-empty string.
    9. ``dedup_store.has_seen(nonce)`` -> NonceReplayError.
    10. ``dedup_store.mark_seen(nonce)`` + return ValidatedTrigger.

    Any failure BEFORE step 10 is a :class:`TriggerValidationError`
    (or :class:`NonceReplayError`). The nonce is only marked seen after
    full validation so a malformed file cannot poison the dedup store.
    """
    if not path.exists():
        raise TriggerValidationError(f"trigger file missing: {path}")
    try:
        raw = path.read_text()
        body = json.loads(raw)
    except OSError as e:
        raise TriggerValidationError(f"trigger unreadable: {e}") from e
    except json.JSONDecodeError as e:
        raise TriggerValidationError(f"trigger not valid JSON: {e}") from e

    if not isinstance(body, dict):
        raise TriggerValidationError(
            f"trigger not a JSON object (got {type(body).__name__})"
        )

    keys = set(body.keys())
    if keys != ALLOWED_KEYS:
        extra = keys - ALLOWED_KEYS
        missing = ALLOWED_KEYS - keys
        raise TriggerValidationError(
            f"trigger schema mismatch: extra={sorted(extra)}, "
            f"missing={sorted(missing)}"
        )

    op = body["op"]
    if op not in VALID_OPS:
        raise TriggerValidationError(f"invalid op: {op!r}")

    target_sha = body["target_sha"]
    if op == "update":
        if not isinstance(target_sha, str) or not SHA_RE.match(target_sha):
            raise TriggerValidationError(
                f"update requires 40-char lowercase hex SHA, got {target_sha!r}"
            )
    else:  # rollback
        if target_sha != ROLLBACK_SENTINEL and (
            not isinstance(target_sha, str) or not SHA_RE.match(target_sha)
        ):
            raise TriggerValidationError(
                "rollback target_sha must be 'previous' or 40-char lowercase "
                f"hex SHA, got {target_sha!r}"
            )

    requested_at = body["requested_at"]
    if not _parse_iso_utc(requested_at):
        raise TriggerValidationError(
            f"requested_at not ISO-8601 UTC 'YYYY-MM-DDTHH:MM:SSZ': "
            f"{requested_at!r}"
        )

    requested_by = body["requested_by"]
    if not isinstance(requested_by, str) or not requested_by:
        raise TriggerValidationError(
            f"requested_by must be non-empty string, got {requested_by!r}"
        )

    nonce = body["nonce"]
    if not isinstance(nonce, str) or not nonce:
        raise TriggerValidationError(
            f"nonce must be non-empty string, got {nonce!r}"
        )

    if dedup_store.has_seen(nonce):
        raise NonceReplayError(f"nonce already processed: {nonce}")

    dedup_store.mark_seen(nonce)
    return ValidatedTrigger(
        op=op,
        target_sha=target_sha,
        requested_at=requested_at,
        requested_by=requested_by,
        nonce=nonce,
        raw_body=dict(body),
    )
