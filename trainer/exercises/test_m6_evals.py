"""Checkpoint tests for Module 6's exercise. Run with:  uv run python -m trainer.cli check M6"""

from __future__ import annotations

import importlib
import math
import os

import pytest

ex = importlib.import_module(os.environ.get("M6_TARGET", "trainer.exercises.m6_evals"))


@pytest.mark.parametrize("expected, actual, match", [
    ("90", "90.00", True), (90, "90.0", True), ("90.00", "90.01", False),
    ("Robert  Chen", "robert chen", True), ("dispute", "escalate", False),
    (None, None, True), (None, "0", False), ("0", None, False),
])
def test_m6_ex1_exact_match(expected, actual, match):
    assert ex.exact_match(expected, actual) is match


def test_m6_ex2_precision_recall():
    assert ex.precision_recall(["line_total", "overcharge"], ["line_total", "overcharge"]) == (1.0, 1.0)
    p, r = ex.precision_recall(["line_total", "send_email", "line_total"], ["line_total", "overcharge"],
                               optional=["lookup_vendor_history"])
    assert (p, r) == (0.5, 0.5)
    assert ex.precision_recall(["lookup_vendor_history"], [], ["lookup_vendor_history"]) == (1.0, 1.0)
    assert ex.precision_recall([], ["line_total"]) == (1.0, 0.0)


def test_m6_ex3_pass_at_k():
    assert ex.pass_at_k(10, 0, 1) == 0.0
    assert ex.pass_at_k(10, 10, 5) == 1.0
    assert ex.pass_at_k(5, 2, 1) == pytest.approx(0.4)
    assert ex.pass_at_k(5, 2, 2) == pytest.approx(1 - math.comb(3, 2) / math.comb(5, 2))  # 0.7
    assert ex.pass_at_k(4, 2, 3) == 1.0  # only 2 failures, any 3 attempts include a success
    for bad in [(3, 4, 1), (3, 1, 0), (3, 1, 4)]:
        with pytest.raises(ValueError):
            ex.pass_at_k(*bad)


def test_m6_ex4_regressions():
    before = {"pass_at_1": 0.90, "tool_recall": 0.95, "forbidden_verdicts": 0.0, "avg_steps": 3.0, "old": 1.0}
    after = {"pass_at_1": 0.80, "tool_recall": 0.93, "forbidden_verdicts": 1.0, "avg_steps": 3.04, "new": 0.0}
    assert ex.regressions(before, after, lower_is_better=frozenset({"forbidden_verdicts", "avg_steps"})) == [
        "forbidden_verdicts", "pass_at_1"]
    assert ex.regressions(before, before) == []


def test_m6_ex5_cosine_and_nearest():
    assert ex.cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert ex.cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert ex.cosine([1, 1], [2, 2]) == pytest.approx(1.0)  # direction, not length
    assert ex.cosine([0, 0], [1, 1]) == 0.0
    centroids = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    assert ex.nearest([0.1, 0.9, 0.2], centroids) == 1
    assert ex.nearest([1, 1, 0], centroids) == 0  # tie -> lower index
