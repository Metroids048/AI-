"""Evaluate supported WorldQuant-style expressions over crypto-native data."""

from __future__ import annotations

import ast
from collections.abc import Callable

import pandas as pd

from shared.models import AlphaPlan

from . import operators as op
from .expression_parser import GROUP_ALIAS_MAP, parse_alpha_expression


class UnsupportedAlphaExpression(ValueError):
    """Raised when an expression cannot be executed on crypto-native inputs."""


OPERATOR_FUNCTIONS: dict[str, Callable] = {
    "rank": op.rank,
    "ts_delta": op.ts_delta,
    "ts_mean": op.ts_mean,
    "ts_std": op.ts_std,
    "ts_rank": op.ts_rank,
    "ts_zscore": op.ts_zscore,
    "delay": op.delay,
    "correlation": op.correlation,
    "group_rank": op.group_rank,
    "group_neutralize": op.group_neutralize,
    "scale": op.scale,
    "decay_linear": op.decay_linear,
}


def evaluate_alpha_expression(expr: str, frame: pd.DataFrame) -> pd.Series:
    """Evaluate `expr` against an OHLCV-like frame and return a signal series."""
    plan = parse_alpha_expression(expr)
    return evaluate_alpha_plan(plan, frame)


def evaluate_alpha_plan(plan: AlphaPlan, frame: pd.DataFrame) -> pd.Series:
    """Evaluate a parsed AlphaPlan over an OHLCV-like frame."""
    if not plan.evaluable:
        raise UnsupportedAlphaExpression(_unsupported_message(plan))
    context = build_evaluation_context(frame)
    tree = ast.parse(plan.raw_expression, mode="eval")
    result = _evaluate_node(tree.body, context)
    if isinstance(result, (int, float)):
        result = pd.Series(float(result), index=frame.index, dtype="float64")
    if not isinstance(result, pd.Series):
        raise UnsupportedAlphaExpression("alpha evaluator produced a non-series result")
    sanitized = result.replace([pd.NA, pd.NaT, float("inf"), float("-inf")], 0.0)
    sanitized = sanitized.astype("float64")
    return sanitized.fillna(0.0)


def build_evaluation_context(frame: pd.DataFrame) -> dict[str, pd.Series]:
    """Build the crypto-native execution context for supported alpha inputs."""
    if frame.empty:
        raise UnsupportedAlphaExpression("alpha evaluator requires at least one row of market data")
    close = _series_from_frame(frame, "close")
    high = _series_from_frame(frame, "high")
    low = _series_from_frame(frame, "low")
    volume = _series_from_frame(frame, "volume")
    context = {
        "open": _series_from_frame(frame, "open"),
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "vwap": _series_from_frame(frame, "vwap") if "vwap" in frame.columns else ((high + low + close) / 3.0),
        "returns": close.pct_change().fillna(0.0),
        "adv20": volume.rolling(window=20, min_periods=1).mean(),
        "funding_rate": _optional_series(frame, "funding_rate"),
        "open_interest": _optional_series(frame, "open_interest"),
        "long_ratio": _optional_series(frame, "long_ratio"),
        "short_ratio": _optional_series(frame, "short_ratio"),
        "liquidation_usd": _optional_series(frame, "liquidation_usd"),
    }
    context["market"] = pd.Series("market", index=frame.index, dtype="object")
    context["volatility_regime"] = _bucketize(context["returns"].rolling(window=20, min_periods=5).std(), "vol")
    context["funding_regime"] = _funding_regime(context["funding_rate"])
    context["liquidity_regime"] = _bucketize(context["adv20"], "liq")
    return context


def _evaluate_node(node: ast.AST, context: dict[str, pd.Series]) -> pd.Series | float:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int) and not isinstance(node.value, bool):
            return int(node.value)
        if isinstance(node.value, float):
            return float(node.value)
        raise UnsupportedAlphaExpression(f"unsupported constant type: {type(node.value).__name__}")
    if isinstance(node, ast.Name):
        identifier = node.id.lower()
        if identifier in context:
            return context[identifier]
        if identifier in GROUP_ALIAS_MAP:
            mapped = GROUP_ALIAS_MAP[identifier]
            return context[mapped]
        raise UnsupportedAlphaExpression(f"unsupported input field: {identifier}")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = _evaluate_node(node.operand, context)
        return -operand
    if isinstance(node, ast.BinOp):
        left = _evaluate_node(node.left, context)
        right = _evaluate_node(node.right, context)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        raise UnsupportedAlphaExpression(f"unsupported arithmetic operator: {type(node.op).__name__}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise UnsupportedAlphaExpression("unsupported callable expression")
        if node.keywords:
            raise UnsupportedAlphaExpression("keyword arguments are not supported")
        function_name = node.func.id.lower()
        function = OPERATOR_FUNCTIONS.get(function_name)
        if function is None:
            raise UnsupportedAlphaExpression(f"unsupported operator: {function_name}")
        args = [_evaluate_node(arg, context) for arg in node.args]
        return function(*args)
    raise UnsupportedAlphaExpression(f"unsupported AST node: {type(node).__name__}")


def _unsupported_message(plan: AlphaPlan) -> str:
    parts: list[str] = []
    if plan.unsupported_operators:
        parts.append(f"operators={','.join(plan.unsupported_operators)}")
    if plan.unsupported_inputs:
        parts.append(f"inputs={','.join(plan.unsupported_inputs)}")
    if not parts:
        parts.append("expression is not evaluable on crypto-native inputs")
    return "; ".join(parts)


def _series_from_frame(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        raise UnsupportedAlphaExpression(f"required OHLCV column missing: {column}")
    return frame[column].astype("float64")


def _optional_series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column in frame.columns:
        return frame[column].astype("float64").fillna(0.0)
    return pd.Series(0.0, index=frame.index, dtype="float64")


def _bucketize(series: pd.Series, prefix: str) -> pd.Series:
    filled = series.bfill().ffill().fillna(0.0)
    ranked = filled.rank(method="average", pct=True)
    categories = pd.Series("mid", index=filled.index, dtype="object")
    categories[ranked <= 0.33] = f"{prefix}_low"
    categories[ranked >= 0.67] = f"{prefix}_high"
    return categories


def _funding_regime(series: pd.Series) -> pd.Series:
    categories = pd.Series("funding_neutral", index=series.index, dtype="object")
    categories[series > 0.0001] = "funding_positive"
    categories[series < -0.0001] = "funding_negative"
    return categories
