from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from services.automated_trading.infrastructure.models import MicrostructureSnapshot, V2DecisionSnapshot


@dataclass(frozen=True)
class ReadinessReport:
    total_candidate_windows: int
    btc_candidate_windows: int
    eth_candidate_windows: int
    candidate_coverage_ratio: float
    overall_coverage_ratio: float
    ready: bool
    state: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def evaluate_readiness(session: Session, *, window_minutes: int = 5) -> ReadinessReport:
    decisions = list(
        session.scalars(select(V2DecisionSnapshot).where(V2DecisionSnapshot.symbol.in_(("BTC/USDT", "ETH/USDT"))))
    )
    collection_start = session.scalar(
        select(MicrostructureSnapshot.received_at).order_by(MicrostructureSnapshot.received_at).limit(1)
    )
    total = btc = eth = covered = 0
    for decision in decisions:
        payload = decision.payload if isinstance(decision.payload, dict) else {}
        candidate = payload.get("candidate") or payload.get("trade_candidate_payload")
        funnel = payload.get("decision", {}).get("funnel", {}) if isinstance(payload.get("decision"), dict) else {}
        if not candidate and not funnel.get("created_candidate") and not funnel.get("candidate_id"):
            continue
        if (
            collection_start is not None
            and decision.decision_time + timedelta(minutes=window_minutes) < collection_start
        ):
            continue
        total += 1
        if decision.symbol == "BTC/USDT":
            btc += 1
        if decision.symbol == "ETH/USDT":
            eth += 1
        start = decision.decision_time - timedelta(minutes=window_minutes)
        end = decision.decision_time + timedelta(minutes=window_minutes)
        has_row = session.scalar(
            select(MicrostructureSnapshot.snapshot_id)
            .where(MicrostructureSnapshot.symbol == decision.symbol)
            .where(MicrostructureSnapshot.received_at >= start)
            .where(MicrostructureSnapshot.received_at <= end)
            .where(MicrostructureSnapshot.is_valid.is_(True))
            .limit(1)
        )
        covered += int(has_row is not None)
    candidate_ratio = covered / total if total else 0.0
    all_rows = list(session.scalars(select(MicrostructureSnapshot)))
    valid_rows = sum(1 for row in all_rows if row.is_valid)
    overall = valid_rows / len(all_rows) if all_rows else 0.0
    ready = total >= 100 and btc >= 40 and eth >= 40 and candidate_ratio >= 0.95 and overall >= 0.95
    state = "MICROSTRUCTURE_PIPELINE_READY_AND_COLLECTING" if ready else "MICROSTRUCTURE_COLLECTING_NOT_READY"
    return ReadinessReport(total, btc, eth, candidate_ratio, overall, ready, state)
