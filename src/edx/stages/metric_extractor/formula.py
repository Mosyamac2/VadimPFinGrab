"""Safe AST-based evaluator for metric formulas (e.g. EBITDA derivations).

Operators allowed: ``+ - * /``. Operands: numbers, identifiers (looked up in
the supplied values dict). Any other syntax is rejected at parse time so the
operator can author formulas in ``metrics.yaml`` without opening an
``eval``-shaped foot-gun.
"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from typing import Final

_BIN_OPS: Final = (ast.Add, ast.Sub, ast.Mult, ast.Div)
_UNARY_OPS: Final = (ast.USub, ast.UAdd)


def safe_formula_eval(
    expression: str, values: Mapping[str, float | None]
) -> float | None:
    """Evaluate ``expression`` against ``values``. Returns None if any operand
    is missing.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"invalid formula: {expression!r}") from exc
    return _eval(tree.body, values)


def _eval(
    node: ast.AST, values: Mapping[str, float | None]
) -> float | None:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, int | float):
            return float(node.value)
        raise ValueError(f"unsupported literal: {node.value!r}")
    if isinstance(node, ast.Name):
        if node.id not in values:
            return None
        v = values[node.id]
        return None if v is None else float(v)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, _UNARY_OPS):
        operand = _eval(node.operand, values)
        if operand is None:
            return None
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.BinOp) and isinstance(node.op, _BIN_OPS):
        left = _eval(node.left, values)
        right = _eval(node.right, values)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                return None
            return left / right
    raise ValueError(f"unsupported expression node: {type(node).__name__}")
