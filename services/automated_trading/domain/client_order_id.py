"""Deterministic Client Order IDs for V2 (plan section 6.2).

The Client Order ID is the only handle that survives a submission whose outcome
is undetermined. If a retry generated a fresh ID, an EXCHANGE_UNKNOWN timeout
would become a duplicate exchange position — the exact failure this rebuild
exists to eliminate.

Properties enforced here:
- Deterministic: the same (intent/position, leg) always yields the same ID.
- Distinct across legs: entry, exit, stop and target never collide.
- Binance-safe: <= 36 chars, ``[A-Za-z0-9-]`` only.
- Reverse-parsable: the prefix identifies the engine and leg kind locally.
- Carries no strategy key or secret, only a hash.

Format:
    A2E-{intent_hash_20}-{leg}    entry
    A2X-{intent_hash_20}-{leg}    reduce-only exit
    A2S-{position_hash_18}        stop-loss
    A2T-{position_hash_18}        take-profit
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum

# Binance USDT-M accepts up to 36 characters for newClientOrderId.
MAX_CLIENT_ORDER_ID_LENGTH = 36
CLIENT_ORDER_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")

_INTENT_HASH_LENGTH = 20
_POSITION_HASH_LENGTH = 18


class OrderLegKind(StrEnum):
    """Which leg of a position lifecycle an order belongs to."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"
    STOP = "STOP"
    TARGET = "TARGET"


_PREFIX_BY_LEG = {
    OrderLegKind.ENTRY: "A2E",
    OrderLegKind.EXIT: "A2X",
    OrderLegKind.STOP: "A2S",
    OrderLegKind.TARGET: "A2T",
}


def _hash(*parts: str, length: int) -> str:
    """Stable hex digest over the given parts.

    A separator that cannot appear inside the parts keeps ("ab", "c") distinct
    from ("a", "bc").
    """
    payload = "\x1f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def _validate(client_order_id: str) -> str:
    if len(client_order_id) > MAX_CLIENT_ORDER_ID_LENGTH:
        raise ValueError(
            f"client order id {client_order_id!r} is {len(client_order_id)} chars, "
            f"exceeding the {MAX_CLIENT_ORDER_ID_LENGTH} char exchange limit"
        )
    if not CLIENT_ORDER_ID_PATTERN.match(client_order_id):
        raise ValueError(f"client order id {client_order_id!r} contains characters outside [A-Za-z0-9-]")
    return client_order_id


def entry_client_order_id(intent_id: str, *, leg: int = 1) -> str:
    """Client Order ID for an entry submission.

    ``leg`` exists for future multi-leg entries; it is 1 for the single-leg
    Market Entry the first V2 release supports. Retrying the same intent must
    pass the same ``leg`` so the ID is unchanged.
    """
    if not intent_id:
        raise ValueError("intent_id is required")
    if leg < 1:
        raise ValueError(f"leg must be >= 1, got {leg}")
    digest = _hash(intent_id, str(leg), length=_INTENT_HASH_LENGTH)
    return _validate(f"{_PREFIX_BY_LEG[OrderLegKind.ENTRY]}-{digest}-{leg}")


def exit_client_order_id(position_id: str, *, attempt: int = 1) -> str:
    """Client Order ID for a reduce-only exit submission.

    ``attempt`` distinguishes successive *distinct* exit decisions on the same
    position (for example a partial reduce followed by a full close). A retry of
    the same exit decision must reuse the same attempt number.
    """
    if not position_id:
        raise ValueError("position_id is required")
    if attempt < 1:
        raise ValueError(f"attempt must be >= 1, got {attempt}")
    digest = _hash(position_id, str(attempt), length=_INTENT_HASH_LENGTH)
    return _validate(f"{_PREFIX_BY_LEG[OrderLegKind.EXIT]}-{digest}-{attempt}")


def stop_client_order_id(position_id: str, *, revision: int = 1) -> str:
    """Client Order ID for a stop-loss protection order.

    ``revision`` increments when protection is re-submitted after a partial fill
    changed the covered quantity.
    """
    if not position_id:
        raise ValueError("position_id is required")
    if revision < 1:
        raise ValueError(f"revision must be >= 1, got {revision}")
    digest = _hash(position_id, "stop", str(revision), length=_POSITION_HASH_LENGTH)
    return _validate(f"{_PREFIX_BY_LEG[OrderLegKind.STOP]}-{digest}")


def target_client_order_id(position_id: str, *, revision: int = 1) -> str:
    """Client Order ID for a take-profit protection order."""
    if not position_id:
        raise ValueError("position_id is required")
    if revision < 1:
        raise ValueError(f"revision must be >= 1, got {revision}")
    digest = _hash(position_id, "target", str(revision), length=_POSITION_HASH_LENGTH)
    return _validate(f"{_PREFIX_BY_LEG[OrderLegKind.TARGET]}-{digest}")


def leg_kind_of(client_order_id: str) -> OrderLegKind | None:
    """Reverse-parse the leg kind from a Client Order ID prefix.

    Returns None for IDs this engine did not mint, which is how reconciliation
    tells a V2 order apart from a foreign or legacy one.
    """
    prefix = client_order_id.split("-", 1)[0]
    for leg, leg_prefix in _PREFIX_BY_LEG.items():
        if prefix == leg_prefix:
            return leg
    return None


def is_v2_client_order_id(client_order_id: str) -> bool:
    """True when this engine minted the ID."""
    return leg_kind_of(client_order_id) is not None
