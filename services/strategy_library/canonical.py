"""Canonical serialization primitives for research/runtime parity."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def _normalize(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _normalize(value.model_dump(mode="json"))
    if hasattr(value, "__dataclass_fields__"):
        from dataclasses import asdict

        return _normalize(asdict(value))
    if isinstance(value, Mapping):
        if all(isinstance(key, str) for key in value):
            return {key: _normalize(item) for key, item in value.items()}
        items = [[_normalize(key), _normalize(item)] for key, item in value.items()]
        items.sort(key=lambda item: json.dumps(item[0], sort_keys=True, separators=(",", ":"), default=str))
        return {"__type__": "mapping", "items": items}
    if isinstance(value, tuple):
        return {"__type__": "tuple", "items": [_normalize(item) for item in value]}
    if isinstance(value, list):
        return {"__type__": "list", "items": [_normalize(item) for item in value]}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return {
            "__type__": type(value).__name__,
            "items": [_normalize(item) for item in value],
        }
    if isinstance(value, Decimal):
        return {"__type__": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"__type__": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"__type__": "date", "value": value.isoformat()}
    return value


def canonical_json(value: Any) -> str:
    """Serialize nested Pydantic/dataclass primitives deterministically."""

    return json.dumps(_normalize(value), sort_keys=True, separators=(",", ":"), default=str)


def canonical_hash(value: Any) -> str:
    """Return the SHA-256 hash of the canonical JSON representation."""

    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
