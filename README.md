# EACOS: AI Engineering Sprint & AuditGate

This repository has two parts that are meant to be used together:

1. **`trainer/`** is a code-along CLI that diagnoses where you're starting from and walks you through a 90-day AI engineering sprint (adjustable from 60 to 180 days) in seven modules: from Python basics, through hand-built agents, to evaluation engineering.
2. **`auditgate/`** is the product you build along the way. **AuditGate** is a local-first engine that extracts and audits invoices, bids and work orders, made for business owners who don't trust public AI models with private data or with getting numbers right.

> The existing EACOS reference folders (`architecture/`, `frameworks/`, `governance/`, `tutorials/`, …) are unchanged. `tutorials/langgraph` is the suggested follow-on for Module 4's stretch goal.

---

## Quick start

```bash
# Python 3.12+ and uv required (https://docs.astral.sh/uv/)
uv sync                                   # create .venv and install everything
cp .env.example .env                      # default provider "heuristic" is 100% offline

uv run python -m trainer.cli assess       # 1. diagnostic, about 15 minutes (add --days 120 for a steadier pace)
uv run python -m trainer.cli plan         # 2. your calibrated 90-day plan
uv run python -m trainer.cli module M0    # 3. start learning (M0 is skipped if you already know Python)

uv run pytest                             # full test suite
uv run python -m auditgate.evals.runner   # eval gate: accuracy, schema adherence, PII leakage
uv run streamlit run auditgate/ui.py      # dashboard at http://localhost:8501
uv run uvicorn auditgate.api:app          # local REST API at http://127.0.0.1:8000/docs
uv run python -m auditgate.agent          # exception-resolution agent, offline scripted demo
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
| `assess` | 16 questions in 8 domains (Python data structures, API mechanics, validation schemas, orchestration, data security, tool calling, state & persistence, observability), weighted by difficulty. Press `s` to skip a question instead of guessing. `--answers "a,b,..."` runs it non-interactively; `--days 120` sets the sprint length. |
| `plan` | Your schedule. Each module is set to **fast-track**, **accelerated** or **full** based on your score in its domain. Days you save on what you already know go to your weak spots. |
| `modules` / `module M0` | Overview, objectives, key concepts, code-along steps tied to real files, checkpoint, milestone and stretch goal. Each module shows a build status: **ready**, **partial** (some steps are marked *coming in a later stage*) or **planned**. |
| `check M0` | Runs that module's checkpoint tests and records completion. |
| `eval` | Runs the AuditGate eval gate. |
| `status` | Where you are and what to do next. |

**Levels:** Foundation (<35%) → Builder (35–60%) → Practitioner (60–85%) → Architect (≥85%).

**Module 0** is for anyone who isn't yet comfortable reading Python. It only appears in your plan if you scored below 80% on Python data structures; otherwise it's skipped and its days go to other modules. Write your answers in `trainer/exercises/m0_basics.py` and check them with `check M0`. Each passing test is one finished exercise. Reference solutions are in `trainer/exercises/solutions/`; try the exercises yourself before you look.

**Module 2** works the same way. In `trainer/exercises/m2_react.py` you build the four parts of an agent yourself: a tool schema, error feedback, safe tool execution and the loop. A fake model drives the tests, so you don't need an API key. Then compare your version with the production one in `auditgate/agent/`.

### The modules

| # | Module | You build | Status |
|---|---|---|---|
| M0 | Python Basics *(optional)* | `trainer/exercises/m0_basics.py`: 10 invoice-themed exercises, ending with fixing a data-leak bug | ready |
| M1 | Typed Data Contracts & Static Validation | `extraction/schemas.py`: Pydantic schemas, deterministic audit rules, mypy | ready |
| M2 | Agents from Scratch: Tool Calling & Self-Correction | `trainer/exercises/m2_react.py`, then `auditgate/agent/`: a hand-built ReAct loop over the raw SDKs | ready |
| M3 | Security, Guardrails & Deterministic Compliance | `security/sanitizer.py`, the agent's `enforce_policy`; injection evals, sandboxing and statutory rules come later | partial |
| M4 | State Graphs, Persistence & Human Review | `pipeline.py`, `api.py`; LangGraph, SQLite checkpoints and idempotent webhooks come later | partial |
| M5 | Tool Protocols (MCP) & Observability (OpenTelemetry) | MCP server and OTel traces | planned |
| M6 | Evaluation Engineering, CI & Shipping | `evals/`, `ui.py`, pitch; Pass@k, synthetic data, clustering and CI come later | partial |

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

### Exception-resolution agent (`auditgate/agent/`)

When a document fails the audit, the agent investigates and proposes **approve**, **dispute** (with the exact overcharge and a draft email) or **escalate**. It's a ReAct loop written by hand against the raw Anthropic and OpenAI SDKs, with no agent framework:

```
task (sanitized report) ─► model ─► tool calls ─► registry.execute() ─► results (errors included) ─┐
                             ▲                     args rehydrated,        re-sanitized             │
                             └─────────────────────────────────────────────────────────────────────┘
                 stops when submit_resolution validates · or after max_steps → escalate to a human
                                         ▼
                 enforce_policy(): plain Python rules the model can't override
```

- **Tool schemas come from Pydantic models**, so the schema the model sees is exactly what gets validated.
- **Errors are returned, not raised.** Validation errors name each field and what was sent. Exceptions report their type and location. Unknown tools list the valid ones. Arguments that aren't valid JSON are reported back instead of crashing the loop.
- **PII boundary:** the model only ever sees placeholders. Tools run on real values locally, and their output is sanitized again before the model sees it. Any value masked once stays masked for the rest of the run.
- **The model never does arithmetic.** It has to use the `line_total`, `add_amounts` and `overcharge` tools.
- **Policy sits above the model.** An invoice with audit errors is never auto-approved, and a disputed amount can never exceed the document total.
- **`--provider scripted`** replays a fixed run, including one deliberate mistake, so you can watch the self-correction offline. Use `--provider anthropic|openai|local` for a real model.

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
              agent/models.py · agent/tools.py · agent/loop.py · agent/resolver.py · agent/data/
              security/sanitizer.py
              extraction/schemas.py · extraction/extractor.py
              evals/runner.py · evals/golden_dataset.json
trainer/exercises/           m0_basics.py · m2_react.py (yours to edit) · test_*.py · solutions/
tests/                       test_pipeline.py · test_agent.py; names prefixed m1–m6 / trainer → per-module checkpoints
```
