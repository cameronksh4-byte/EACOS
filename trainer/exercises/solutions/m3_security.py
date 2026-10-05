"""Reference solutions for trainer/exercises/m3_security.py. Compare with auditgate/security/."""

from __future__ import annotations

import ast
import re
from urllib.parse import urlparse

from trainer.exercises.m3_security import INVISIBLE_CHARS, SAFE_FUNCTIONS


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if len(digits) != len(number.replace(" ", "").replace("-", "")) or not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def looks_like_injection(text: str) -> bool:
    for ch in INVISIBLE_CHARS:
        text = text.replace(ch, "")
    patterns = [r"ignore.*instructions", r"pre-?approved", r"you are now"]
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def disallowed_domains(text: str, allowed: set[str]) -> list[str]:
    bad = []
    for link in re.findall(r"https?://[^\s]+", text):
        host = (urlparse(link).hostname or "").lower().removeprefix("www.")
        if not any(host == a or host.endswith("." + a) for a in allowed):
            bad.append(host)
    return bad


_ALLOWED = (ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Name, ast.Load, ast.Constant,
            ast.Call, ast.And, ast.Or, ast.Not, ast.USub, ast.Add, ast.Sub, ast.Mult, ast.Div,
            ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def is_safe_expression(code: str) -> bool:
    try:
        tree = ast.parse(code, mode="eval")
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            return False
        if isinstance(node, ast.Name) and node.id.startswith("_"):
            return False
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in SAFE_FUNCTIONS):
            return False
    return True
