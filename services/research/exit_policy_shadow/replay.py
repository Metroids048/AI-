"""Point-in-time exit-policy replay engine. Read-only.

Replays one real entry under one shadow exit policy. Bars are processed strictly
in order; the engine stops at the first bar where the exit completes, so excursions
are naturally truncated at the policy's exit point.
"""

from __future__ import annotations

from decimal import Decimal

from services.research.exit_policy_shadow.contracts import (
    Bar,
    ExitLeg,
    ExitPolicyId,
    ExitReason,
    IntrabarResolution,
    RealEntry,
    Regime,
    ShadowOutcome,
)
from services.research.exit_policy_shadow.excursions import compute_excursions
from services.research.exit_policy_shadow.policies import build_initial_geometry


def replay_entry_under_policy(
    *,
    entry: RealEntry,
    bars: list[Bar],
    policy: ExitPolicyId,
    regime: Regime,
    entry_context: dict[str, Decimal] | None = None,
) -> ShadowOutcome:
    """Replay one real entry under one shadow exit policy.

    Thin wrapper over :func:`replay_entry` for callers that pass policy last.
    ``entry_context`` carries entry-time indicator values (e.g. ``atr14``).
    """
    return replay_entry(
        entry=entry,
        policy=policy,
        bars=bars,
        entry_context=entry_context or {},
        regime=regime,
    )


# Binance USDT-M perpetual typical fees: maker 0.02%, taker 0.05%.
# Testnet_sampling_v2 was observed using market orders -> taker rate.
TAKER_FEE_BPS = Decimal("5")
ESTIMATED_SLIPPAGE_BPS = Decimal("1")


def replay_entry(
    *,
    entry: RealEntry,
    policy: ExitPolicyId,
    bars: list[Bar],
    entry_context: dict[str, Decimal],
    regime: Regime,
) -> ShadowOutcome:
    """Replay ``entry`` under ``policy`` over ``bars``.

    ``bars`` must be strictly chronological and must start after or at the fill
    timestamp. The engine exits at the first bar satisfying the policy's geometry.

    Intrabar ambiguity: when a bar's high/low bracket both stop and target, the
    primary result uses STOP_FIRST (conservative), and a sensitivity result is
    computed with TARGET_FIRST (optimistic).

    Returns
    -------
    ShadowOutcome
        The complete exit result, including excursions measured from entry to the
        actual exit bar.
    """
    initial_stop, initial_target = build_initial_geometry(
        policy=policy,
        side=entry.side,
        entry_price=entry.average_fill_price,
        entry_context=entry_context,
        regime=regime,
    )

    # Scale-out policies produce multiple legs; single-target policies have one.
    if policy == ExitPolicyId.SCALE_OUT_RUNNER:
        return _replay_scale_out(
            entry=entry,
            bars=bars,
            initial_stop=initial_stop,
            initial_target=initial_target,
            regime=regime,
        )

    # Single-target policies (A, B, C, or E-delegated).
    return _replay_single_target(
        entry=entry,
        policy=policy,
        bars=bars,
        initial_stop=initial_stop,
        initial_target=initial_target,
        regime=regime,
    )


