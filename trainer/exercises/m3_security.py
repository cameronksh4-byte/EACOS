"""Module 3 - Security primitives, built by hand.

Four small functions that protect a real system. Each mirrors production code in
auditgate/security/ - read that AFTER your tests pass.

    uv run python -m trainer.cli check M3
"""

from __future__ import annotations

import ast  # noqa: F401 - you'll need it in exercise 4
import re  # noqa: F401 - you'll need it in exercises 2 and 3
from urllib.parse import urlparse  # noqa: F401 - you'll need it in exercise 3

# --------------------------------------------------------------------------
# Exercise 1 - tell real card numbers from invoice numbers
# --------------------------------------------------------------------------


def luhn_valid(number: str) -> bool:
    """Return True if `number` passes the Luhn checksum. Ignore spaces and dashes.

    Only 13-19 digit numbers can be cards; anything else is False.

    Algorithm: starting from the RIGHTMOST digit, double every second digit
    (the 2nd, 4th, ... from the right). If doubling gives more than 9, subtract 9.
    Add all the digits. The number is valid if the total is divisible by 10.

    >>> luhn_valid("4111 1111 1111 1111")
    True
    >>> luhn_valid("4111 1111 1111 1112")
    False
    """
    raise NotImplementedError("Exercise 1: luhn_valid")


# --------------------------------------------------------------------------
# Exercise 2 - spot instructions hidden in a document
# --------------------------------------------------------------------------

INVISIBLE_CHARS = "​‌‍⁠﻿"   # zero-width characters attackers use to hide text


def looks_like_injection(text: str) -> bool:
    """Return True if the text contains an attempt to instruct an AI.

    1. First REMOVE every character in INVISIBLE_CHARS (attackers split words with them:
       "ig​nore" looks like "ignore" to a human but not to a naive search).
    2. Then return True if, ignoring case, the text contains any of:
         - "ignore" followed later on the same line by "instructions"
         - "pre-approved" or "preapproved"
         - "you are now"
       Otherwise return False.

    Hint: re.search(r"ignore.*instructions", text, re.IGNORECASE) - `.` doesn't match newlines.
    """
    raise NotImplementedError("Exercise 2: looks_like_injection")


# --------------------------------------------------------------------------
# Exercise 3 - stop links to unknown sites leaving in an email
# --------------------------------------------------------------------------


def disallowed_domains(text: str, allowed: set[str]) -> list[str]:
    """Return the domains of every http(s) link in `text` that is NOT allowed, in order.

    A domain is allowed if it equals an allowed domain or is a subdomain of one
    ("billing.acme.com" is allowed when "acme.com" is). Ignore a leading "www.".

    >>> disallowed_domains("Pay at https://acme.com/pay or http://evil.example/x", {"acme.com"})
    ['evil.example']

    Hint: re.findall(r"https?://[^\\s]+", text) finds links; urlparse(link).hostname gives the domain.
    """
    raise NotImplementedError("Exercise 3: disallowed_domains")


# --------------------------------------------------------------------------
# Exercise 4 - allow only safe rule expressions
# --------------------------------------------------------------------------

SAFE_FUNCTIONS = {"min", "max", "abs", "round"}


def is_safe_expression(code: str) -> bool:
    """Return True only if `code` is a single expression built from:
    numbers/strings, variable names (not starting with "_"), + - * / comparisons,
    and/or/not, and calls to functions in SAFE_FUNCTIONS.

    Return False for anything else - imports, attribute access (x.y), other function
    calls, or code that doesn't parse.

    >>> is_safe_expression("total > 5000 and vendor != 'Acme'")
    True
    >>> is_safe_expression("__import__('os').system('rm -rf /')")
    False

    Hint: tree = ast.parse(code, mode="eval"); then loop over ast.walk(tree) and check
    isinstance(node, (...allowed node types...)). Catch SyntaxError -> False.
    Allowed node types: ast.Expression, ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare,
    ast.Name, ast.Load, ast.Constant, ast.Call, ast.And, ast.Or, ast.Not, ast.USub,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE.
    For ast.Call, also require node.func to be an ast.Name whose id is in SAFE_FUNCTIONS.
    """
    raise NotImplementedError("Exercise 4: is_safe_expression")
