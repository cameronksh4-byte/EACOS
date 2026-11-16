"""Build the shared baseline knowledge base from the IT Help Desk RAG Knowledge Base page.

    python -m ingest.build_shared_kb            # (re)write knowledge_base/sops/{cases,escalation}
    python -m ingest.build_shared_kb --check    # exit 1 if the files are out of date

The source page (knowledge_base/source/IT_Help_Desk_RAG_Knowledge_Base.html)
keeps its case library and must-escalate list as two JavaScript array literals,
`var CASES=[...]` and `var ESC=[...]`. They are read here as data with a small
literal parser. Nothing in the page is ever executed.

Each case becomes one SOP, kept to a single chunk (no `#`-`###` headings below
the title), so the model always sees a case's steps together with its
escalation point:

* "Guide, then escalate if..." cases  -> KB-<AREA>-NN
* "Escalate to human" cases           -> KB-ESC-<AREA>-NN   (escalate-only)
* Must-escalate triggers              -> KB-ESC-TRIGGER-NN  (escalate-only)

Escalate-only SOPs (prefix KB-ESC-) never resolve a ticket; see
`app.schemas.ticket.is_escalate_only`. The hand-written policy SOPs in
knowledge_base/sops/policy are not touched by this script.

Ingest the result under the shared tenant (SHARED_KB_TENANT_ID, default shared-kb):

    python -m ingest.sync_docs --tenant shared-kb --docs knowledge_base/sops --prune
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from app.schemas.ticket import DOC_ID_PATTERN, ESCALATE_ONLY_DOC_PREFIX

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "knowledge_base" / "source" / "IT_Help_Desk_RAG_Knowledge_Base.html"
OUT = ROOT / "knowledge_base" / "sops"
GENERATED_DIRS = ("cases", "escalation")
RESEARCH_DATE = "2026-11-13"  # the source page's research run
SINGLE_CHUNK_LIMIT = 1800  # app.services.chunking.MAX_CHUNK_CHARS

AREA_CODES = {
    "Email / Outlook": "EMAIL",
    "Printers": "PRINT",
    "Wi-Fi / Network": "NET",
    "VPN": "VPN",
    "Passwords / Accounts": "ACCT",
    "Slow Computers": "PERF",
    "Software / Apps": "APP",
    "Shared Drives / Files": "FILES",
    "Monitors / Hardware": "HW",
    "Phones / Mobile": "MOBILE",
    "Security / Phishing": "SEC",
}
HANDLING = {
    "guide": "The agent can guide the user through this.",
    "mixed": "Guide the user through the steps, then escalate if the escalation point is reached.",
    "esc": ("Escalate-only. Give only the safe first steps below, collect the handoff packet "
            "(KB-POLICY-HANDOFF), and hand off to a person. Don't troubleshoot further."),
}
ESCALATE_VERDICTS = {"esc"}
FIRST_QUESTIONS = "One user, or everyone? When did it start, and what changed?"
FOOTER = ("Shared baseline knowledge base: generic steps. A client's own SOP for the same issue "
          "wins. Never collect a password or MFA code; never make admin or permission changes.")


# ---------------------------------------------------------------------------
# A minimal JavaScript literal reader: arrays, objects with bare or quoted
# keys, strings, numbers, true/false/null. No expressions, no execution.
# ---------------------------------------------------------------------------

class LiteralError(ValueError):
    pass


_IDENT = re.compile(r"[A-Za-z_$][\w$]*")
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


class _Reader:
    def __init__(self, src: str, pos: int) -> None:
        self.src, self.pos = src, pos

    def _ws(self) -> None:
        while self.pos < len(self.src) and self.src[self.pos] in " \t\r\n":
            self.pos += 1

    def _peek(self) -> str:
        self._ws()
        if self.pos >= len(self.src):
            raise LiteralError("unexpected end of input")
        return self.src[self.pos]

    def value(self):
        c = self._peek()
        if c == "[":
            return self._array()
        if c == "{":
            return self._object()
        if c in "\"'":
            return self._string()
        m = _NUMBER.match(self.src, self.pos)
        if m:
            self.pos = m.end()
            text = m.group(0)
            return float(text) if "." in text else int(text)
        m = _IDENT.match(self.src, self.pos)
        if m and m.group(0) in ("true", "false", "null"):
            self.pos = m.end()
            return {"true": True, "false": False, "null": None}[m.group(0)]
        raise LiteralError(f"unsupported token at {self.pos}: {self.src[self.pos:self.pos + 30]!r}")

    def _array(self) -> list:
        self.pos += 1
        items: list = []
        while self._peek() != "]":
            items.append(self.value())
            if self._peek() == ",":
                self.pos += 1
            elif self._peek() != "]":
                raise LiteralError(f"expected , or ] at {self.pos}")
        self.pos += 1
        return items

    def _object(self) -> dict:
        self.pos += 1
        out: dict = {}
        while self._peek() != "}":
            c = self._peek()
            if c in "\"'":
                key = self._string()
            else:
                m = _IDENT.match(self.src, self.pos)
                if not m:
                    raise LiteralError(f"bad object key at {self.pos}")
                key, self.pos = m.group(0), m.end()
            if self._peek() != ":":
                raise LiteralError(f"expected : at {self.pos}")
            self.pos += 1
            out[key] = self.value()
            if self._peek() == ",":
                self.pos += 1
            elif self._peek() != "}":
                raise LiteralError(f"expected , or }} at {self.pos}")
        self.pos += 1
        return out

    def _string(self) -> str:
        quote = self.src[self.pos]
        self.pos += 1
        buf: list[str] = []
        while True:
            if self.pos >= len(self.src):
                raise LiteralError("unterminated string")
            c = self.src[self.pos]
            if c == quote:
                self.pos += 1
                return "".join(buf)
            if c == "\n":
                raise LiteralError(f"newline in string at {self.pos}")
            if c == "\\":
                n = self.src[self.pos + 1]
                if n == "u":
                    buf.append(chr(int(self.src[self.pos + 2:self.pos + 6], 16)))
                    self.pos += 6
                    continue
                buf.append(_ESCAPES.get(n, n))
                self.pos += 2
                continue
            buf.append(c)
            self.pos += 1


def read_array(html: str, name: str) -> list[dict]:
    m = re.search(rf"\bvar\s+{re.escape(name)}\s*=\s*", html)
    if not m:
        raise LiteralError(f"var {name} not found in the source page")
    value = _Reader(html, m.end()).value()
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise LiteralError(f"{name} is not an array of objects")
    return value


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _text(value: object) -> str:
    """One line of plain text: tags stripped, whitespace collapsed."""
    if not isinstance(value, str):
        raise LiteralError(f"expected text, got {type(value).__name__}")
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", "", value)).strip()


def _md_line(value: object) -> str:
    # A leading # would start a heading (and a new chunk); escape it.
    line = _text(value)
    return "\\" + line if line.startswith("#") else line


def _front_matter(doc_id: str, title: str, system_name: str) -> str:
    if not re.match(DOC_ID_PATTERN, doc_id):
        raise LiteralError(f"invalid doc_id {doc_id!r}")
    title = title.replace("\n", " ")
    return (f"---\ndoc_id: {doc_id}\ntitle: {title}\nsystem_name: {system_name}\n"
            f"os_platform: any\nlast_verified_date: {RESEARCH_DATE}\n---\n")


@dataclass
class Doc:
    path: str  # relative to OUT
    doc_id: str
    text: str


def case_docs(cases: list[dict]) -> list[Doc]:
    docs: list[Doc] = []
    seen: dict[str, int] = {}
    for case in cases:
        area = _text(case.get("cat", ""))
        code = AREA_CODES.get(area) or re.sub(r"[^A-Z0-9]+", "", area.split("/")[0].upper())[:12] or "MISC"
        verdict = _text(case.get("v", "")).lower()
        if verdict not in HANDLING:
            raise LiteralError(f"case {case.get('title')!r}: unknown handling {verdict!r}")
        seen[code] = seen.get(code, 0) + 1
        prefix = ESCALATE_ONLY_DOC_PREFIX if verdict in ESCALATE_VERDICTS else "KB-"
        doc_id = f"{prefix}{code}-{seen[code]:02d}"
        title = _text(case["title"])
        steps = [s for s in (_md_line(s) for s in case.get("steps", [])) if s]
        if not steps:
            raise LiteralError(f"case {title!r} has no steps")
        label = "Safe first steps" if verdict in ESCALATE_VERDICTS else "Steps for the user, in order"
        lines = [
            _front_matter(doc_id, title, area),
            f"# {title}",
            "",
            f"Area: {area}. Handling: {HANDLING[verdict]}",
            "",
            f"**What users say:** {_md_line(case.get('sym', ''))}",
            "",
            f"**Ask first:** {FIRST_QUESTIONS}",
            "",
            f"**{label}:**",
            *[f"{i}. {s}" for i, s in enumerate(steps, 1)],
            "",
            f"**If that doesn't fix it:** {_md_line(case.get('fail', ''))}",
            "",
            f"**Escalate to a person when:** {_md_line(case.get('esc', ''))}",
            "",
            FOOTER,
            "",
        ]
        docs.append(Doc(f"cases/{doc_id.lower()}.md", doc_id, "\n".join(lines)))
    return docs


def escalation_docs(triggers: list[dict]) -> list[Doc]:
    docs: list[Doc] = []
    for i, item in enumerate(triggers, 1):
        n = item.get("n", i)
        if not isinstance(n, int) or n < 1:
            raise LiteralError(f"escalation item {i}: bad number {n!r}")
        doc_id = f"{ESCALATE_ONLY_DOC_PREFIX}TRIGGER-{n:02d}"
        title = f"Must escalate: {_text(item['t'])}"
        lines = [
            _front_matter(doc_id, title, "Must escalate"),
            f"# {title}",
            "",
            "Handling: Escalate-only. The agent does not attempt a fix. It gives only the safe first "
            "aid below, collects the handoff packet (KB-POLICY-HANDOFF), and hands off immediately "
            "to the client's named human contact, with urgency.",
            "",
            f"**Triggers:** {_md_line(item.get('trig', ''))}",
            "",
            f"**Collect for the handoff:** {_md_line(item.get('collect', ''))}",
            "",
            f"**Safe first aid only:** {_md_line(item.get('aid', ''))}",
            "",
            FOOTER,
            "",
        ]
        docs.append(Doc(f"escalation/{doc_id.lower()}.md", doc_id, "\n".join(lines)))
    return docs


def build(source: Path = SOURCE) -> list[Doc]:
    html = source.read_text(encoding="utf-8")
    docs = case_docs(read_array(html, "CASES")) + escalation_docs(read_array(html, "ESC"))
    ids = [d.doc_id for d in docs]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise LiteralError(f"duplicate doc_ids: {dupes}")
    for d in docs:
        body = d.text.split("\n---\n", 1)[1]
        if len(body.strip()) > SINGLE_CHUNK_LIMIT:
            raise LiteralError(f"{d.doc_id} is {len(body)} chars; it would split across chunks")
    return docs


def _existing(out: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for sub in GENERATED_DIRS:
        for p in sorted((out / sub).glob("*.md")):
            found[f"{sub}/{p.name}"] = p.read_text(encoding="utf-8")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--check", action="store_true", help="report whether the files are up to date; write nothing")
    args = parser.parse_args(argv)

    docs = build(args.source)
    wanted = {d.path: d.text for d in docs}
    have = _existing(args.out)
    stale = sorted(set(have) - set(wanted))
    changed = sorted(p for p, t in wanted.items() if have.get(p) != t)

    if args.check:
        if stale or changed:
            print(f"out of date: {len(changed)} to write, {len(stale)} to remove "
                  "(run python -m ingest.build_shared_kb)")
            return 1
        print(f"up to date: {len(docs)} generated SOPs")
        return 0

    for sub in GENERATED_DIRS:
        (args.out / sub).mkdir(parents=True, exist_ok=True)
    for path in changed:
        (args.out / path).write_text(wanted[path], encoding="utf-8")
    for path in stale:
        (args.out / path).unlink()
    print(f"{len(docs)} SOPs: {len(changed)} written, {len(stale)} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
