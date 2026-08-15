"""Pure canonicalisation and evaluation helpers for a Testnet trade audit.

The exchange trade receipt is the source of truth.  This module deliberately
does not import any execution service or mutate any state.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")


def as_decimal(value: object, default: Decimal = ZERO) -> Decimal:
    """Parse a finite Decimal without silently manufacturing a value."""
    if value in (None, ""):
        return default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"not a decimal: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"not a finite decimal: {value!r}")
    return result


def canonical_symbol(value: object) -> str:
    """Normalise Binance and CCXT perpetual symbols to ``BASE/USDT``."""
    raw = str(value or "").upper().replace(":USDT", "")
    if "/" in raw:
        return raw
    if raw.endswith("USDT"):
        return f"{raw[:-4]}/USDT"
    return raw


def event_time(row: Mapping[str, Any]) -> datetime:
    """Return a UTC timestamp from Binance raw or canonical trade rows."""
    value = row.get("time", row.get("timestamp"))
    if value is None:
        raise ValueError("trade receipt has no timestamp")
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    numeric = int(str(value))
    if numeric > 10_000_000_000:
        return datetime.fromtimestamp(numeric / 1000, tz=UTC)
    return datetime.fromtimestamp(numeric, tz=UTC)


def trade_side(row: Mapping[str, Any]) -> str:
    side = str(row.get("side") or "").upper()
    if side in {"BUY", "SELL"}:
        return side
    buyer = row.get("buyer")
    if buyer is not None:
        return "BUY" if bool(buyer) else "SELL"
    raise ValueError("trade receipt has no BUY/SELL side")


@dataclass
class TradeEpisode:
    """One exchange position lifecycle from open to flat (or current open)."""

    episode_id: str
    symbol: str
    direction: str
    entry_time: datetime
    entry_price: Decimal
    entry_quantity: Decimal
    fills: list[dict[str, Any]] = field(default_factory=list)
    exit_time: datetime | None = None
    exit_price: Decimal | None = None
    exit_quantity: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    commission: Decimal = ZERO
    funding: Decimal = ZERO
    local_context: dict[str, Any] = field(default_factory=dict)
    exit_reason: str = "UNKNOWN"
    mfe_pct: Decimal | None = None
    mae_pct: Decimal | None = None

    @property
    def status(self) -> str:
        return "CLOSED" if self.exit_time is not None else "OPEN"

    @property
    def net_pnl(self) -> Decimal:
        return self.realized_pnl - self.commission + self.funding

    @property
    def holding_seconds(self) -> int | None:
        if self.exit_time is None:
            return None
        return int((self.exit_time - self.entry_time).total_seconds())

    def as_row(self) -> dict[str, Any]:
        realized_move_pct: Decimal | None = None
        if self.exit_price is not None and self.entry_price > ZERO:
            sign = Decimal("1") if self.direction == "long" else Decimal("-1")
            realized_move_pct = sign * (self.exit_price - self.entry_price) / self.entry_price * Decimal("100")
        giveback = None
        if self.mfe_pct is not None and realized_move_pct is not None:
            giveback = max(ZERO, self.mfe_pct - realized_move_pct)
        return {
            "episode_id": self.episode_id,
            "symbol": self.symbol,
            "direction": self.direction,
            "status": self.status,
            "entry_time": self.entry_time.isoformat(),
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "entry_price": str(self.entry_price),
            "exit_price": str(self.exit_price) if self.exit_price is not None else None,
            "entry_quantity": str(self.entry_quantity),
            "exit_quantity": str(self.exit_quantity),
            "realized_pnl": str(self.realized_pnl),
            "commission": str(self.commission),
            "funding": str(self.funding),
            "net_pnl": str(self.net_pnl),
            "holding_seconds": self.holding_seconds,
            "exit_reason": self.exit_reason,
            "mfe_pct": str(self.mfe_pct) if self.mfe_pct is not None else None,
            "mae_pct": str(self.mae_pct) if self.mae_pct is not None else None,
            "giveback_pct": str(giveback) if giveback is not None else None,
            **self.local_context,
        }


def _trade_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    quantity = as_decimal(raw.get("qty", raw.get("amount")))
    price = as_decimal(raw.get("price"))
    if quantity <= ZERO or price <= ZERO:
        raise ValueError("trade receipt requires positive price and quantity")
    trade_id = str(raw.get("id", raw.get("tradeId", ""))).strip()
    order_id = str(raw.get("orderId", raw.get("order", ""))).strip()
    if not trade_id or not order_id:
        raise ValueError("trade receipt requires trade id and order id")
    return {
        "trade_id": trade_id,
        "order_id": order_id,
        "symbol": canonical_symbol(raw.get("symbol")),
        "side": trade_side(raw),
        "quantity": quantity,
        "price": price,
        "commission": as_decimal(raw.get("commission", (raw.get("fee") or {}).get("cost"))),
        "realized_pnl": as_decimal(raw.get("realizedPnl", raw.get("realized_pnl"))),
        "time": event_time(raw),
        "raw": dict(raw),
    }


def build_trade_episodes(
    trades: Iterable[Mapping[str, Any]],
    *,
    funding: Iterable[Mapping[str, Any]] = (),
    local_by_trade: Mapping[tuple[str, str], Mapping[str, Any]] | None = None,
    exit_reason_by_order: Mapping[str, str] | None = None,
) -> list[TradeEpisode]:
    """Aggregate exchange fills into canonical lifecycle episodes.

    This handles partial fills and partial exits.  A single fill that reverses a
    one-way account closes the old lifecycle first and starts a new one for the
    residual quantity; Binance's realised PnL is retained with the close.
    """
    local_by_trade = local_by_trade or {}
    exit_reason_by_order = exit_reason_by_order or {}
    parsed = sorted((_trade_record(item) for item in trades), key=lambda item: (item["time"], item["trade_id"]))
    open_by_symbol: dict[str, tuple[TradeEpisode, Decimal]] = {}
    complete: list[TradeEpisode] = []
    counter = 0

    def open_episode(record: dict[str, Any], signed_quantity: Decimal) -> tuple[TradeEpisode, Decimal]:
        nonlocal counter
        counter += 1
        direction = "long" if signed_quantity > ZERO else "short"
        local = dict(local_by_trade.get((record["symbol"], record["trade_id"]), {}))
        episode = TradeEpisode(
            episode_id=f"{record['symbol'].replace('/', '-')}-{counter:04d}",
            symbol=record["symbol"],
            direction=direction,
            entry_time=record["time"],
            entry_price=record["price"],
            entry_quantity=abs(signed_quantity),
            fills=[record],
            commission=record["commission"],
            local_context=local,
        )
        return episode, signed_quantity

    for record in parsed:
        signed = record["quantity"] if record["side"] == "BUY" else -record["quantity"]
        current = open_by_symbol.get(record["symbol"])
        if current is None:
            open_by_symbol[record["symbol"]] = open_episode(record, signed)
            continue

        episode, net_quantity = current
        same_direction = (net_quantity > ZERO and signed > ZERO) or (net_quantity < ZERO and signed < ZERO)
        if same_direction:
            total = abs(net_quantity) + abs(signed)
            episode.entry_price = (episode.entry_price * abs(net_quantity) + record["price"] * abs(signed)) / total
            episode.entry_quantity += abs(signed)
            episode.commission += record["commission"]
            episode.fills.append(record)
            open_by_symbol[record["symbol"]] = (episode, net_quantity + signed)
            continue

        before_abs = abs(net_quantity)
        close_quantity = min(before_abs, abs(signed))
        episode.exit_quantity += close_quantity
        episode.exit_price = record["price"]
        episode.realized_pnl += record["realized_pnl"]
        episode.commission += record["commission"]
        episode.fills.append(record)
        remaining = net_quantity + signed
        if remaining == ZERO:
            episode.exit_time = record["time"]
            episode.exit_reason = exit_reason_by_order.get(record["order_id"], "UNKNOWN")
            complete.append(episode)
            del open_by_symbol[record["symbol"]]
        elif (net_quantity > ZERO) == (remaining > ZERO):
            open_by_symbol[record["symbol"]] = (episode, remaining)
        else:
            episode.exit_time = record["time"]
            episode.exit_reason = exit_reason_by_order.get(record["order_id"], "REVERSAL")
            complete.append(episode)
            residual = abs(remaining)
            reversal_record = {**record, "quantity": residual, "commission": ZERO, "realized_pnl": ZERO}
            open_by_symbol[record["symbol"]] = open_episode(reversal_record, remaining)

    episodes = [*complete, *(value[0] for value in open_by_symbol.values())]
    funding_rows = sorted(funding, key=event_time)
    for episode in episodes:
        upper = episode.exit_time or datetime.now(UTC)
        for row in funding_rows:
            if canonical_symbol(row.get("symbol")) != episode.symbol:
                continue
            when = event_time(row)
            if episode.entry_time <= when <= upper:
                episode.funding += as_decimal(row.get("income"))
    return sorted(episodes, key=lambda episode: (episode.entry_time, episode.episode_id))


def add_excursions(episode: TradeEpisode, bars: Iterable[Mapping[str, Any]]) -> None:
    """Attach data-backed MFE/MAE. Missing bars remain explicitly unmeasured."""
    prices = [(as_decimal(bar.get("high")), as_decimal(bar.get("low"))) for bar in bars]
    if not prices or episode.entry_price <= ZERO:
        return
    if episode.direction == "long":
        best = max(high for high, _ in prices) - episode.entry_price
        worst = min(low for _, low in prices) - episode.entry_price
    else:
        best = episode.entry_price - min(low for _, low in prices)
        worst = episode.entry_price - max(high for high, _ in prices)
    episode.mfe_pct = max(ZERO, best) / episode.entry_price * Decimal("100")
    episode.mae_pct = min(ZERO, worst) / episode.entry_price * Decimal("100")


def evaluate_closed_episodes(episodes: Iterable[TradeEpisode]) -> dict[str, Any]:
    """Return post-cost actual performance without inferring missing outcomes."""
    closed = [episode for episode in episodes if episode.status == "CLOSED"]
    pnl = [episode.net_pnl for episode in closed]
    wins = [item for item in pnl if item > ZERO]
    losses = [item for item in pnl if item < ZERO]
    gross_profit = sum(wins, ZERO)
    gross_loss = abs(sum(losses, ZERO))
    cumulative = ZERO
    peak = ZERO
    max_drawdown = ZERO
    for item in pnl:
        cumulative += item
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return {
        "trades": len(closed),
        "net_pnl": str(sum(pnl, ZERO)),
        "commission": str(sum((episode.commission for episode in closed), ZERO)),
        "funding": str(sum((episode.funding for episode in closed), ZERO)),
        "profit_factor": str(gross_profit / gross_loss) if gross_loss > ZERO else None,
        "expectancy": str(sum(pnl, ZERO) / Decimal(len(pnl))) if pnl else None,
        "win_rate": str(Decimal(len(wins)) / Decimal(len(pnl))) if pnl else None,
        "avg_win": str(gross_profit / Decimal(len(wins))) if wins else None,
        "avg_loss": str(sum(losses, ZERO) / Decimal(len(losses))) if losses else None,
        "max_drawdown": str(max_drawdown),
    }


def grouped_losses(episodes: Iterable[TradeEpisode], key: str) -> list[dict[str, Any]]:
    """Aggregate closed realised loss causes by a canonical episode field."""
    groups: dict[str, list[Decimal]] = defaultdict(list)
    for episode in episodes:
        if episode.status != "CLOSED" or episode.net_pnl >= ZERO:
            continue
        value = episode.as_row().get(key) or "UNKNOWN"
        groups[str(value)].append(episode.net_pnl)
    rows = [
        {"cause": cause, "trades": len(values), "net_pnl": str(sum(values, ZERO))}
        for cause, values in groups.items()
    ]
    return sorted(rows, key=lambda row: Decimal(row["net_pnl"]))
