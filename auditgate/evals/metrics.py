"""Deterministic evaluation metrics: same inputs, same score, every time.

No "rate this answer 1-10" judges here. Each metric is a precise, programmatic check
you can explain to a client and gate a release on.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation


def exact_match(expected: object, actual: object) -> bool:
    """Equal after normalisation: numbers compare as Decimals, strings case/space-insensitively."""
    if expected is None or actual is None:
        return expected is actual
    try:
        return Decimal(str(expected)) == Decimal(str(actual))
    except InvalidOperation:
        return " ".join(str(expected).lower().split()) == " ".join(str(actual).lower().split())


def precision_recall(called: Iterable[str], required: Iterable[str],
                     optional: Iterable[str] = ()) -> tuple[float, float]:
    """Tool-selection precision and recall.

    precision = share of the tools the agent called that were reasonable (required or optional)
    recall    = share of the required tools the agent actually called
    Empty denominators score 1.0 (nothing called = nothing wrong; nothing required = nothing missed).
    """
    called, required, ok = set(called), set(required), set(required) | set(optional)
    precision = len(called & ok) / len(called) if called else 1.0
    recall = len(called & required) / len(required) if required else 1.0
    return precision, recall


def pass_at_k(n: int, c: int, k: int) -> float:
    """Unbiased Pass@k: probability that at least one of k attempts (drawn from n, c correct) succeeds.

    pass@k = 1 - C(n - c, k) / C(n, k)    (Chen et al., 2021)
    """
    if not 0 <= c <= n or not 1 <= k <= n:
        raise ValueError("need 0 <= c <= n and 1 <= k <= n")
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def mean(values: Sequence[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 1.0


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
