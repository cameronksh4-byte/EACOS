# EACOS: AI Engineering Sprint & AuditGate

This repository has two parts that are meant to be used together:

1. **`trainer/`** is a code-along CLI. It diagnoses where you're starting from, then walks you through a 120-day (or 60–180-day) AI engineering curriculum in seven modules, from Python basics to hand-built agents, durable state graphs, MCP, OpenTelemetry and evaluation engineering.
2. **`auditgate/`** is the product you build along the way. **AuditGate** is a local-first engine that extracts and audits invoices, bids and work orders, and an agent that resolves the exceptions. It's made for business owners who don't trust public AI models with private data or with getting numbers right.

Every module pairs a **hands-on exercise** (you write the code; tests grade it) with the **production version** inside AuditGate, so you build each idea once by hand and then see how it holds up in a real system.

> The existing EACOS reference folders (`architecture/`, `frameworks/`, `governance/`, `tutorials/`, …) are unchanged. `tutorials/langgraph` is part of Module 4.

---

## Quick start

```bash
# Python 3.12+ and uv required (https://docs.astral.sh/uv/)
uv sync                                          # create .venv and install everything
cp .env.example .env                             # default provider "heuristic" is 100% offline

uv run python -m trainer.cli assess --days 120   # 1. diagnostic, about 15 minutes
uv run python -m trainer.cli plan                # 2. your calibrated schedule
uv run python -m trainer.cli module M0           # 3. start learning (M0 is skipped if you already know Python)
uv run python -m trainer.cli check M0            # 4. grade your exercise
```

Everything else:

```bash
uv run pytest                                    # full test suite
uv run python -m trainer.cli eval                # both eval gates (documents + agent), offline
uv run streamlit run auditgate/ui.py             # dashboard at http://localhost:8501
uv run uvicorn auditgate.api:app                 # local REST API at http://127.0.0.1:8000/docs
uv run python -m auditgate.agent                 # exception-resolution agent, offline scripted demo
uv run python -m auditgate.mcp_server            # AuditGate as an MCP server (stdio)
```

---

## The trainer

| Command | What it does |
|---|---|
| `assess` | 16 questions across 8 areas, weighted by difficulty. Press `s` to skip a question instead of guessing. `--answers "a,b,..."` runs it non-interactively; `--days 120` sets the sprint length (60–180). |
| `plan` | Your schedule. Each module is set to **fast-track**, **accelerated** or **full** based on your score in its area. Days saved on what you already know go to your weak areas. Module 0 is skipped entirely if you score 80% or more on Python. |
| `modules` / `module M2` | Overview, objectives, key concepts, code-along steps tied to real files, checkpoint, milestone and stretch goal. |
| `check M2` | Grades your exercise and the module's production tests, and records completion. Each failure is one line telling you what's left. |
| `eval` | Runs the document and agent evaluation gates. |
| `status` | Where you are and what to do next. |

**Levels:** Foundation (<35%) → Builder (35–60%) → Practitioner (60–85%) → Architect (≥85%).

### The modules

| # | Module | Your exercise (`trainer/exercises/`) | Production code you then study |
|---|---|---|---|
| M0 | Python Basics *(optional)* | `m0_basics.py`: 10 invoice-themed exercises, ending with fixing a data-leak bug | — |
| M1 | Typed Data Contracts & Static Validation | code-along in the real schemas, plus mypy | `extraction/schemas.py` |
| M2 | Agents from Scratch: Tool Calling & Self-Correction | `m2_react.py`: tool schema, error feedback, safe execution, the ReAct loop | `agent/models.py`, `tools.py`, `loop.py`, `resolver.py` |
| M3 | Security, Guardrails & Deterministic Compliance | `m3_security.py`: Luhn, injection detection, link allowlist, safe-expression check | `security/` (sanitizer, injection, egress, sandbox, compliance) |
| M4 | State Graphs, Persistence & Human Review | `m4_graphs.py`: router, SQLite checkpointer, pause/resume runner, exactly-once handler | `agent/graph.py`, `api.py`, `pipeline.process_batch` |
| M5 | Tool Protocols (MCP) & Observability (OpenTelemetry) | `m5_protocols.py`: prompt versions, mini tracer, PII-safe attributes, cost, raw JSON-RPC MCP | `mcp_server.py`, `observability.py` |
| M6 | Evaluation Engineering, CI & Shipping | `m6_evals.py`: exact match, precision/recall, Pass@k, regressions, cosine/nearest | `evals/` (metrics, runners, synth, cluster), `.github/workflows/ci.yml` |

Reference solutions are in `trainer/exercises/solutions/`. Try each exercise yourself before you look; the struggle is where the learning happens. The test suite checks that every solution passes and that every shipped exercise starts unsolved.

---

## AuditGate architecture

```
 file (PDF/XLSX/CSV/TXT) ─► load_text()                            local only
        │
        ├─► detect_injection() ──► POSSIBLE_PROMPT_INJECTION (review)   strip invisible characters
        ▼
   Sanitizer ─► "Attn: [[PERSON_1]] … Acct #: [[BANK_ACCOUNT_1]]"       Vault stays on this machine
        ▼
   Extractor (heuristic | local LLM | OpenAI | Anthropic)              structured output
        ▼
   rehydrate ─► Pydantic validation ─► audit() rules ─► compliance RuleSet ─► AuditReport (PASS / REVIEW / FAIL)
                                                                                    │ FAIL / REVIEW
                                                                                    ▼
   Resolver agent: ReAct loop or LangGraph ─► tools (vendor history, POs, calculator) ─► Resolution
        ▼                                                  every step traced (OpenTelemetry)
   enforce_policy() + egress check  ─►  human_review (pauses, survives restarts)  ─►  outbox (exactly once)
```

