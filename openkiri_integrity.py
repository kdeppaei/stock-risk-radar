from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from threading import Lock
from typing import Any


HEX_32_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


def looks_like_32_hex(value: str) -> bool:
    """Return whether *value* has the shape of a 128-bit hexadecimal digest.

    Shape alone does not identify the algorithm that produced the value.
    """

    return bool(HEX_32_PATTERN.fullmatch(value.strip()))


def md5_hex(value: str) -> str:
    """Return an MD5 digest for a local, non-security verification demo."""

    return hashlib.md5(value.encode("utf-8"), usedforsecurity=False).hexdigest()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        if value == 0:
            return 0.0
    return value


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value deterministically for fingerprinting."""

    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint_payload(value: Any) -> tuple[str, int]:
    canonical = canonical_json(value).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest(), len(canonical)


@dataclass
class SnapshotFingerprintTracker:
    """Track the last SHA-256 fingerprint for each analysis request key."""

    _fingerprints: dict[tuple[str, str, str], str] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def observe(self, key: tuple[str, str, str], payload: Any) -> dict[str, Any]:
        fingerprint, canonical_bytes = fingerprint_payload(payload)
        with self._lock:
            previous = self._fingerprints.get(key)
            self._fingerprints[key] = fingerprint
        return {
            "algorithm": "sha256",
            "fingerprint": fingerprint,
            "changed_from_previous": None if previous is None else previous != fingerprint,
            "canonical_bytes": canonical_bytes,
            "purpose": "reproducibility and market-data snapshot change detection",
        }

