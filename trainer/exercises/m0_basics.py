"""Module 0 - Python Basics, the invoice way.

HOW TO WORK THROUGH THIS FILE
-----------------------------
1. Read an exercise's docstring. The ">>>" lines show what it should return.
2. Delete the `raise NotImplementedError(...)` line and write your code.
3. Check your progress any time:

       uv run python -m trainer.cli check M0

   Every exercise you finish turns a test from red to green.
4. Stuck for more than 20 minutes? Re-read the hint, try the idea in
   `uv run python` (the interactive shell), and only then peek at
   trainer/exercises/solutions/m0_basics.py.

The exercises get harder as you go. Each one uses a Python skill that
AuditGate relies on, and the comment above each says which.
"""

from __future__ import annotations

# --------------------------------------------------------------------------
# Exercise 1 - f-strings and number formatting
# --------------------------------------------------------------------------


def format_money(amount: float) -> str:
    """Format a number as US dollars with thousands separators and 2 decimals.

    >>> format_money(1250)
    '$1,250.00'
    >>> format_money(3.5)
    '$3.50'

    Hint: f"{value:,.2f}" formats a number with commas and 2 decimal places.
    """
    raise NotImplementedError("Exercise 1: format_money")


# --------------------------------------------------------------------------
# Exercise 2 - string methods
# --------------------------------------------------------------------------


def parse_amount(text: str) -> float:
    """Turn money text from a document into a number.

    >>> parse_amount("$1,250.00")
    1250.0
    >>> parse_amount("  40 ")
    40.0

    Hint: .strip() removes spaces at the ends, .replace("$", "") removes
    a character, and float("12.5") turns text into a number.
    """
    raise NotImplementedError("Exercise 2: parse_amount")


# --------------------------------------------------------------------------
# Exercise 3 - arithmetic and round()
# --------------------------------------------------------------------------


def line_total(quantity: float, unit_price: float) -> float:
    """Return quantity x unit price, rounded to 2 decimal places.

    >>> line_total(3, 19.99)
    59.97

    Hint: round(number, 2)
    """
    raise NotImplementedError("Exercise 3: line_total")


# --------------------------------------------------------------------------
# Exercise 4 - lists of dictionaries and for-loops
# --------------------------------------------------------------------------


def invoice_total(line_items: list[dict]) -> float:
    """Add up every line item. Each item has "quantity" and "unit_price" keys.

    >>> invoice_total([
    ...     {"description": "Labor", "quantity": 4, "unit_price": 95.0},
    ...     {"description": "Valve", "quantity": 2, "unit_price": 12.5},
    ... ])
    405.0
    >>> invoice_total([])
    0.0

    Hint: start with total = 0.0, loop over the list, and reuse line_total().
    Round the final answer to 2 decimals.
    """
    raise NotImplementedError("Exercise 4: invoice_total")


# --------------------------------------------------------------------------
# Exercise 5 - building a dictionary (this was assessment question PY1)
# --------------------------------------------------------------------------


def count_document_types(doc_types: list[str]) -> dict[str, int]:
    """Count how many times each document type appears.

    >>> count_document_types(["invoice", "bid", "invoice"])
    {'invoice': 2, 'bid': 1}

    Hint: counts.get(key, 0) returns 0 when the key isn't there yet.
    """
    raise NotImplementedError("Exercise 5: count_document_types")


# --------------------------------------------------------------------------
# Exercise 6 - split() and join()
# --------------------------------------------------------------------------


def normalize_vendor(name: str) -> str:
    """Clean up a vendor name so the same vendor always matches itself.

    Collapse runs of spaces into one, trim the ends, and Title Case each word.

    >>> normalize_vendor("  ridgeline    PLUMBING ")
    'Ridgeline Plumbing'

    Hint: "a  b".split() gives ["a", "b"]; " ".join(words) glues them back;
    "hello".title() gives "Hello".
    """
    raise NotImplementedError("Exercise 6: normalize_vendor")


# --------------------------------------------------------------------------
# Exercise 7 - if-statements, and booleans
# --------------------------------------------------------------------------


def overdue_invoices(invoices: list[dict], today: str) -> list[str]:
    """Return the invoice numbers that are past due AND not yet paid.

    Each invoice looks like {"number": "A-1", "due_date": "2024-03-01", "paid": False}.
    Dates are ISO strings (YYYY-MM-DD), so you can compare them directly with <
    because "2024-03-01" < "2024-03-15" is True. Keep the original order.

    >>> overdue_invoices([
    ...     {"number": "A-1", "due_date": "2024-03-01", "paid": False},
    ...     {"number": "A-2", "due_date": "2024-03-01", "paid": True},
    ...     {"number": "A-3", "due_date": "2024-04-01", "paid": False},
    ... ], today="2024-03-15")
    ['A-1']

    Hint: `if a and not b:`
    """
    raise NotImplementedError("Exercise 7: overdue_invoices")


# --------------------------------------------------------------------------
# Exercise 8 - dictionaries whose values are lists
# --------------------------------------------------------------------------


def group_by_vendor(invoices: list[dict]) -> dict[str, list[str]]:
    """Group invoice numbers by vendor, keeping the original order.

    >>> group_by_vendor([
    ...     {"vendor": "Acme", "number": "1"},
    ...     {"vendor": "Bolt", "number": "2"},
    ...     {"vendor": "Acme", "number": "3"},
    ... ])
    {'Acme': ['1', '3'], 'Bolt': ['2']}

    Hint: if the vendor isn't a key yet, start it with an empty list, then .append().
    """
    raise NotImplementedError("Exercise 8: group_by_vendor")


# --------------------------------------------------------------------------
# Exercise 9 - handling "nothing" with None
# --------------------------------------------------------------------------


def largest_invoice(invoices: list[dict]) -> dict | None:
    """Return the invoice with the biggest "total", or None if the list is empty.

    >>> largest_invoice([{"number": "1", "total": 50.0}, {"number": "2", "total": 75.0}])
    {'number': '2', 'total': 75.0}
    >>> largest_invoice([]) is None
    True

    Hint: check `if not invoices:` first. Then keep track of the biggest one
    seen so far as you loop (or look up max() with key=).
    """
    raise NotImplementedError("Exercise 9: largest_invoice")


# --------------------------------------------------------------------------
# Exercise 10 - FIX THE BUG (this was assessment question PY2)
# --------------------------------------------------------------------------


def collect_tokens(token: str, found: list[str] = []) -> list[str]:  # noqa: B006 - the bug is the lesson
    """Add a token to a list and return the list.

    This function already "works", but it has the shared-default bug from
    assessment question PY2: separate calls share ONE list, so tokens from one
    document leak into the next. In AuditGate, that's a privacy bug.

    Fix it so each call without `found` starts with a fresh, empty list.

    >>> collect_tokens("EMAIL_1")
    ['EMAIL_1']
    >>> collect_tokens("PHONE_1")
    ['PHONE_1']

    Hint: use `found: list[str] | None = None`, then create the list inside
    the function when found is None.
    """
    found.append(token)
    return found
