"""
Safe mathematical calculation tool.
Evaluates arithmetic expressions and common math functions without arbitrary code execution.
"""
from __future__ import annotations

import ast
import logging
import math
import operator
from typing import Any

from bot.llm.tools._types import ToolEntry

# Safe operators
_SAFE_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Safe math functions
_SAFE_FUNCTIONS = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "sqrt": math.sqrt,
    "cbrt": lambda x: x ** (1 / 3),
    "log": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "exp": math.exp,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
    "factorial": math.factorial,
    "radians": math.radians,
    "degrees": math.degrees,
    "pi": math.pi,
    "e": math.e,
}


def _safe_eval_node(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError(f"Desteklenmeyen sabit: {node.value}")
    elif isinstance(node, ast.BinOp):
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        op_type = type(node.op)
        if op_type in _SAFE_OPERATORS:
            if op_type == ast.Pow and (isinstance(right, (int, float)) and right > 1000):
                raise ValueError("Kuvvet üssü çok büyük (max 1000)")
            return _SAFE_OPERATORS[op_type](left, right)
        raise ValueError(f"Desteklenmeyen işlem: {op_type.__name__}")
    elif isinstance(node, ast.UnaryOp):
        operand = _safe_eval_node(node.operand)
        op_type = type(node.op)
        if op_type in _SAFE_OPERATORS:
            return _SAFE_OPERATORS[op_type](operand)
        raise ValueError(f"Desteklenmeyen tekli işlem: {op_type.__name__}")
    elif isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Yalnızca basit fonksiyon çağrıları desteklenir")
        func_name = node.func.id
        if func_name not in _SAFE_FUNCTIONS:
            raise ValueError(f"'{func_name}' fonksiyonuna izin verilmiyor")
        args = [_safe_eval_node(arg) for arg in node.args]
        return _SAFE_FUNCTIONS[func_name](*args)
    elif isinstance(node, ast.Name):
        if node.id in _SAFE_FUNCTIONS:
            val = _SAFE_FUNCTIONS[node.id]
            if isinstance(val, (int, float)):
                return val
        raise ValueError(f"'{node.id}' değişkeni tanımlı değil")
    else:
        raise ValueError(f"Desteklenmeyen ifade: {type(node).__name__}")


def calculate(expression: str) -> str:
    """Safely evaluate a mathematical expression."""
    clean_expr = expression.strip().replace("^", "**").replace("×", "*").replace("÷", "/")
    try:
        parsed = ast.parse(clean_expr, mode="eval")
        result = _safe_eval_node(parsed.body)
        if isinstance(result, float) and result.is_integer():
            result = int(result)
        return f"{expression} = {result}"
    except Exception as e:
        return f"Hesaplama hatası ({expression}): {e}"


CALCULATE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": "Evaluate mathematical expressions accurately. Supports basic arithmetic (+, -, *, /, //, %, **), powers, roots, logarithms, trigonometry (sin, cos, tan), abs, and round.",
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The math expression to evaluate, e.g. '150 * 1.2', 'sqrt(144) + 10', or '(45000 / 12) * 0.8'",
                }
            },
            "required": ["expression"],
        },
    },
}

TOOL_NAME = "calculate"
TOOL_ENTRY = ToolEntry(
    schema=CALCULATE_SCHEMA,
    fn=calculate,
)
