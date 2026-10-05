"""Reference solutions for trainer/exercises/m0_basics.py.

There is usually more than one good answer. If yours passes the tests, it's right.
Comments mention the shorter "Pythonic" versions you'll see in real code.
"""

from __future__ import annotations


def format_money(amount: float) -> str:
    return f"${amount:,.2f}"


def parse_amount(text: str) -> float:
    cleaned = text.strip().replace("$", "").replace(",", "")
    return float(cleaned)


def line_total(quantity: float, unit_price: float) -> float:
    return round(quantity * unit_price, 2)


def invoice_total(line_items: list[dict]) -> float:
    total = 0.0
    for item in line_items:
        total += line_total(item["quantity"], item["unit_price"])
    return round(total, 2)
    # Shorter: round(sum(line_total(i["quantity"], i["unit_price"]) for i in line_items), 2)


def count_document_types(doc_types: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for doc_type in doc_types:
        counts[doc_type] = counts.get(doc_type, 0) + 1
    return counts
    # Shorter: dict(collections.Counter(doc_types))


def normalize_vendor(name: str) -> str:
    words = name.split()
    return " ".join(word.title() for word in words)


def overdue_invoices(invoices: list[dict], today: str) -> list[str]:
    overdue = []
    for invoice in invoices:
        if invoice["due_date"] < today and not invoice["paid"]:
            overdue.append(invoice["number"])
    return overdue
    # Shorter: [i["number"] for i in invoices if i["due_date"] < today and not i["paid"]]


def group_by_vendor(invoices: list[dict]) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for invoice in invoices:
        if invoice["vendor"] not in groups:
            groups[invoice["vendor"]] = []
        groups[invoice["vendor"]].append(invoice["number"])
    return groups
    # Shorter: groups.setdefault(invoice["vendor"], []).append(invoice["number"])


def largest_invoice(invoices: list[dict]) -> dict | None:
    if not invoices:
        return None
    biggest = invoices[0]
    for invoice in invoices[1:]:
        if invoice["total"] > biggest["total"]:
            biggest = invoice
    return biggest
    # Shorter: max(invoices, key=lambda i: i["total"], default=None)


def collect_tokens(token: str, found: list[str] | None = None) -> list[str]:
    if found is None:
        found = []
    found.append(token)
    return found
