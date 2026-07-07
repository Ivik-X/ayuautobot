from __future__ import annotations

import ast
import operator

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARYOPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_MAX_POWER_EXPONENT = 1000


class CalcError(Exception):
    pass


def _eval_node(node: ast.AST):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise CalcError("разрешены только числа")
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_BINOPS:
            raise CalcError("операция не поддерживается")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if op_type is ast.Pow and (abs(right) > _MAX_POWER_EXPONENT):
            raise CalcError("слишком большая степень")
        try:
            return _ALLOWED_BINOPS[op_type](left, right)
        except ZeroDivisionError as exc:
            raise CalcError("деление на ноль") from exc
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _ALLOWED_UNARYOPS:
            raise CalcError("операция не поддерживается")
        return _ALLOWED_UNARYOPS[op_type](_eval_node(node.operand))
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    raise CalcError("выражение не поддерживается")


def calculate(expression: str) -> float:
    expression = expression.strip()
    if len(expression) > 200:
        raise CalcError("слишком длинное выражение")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalcError("не удалось разобрать выражение") from exc
    return _eval_node(tree)
