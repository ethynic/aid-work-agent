"""Protocol negotiation and canonical digest helpers."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

SUPPORTED_PROTOCOL_VERSIONS = ("1.0",)


class ProtocolVersionError(ValueError):
    pass


def negotiate_version(client_versions: Iterable[str]) -> str:
    # Numeric ordering is required once a minor reaches two digits: lexical
    # sorting incorrectly ranks 1.9 above 1.10 and would diverge from the JS
    # contract generator's negotiation logic.
    common = sorted(
        set(client_versions).intersection(SUPPORTED_PROTOCOL_VERSIONS),
        key=lambda version: tuple(int(part) for part in version.split(".")),
        reverse=True,
    )
    if not common:
        raise ProtocolVersionError("No compatible Desktop Agent protocol version")
    return common[0]


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()
