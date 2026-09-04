"""Operator-controlled attribution of one historical aggregate exchange fill.

This module has no exchange-write capability.  It prepares immutable manifests
in each participating local database and finalizes only after every declared
participant has prepared the same manifest.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.automated_trading.domain.enums import V2PositionState
from services.automated_trading.infrastructure.models import (
    V2AdjudicationAllocation,
    V2AdjudicationCase,
    V2AdjudicationFinalization,
    V2ExchangeFill,
    V2ManagedPosition,
)
from services.automated_trading.infrastructure.repository import AutomatedTradingRepository

_QUANTITY_PRECISION = Decimal("0.00000001")


def _normalized_quantity(value: Decimal) -> Decimal:
    """Avoid SQLite Numeric float round-trips changing exact quantity identity."""
    return Decimal(str(value)).quantize(_QUANTITY_PRECISION)


@dataclass(frozen=True)
class ExchangeAggregateExitEvidence:
    exchange_order_id: str
    exchange_trade_id: str
    symbol: str
    side: str
    reduce_only: bool
    executed_quantity: Decimal
    trade_quantity: Decimal
    price: Decimal
    exchange_event_time: datetime
    verification_reference: str = ""

    @property
    def evidence_hash(self) -> str:
        payload = {
            "exchange_order_id": self.exchange_order_id,
            "exchange_trade_id": self.exchange_trade_id,
            "symbol": self.symbol,
            "side": self.side.upper(),
            "reduce_only": self.reduce_only,
            "executed_quantity": str(self.executed_quantity),
            "trade_quantity": str(self.trade_quantity),
            "price": str(self.price),
            "exchange_event_time": self.exchange_event_time.isoformat(),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AllocationSpec:
    database_identity: str
    position_id: str
    allocated_quantity: Decimal


@dataclass(frozen=True)
class AdjudicationManifest:
    adjudication_id: str
    symbol: str
    exchange_order_id: str
    exchange_trade_id: str
    exchange_fill_quantity: Decimal
    exchange_fill_side: str
    exchange_fill_price: Decimal
    exchange_fill_timestamp: datetime
    operator_identity: str
    operator_reason: str
    allocations: tuple[AllocationSpec | tuple[str, str, Decimal], ...]
    account_scope_key: str

    @property
    def case_key(self) -> str:
        payload = {
            "account_scope_key": self.account_scope_key,
            "symbol": self.symbol,
            "exchange_order_id": self.exchange_order_id,
            "exchange_trade_id": self.exchange_trade_id,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def normalized_allocations(self) -> tuple[AllocationSpec, ...]:
        result: list[AllocationSpec] = []
        for item in self.allocations:
            if isinstance(item, AllocationSpec):
                result.append(item)
            else:
                database_identity, position_id, quantity = item
                result.append(AllocationSpec(database_identity, position_id, Decimal(quantity)))
        return tuple(result)

    @property
    def manifest_hash(self) -> str:
        payload = {
            "symbol": self.symbol,
            "account_scope_key": self.account_scope_key,
            "exchange_order_id": self.exchange_order_id,
            "exchange_trade_id": self.exchange_trade_id,
            "exchange_fill_quantity": str(self.exchange_fill_quantity),
            "exchange_fill_side": self.exchange_fill_side.upper(),
            "exchange_fill_price": str(self.exchange_fill_price),
            "exchange_fill_timestamp": self.exchange_fill_timestamp.isoformat(),
            "operator_identity": self.operator_identity,
            "operator_reason": self.operator_reason,
            "allocations": [
                {
                    "database_identity": item.database_identity,
                    "position_id": item.position_id,
                    "allocated_quantity": str(item.allocated_quantity),
                }
                for item in sorted(
                    self.normalized_allocations(), key=lambda value: (value.database_identity, value.position_id)
                )
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class BinanceAggregateExitEvidenceProvider:
    """Read a single exchange order and trade through the existing V2 adapter.

    The provider has no submit/cancel capability.  It intentionally derives the
    adjudication receipt from Binance responses rather than accepting quantities
    supplied by the operator.
    """

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def __call__(self, exchange_order_id: str, exchange_trade_id: str, symbol: str) -> ExchangeAggregateExitEvidence:
        order = self._adapter.query_filled_order_by_id(symbol, exchange_order_id)
        if order is None or order.exchange_order_id != exchange_order_id or order.status != "filled":
            raise ValueError("ADJUDICATION_EXCHANGE_ORDER_NOT_CONFIRMED")
        fills = self._adapter.fetch_fills(symbol, exchange_order_id)
        matching = tuple(fill for fill in fills if fill.trade_id == exchange_trade_id)
        if len(matching) != 1:
            raise ValueError("ADJUDICATION_EXCHANGE_TRADE_NOT_CONFIRMED")
        fill = matching[0]
        if fill.exchange_order_id != exchange_order_id:
            raise ValueError("ADJUDICATION_EXCHANGE_ID_MISMATCH")
        return ExchangeAggregateExitEvidence(
            exchange_order_id=order.exchange_order_id,
            exchange_trade_id=fill.trade_id,
            symbol=order.symbol,
            side=order.side,
            reduce_only=order.reduce_only,
            executed_quantity=order.quantity,
            trade_quantity=fill.filled_quantity,
            price=fill.fill_price,
            exchange_event_time=fill.fill_timestamp,
            verification_reference=f"binance-read:{exchange_order_id}/{exchange_trade_id}",
        )


def _validate_manifest(manifest: AdjudicationManifest, evidence: ExchangeAggregateExitEvidence) -> None:
    if not evidence.verification_reference.strip():
        raise ValueError("UNVERIFIED_EXCHANGE_EVIDENCE")
    if not manifest.operator_identity.strip() or not manifest.operator_reason.strip():
        raise ValueError("operator identity and reason are mandatory")
    if manifest.symbol != evidence.symbol:
        raise ValueError("ADJUDICATION_SYMBOL_MISMATCH")
    if (
        manifest.exchange_order_id != evidence.exchange_order_id
        or manifest.exchange_trade_id != evidence.exchange_trade_id
    ):
        raise ValueError("ADJUDICATION_EXCHANGE_ID_MISMATCH")
    if manifest.exchange_fill_side.upper() != evidence.side.upper() or not evidence.reduce_only:
        raise ValueError("ADJUDICATION_SIDE_OR_REDUCE_ONLY_MISMATCH")
    if (
        manifest.exchange_fill_price != evidence.price
        or manifest.exchange_fill_timestamp != evidence.exchange_event_time
    ):
        raise ValueError("ADJUDICATION_RECEIPT_MISMATCH")
    if _normalized_quantity(evidence.executed_quantity) != _normalized_quantity(evidence.trade_quantity):
        raise ValueError("AMBIGUOUS_AGGREGATE_FILL")
    if _normalized_quantity(manifest.exchange_fill_quantity) != _normalized_quantity(evidence.executed_quantity):
        raise ValueError("ADJUDICATION_QUANTITY_MISMATCH")
    allocations = manifest.normalized_allocations()
    if not allocations or any(item.allocated_quantity <= 0 for item in allocations):
        raise ValueError("INVALID_ALLOCATION_QUANTITY")
    total = sum((item.allocated_quantity for item in allocations), Decimal("0"))
    if _normalized_quantity(total) < _normalized_quantity(evidence.executed_quantity):
        raise ValueError("UNALLOCATED_EXCHANGE_QUANTITY")
    if _normalized_quantity(total) > _normalized_quantity(evidence.executed_quantity):
        raise ValueError("OVER_ALLOCATED_EXCHANGE_QUANTITY")
    if len({(item.database_identity, item.position_id) for item in allocations}) != len(allocations):
        raise ValueError("DUPLICATE_ALLOCATION")


def _existing_case(session: Session, manifest: AdjudicationManifest) -> V2AdjudicationCase | None:
    return session.scalar(select(V2AdjudicationCase).where(V2AdjudicationCase.case_key == manifest.case_key))


def prepare_adjudication(
    session: Session,
    *,
    manifest: AdjudicationManifest,
    database_identity: str,
    evidence_provider: Callable[[str, str, str], ExchangeAggregateExitEvidence] | None = None,
) -> V2AdjudicationCase:
    """Prepare one participant without changing position or reconciliation state."""
    if evidence_provider is None:
        raise ValueError("UNVERIFIED_EXCHANGE_EVIDENCE")
    evidence = evidence_provider(manifest.exchange_order_id, manifest.exchange_trade_id, manifest.symbol)
    _validate_manifest(manifest, evidence)
    participant = [item for item in manifest.normalized_allocations() if item.database_identity == database_identity]
    if not participant:
        raise ValueError("database identity is not declared in adjudication manifest")

    existing = _existing_case(session, manifest)
    if existing is not None:
        if existing.manifest_hash != manifest.manifest_hash:
            raise ValueError("ADJUDICATION_MANIFEST_CONFLICT")
        if existing.evidence_hash != evidence.evidence_hash:
            raise ValueError("ADJUDICATION_EVIDENCE_CHANGED")
        prepared = tuple(
            session.scalars(
                select(V2AdjudicationAllocation).where(
                    V2AdjudicationAllocation.adjudication_id == existing.adjudication_id,
                    V2AdjudicationAllocation.database_identity == database_identity,
                )
            )
        )
        declared = {(item.position_id, item.allocated_quantity) for item in participant}
        actual = {(item.position_id, item.allocated_quantity) for item in prepared}
        if actual == declared:
            return existing
        if actual:
            raise ValueError("adjudication manifest conflicts with prepared allocation")
        _validate_participant(session, manifest, participant)
        for item in participant:
            session.add(
                V2AdjudicationAllocation(
                    adjudication_id=existing.adjudication_id,
                    manifest_hash=manifest.manifest_hash,
                    evidence_hash=evidence.evidence_hash,
                    database_identity=item.database_identity,
                    position_id=item.position_id,
                    allocated_quantity=item.allocated_quantity,
                    before_state=V2PositionState.QUARANTINED.value,
                )
            )
        session.flush()
        return existing

    _validate_participant(session, manifest, participant)
    case = V2AdjudicationCase(
        adjudication_id=manifest.adjudication_id,
        case_key=manifest.case_key,
        exchange_account_identity=manifest.account_scope_key,
        manifest_hash=manifest.manifest_hash,
        symbol=manifest.symbol,
        exchange_order_id=manifest.exchange_order_id,
        exchange_trade_id=manifest.exchange_trade_id,
        exchange_fill_quantity=manifest.exchange_fill_quantity,
        exchange_fill_side=manifest.exchange_fill_side.upper(),
        exchange_fill_price=manifest.exchange_fill_price,
        exchange_fill_timestamp=manifest.exchange_fill_timestamp,
        operator_identity=manifest.operator_identity,
        operator_reason=manifest.operator_reason,
        evidence_hash=evidence.evidence_hash,
    )
    session.add(case)
    session.flush()
    for item in participant:
        session.add(
            V2AdjudicationAllocation(
                adjudication_id=manifest.adjudication_id,
                manifest_hash=manifest.manifest_hash,
                evidence_hash=evidence.evidence_hash,
                database_identity=item.database_identity,
                position_id=item.position_id,
                allocated_quantity=item.allocated_quantity,
                before_state=V2PositionState.QUARANTINED.value,
            )
        )
    try:
        session.flush()
    except IntegrityError as exc:
        raise ValueError("ADJUDICATION_MANIFEST_CONFLICT") from exc
    return case


def _validate_participant(
    session: Session,
    manifest: AdjudicationManifest,
    participant: Sequence[AllocationSpec],
) -> None:
    for item in participant:
        position = session.get(V2ManagedPosition, item.position_id)
        if position is None or position.state != V2PositionState.QUARANTINED.value:
            raise ValueError("adjudication requires a QUARANTINED lifecycle")
        if position.symbol != manifest.symbol:
            raise ValueError("ADJUDICATION_SYMBOL_MISMATCH")
        expected_side = "BUY" if position.direction == "short" else "SELL"
        if manifest.exchange_fill_side.upper() != expected_side:
            raise ValueError("ADJUDICATION_SIDE_OR_DIRECTION_MISMATCH")
        if Decimal(str(position.quantity)) != item.allocated_quantity:
            raise ValueError("AMBIGUOUS_AGGREGATE_FILL")
        local_trade = session.scalar(
            select(V2ExchangeFill).where(
                V2ExchangeFill.trade_id == manifest.exchange_trade_id,
            )
        )
        if local_trade is not None:
            raise ValueError("AMBIGUOUS_AGGREGATE_FILL")
        consumed = session.scalar(
            select(V2AdjudicationAllocation).where(
                V2AdjudicationAllocation.position_id == item.position_id,
                V2AdjudicationAllocation.adjudication_id != manifest.adjudication_id,
            )
        )
        if consumed is not None:
            raise ValueError("lifecycle allocation is already consumed")


def finalize_adjudication(
    sessions: Mapping[str, Session],
    *,
    manifest: AdjudicationManifest,
    evidence_provider: Callable[[str, str, str], ExchangeAggregateExitEvidence],
) -> None:
    """Finalize all prepared participants after a complete cross-database preflight."""
    evidence = evidence_provider(manifest.exchange_order_id, manifest.exchange_trade_id, manifest.symbol)
    _validate_manifest(manifest, evidence)
    allocations = manifest.normalized_allocations()
    declared_identities = {item.database_identity for item in allocations}
    if set(sessions) != declared_identities:
        raise ValueError("FINALIZABLE requires one session per declared database")
    participant_case_ids: dict[str, str] = {}
    for database_identity, session in sessions.items():
        conflicting_case = session.scalar(
            select(V2AdjudicationCase).where(
                V2AdjudicationCase.case_key == manifest.case_key,
                V2AdjudicationCase.manifest_hash != manifest.manifest_hash,
            )
        )
        if conflicting_case is not None:
            raise ValueError("FINALIZABLE exchange trade is already prepared by a different adjudication")
        case = session.scalar(select(V2AdjudicationCase).where(V2AdjudicationCase.case_key == manifest.case_key))
        if (
            case is None
            or case.manifest_hash != manifest.manifest_hash
            or case.evidence_hash != evidence.evidence_hash
            or case.exchange_order_id != manifest.exchange_order_id
            or case.exchange_trade_id != manifest.exchange_trade_id
            or _normalized_quantity(case.exchange_fill_quantity)
            != _normalized_quantity(manifest.exchange_fill_quantity)
        ):
            raise ValueError("FINALIZABLE requires every participant prepared with the same manifest")
        participant_case_ids[database_identity] = case.adjudication_id
        declared = {
            (item.position_id, item.allocated_quantity)
            for item in allocations
            if item.database_identity == database_identity
        }
        prepared = {
            (
                item.position_id,
                _normalized_quantity(item.allocated_quantity),
                item.manifest_hash,
                item.evidence_hash,
            )
            for item in session.scalars(
                select(V2AdjudicationAllocation).where(
                    V2AdjudicationAllocation.adjudication_id == case.adjudication_id,
                    V2AdjudicationAllocation.database_identity == database_identity,
                )
            )
        }
        expected_prepared = {
            (position_id, _normalized_quantity(quantity), manifest.manifest_hash, evidence.evidence_hash)
            for position_id, quantity in declared
        }
        if expected_prepared != prepared:
            raise ValueError("FINALIZABLE requires every declared allocation to be prepared")
    for database_identity, session in sessions.items():
        repo = AutomatedTradingRepository(session)
        for item in allocations:
            if item.database_identity != database_identity:
                continue
            position = session.get(V2ManagedPosition, item.position_id)
            finalization = session.scalar(
                select(V2AdjudicationFinalization).where(
                    V2AdjudicationFinalization.adjudication_id == participant_case_ids[database_identity],
                    V2AdjudicationFinalization.position_id == item.position_id,
                )
            )
            if position is None:
                raise ValueError("RECOVERY_FINALIZATION_INCOMPLETE")
            if finalization is not None:
                if (
                    finalization.manifest_hash != manifest.manifest_hash
                    or finalization.evidence_hash != evidence.evidence_hash
                    or finalization.exchange_trade_id != manifest.exchange_trade_id
                    or finalization.allocated_quantity != item.allocated_quantity
                ):
                    raise ValueError("RECOVERY_FINALIZATION_CONFLICT")
                continue
            if position.state != V2PositionState.QUARANTINED.value:
                raise ValueError("RECOVERY_FINALIZATION_INCOMPLETE")
            repo.finalize_quarantined_historical_attribution(
                position_id=item.position_id,
                adjudication_id=participant_case_ids[database_identity],
                exchange_order_id=manifest.exchange_order_id,
                exchange_trade_id=manifest.exchange_trade_id,
                manifest_hash=manifest.manifest_hash,
                evidence_hash=evidence.evidence_hash,
                database_identity=database_identity,
                aggregate_quantity=manifest.exchange_fill_quantity,
                allocated_quantity=item.allocated_quantity,
                occurred_at=manifest.exchange_fill_timestamp,
                operator_identity=manifest.operator_identity,
                operator_reason=manifest.operator_reason,
            )
        session.flush()
