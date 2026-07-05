"""Parse a WorldQuant-style alpha expression into a structured AlphaPlan."""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable

from shared.models import AlphaOperator, AlphaPlan

GROUP_ALIAS_MAP = {
    "industry": "volatility_regime",
    "sector": "funding_regime",
    "subindustry": "liquidity_regime",
    "market": "market",
}

SUPPORTED_INPUTS = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "returns",
    "adv20",
    "funding_rate",
    "open_interest",
    "long_ratio",
    "short_ratio",
    "liquidation_usd",
}

SUPPORTED_OPERATORS = {operator.value: operator for operator in AlphaOperator}
WINDOWED_OPERATORS = {
    "delay",
    "ts_delta",
    "ts_mean",
    "ts_std",
    "ts_rank",
    "ts_zscore",
    "correlation",
    "decay_linear",
}
GROUP_OPERATORS = {"group_rank", "group_neutralize"}
TOKEN_RE = re.compile(r"\b[a-zA-Z_][a-zA-Z0-9_]*\b")
SUPPORTED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


class _PlanVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.operators: list[AlphaOperator] = []
        self.inputs: list[str] = []
        self.windows: list[int] = []
        self.group_aliases: dict[str, str] = {}
        self.unsupported_inputs: list[str] = []
        self.unsupported_operators: list[str] = []
        self.unsupported_syntax: list[str] = []
        self._known_functions: list[str] = []
        self._group_identifiers: set[str] = set()

    def visit_Call(self, node: ast.Call) -> None:
        func_name = self._function_name(node)
        if func_name is None:
            self.unsupported_operators.append("<dynamic_call>")
        else:
            self._known_functions.append(func_name)
            if func_name in SUPPORTED_OPERATORS:
                self.operators.append(SUPPORTED_OPERATORS[func_name])
            else:
                self.unsupported_operators.append(func_name)
            if func_name in WINDOWED_OPERATORS:
                for value in self._window_values(node.args[1:]):
                    self.windows.append(value)
            if func_name in GROUP_OPERATORS and len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
                raw_group = node.args[1].id.lower()
                self._group_identifiers.add(raw_group)
                mapped_group = GROUP_ALIAS_MAP.get(raw_group)
                if mapped_group is None:
                    self.unsupported_inputs.append(raw_group)
                else:
                    self.group_aliases[raw_group] = mapped_group
        for arg in node.args:
            self.visit(arg)
        for keyword in node.keywords:
            self.unsupported_syntax.append(f"keyword:{keyword.arg or '<dynamic>'}")

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if not isinstance(node.op, SUPPORTED_BINOPS):
            self.unsupported_syntax.append(type(node.op).__name__.lower())
        self.visit(node.left)
        self.visit(node.right)

    def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
        if not isinstance(node.op, ast.USub):
            self.unsupported_syntax.append(type(node.op).__name__.lower())
        self.visit(node.operand)

    def visit_Compare(self, node: ast.Compare) -> None:
        self.unsupported_syntax.append("compare")
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.unsupported_syntax.append(type(node.op).__name__.lower())
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.unsupported_syntax.append("ifexp")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        self.unsupported_syntax.append("attribute")
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        self.unsupported_syntax.append("subscript")
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        identifier = node.id.lower()
        if identifier in self._known_functions or identifier in self._group_identifiers:
            return
        self.inputs.append(identifier)
        if identifier not in SUPPORTED_INPUTS:
            self.unsupported_inputs.append(identifier)

    @staticmethod
    def _function_name(node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id.lower()
        return None

    @staticmethod
    def _window_values(nodes: Iterable[ast.AST]) -> list[int]:
        windows: list[int] = []
        for node in nodes:
            if isinstance(node, ast.Constant) and isinstance(node.value, int):
                windows.append(int(node.value))
        return windows


def parse_alpha_expression(expr: str) -> AlphaPlan:
    """Parse `expr` into an AlphaPlan with support metadata for crypto execution."""
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        return AlphaPlan(
            raw_expression=expr,
            evaluable=False,
            unsupported_operators=["<parse_error>"],
            parameters={"parse_error": str(exc)},
            behavior_signature=_behavior_signature(expr, {}),
        )

    visitor = _PlanVisitor()
    visitor.visit(tree.body)
    operators = _unique(visitor.operators)
    inputs = _unique(visitor.inputs)
    windows = _unique(visitor.windows)
    unsupported_inputs = _unique(visitor.unsupported_inputs)
    unsupported_operators = _unique([*visitor.unsupported_operators, *visitor.unsupported_syntax])
    supported_inputs = [item for item in inputs if item in SUPPORTED_INPUTS]
    return AlphaPlan(
        raw_expression=expr,
        operators=operators,
        inputs=inputs,
        windows=windows,
        parameters={"windows": windows, "max_window": max(windows) if windows else 0},
        group_aliases=visitor.group_aliases,
        behavior_signature=_behavior_signature(expr, visitor.group_aliases),
        supported_inputs=supported_inputs,
        unsupported_inputs=unsupported_inputs,
        unsupported_operators=unsupported_operators,
        evaluable=not unsupported_inputs and not unsupported_operators,
    )


def _unique(values: Iterable) -> list:
    return list(dict.fromkeys(values))


def _behavior_signature(expr: str, group_aliases: dict[str, str]) -> str:
    normalized_expr = _normalize_groups(expr, group_aliases)
    return f"{_field_signature(normalized_expr, max_fields=8)}::{_behavior_operator_skeleton(normalized_expr)}"


def _extract_expression_fields(expr: str) -> list[str]:
    fields: list[str] = []
    for token in TOKEN_RE.findall(expr.lower()):
        if token in SUPPORTED_OPERATORS or token in GROUP_ALIAS_MAP:
            continue
        fields.append(token)
    return _unique(fields)


def _normalize_groups(expr: str, group_aliases: dict[str, str]) -> str:
    normalized = expr.lower()
    for raw_group, mapped_group in group_aliases.items():
        normalized = re.sub(rf"\b{re.escape(raw_group)}\b", mapped_group, normalized)
    return normalized


def _sig(expr: str) -> str:
    return re.sub(r"\s+", " ", str(expr or "").strip())


def _strip_outer_parens(expr: str) -> str:
    text = str(expr or "").strip()
    while text.startswith("(") and text.endswith(")"):
        depth = 0
        balanced = True
        for index, char in enumerate(text):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            if depth == 0 and index < len(text) - 1:
                balanced = False
                break
        if not balanced:
            break
        text = text[1:-1].strip()
    return text


def _field_signature(expr: str, max_fields: int = 4) -> str:
    fields = sorted(_extract_expression_fields(expr))[:max_fields]
    return "|".join(fields) if fields else "-"


def _shape_key(expr: str) -> str:
    normalized = _sig(expr).lower()
    normalized = re.sub(r"\b\d+(?:\.\d+)?\b", "#", normalized)
    reserved = set(SUPPORTED_OPERATORS) | set(SUPPORTED_INPUTS) | set(GROUP_ALIAS_MAP.values())

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if token in reserved:
            return token
        return "f"

    return TOKEN_RE.sub(_replace, normalized)


def _operator_skeleton(expr: str) -> str:
    normalized = _shape_key(expr)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"#+(?:\.#*)?", "#", normalized)
    return normalized


def _behavior_operator_skeleton(expr: str) -> str:
    normalized = _strip_outer_parens(_operator_skeleton(expr))
    for _ in range(3):
        if normalized.startswith("-(") and normalized.endswith(")"):
            normalized = _strip_outer_parens(normalized[1:])
        elif normalized.startswith("-"):
            normalized = normalized[1:]
        else:
            break
        normalized = _strip_outer_parens(normalized)
    normalized = re.sub(r"(?<=\))[-+]#", "", normalized)
    normalized = re.sub(r"(?<=\w)[-+]#", "", normalized)
    normalized = re.sub(r"\*-?#", "", normalized)
    normalized = re.sub(r"-rank\(", "rank(", normalized)
    normalized = re.sub(r"\+-", "+", normalized)
    normalized = re.sub(r"--", "", normalized)
    return _strip_outer_parens(normalized)
