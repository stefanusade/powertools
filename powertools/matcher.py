"""Pencocokan teks (fuzzy), kalkulator, dan konversi kecil-kecilan."""

from __future__ import annotations

import ast
import math
import operator
import re

# --------------------------------------------------------------------- fuzzy

_WORD_SEP = set(" .-_/()[]{}+&,:;")


def fuzzy_score(query: str, text: str) -> float:
    """Skor kemiripan; -1 kalau tidak cocok sama sekali.

    Angka lebih besar = lebih relevan. Cukup murah untuk beberapa ratus
    kandidat dan dipanggil hanya saat user mengetik.
    """
    if not query:
        return 0.0
    q = query.lower()
    t = text.lower()
    if not t:
        return -1.0
    if q == t:
        return 200.0
    if t.startswith(q):
        return 150.0 - len(t) * 0.1

    idx = t.find(q)
    if idx >= 0:
        score = 120.0 - idx
        if idx > 0 and t[idx - 1] in _WORD_SEP:
            score += 12.0
        return score - len(t) * 0.05

    # Cocok sebagai subsequence (huruf muncul berurutan, tidak harus berdempet).
    pos = 0
    prev = -2
    score = 0.0
    for ch in q:
        found = t.find(ch, pos)
        if found < 0:
            return -1.0
        if found == prev + 1:
            score += 6.0
        if found == 0 or t[found - 1] in _WORD_SEP:
            score += 8.0
        score += 2.0
        prev = found
        pos = found + 1
    return score * 10.0 / max(len(t), 1)


def match_tokens(query: str, haystack: str) -> bool:
    """Semua kata pada query harus ada (dipakai untuk pencarian clipboard)."""
    words = query.lower().split()
    if not words:
        return True
    hay = haystack.lower()
    return all(w in hay for w in words)


# ---------------------------------------------------------------- calculator

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_FUNCS = {
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "log": math.log10,
    "ln": math.log,
    "log2": math.log2,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
    "exp": math.exp,
}

_CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau}

_MATH_CHARS = re.compile(r"^[0-9a-zA-Z_+\-*/%^().,\s]+$")
_PERCENT_RE = re.compile(
    r"^\s*([0-9]+(?:[.,][0-9]+)?)\s*%\s*(?:of|dari|\*)\s*([0-9]+(?:[.,][0-9]+)?)\s*$",
    re.IGNORECASE,
)


def _to_float(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("boolean bukan angka")
    if isinstance(value, (int, float)):
        return float(value)
    raise ValueError("bukan angka")


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        return _to_float(node.value)
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            raise ValueError("operator tidak didukung")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (abs(right) > 1000 or abs(left) > 1e9):
            raise ValueError("angka terlalu besar")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ValueError("pembagian dengan nol")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_UNARY.get(type(node.op))
        if op is None:
            raise ValueError("operator tidak didukung")
        return op(_eval_node(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _CONSTS:
            return _CONSTS[node.id]
        raise ValueError("nama tidak dikenal")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
            raise ValueError("fungsi tidak didukung")
        if node.keywords:
            raise ValueError("keyword tidak didukung")
        args = [_eval_node(arg) for arg in node.args]
        if len(args) != 1:
            raise ValueError("butuh satu argumen")
        return float(_FUNCS[node.func.id](args[0]))
    raise ValueError("ekspresi tidak didukung")


def format_number(value: float) -> str:
    if not math.isfinite(value):
        return str(value)
    if abs(value - round(value)) < 1e-10 and abs(value) < 1e15:
        return str(int(round(value)))
    text = f"{value:.10g}"
    return text.replace("e+", "e")


def _normalize_expression(expr: str) -> str:
    expr = expr.strip().rstrip("=").strip()
    expr = expr.replace("×", "*").replace("÷", "/").replace("−", "-")
    expr = expr.replace("^", "**")
    return expr


def percent_of(query: str) -> str | None:
    match = _PERCENT_RE.match(query)
    if not match:
        return None
    percent = float(match.group(1).replace(",", "."))
    total = float(match.group(2).replace(",", "."))
    return format_number(percent / 100.0 * total)


def looks_like_math(query: str) -> bool:
    expr = _normalize_expression(query)
    if not expr or len(expr) > 200:
        return False
    if not _MATH_CHARS.match(expr):
        return False
    if not any(ch.isdigit() for ch in expr):
        return False
    return any(ch in expr for ch in "+-*/%^") or bool(re.search(r"[a-zA-Z]+\s*\(", expr))


def evaluate(query: str) -> str | None:
    """Hitung ekspresi matematis. Return None kalau bukan ekspresi valid."""
    expr = _normalize_expression(query)
    if not expr or len(expr) > 200:
        return None
    if not _MATH_CHARS.match(expr):
        return None
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        return None
    try:
        value = _eval_node(tree)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return None
    if not math.isfinite(value) or abs(value) > 1e15:
        return None
    return format_number(value)


def calculate(query: str) -> str | None:
    """Kalkulator lengkap: persen, lalu ekspresi biasa."""
    percent = percent_of(query)
    if percent is not None:
        return percent
    if not looks_like_math(query):
        return None
    return evaluate(query)