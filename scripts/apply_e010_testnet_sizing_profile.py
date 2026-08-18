"""Apply the operator-authorized directional Testnet sizing profile.

This compatibility script stages the directional Testnet Canary 30x / 5%-margin profile.

    max_margin_fraction  0.05
    max_symbol_exposure  1.50 (0.05 margin x 30 leverage)
    max_total_exposure   7.50 (five entry slots)
    max_leverage         30
    risk_per_trade       0.10 (diagnostic in Canary sampling)

5% of equity is margin (~350 USDT at 7000 USDT equity), so the entry notional
is ~10,500 USDT at 30x. The Canary V2 path targets this margin directly; stop-risk
metrics remain calculated and recorded diagnostically.

MEASURED BASELINE (live Testnet profile, 2026-08-12, equity 7349 USDT):

    exposure 0.35 @ 40x  ->  notional 2572.15 USDT,  margin 64.30 USDT per symbol

The 64.30 USDT margin is what the account screen shows as "about 60U". It is
MARGIN, not position size. The Canary target is explicitly 5% margin, which is
~367.45 USDT at this equity and ~11,023.50 USDT notional at 30x.

The Canary V2 sizing path targets `equity * 0.05 * 30`; exchange quantity-step
rounding may only make actual margin slightly lower. Risk metrics remain recorded
for diagnosis and do not shrink the Canary target.

CHAIN-STALL CAUTION: the cap gates new entries only and never reduces or closes
an open position. But a symbol already above the requested cap has every new
entry rejected with max_symbol_exposure_exceeded until it exits on its own
stop/target. The script reports this before writing.

Explicitly NOT in scope: entry signal thresholds, gate conditions, stop/target
geometry, protection orders, reconciliation, the external-position baseline, and
production/Mainnet defaults, or the existing open position.

Writes into the pending ConfigSnapshot so the change activates on the NEXT cycle
through the existing bootstrap path — it never mutates an in-flight cycle.

Usage:
    python scripts/apply_e010_testnet_sizing_profile.py --dry-run
    python scripts/apply_e010_testnet_sizing_profile.py --apply
    python scripts/apply_e010_testnet_sizing_profile.py --leverage 30 --margin 0.05 --apply
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

from services.data.universe import AUTO_SIMULATION_EXECUTION_SYMBOLS

TARGET_MAX_LEVERAGE = 30.0
TARGET_MAX_MARGIN_FRACTION = 0.05
TARGET_MAX_SYMBOL_EXPOSURE = 1.50
TARGET_MAX_OPEN_POSITIONS = 5
TARGET_MAX_TOTAL_EXPOSURE = TARGET_MAX_SYMBOL_EXPOSURE * TARGET_MAX_OPEN_POSITIONS
TARGET_RISK_PER_TRADE = 0.10

EXECUTION_SYMBOLS = AUTO_SIMULATION_EXECUTION_SYMBOLS


def _config_hash(config: dict[str, Any]) -> str:
    """Stable hash over the snapshot payload, matching repository conventions."""
    import hashlib

    encoded = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolved_preview(profile: dict[str, Any]) -> list[str]:
    """Show what the cycle will actually receive per execution symbol."""
    from services.automated_trading.application.operator_profile import (
        apply_testnet_canary_runtime_contract,
        resolve_v2_execution_settings,
    )

    lines = []
    for symbol in EXECUTION_SYMBOLS:
        settings = apply_testnet_canary_runtime_contract(
            resolve_v2_execution_settings(symbol, profile),
            symbol=symbol,
            execution_mode="BINANCE_TESTNET",
            entry_authority="TESTNET_CANARY",
        )
        lines.append(
            f"    {symbol}: leverage={settings.max_leverage}x "
            f"max_margin_fraction={settings.max_margin_fraction} "
            f"max_position_fraction={settings.max_position_fraction} "
            f"risk_per_trade={settings.risk_per_trade}"
        )
    return lines


def _apply_targets(profile: dict[str, Any], *, leverage: float, margin: float) -> dict[str, Any]:
    """Return a copy with the requested leverage/margin applied, tiers included."""
    updated = copy.deepcopy(profile)
    exposure = min(margin * leverage, TARGET_MAX_SYMBOL_EXPOSURE)
    updated["max_leverage"] = leverage
    updated["max_margin_fraction"] = margin
    updated["max_symbol_exposure"] = exposure
    updated["max_total_exposure"] = exposure * TARGET_MAX_OPEN_POSITIONS
    updated["max_open_positions"] = TARGET_MAX_OPEN_POSITIONS
    updated["max_symbols"] = len(EXECUTION_SYMBOLS)
    updated["risk_per_trade"] = TARGET_RISK_PER_TRADE
    updated["execution_mode"] = "binance_testnet"
    updated["simulation_sampling_fallback_enabled"] = True

    # Asset tiers override profile-wide values for BTC/ETH, so they must move too
    # or the tier would silently keep the previous band.
    tiers = updated.get("asset_risk_tiers")
    if isinstance(tiers, dict):
        for tier_name, tier in tiers.items():
            if not isinstance(tier, dict):
                continue
            symbols = tier.get("symbols") or []
            if any(symbol in EXECUTION_SYMBOLS for symbol in symbols):
                tier["leverage"] = leverage
                tier["max_position_fraction"] = exposure
                tier["max_leverage"] = leverage
                tier["risk_per_trade"] = TARGET_RISK_PER_TRADE
                tier["max_margin_fraction"] = margin
                print(f"  tier '{tier_name}' -> {leverage:g}x / {exposure:g} (covers {symbols})")
    return updated


def _chain_stall_warnings(exposure: float) -> list[str]:
    """Warn when open positions already exceed the requested cap.

    The cap only gates NEW entries — it never reduces or closes an open position
    (see tests/automated_trading/application/test_e010_aggressive_sizing.py::
    TestE010ExposureCapNeverTouchesExistingPositions). But if a live position is
    already above the requested cap, Gatekeeper will reject every new entry for
    that symbol with max_symbol_exposure_exceeded until the position exits on its
    own stop/target. That stalls the automated entry chain, so it must be stated
    before the change is written, not discovered afterwards.
    """
    import json as _json
    import urllib.request

    warnings: list[str] = []
    token = "dev-admin-token"
    env_path = Path("frontend/admin/.env")
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("VITE_ADMIN_API_TOKEN="):
                token = line.split("=", 1)[1].strip().strip('"')

    try:
        request = urllib.request.Request(
            "http://127.0.0.1:8016/api/v1/runtime/snapshot",
            headers={"Authorization": f"Bearer {token}"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=15) as response:
            snapshot = _json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [f"could not read live positions to check for chain stall: {exc}"]

    exchange = (snapshot.get("exchange") or {}).get("value") or {}
    account = exchange.get("account") or {}
    equity = float(account.get("margin_balance") or account.get("wallet_balance") or 0)
    if equity <= 0:
        return ["live equity unavailable; chain-stall check skipped"]

    for position in exchange.get("positions") or []:
        contracts = abs(float(position.get("contracts") or 0))
        mark = float(position.get("mark_price") or 0)
        if contracts <= 0 or mark <= 0:
            continue
        fraction = (contracts * mark) / equity
        if fraction > exposure:
            warnings.append(
                f"{position.get('symbol')} is at {fraction:.2%} of equity, above the "
                f"requested {exposure:.2%} cap — new entries for this symbol will be "
                "rejected until it exits on its own stop/target"
            )
    return warnings


def _require_no_pending_snapshot(snapshots: Any, paper_run_id: str) -> None:
    """Fail closed rather than discard another operator's NEXT_CYCLE change."""
    pending = snapshots.get_pending(paper_run_id)
    if pending is not None:
        raise RuntimeError(
            "a pending ConfigSnapshot already exists; refusing to overwrite "
            f"{pending.config_snapshot_id}. Activate or reconcile that change, then rerun."
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="show the change, write nothing")
    group.add_argument("--apply", action="store_true", help="stage the change for the next cycle")
    parser.add_argument(
        "--leverage",
        type=float,
        default=TARGET_MAX_LEVERAGE,
        help=f"requested leverage (default {TARGET_MAX_LEVERAGE:g})",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=TARGET_MAX_MARGIN_FRACTION,
        help=f"target margin fraction (default {TARGET_MAX_MARGIN_FRACTION:g})",
    )
    args = parser.parse_args()

    if args.leverage != TARGET_MAX_LEVERAGE or args.margin != TARGET_MAX_MARGIN_FRACTION:
        print(
            f"refusing Canary contract drift; required values are exactly "
            f"{TARGET_MAX_LEVERAGE:g}x / {TARGET_MAX_MARGIN_FRACTION:g} margin",
            file=sys.stderr,
        )
        return 2

    from services.database import get_session_factory
    from services.strategy_library import ConfigSnapshotRepository, PaperRunRepository

    with get_session_factory()() as session:
        paper_repo = PaperRunRepository(session)
        running = [
            run
            for run in paper_repo.list_paper_runs()
            if run.paper_status == "running" and run.execution_profile.get("strategy_lane") == "directional"
        ]

        if not running:
            print("ERROR: no running directional PaperRun; nothing to update", file=sys.stderr)
            return 1
        if len(running) != 1:
            print(
                f"ERROR: {len(running)} running directional PaperRuns; "
                "V2 entry configuration is fail-closed and this script will not guess",
                file=sys.stderr,
            )
            return 1

        run = running[0]
        assert run.paper_run_id is not None
        current = run.execution_profile

        print(f"PaperRun: {run.paper_run_id}")
        print("\nCurrent profile-wide values:")
        print(f"  risk_per_trade      = {current.get('risk_per_trade')}")
        print(f"  max_leverage        = {current.get('max_leverage')}")
        print(f"  max_margin_fraction = {current.get('max_margin_fraction')}")
        print(f"  max_symbol_exposure = {current.get('max_symbol_exposure')}")
        print("\nCurrent resolved settings (what the cycle receives):")
        for line in _resolved_preview(current):
            print(line)

        print(f"\nApplying targets (leverage={args.leverage:g}x margin={args.margin:g}):")
        updated = _apply_targets(current, leverage=args.leverage, margin=args.margin)

        print("\nTarget profile-wide values:")
        print(f"  risk_per_trade      = {updated['risk_per_trade']}")
        print(f"  max_leverage        = {updated['max_leverage']}")
        print(f"  max_margin_fraction = {updated['max_margin_fraction']}")
        print(f"  max_symbol_exposure = {updated['max_symbol_exposure']}")
        print(f"  max_total_exposure  = {updated['max_total_exposure']}")
        print("\nTarget resolved settings:")
        for line in _resolved_preview(updated):
            print(line)

        equity_example = 7349.0
        notional = equity_example * args.margin * args.leverage
        print(f"\nExpected notional at {equity_example:.0f} USDT equity: {notional:.2f} USDT")
        print(f"  margin at {args.leverage:g}x            : {notional / args.leverage:.2f} USDT")

        stalls = _chain_stall_warnings(updated["max_symbol_exposure"])
        if stalls:
            print("\nCHAIN-STALL WARNING (entries blocked, positions NOT touched):")
            for warning in stalls:
                print(f"  - {warning}")
        else:
            print("\nChain-stall check: no open position exceeds the requested cap.")

        if args.dry_run:
            print("\nDRY RUN: nothing written.")
            return 0

        # Stage through the existing ConfigSnapshot contract so the change lands on
        # the NEXT cycle via activate_pending, never mid-cycle. create_snapshot
        # verifies base_config_hash, so a concurrent operator edit fails loudly
        # instead of being silently overwritten.
        from shared.models import ConfigSnapshot

        snapshots = ConfigSnapshotRepository(session)
        active = snapshots.get_active(run.paper_run_id)
        try:
            _require_no_pending_snapshot(snapshots, run.paper_run_id)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        config_payload = dict(active.config) if active is not None else {}
        config_payload["execution_profile"] = updated

        snapshot = ConfigSnapshot.create(
            paper_run_id=run.paper_run_id,
            config=config_payload,
            created_by="e010_testnet_sizing_script",
            effective_cycle_id="NEXT_CYCLE",
            previous_snapshot_id=active.config_snapshot_id if active is not None else None,
        )
        try:
            staged = snapshots.stage_execution_profile_snapshot(
                snapshot,
                execution_profile=updated,
                base_config_hash=run.active_config_hash,
            )
        except Exception as exc:  # fail closed: no partial profile/snapshot write
            print(f"ERROR: failed to stage sizing configuration safely: {exc}", file=sys.stderr)
            return 1

        print(f"\nAPPLIED. Staged ConfigSnapshot {staged.config_snapshot_id}")
        print("Activates on the next scheduler cycle via activate_pending;")
        print("no in-flight cycle was mutated.")
        print(json.dumps(updated, indent=2, sort_keys=True, default=str))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
