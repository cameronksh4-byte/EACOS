# Transparency Log: RGV Cross-Border Logistics Triage Engine

| | |
|---|---|
| Log opened | October 17, 2026 |
| Architecture status | Approved for Secure MVP Build |
| Frameworks cited | 19 CFR § 111.24, 19 CFR Part 163, NIST AI RMF 1.0, TDPSA, TRAIGA |

Each entry below gives the approved decision **verbatim**, then:

* **Implementation**: where the code enforces it.
* **Review notes**: places where the code, the platform or the citation differs from the decision as written.

Items marked *counsel* need confirmation from a licensed customs attorney or privacy counsel. They are flagged here, not resolved.

---

## Entry 001: Ingestion and webhook security

> **Decision:** Implement FastAPI backend with public documentation (/docs, /redoc) explicitly disabled.
> **Rationale:** Prevents automated bot scanners and malicious actors from fingerprinting internal endpoint schemas.
>
> **Decision:** Enforce HMAC-SHA256 constant-time verification on WhatsApp webhooks and defusedxml parsing for Mexican SAT CFDI 4.0 XML files.
> **Rationale:** Neutralizes timing side-channel attacks, request forgery, and XML External Entity (XXE) memory exhaustion vectors.

**Implementation:**
* `triage_engine/main.py`: `docs_url`, `redoc_url` **and** `openapi_url` are all `None`. Disabling only `/docs` and `/redoc` would still publish the schema at `/openapi.json`.
* `security/signatures.py`: `hmac.compare_digest` over the raw body against `X-Hub-Signature-256`. A missing secret fails closed.
* `security/body_limit.py`: the 10 MB limit also applies to streamed and chunked bodies.
* `security/xml_safe.py`: `defusedxml` with `forbid_dtd`, `forbid_entities` and `forbid_external`.
  * Tests cover file XXE, SSRF to the instance metadata endpoint, parameter entities, external DTDs and billion-laughs.
* Inbound e-mail is also authenticated. The relay's HMAC covers the SMTP envelope, then SPF and aligned DKIM must pass.

**Review notes:** None. Implemented as decided.

---

## Entry 002: Data residency and cloud infrastructure

> **Decision:** Deploy compute on Render / AWS App Runner and database/storage on Supabase Pro in the US-East (N. Virginia / Texas proximity) region.
> **Rationale:** Satisfies U.S. customs broker data residency mandates under 19 CFR § 111.24; prevents international data egress.
>
> **Decision:** Enforce PostgreSQL Row-Level Security (RLS) keyed to tenant_id = auth.uid().
> **Rationale:** Guarantees multi-tenant isolation at the database kernel level, eliminating cross-broker data leaks.

**Implementation:**
* **Region guard:**
  * `config.py` rejects any `aws_region` outside the commercial US regions.
  * It also rejects Bedrock cross-region inference profiles that can route traffic outside the US (`global.`, `eu.`, `apac.`, …).
  * Bedrock inference, the S3 audit bucket and compute all share that region.
* **RLS:**
  * Every database transaction runs as role `authenticated`, with `request.jwt.claims.sub` set to the tenant (`storage/db.py`). The `tenant_id = (select auth.uid())` policies therefore bind the backend's own queries.
  * The backend login role has no table privileges and never bypasses RLS.
  * The Supabase `service_role` key is never used.
* **Verified:** 10 integration tests run against real Postgres 16 (`tests/integration/`). They cover cross-tenant reads, updates and inserts, which are refused.

**Review notes:**
1. **Citation (*counsel*).** § 111.24 covers the *confidentiality* of broker records. Where records must be kept appears to be § 111.23 ("Retention of records"). Cite both if residency and confidentiality are both intended.
2. **Render vs. App Runner.** Prefer **AWS App Runner** or ECS Fargate.
   * Render offers no AWS IAM role, so Bedrock and S3 would need long-lived AWS access keys stored on a third-party platform.
   * App Runner gives the service a short-lived instance role.
   * The container (`Dockerfile`) runs on either.
3. **"Texas proximity".** Neither Supabase nor AWS offers a Texas region. Supabase's US-East choices are `us-east-1` (N. Virginia) and `us-east-2` (Ohio). Use `us-east-1` to sit beside Bedrock and S3. The code cannot check the Supabase project's region, so confirm it when the project is created.
4. **Wording.** RLS is enforced by the Postgres engine on every query. It is not an operating-system kernel control. "Database engine level" is more accurate.

---

## Entry 003: Hybrid AI and deterministic validation model

> **Decision:** Restrict Multimodal Vision-Language Models (Claude 3.5 Sonnet via AWS Bedrock ZDR endpoints) strictly to unstructured visual extraction, routing all calculations through rigid Pydantic v2 models.
> **Rationale:** Eliminates LLM arithmetic hallucinations. Unit conversions (KG to LBS), pallet tare weight math, and variance thresholds are governed entirely by deterministic Python code.
>
> **Decision:** Implement PII masking middleware for Mexican driver licenses and personal RFC/CURP identifiers.
> **Rationale:** Aligns with Texas Data Privacy and Security Act (TDPSA) and reduces liability regarding driver personal data.

**Implementation:**
* **The model only transcribes.**
  * Pounds, pallet tare, gross estimates and variance are `computed_field`s or plain Python in `schemas/units.py` and `services/reconciliation.py`.
  * They are left out of the schema the model fills in, so the model is never asked to calculate.
  * Line-item sums are validated, and a mismatch is sent back to the model to re-read.