def _replay_single_target(
    *,
    entry: RealEntry,
    policy: ExitPolicyId,
    bars: list[Bar],
    initial_stop: Decimal,
    initial_target: Decimal | None,
    regime: Regime,
) -> ShadowOutcome:
    """Replay a single-leg exit: first bar to hit stop or target closes the entire position."""
    bars_until_exit: list[Bar] = []
    exit_bar: Bar | None = None
    exit_price: Decimal | None = None
    exit_reason: ExitReason | None = None
    intrabar_resolution = IntrabarResolution.UNAMBIGUOUS
    sensitivity_exit_price: Decimal | None = None

    for bar in bars:
        if bar.time < entry.fill_timestamp:
            continue

        bars_until_exit.append(bar)

        hit_stop = _bar_touches(entry.side, bar, initial_stop, "stop")
        hit_target = initial_target is not None and _bar_touches(entry.side, bar, initial_target, "target")

        if hit_stop and hit_target:
            # Ambiguous: resolve conservatively to stop, report target as sensitivity.
            exit_bar = bar
            exit_price = initial_stop
            exit_reason = ExitReason.STOP
            intrabar_resolution = IntrabarResolution.STOP_FIRST
            sensitivity_exit_price = initial_target
            break

        if hit_stop:
            exit_bar = bar
            exit_price = initial_stop
            exit_reason = ExitReason.STOP
            break

        if hit_target:
            exit_bar = bar
            exit_price = initial_target
            exit_reason = ExitReason.TARGET
            break

    if exit_bar is None:
        # Data exhausted before exit.
        exit_bar = (
            bars[-1]
            if bars
            else Bar(
                time=entry.fill_timestamp,
                open=entry.average_fill_price,
                high=entry.average_fill_price,
                low=entry.average_fill_price,
                close=entry.average_fill_price,
                volume=Decimal("0"),
            )
        )
        exit_price = exit_bar.close
        exit_reason = ExitReason.DATA_EXHAUSTED

    assert exit_price is not None and exit_reason is not None

    risk_per_unit = abs(entry.average_fill_price - initial_stop)
    excursions = compute_excursions(
        side=entry.side,
        entry_price=entry.average_fill_price,
        quantity=entry.filled_quantity,
        bars=bars_until_exit,
        risk_per_unit=risk_per_unit,
    )

    gross_pnl = _compute_gross_pnl(
        side=entry.side,
        entry_price=entry.average_fill_price,
        exit_price=exit_price,
        quantity=entry.filled_quantity,
    )
    exit_fee = _estimate_exit_fee(exit_price, entry.filled_quantity)
    slippage = _estimate_slippage(exit_price, entry.filled_quantity)
    net_pnl = gross_pnl - entry.entry_fee_usdt - exit_fee - slippage

    sensitivity_net: Decimal | None = None
    if sensitivity_exit_price is not None:
        sens_gross = _compute_gross_pnl(
            entry.side, entry.average_fill_price, sensitivity_exit_price, entry.filled_quantity
        )
        sens_exit_fee = _estimate_exit_fee(sensitivity_exit_price, entry.filled_quantity)
        sens_slip = _estimate_slippage(sensitivity_exit_price, entry.filled_quantity)
        sensitivity_net = sens_gross - entry.entry_fee_usdt - sens_exit_fee - sens_slip

    holding_minutes = (exit_bar.time - entry.fill_timestamp).total_seconds() / 60

    regime_reason: str | None = None
    if policy == ExitPolicyId.REGIME_AWARE:
        from services.research.exit_policy_shadow.policies import resolve_regime_policy

        selection = resolve_regime_policy(regime)
        regime_reason = selection.reason

    leg = ExitLeg(
        label="exit",
        price=exit_price,
        quantity=entry.filled_quantity,
        quantity_fraction=Decimal("1"),
        filled_at=exit_bar.time,
        reason=exit_reason,
        intrabar=intrabar_resolution,
    )

    return ShadowOutcome(
        position_id=entry.position_id,
        symbol=entry.symbol,
        side=entry.side,
        policy=policy,
        regime=regime,
        entry_price=entry.average_fill_price,
        entry_quantity=entry.filled_quantity,
        initial_stop_price=initial_stop,
        initial_target_price=initial_target,
        legs=(leg,),
        remaining_quantity=Decimal("0"),
        final_reason=exit_reason,
        holding_time_minutes=Decimal(str(holding_minutes)),
        excursions=excursions,
        gross_pnl_usdt=gross_pnl,
        entry_fee_usdt=entry.entry_fee_usdt,
        estimated_exit_fee_usdt=exit_fee,
        estimated_slippage_usdt=slippage,
        net_pnl_usdt=net_pnl,
        ambiguous_intrabar=(intrabar_resolution != IntrabarResolution.UNAMBIGUOUS),
        sensitivity_net_pnl_usdt=sensitivity_net,
        regime_selection_reason=regime_reason,
    )