### Design decisions worth understanding

- **Schemas capture what's on the page; audits decide whether it adds up.** Arithmetic checks are deliberately not validators, because retry-on-validation would push the model to invent numbers that balance.
- **The model never does arithmetic.** The agent has to use the `line_total`, `add_amounts` and `overcharge` tools.
- **Policy sits above the model.** Plain Python rules override any model output. An invoice with audit errors is never auto-approved, a disputed amount can't exceed the document total, and outbound text can't contain unknown links or bank, card or tax identifiers.
- **PII boundary everywhere.** Models, MCP clients and traces only see placeholders. Tools run on real values locally. Values are masked by detectors, by label, by field (`bill_to`, `contact`, …), and wherever they reappear in the same run.
- **Errors are returned, not raised.** Tool failures go back to the model as precise, actionable messages (field, problem, what was sent), so it can fix its own call.
- **Durable by default.** The graph version checkpoints to SQLite after every node. A dispute can wait days for a human and resume after a restart, and a retried approval webhook only acts once.

### Agent, two ways

| | `agent/loop.py` (Module 2) | `agent/graph.py` (Module 4) |
|---|---|---|
| Shape | a `while` loop with a step budget | a LangGraph `StateGraph`: agent, tools, repair, nudge, policy, human_review, finalize |
| On a bad tool call | the error goes back in the transcript | routed to a **repair** node; too many repairs routes to a human |
| Human in the loop | — | `interrupt()` at `human_review`; resume via `POST /webhooks/approval` |
| Persistence | in memory | SQLite checkpoints (`ResolutionService`), idempotent webhooks, outbox |

### Evaluation gates

**Documents** (`evals/runner.py`, 12 golden + 32 synthetic cases)

| Metric | Gate |
|---|---|
| field_accuracy | ≥ 95% |
| status_accuracy | ≥ 90% |
| finding_recall | ≥ 90% |
| schema_adherence | 100% |
| PII values in outbound payloads | **0** |

**Agent** (`evals/agent_runner.py`, 8 cases with recorded reference runs)

| Metric | Gate (real model) | Gate (replay) |
|---|---|---|
| Pass@1 (and Pass@k with `-n`/`-k`) | ≥ 75% | 100% |
| Schema compliance (valid answers ÷ answers submitted) | ≥ 80% | 100% |
| Tool-selection recall / precision | ≥ 80% / ≥ 70% | 100% |
| Forbidden verdicts (e.g. approving an injected invoice) | **0** | **0** |

Use `--baseline before.json` to compare against an earlier run after changing a prompt or model; any regression beyond 5 points fails the run. **Synthetic data** (`evals/synth.py`) applies mutations whose expected answers are computed, not guessed. The first run found two real bugs: spelled-out emails leaked, and spaced-out layouts broke bid parsing. Both are fixed. **Clustering** (`evals/cluster.py`) groups failures by root cause, using TF-IDF offline or any embedding model. **CI** (`.github/workflows/ci.yml`) runs the tests and both gates on every push. A manually triggered job scores a real model.

### Known limitations (be honest with clients about these)

- The heuristic extractor expects `Label: value` layouts. Scanned images need OCR first, and free-form layouts need an LLM backend.
- The real-model paths (Anthropic, OpenAI, Ollama) are built against the raw SDKs and tested with fake clients, but scoring them needs your API key or a local model.
- Injection detection is a signal, not a guarantee. The structural defenses (data-only prompts, policy above the model, egress checks) are what hold when someone finds a new phrasing.
- The sandbox (allowlisted syntax plus a restricted subprocess) is defense in depth, not a container. For hostile code, run it in a locked-down container or microVM.
- **Compliance rules ship as templates, with no legal values.** You supply each number from the actual statute or contract, cite it and record who verified it. Unverified rules can only warn.
- Name detection relies on labels and fields; free-text names need the optional spaCy model. Street-address detection covers common US formats.
- Checkpoint databases contain the PII vault. Keep them on the machine (they're git-ignored).

Optional NER-based name detection:

```bash
uv pip install "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
uv run python -m auditgate.evals.runner --spacy
```

---

## Project layout

```
.env.example                    configuration template (never commit .env)
.github/workflows/ci.yml        tests + both eval gates on every push
sales_collateral/client_pitch.md
trainer/        cli.py · assessment.py · assessment_bank.json · curriculum.json
trainer/exercises/              m0 · m2 · m3 · m4 · m5 · m6 exercises (yours to edit) · test_*.py · solutions/
auditgate/      config.py · pipeline.py · api.py · ui.py · mcp_server.py · observability.py
  agent/        models.py · tools.py · loop.py · resolver.py · graph.py · data/vendors.json
  security/     sanitizer.py · injection.py · egress.py · sandbox.py · compliance.py
  extraction/   schemas.py · extractor.py
  evals/        runner.py · agent_runner.py · metrics.py · synth.py · cluster.py
                golden_dataset.json · synthetic_dataset.json · agent_cases.json
tests/          test_pipeline · test_agent · test_security · test_graph · test_protocols · test_evals
                (test names prefixed m1–m6 / trainer → per-module checkpoints)
```
