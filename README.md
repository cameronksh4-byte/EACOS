# EACOS: AI Engineering Sprint & AuditGate

This repository has two parts that are meant to be used together:

1. **`trainer/`** is a code-along CLI that diagnoses where you're starting from and walks you through a 90-day AI engineering sprint in five modules.
2. **`auditgate/`** is the product you build along the way. **AuditGate** is a local-first engine that extracts and audits invoices, bids and work orders, made for business owners who don't trust public AI models with private data or with getting numbers right.

> The existing EACOS reference folders (`architecture/`, `frameworks/`, `governance/`, `tutorials/`, …) are unchanged. `tutorials/langgraph` is the suggested follow-on for Module 4's stretch goal.

---

## Quick start

```bash
# Python 3.12+ and uv required (https://docs.astral.sh/uv/)
uv sync                                   # create .venv and install everything
cp .env.example .env                      # default provider "heuristic" is 100% offline

uv run python -m trainer.cli assess       # 1. diagnostic, about 10 minutes
uv run python -m trainer.cli plan         # 2. your calibrated 90-day plan
uv run python -m trainer.cli module M1    # 3. start learning

uv run pytest                             # full test suite
uv run python -m auditgate.evals.runner   # eval gate: accuracy, schema adherence, PII leakage
uv run streamlit run auditgate/ui.py      # dashboard at http://localhost:8501
uv run uvicorn auditgate.api:app          # local REST API at http://127.0.0.1:8000/docs
```

Optional NER-based name detection (better at catching names written in free text):

```bash
uv pip install "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
uv run python -m auditgate.evals.runner --spacy
```

---

## The trainer

| Command | What it does |
|---|---|
| `assess` | 10 questions in 5 domains (Python data structures, API mechanics, validation schemas, orchestration, data security), weighted by difficulty. Press `s` to skip a question instead of guessing. `--answers "a,b,..."` runs it non-interactively. |
| `plan` | Your 90-day schedule. Each module is set to **fast-track**, **accelerated** or **full** based on your score in its domain. Days you save on what you already know go to your weak spots. |
| `modules` / `module M3` | Overview, objectives, key concepts, code-along steps tied to real files, checkpoint, milestone and stretch goal. |
| `check M3` | Runs that module's checkpoint tests (`pytest -k m3`) and records completion. |
| `eval` | Runs the AuditGate eval gate. |
| `status` | Where you are and what to do next. |

**Levels:** Foundation (<35%) → Builder (35–60%) → Practitioner (60–85%) → Architect (≥85%).

### The five modules

| # | Module | You build |
|---|---|---|
| M1 | Typed Data & Validation Schemas | `extraction/schemas.py`: documents as Pydantic models, plus deterministic audit rules |
| M2 | LLM API Mechanics & Structured Outputs | `extraction/extractor.py`: instructor with Ollama, OpenAI or Anthropic behind one interface |
| M3 | Data Security & PII Sanitization | `security/sanitizer.py`: reversible tokenization, with leakage measured |
| M4 | Agent Workflows & Orchestration | `pipeline.py`, `api.py`: staged pipeline, human routing, local service |
| M5 | Automated Evaluations & Shipping | `evals/`, `ui.py`, `sales_collateral/`: release gates, dashboard and pitch |

---

## AuditGate architecture

```
 file (PDF/XLSX/CSV/TXT)
        │  load_text()            local only: pdfplumber / pandas
        ▼
   raw text ──► Sanitizer ──► "Attn: [[PERSON_1]] … Acct #: [[BANK_ACCOUNT_1]]"     ◄─ outbound_payload
        │        │ Vault (stays local)          │
        │        │                              ▼
        │        │                  Extractor (heuristic | local LLM | OpenAI | Anthropic)
        │        │                              │  structured output via instructor
        │        ▼                              ▼
        │   rehydrate() ◄──────────── dict with placeholders
        ▼
  Pydantic schema validation ──► audit() rules ──► AuditReport: PASS / REVIEW / FAIL + findings
```

Design decisions worth understanding:

- **Schemas capture what's on the page; audits decide whether it adds up.** Arithmetic checks are deliberately *not* Pydantic validators. Instructor sends validation errors back to the model and retries, which would push the model to *invent* numbers that balance and hide the overbilling we're trying to catch.
- **Everything is sanitized, even when the backend is local.** It's defense in depth, and it gives every backend the same code path, so leakage can be measured the same way for all of them.
- **The offline heuristic extractor is a real backend, not a mock.** It's the zero-trust default and the baseline every LLM has to beat in evals.
- **Failures become findings, not exceptions.** A timeout or bad model output turns into `EXTRACTION_FAILED` with status FAIL, and the batch keeps going.

### Eval gates (`auditgate/evals/runner.py`)

| Metric | Gate |
|---|---|
| field_accuracy | ≥ 95% |
| status_accuracy | ≥ 90% |
| finding_recall | ≥ 90% |
| schema_adherence (valid documents parse, broken ones are rejected) | 100% |
| PII values in the outbound payload | **0** |

The golden dataset has 11 edge cases: line-item math errors, inflated totals, a missing total, EUR with US-style dates, a due date before the issue date, a document heavy with PII, a prompt injection, bid sums that don't match, an emergency work order with no cap, and a work order with no tasks. The runner exits non-zero if any gate fails, so you can use it as a CI step.

### Known limitations (be honest with clients about these)

- The heuristic extractor expects `Label: value` layouts. Scanned images need OCR first, and free-form layouts need an LLM backend.
- Detection based on regexes and labels catches structured identifiers reliably. Names written in free text need the optional spaCy model, and small NER models sometimes redact more than necessary (for example, business names). Over-redacting is harmless because values are restored afterwards; under-redacting is what the leakage gate is there to catch.
- Street-address detection covers common US formats only.

---

## Project layout

```
.env.example                 configuration template (never commit .env)
sales_collateral/client_pitch.md
trainer/      cli.py · assessment.py · assessment_bank.json · curriculum.json
auditgate/    config.py · pipeline.py · api.py · ui.py
              security/sanitizer.py
              extraction/schemas.py · extraction/extractor.py
              evals/runner.py · evals/golden_dataset.json
tests/test_pipeline.py       test names prefixed m1–m5 / trainer → per-module checkpoints
```
