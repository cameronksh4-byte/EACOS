"""Checkpoint tests for Module 0. Run with:  uv run python -m trainer.cli check M0

These grade YOUR file (trainer/exercises/m0_basics.py). The main test suite runs
the same tests against the reference solutions by setting M0_TARGET.
"""

from __future__ import annotations

import importlib
import os

import pytest

ex = importlib.import_module(os.environ.get("M0_TARGET", "trainer.exercises.m0_basics"))


def test_m0_ex01_format_money():
    assert ex.format_money(1250) == "$1,250.00"
    assert ex.format_money(3.5) == "$3.50"
    assert ex.format_money(0) == "$0.00"
    assert ex.format_money(1234567.891) == "$1,234,567.89"


def test_m0_ex02_parse_amount():
    assert ex.parse_amount("$1,250.00") == 1250.0
    assert ex.parse_amount("  40 ") == 40.0
    assert ex.parse_amount("$0.99") == 0.99


def test_m0_ex03_line_total():
    assert ex.line_total(3, 19.99) == 59.97
    assert ex.line_total(1, 100) == 100
    assert ex.line_total(2, 0.1) == 0.2


def test_m0_ex04_invoice_total():
    items = [{"description": "Labor", "quantity": 4, "unit_price": 95.0},
             {"description": "Valve", "quantity": 2, "unit_price": 12.5}]
    assert ex.invoice_total(items) == 405.0
    assert ex.invoice_total([]) == 0.0
    assert ex.invoice_total([{"description": "x", "quantity": 3, "unit_price": 0.1}]) == 0.3


def test_m0_ex05_count_document_types():
    assert ex.count_document_types(["invoice", "bid", "invoice"]) == {"invoice": 2, "bid": 1}
    assert ex.count_document_types([]) == {}


def test_m0_ex06_normalize_vendor():
    assert ex.normalize_vendor("  ridgeline    PLUMBING ") == "Ridgeline Plumbing"
    assert ex.normalize_vendor("acme") == "Acme"


def test_m0_ex07_overdue_invoices():
    invoices = [{"number": "A-1", "due_date": "2024-03-01", "paid": False},
                {"number": "A-2", "due_date": "2024-03-01", "paid": True},
                {"number": "A-3", "due_date": "2024-04-01", "paid": False},
                {"number": "A-4", "due_date": "2024-02-01", "paid": False}]
    assert ex.overdue_invoices(invoices, today="2024-03-15") == ["A-1", "A-4"]
    assert ex.overdue_invoices(invoices, today="2024-03-01") == ["A-4"]  # due today is not overdue yet
    assert ex.overdue_invoices([], today="2024-03-15") == []


def test_m0_ex08_group_by_vendor():
    invoices = [{"vendor": "Acme", "number": "1"}, {"vendor": "Bolt", "number": "2"},
                {"vendor": "Acme", "number": "3"}]
    assert ex.group_by_vendor(invoices) == {"Acme": ["1", "3"], "Bolt": ["2"]}
    assert ex.group_by_vendor([]) == {}


def test_m0_ex09_largest_invoice():
    a, b = {"number": "1", "total": 50.0}, {"number": "2", "total": 75.0}
    assert ex.largest_invoice([a, b]) == b
    assert ex.largest_invoice([b, a]) == b
    assert ex.largest_invoice([]) is None


def test_m0_ex10_collect_tokens_has_no_shared_state():
    assert ex.collect_tokens("EMAIL_1") == ["EMAIL_1"]
    assert ex.collect_tokens("PHONE_1") == ["PHONE_1"], "calls are sharing one list - see assessment question PY2"
    mine = ["A"]
    assert ex.collect_tokens("B", mine) == ["A", "B"]
    assert mine == ["A", "B"]