* **CFDI XML never reaches the model.** It is parsed deterministically (`services/cfdi.py`). Only PDFs and images go to Bedrock.
* **No-retention check.** Startup fails if Bedrock model invocation logging would keep prompts or outputs.
* **PII handling** (`services/ingestion.py`):
  * CURPs, persona-física RFCs, labelled licence numbers, WhatsApp phone numbers and envelope senders are replaced with tenant-scoped HMAC tokens.
  * This happens before storage, logging, auditing and text prompts.
  * Company RFCs are kept because customs filings need them.

**Review notes:**
1. **Model substitution.** Claude 3.5 Sonnet was **retired on 2025-10-28** and can no longer be called. The build uses its successor, `anthropic.claude-sonnet-5-5`, which runs under zero data retention. The model ID is set by `TRIAGE_BEDROCK_MODEL_ID`. See Entry 005.
2. **"Middleware".** Masking runs as a pipeline stage *after* signature verification, not as an ASGI middleware that rewrites the body. Rewriting the body first would break the HMAC and DKIM checks, which must see the original bytes. The protective effect is the same.
3. **Scope of masking (disclose).** Masking applies to text. A PDF or photo of a BOL reaches Bedrock **unredacted**, including any printed licence number. The protection there is that Bedrock keeps nothing, plus tokenising the extracted values before storage.
4. **TDPSA (*counsel*).** Whether TDPSA applies to Mexican-resident drivers, and which TRAIGA provisions bind a private B2B tool, has not been decided. The controls above minimise data either way.

---

## Entry 004: Audit trail and CBP record-keeping

> **Decision:** Implement AWS S3 Object Lock (Compliance Mode) with SHA-256 hash chaining for all incoming triage packets and human-in-the-loop approvals.
> **Rationale:** Fulfills the 5-year CBP recordkeeping mandate under 19 CFR Part 163 and provides verifiable proof of "Reasonable Care."
>
> **Decision:** Position the app strictly as a "Pre-Bridge Triage & Proofreading Utility" with required human broker sign-off.
> **Rationale:** Avoids unlicensed customs business violations (19 CFR Part 111) while providing indispensable operational leverage.

**Implementation:**
* **Hash chain** (`storage/audit.py`):
  * Each tenant has its own chain of S3 objects.
  * Each object is written with `COMPLIANCE` Object Lock, `If-None-Match: *` (so it can never be overwritten) and an S3-verified SHA-256 checksum.
  * Every event links to the previous one by SHA-256.
* **Full records archived:** every **triage packet** and every **broker review** is archived as its own Object Lock object under `records/v1/`. The archive's SHA-256 is written into a chained audit event, so the archive is covered by the chain.
* **Required broker sign-off** (`api/packets.py`, `schemas/review.py`, `migrations/002_broker_review.sql`):
  * Every packet starts as `PENDING_BROKER_REVIEW`. The engine's CLEAR/REVIEW/HOLD result is labelled *advisory*.
  * Only a broker decision moves a packet to `APPROVED_BY_BROKER`, `REJECTED_BY_BROKER` or `CORRECTION_REQUESTED`.
  * Brokers sign in with Supabase Auth, and the token is checked on every request.
* **Reasonable-care rules for approvals:**
  * The broker must acknowledge every WARNING and CRITICAL finding by code.
  * Approving a HOLD needs a written override of at least 20 characters. The database enforces this too.
  * Approvals need a multi-factor session (`aal2`).
  * Approvals and rejections are final, enforced by a database trigger.
  * The database refuses any status change that doesn't come with a matching review record.
  * The reviewer must be the signed-in user.
* **Retention:** at least 1,827 days, and the config refuses less. The default is **1,917 days** because records are locked at triage time, which comes before the entry date that starts the five-year clock.

**Review notes:**
1. **Source documents are not retained (decision needed).**
   * Today the original PDF, image and XML files are kept only as SHA-256 digests. Archived packets hold the extracted, tokenised data.
   * If the broker's Part 163 record copy must be the original document, the originals need their own Object Lock store.
   * That conflicts with TDPSA-style minimisation, because a BOL image can carry driver data that cannot be redacted.
   * Choose one: (a) keep originals under Object Lock with tight access, (b) keep only extractions and rely on the broker's own document system, or (c) keep originals for HOLD and REVIEW loads only.
2. **Who is the "broker"?** Following Entry 002, a tenant is one Supabase user (`tenant_id = auth.uid()`). A brokerage with several licensed brokers therefore shares one sign-in, which weakens individual accountability for approvals. Recommended next step: add an organisation table with per-user membership, then key RLS on membership.

---

## Entry 005: Model substitution (added October 17, 2026)

* **Decision:** Use `anthropic.claude-sonnet-5-5` on Amazon Bedrock in place of the retired Claude 3.5 Sonnet.
* **Rationale:** The approved model can no longer be called. Its successor runs under zero data retention and is called in-region. The model's role is unchanged: transcription only (Entry 003).
* **Controls:**
  * Every extraction records the model ID, prompt version, input and output SHA-256, token counts and validation outcome in the hash chain (NIST AI RMF *Measure 2* / *Manage 4*).
  * When no valid extraction is produced, the load fails closed to broker follow-up.

## Entry 006: Open items

| Item | Owner | Status |
|---|---|---|
| Confirm § 111.23 / § 111.24 citations | Counsel | Open |
| TDPSA / TRAIGA applicability | Counsel | Open |
| Source-document retention option (Entry 004, note 1) | Product + counsel | Open |
| Per-broker identities within a tenant (Entry 004, note 2) | Engineering | Proposed |
| Confirm Supabase project region `us-east-1` | Ops | At provisioning |
| Durable queue (SQS) between webhooks and processing | Engineering | Before production volume |