def _replay_scale_out(
    *,
    entry: RealEntry,
    bars: list[Bar],
    initial_stop: Decimal,
    initial_target: Decimal | None,
    regime: Regime,
) -> ShadowOutcome:
    """Replay a laddered exit: 35% at 1R, 40% at 1.8R, 25% runner with trailing stop.

    This is a generic multi-target implementation independent of any research candidate.
    """
    risk = abs(entry.average_fill_price - initial_stop)
    if risk <= 0:
        # Fallback: treat as single-target if risk is ill-defined.
        return _replay_single_target(
            entry=entry,
            policy=ExitPolicyId.SCALE_OUT_RUNNER,
            bars=bars,
            initial_stop=initial_stop,
            initial_target=initial_target,
            regime=regime,
        )

    # Define the ladder.
    if entry.side == "long":
        tp1 = entry.average_fill_price + risk  # 1R
        tp2 = entry.average_fill_price + Decimal("1.8") * risk
        tp3 = entry.average_fill_price + Decimal("2.5") * risk
    else:
        tp1 = entry.average_fill_price - risk
        tp2 = entry.average_fill_price - Decimal("1.8") * risk
        tp3 = entry.average_fill_price - Decimal("2.5") * risk

    fractions = [Decimal("0.35"), Decimal("0.40"), Decimal("0.25")]
    targets = [tp1, tp2, tp3]
    labels = ["TP1_1R", "TP2_1.8R", "TP3_2.5R_runner"]

    legs: list[ExitLeg] = []
    remaining = entry.filled_quantity
    bars_until_exit: list[Bar] = []
    stop = initial_stop
    final_reason = ExitReason.DATA_EXHAUSTED
    filled_target_indices: set[int] = set()

    for bar in bars:
        if bar.time < entry.fill_timestamp:
            continue
        bars_until_exit.append(bar)

        # Check stop first (conservative).
        if _bar_touches(entry.side, bar, stop, "stop"):
            if remaining > 0:
                legs.append(
                    ExitLeg(
                        label="stop",
                        price=stop,
                        quantity=remaining,
                        quantity_fraction=remaining / entry.filled_quantity,
                        filled_at=bar.time,
                        reason=ExitReason.STOP,
                        intrabar=IntrabarResolution.UNAMBIGUOUS,
                    )
                )
                remaining = Decimal("0")
            final_reason = ExitReason.STOP
            break

        # Check each target in sequence. A target already consumed in an earlier
        # bar must never fire again — without this guard a single leg can be
        # double-counted across bars, breaking quantity-fraction conservation.
        for i, (target, frac, lbl) in enumerate(zip(targets, fractions, labels, strict=True)):
            if i in filled_target_indices:
                continue
            if _bar_touches(entry.side, bar, target, "target"):
                qty = entry.filled_quantity * frac
                if qty > remaining:
                    qty = remaining
                if qty > 0:
                    legs.append(
                        ExitLeg(
                            label=lbl,
                            price=target,
                            quantity=qty,
                            quantity_fraction=frac,
                            filled_at=bar.time,
                            reason=ExitReason.PARTIAL_TARGET if i < 2 else ExitReason.TARGET,
                            intrabar=IntrabarResolution.UNAMBIGUOUS,
                        )
                    )
                    remaining -= qty
                filled_target_indices.add(i)
                # Move stop to breakeven after first target.
                if i == 0:
                    stop = entry.average_fill_price
                if remaining <= 0:
                    final_reason = ExitReason.TARGET
                    break

        if remaining <= 0:
            break

    if remaining > 0 and bars_until_exit:
        # Data exhausted with position still open.
        last_bar = bars_until_exit[-1]
        legs.append(
            ExitLeg(
                label="exhausted",
                price=last_bar.close,
                quantity=remaining,
                quantity_fraction=remaining / entry.filled_quantity,
                filled_at=last_bar.time,
                reason=ExitReason.DATA_EXHAUSTED,
                intrabar=IntrabarResolution.UNAMBIGUOUS,
            )
        )
        remaining = Decimal("0")

    exit_bar = (
        bars_until_exit[-1]
        if bars_until_exit
        else Bar(
            time=entry.fill_timestamp,
            open=entry.average_fill_price,
            high=entry.average_fill_price,
            low=entry.average_fill_price,
            close=entry.average_fill_price,
            volume=Decimal("0"),
        )
    )

    risk_per_unit = abs(entry.average_fill_price - initial_stop)
    excursions = compute_excursions(
        side=entry.side,
        entry_price=entry.average_fill_price,
        quantity=entry.filled_quantity,
        bars=bars_until_exit,
        risk_per_unit=risk_per_unit,
    )

    gross_pnl = sum(
        (_compute_gross_pnl(entry.side, entry.average_fill_price, leg.price, leg.quantity) for leg in legs),
        Decimal("0"),
    )
    exit_fee = sum((_estimate_exit_fee(leg.price, leg.quantity) for leg in legs), Decimal("0"))
    slippage = sum((_estimate_slippage(leg.price, leg.quantity) for leg in legs), Decimal("0"))
    net_pnl = gross_pnl - entry.entry_fee_usdt - exit_fee - slippage

    holding_minutes = (exit_bar.time - entry.fill_timestamp).total_seconds() / 60

    return ShadowOutcome(
        position_id=entry.position_id,
        symbol=entry.symbol,
        side=entry.side,
        policy=ExitPolicyId.SCALE_OUT_RUNNER,
        regime=regime,
        entry_price=entry.average_fill_price,
        entry_quantity=entry.filled_quantity,
        initial_stop_price=initial_stop,
        initial_target_price=tp1,
        legs=tuple(legs),
        remaining_quantity=remaining,
        final_reason=final_reason,
        holding_time_minutes=Decimal(str(holding_minutes)),
        excursions=excursions,
        gross_pnl_usdt=gross_pnl,
        entry_fee_usdt=entry.entry_fee_usdt,
        estimated_exit_fee_usdt=exit_fee,
        estimated_slippage_usdt=slippage,
        net_pnl_usdt=net_pnl,
        ambiguous_intrabar=False,
        sensitivity_net_pnl_usdt=None,
        regime_selection_reason=None,
    )


def _bar_touches(side: str, bar: Bar, price: Decimal, label: str) -> bool:
    """True if ``bar`` reached ``price`` given the trade direction."""
    if side == "long":
        if label == "stop":
            return bar.low <= price
        else:  # target
            return bar.high >= price
    else:
        if label == "stop":
            return bar.high >= price
        else:
            return bar.low <= price


def _compute_gross_pnl(side: str, entry_price: Decimal, exit_price: Decimal, quantity: Decimal) -> Decimal:
    """Gross PnL before fees/slippage."""
    if side == "long":
        return (exit_price - entry_price) * quantity
    else:
        return (entry_price - exit_price) * quantity


def _estimate_exit_fee(exit_price: Decimal, quantity: Decimal) -> Decimal:
    """Estimate the taker fee for exiting this leg."""
    notional = exit_price * quantity
    return notional * TAKER_FEE_BPS / Decimal("10000")


def _estimate_slippage(exit_price: Decimal, quantity: Decimal) -> Decimal:
    """Estimate slippage for exiting this leg."""
    notional = exit_price * quantity
    return notional * ESTIMATED_SLIPPAGE_BPS / Decimal("10000")
