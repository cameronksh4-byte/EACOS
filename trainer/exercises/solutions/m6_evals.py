"""Reference solutions for trainer/exercises/m6_evals.py. Compare with auditgate/evals/metrics.py."""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation


def exact_match(expected: object, actual: object) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    try:
        return Decimal(str(expected)) == Decimal(str(actual))
    except InvalidOperation:
        return " ".join(str(expected).lower().split()) == " ".join(str(actual).lower().split())


def precision_recall(called: Iterable[str], required: Iterable[str],
                     optional: Iterable[str] = ()) -> tuple[float, float]:
    called, required = set(called), set(required)
    reasonable = required | set(optional)
    precision = len(called & reasonable) / len(called) if called else 1.0
    recall = len(called & required) / len(required) if required else 1.0
    return precision, recall


def pass_at_k(n: int, c: int, k: int) -> float:
    if not (0 <= c <= n and 1 <= k <= n):
        raise ValueError("need 0 <= c <= n and 1 <= k <= n")
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def regressions(before: dict[str, float], after: dict[str, float], tolerance: float = 0.05,
                lower_is_better: frozenset[str] = frozenset()) -> list[str]:
    worse = []
    for name in before.keys() & after.keys():
        delta = after[name] - before[name] if name in lower_is_better else before[name] - after[name]
        if delta > tolerance:
            worse.append(name)
    return sorted(worse)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def nearest(vector: Sequence[float], centroids: Sequence[Sequence[float]]) -> int:
    best, best_score = 0, float("-inf")
    for i, c in enumerate(centroids):
        score = cosine(vector, c)
        if score > best_score:
            best, best_score = i, score
    return best
