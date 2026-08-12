"""E-010: apply the operator-authorized Testnet aggressive sizing profile.

Scope (authorized 2026-08-12): raise the automated entry EXPOSURE only, on the
Binance Testnet operator profile.

    max_symbol_exposure  0.35 -> 0.05   (target notional = 5% of account equity)
    max_leverage         40   -> 50
    risk_per_trade       0.10 -> unchanged (outside this authorization)

"5% position" means the target NOTIONAL is 5% of equity (~365 USDT at ~7300
USDT equity). It is NOT "5% of equity as margin, times 50x leverage" — that
would be 18250 USDT, 250% of equity. Leverage only relaxes the margin ceiling;
the 5% exposure cap is what sets the size. See
tests/automated_trading/application/test_e010_aggressive_sizing.py::
test_s004_five_percent_is_notional_not_margin_times_leverage.

MEASURED BASELINE (live Testnet profile, 2026-08-12, equity 7349 USDT):

    exposure 0.35 @ 40x  ->  notional 2572.15 USDT,  margin 64.30 USDT per symbol

The 64.30 USDT margin is what the account screen shows as "about 60U". It is
MARGIN, not position size. Therefore moving exposure 0.35 -> 0.05 is a ~7x
REDUCTION in notional (2572 -> 367), not an increase, and it drops margin to
~7.3 USDT at 50x.

Raising leverage alone does NOT enlarge the position: _calculate_quantity takes
min(risk_notional, equity*exposure, equity*leverage), and while the exposure cap
binds, 40x and 50x resolve to the identical notional (verified by
test_s005_leverage_raises_capacity_without_inflating_notional). Leverage only
frees margin. To make positions LARGER than today, exposure must go ABOVE 0.35.

CHAIN-STALL CAUTION: the cap gates new entries only and never reduces or closes
an open position. But a symbol already above the requested cap has every new
entry rejected with max_symbol_exposure_exceeded until it exits on its own
stop/target. The script reports this before writing.

Explicitly NOT in scope: entry signal thresholds, gate conditions, stop/target
geometry, protection orders, reconciliation, the external-position baseline, and
production/Mainnet defaults (shared PAPER_RUNTIME_LIMITS stays 40x/0.35/0.10).

Writes into the pending ConfigSnapshot so the change activates on the NEXT cycle
through the existing bootstrap path — it never mutates an in-flight cycle.

Usage:
    python scripts/apply_e010_testnet_sizing_profile.py --dry-run
    python scripts/apply_e010_testnet_sizing_profile.py --apply
    python scripts/apply_e010_testnet_sizing_profile.py --leverage 50 --exposure 0.35 --apply
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

TARGET_MAX_LEVERAGE = 50.0
TARGET_MAX_SYMBOL_EXPOSURE = 0.05

# risk_per_trade is deliberately NOT changed. The operator authorized exactly two
# things this round: 5% notional exposure and 50x leverage. Under a 0.35% stop the
# exposure ceiling binds first anyway, so touching risk_per_trade would change
# nothing observable while widening the diff beyond the authorization.
EXECUTION_SYMBOLS = ("BTC/USDT", "ETH/USDT")


def _config_hash(config: dict[str, Any]) -> str:
    """Stable hash over the snapshot payload, matching repository conventions."""
    import hashlib

    encoded = json.dumps(config, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _resolved_preview(profile: dict[str, Any]) -> list[str]:
    """Show what the cycle will actually receive per execution symbol."""
    from services.automated_trading.application.operator_profile import (
        resolve_v2_execution_settings,
    )

    lines = []
    for symbol in EXECUTION_SYMBOLS:
        settings = resolve_v2_execution_settings(symbol, profile)
        lines.append(
            f"    {symbol}: leverage={settings.max_leverage}x "
            f"max_position_fraction={settings.max_position_fraction} "
            f"risk_per_trade={settings.risk_per_trade}"
        )
    return lines


def _apply_targets(profile: dict[str, Any], *, leverage: float, exposure: float) -> dict[str, Any]:
    """Return a copy with the requested leverage/exposure applied, tiers included."""
    updated = copy.deepcopy(profile)
    updated["max_leverage"] = leverage
    updated["max_symbol_exposure"] = exposure

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
        "--exposure",
        type=float,
        default=TARGET_MAX_SYMBOL_EXPOSURE,
        help=(
            "target notional exposure as a fraction of equity "
            f"(default {TARGET_MAX_SYMBOL_EXPOSURE:g}). This IS the position size; "
            "it is never multiplied by leverage."
        ),
    )
    args = parser.parse_args()

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
        print(f"  max_symbol_exposure = {current.get('max_symbol_exposure')}")
        print("\nCurrent resolved settings (what the cycle receives):")
        for line in _resolved_preview(current):
            print(line)

        print(f"\nApplying targets (leverage={args.leverage:g}x exposure={args.exposure:g}):")
        updated = _apply_targets(current, leverage=args.leverage, exposure=args.exposure)

        print("\nTarget profile-wide values:")
        print(f"  risk_per_trade      = {updated['risk_per_trade']}")
        print(f"  max_leverage        = {updated['max_leverage']}")
        print(f"  max_symbol_exposure = {updated['max_symbol_exposure']}")
        print("\nTarget resolved settings:")
        for line in _resolved_preview(updated):
            print(line)

        equity_example = 7349.0
        notional = equity_example * args.exposure
        print(f"\nExpected notional at {equity_example:.0f} USDT equity: {notional:.2f} USDT")
        print(f"  margin at {args.leverage:g}x            : {notional / args.leverage:.2f} USDT")
        print(
            "  (exposure IS the notional; it is never multiplied by leverage — "
            f"that misreading would give {notional * args.leverage:.0f} USDT)"
        )

        stalls = _chain_stall_warnings(args.exposure)
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
        config_payload = dict(active.config) if active is not None else {}
        config_payload["execution_profile"] = updated

        snapshot = ConfigSnapshot(
            paper_run_id=run.paper_run_id,
            config=config_payload,
            config_hash=_config_hash(config_payload),
            created_by="e010_testnet_sizing_script",
            previous_snapshot_id=active.config_snapshot_id if active is not None else None,
        )
        staged = snapshots.create_snapshot(
            snapshot,
            base_config_hash=run.active_config_hash,
        )
        run.execution_profile = updated
        session.commit()

        print(f"\nAPPLIED. Staged ConfigSnapshot {staged.config_snapshot_id}")
        print("Activates on the next scheduler cycle via activate_pending;")
        print("no in-flight cycle was mutated.")
        print(json.dumps(updated, indent=2, sort_keys=True, default=str))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
