"""Module 6 - Deterministic evaluation metrics, by hand.

Every metric here is a precise calculation you can explain to a client and gate a
release on - no "rate this answer 1-10".

    uv run python -m trainer.cli check M6

Then read auditgate/evals/metrics.py, agent_runner.py, synth.py and cluster.py.
"""

from __future__ import annotations

import math  # noqa: F401 - exercises 3 and 5
from collections.abc import Iterable, Sequence
from decimal import Decimal, InvalidOperation  # noqa: F401 - exercise 1

# --------------------------------------------------------------------------
# Exercise 1 - exact match
# --------------------------------------------------------------------------


def exact_match(expected: object, actual: object) -> bool:
    """True if expected and actual are "the same answer":

    - if either is None: True only if both are None
    - if both can be read as numbers (Decimal(str(x)) works): compare as Decimals,
      so "90", "90.0" and 90 all match
    - otherwise compare as text, ignoring case and extra whitespace:
      "Robert  Chen" matches "robert chen"
    """
    raise NotImplementedError("Exercise 1: exact_match")


# --------------------------------------------------------------------------
# Exercise 2 - tool-selection precision and recall
# --------------------------------------------------------------------------


def precision_recall(called: Iterable[str], required: Iterable[str],
                     optional: Iterable[str] = ()) -> tuple[float, float]:
    """Score which tools an agent chose (duplicates don't count twice - use sets).

    precision = (# called tools that are required OR optional) / (# called tools)
    recall    = (# required tools that were called) / (# required tools)

    If nothing was called, precision is 1.0. If nothing was required, recall is 1.0.
    """
    raise NotImplementedError("Exercise 2: precision_recall")


# --------------------------------------------------------------------------
# Exercise 3 - Pass@k
# --------------------------------------------------------------------------


def pass_at_k(n: int, c: int, k: int) -> float:
    """Probability that at least one of k attempts succeeds, given n attempts of which c succeeded.

        pass@k = 1 - C(n - c, k) / C(n, k)        where C is "n choose k": math.comb

    Special case: if n - c < k, every possible group of k includes a success -> return 1.0.
    Raise ValueError unless 0 <= c <= n and 1 <= k <= n.
    """
    raise NotImplementedError("Exercise 3: pass_at_k")


# --------------------------------------------------------------------------
# Exercise 4 - catch regressions between two eval runs
# --------------------------------------------------------------------------


def regressions(before: dict[str, float], after: dict[str, float], tolerance: float = 0.05,
                lower_is_better: frozenset[str] = frozenset()) -> list[str]:
    """Names of metrics (present in BOTH runs) that got worse by MORE than `tolerance`, sorted.

    For most metrics higher is better (worse = before - after).
    For metrics in lower_is_better, worse = after - before.
    """
    raise NotImplementedError("Exercise 4: regressions")


# --------------------------------------------------------------------------
# Exercise 5 - the heart of clustering
# --------------------------------------------------------------------------


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity: dot(a, b) / (|a| * |b|). Return 0.0 if either vector is all zeros."""
    raise NotImplementedError("Exercise 5a: cosine")


def nearest(vector: Sequence[float], centroids: Sequence[Sequence[float]]) -> int:
    """Index of the centroid most similar (highest cosine) to `vector`. Ties go to the lower index.

    This is the "assignment" step of k-means: repeat it for every failure, recompute the
    centroids, and you have clustering.
    """
    raise NotImplementedError("Exercise 5b: nearest")
