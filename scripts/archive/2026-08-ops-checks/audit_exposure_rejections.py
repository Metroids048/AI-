"""Audit max_symbol/total exposure rejections vs stale equity or ghost positions.

Answers plan item p0-exposure-audit: are exposure rejections caused by real
open positions or by account_equity / position snapshots being wrong?

Usage:
    python scripts/audit_exposure_rejections.py --database-url sqlite:///.local_paper_console.db
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta


@dataclass
class ExposureRejectionRow:
    symbol: str
    rejection_codes: list[str]
    account_equity: float | None
    symbol_exposure: float | None
    total_exposure: float | None
    requested_notional: float | None
    open_positions: int | None
    created_at: str | None


@dataclass
class ExposureAuditReport:
    generated_at: str
    lookback_days: int
    total_orders: int
    exposure_rejection_count: int
    by_code: dict[str, int] = field(default_factory=dict)
    equity_sources: Counter = field(default_factory=Counter)
    sample_rows: list[ExposureRejectionRow] = field(default_factory=list)
    diagnosis: list[str] = field(default_factory=list)


EXPOSURE_CODES = {"max_symbol_exposure_exceeded", "max_total_exposure_exceeded"}


def _parse_risk_state(raw: object) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}
    if isinstance(raw, dict):
        return raw
    return {}


def run_audit(*, database_url: str, lookback_days: int = 14) -> ExposureAuditReport:
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url)
    cutoff = datetime.now(UTC) - timedelta(days=lookback_days)
    with engine.connect() as conn:
        total_orders = conn.execute(text("SELECT COUNT(*) FROM order_executions")).scalar_one()
        rows = conn.execute(
            text(
                "SELECT symbol, rejection_codes, evaluated_risk_state, created_at "
                "FROM order_executions WHERE created_at >= :cutoff ORDER BY created_at DESC"
            ),
            {"cutoff": (cutoff.replace(tzinfo=None) if database_url.startswith("sqlite") else cutoff)},
        ).fetchall()

    report = ExposureAuditReport(
        generated_at=datetime.now(UTC).isoformat(),
        lookback_days=lookback_days,
        total_orders=int(total_orders),
        exposure_rejection_count=0,
    )
    equity_histogram: Counter = Counter()
    low_equity_count = 0
    high_exposure_with_low_equity = 0

    for row in rows:
        codes = row.rejection_codes or []
        if isinstance(codes, str):
            try:
                codes = json.loads(codes)
            except json.JSONDecodeError:
                codes = [codes]
        exposure_hits = [code for code in codes if code in EXPOSURE_CODES]
        if not exposure_hits:
            continue
        report.exposure_rejection_count += 1
        for code in exposure_hits:
            report.by_code[code] = report.by_code.get(code, 0) + 1

        risk = _parse_risk_state(row.evaluated_risk_state)
        equity = risk.get("account_equity")
        symbol_exposure = risk.get("symbol_exposure")
        total_exposure = risk.get("total_exposure")
        requested = risk.get("requested_notional")
        open_positions = risk.get("open_positions")

        if equity is not None:
            equity_bucket = "<=5000" if float(equity) <= 5000 else ("<=10000" if float(equity) <= 10000 else ">10000")
            equity_histogram[equity_bucket] += 1
            if float(equity) <= 5000:
                low_equity_count += 1
        if equity is not None and total_exposure is not None and float(equity) <= 5000 and float(total_exposure) > 0.3:
            high_exposure_with_low_equity += 1

        if len(report.sample_rows) < 25:
            report.sample_rows.append(
                ExposureRejectionRow(
                    symbol=str(row.symbol),
                    rejection_codes=list(exposure_hits),
                    account_equity=float(equity) if equity is not None else None,
                    symbol_exposure=float(symbol_exposure) if symbol_exposure is not None else None,
                    total_exposure=float(total_exposure) if total_exposure is not None else None,
                    requested_notional=float(requested) if requested is not None else None,
                    open_positions=int(open_positions) if open_positions is not None else None,
                    created_at=str(row.created_at),
                )
            )

    if report.exposure_rejection_count == 0:
        report.diagnosis.append("No exposure rejections in lookback window.")
    else:
        # Prefer the measured root cause from real rows: oversized notional vs empty book.
        oversized = 0
        empty_book = 0
        for row in report.sample_rows:
            if row.account_equity and row.requested_notional and row.requested_notional / row.account_equity > 0.25:
                oversized += 1
            if (row.open_positions or 0) == 0 and (row.symbol_exposure or 0) == 0:
                empty_book += 1
        if oversized and empty_book:
            report.diagnosis.append(
                f"Root cause: {oversized}/{len(report.sample_rows)} samples request notional >25% equity "
                f"with open_positions=0 / symbol_exposure=0 — sizing path (risk_per_trade*leverage) exceeded "
                f"max_symbol_exposure; not ghost holdings. Fix: cap notional to max_position_fraction."
            )
        if low_equity_count > report.exposure_rejection_count * 0.5:
            report.diagnosis.append(
                "Majority of exposure rejections used account_equity <= 5000 — likely stale bootstrap "
                "equity rather than real Testnet balance. Fix: sync account_equity from exchange each cycle."
            )
        if high_exposure_with_low_equity > 0:
            report.diagnosis.append(
                f"{high_exposure_with_low_equity} rejections show high total_exposure with low equity — "
                "position notional is inflated relative to denominator (equity sync bug)."
            )
        if report.by_code.get("max_total_exposure_exceeded", 0) > report.by_code.get("max_symbol_exposure_exceeded", 0):
            report.diagnosis.append(
                "max_total_exposure_exceeded dominates — check ghost open positions or cumulative "
                "exposure not cleared after closes."
            )
        elif not (oversized and empty_book):
            report.diagnosis.append(
                "max_symbol_exposure_exceeded dominates — single-symbol concentration or oversized "
                "requested_notional relative to synced equity."
            )

    report.equity_sources = equity_histogram
    return report


def _render_markdown(report: ExposureAuditReport) -> str:
    lines = [
        "# Exposure Rejection Audit",
        "",
        f"- Generated: {report.generated_at}",
        f"- Lookback days: {report.lookback_days}",
        f"- Total orders (all time): {report.total_orders}",
        f"- Exposure rejections (lookback): {report.exposure_rejection_count}",
        "",
        "## Rejection code counts",
        "",
        "| code | count |",
        "| --- | ---: |",
    ]
    for code, count in sorted(report.by_code.items()):
        lines.append(f"| {code} | {count} |")
    lines.extend(["", "## account_equity buckets in rejected orders", ""])
    for bucket, count in report.equity_sources.most_common():
        lines.append(f"- {bucket}: {count}")
    lines.extend(["", "## Diagnosis", ""])
    for item in report.diagnosis:
        lines.append(f"- {item}")
    lines.extend(["", "## Sample rows (up to 25)", ""])
    lines.append("| symbol | codes | equity | sym_exp | tot_exp | req_notional | open_pos | at |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
    for row in report.sample_rows:
        lines.append(
            f"| {row.symbol} | {','.join(row.rejection_codes)} | "
            f"{row.account_equity or ''} | {row.symbol_exposure or ''} | {row.total_exposure or ''} | "
            f"{row.requested_notional or ''} | {row.open_positions or ''} | {row.created_at or ''} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit exposure-related order rejections.")
    parser.add_argument("--database-url", default=os.environ.get("POSTGRES_URL", "sqlite:///.local_paper_console.db"))
    parser.add_argument("--lookback-days", type=int, default=14)
    parser.add_argument("--output", default=None, help="Optional markdown output path")
    args = parser.parse_args()
    report = run_audit(database_url=args.database_url, lookback_days=args.lookback_days)
    markdown = _render_markdown(report)
    print(markdown)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(markdown)


if __name__ == "__main__":
    main()
