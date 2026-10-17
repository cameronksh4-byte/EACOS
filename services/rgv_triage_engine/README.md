# RGV Cross-Border Logistics Triage Engine

A security-first FastAPI backend that takes in Mexican SAT CFDI 4.0 facturas and bills of lading by WhatsApp and e-mail, pulls out their data with Claude on Amazon Bedrock, normalises weights to pounds and reconciles the two documents. Each northbound load gets an advisory **CLEAR**, **REVIEW** or **HOLD** result. Every step is written to a tamper-evident audit trail.

**Positioning:** this is a *pre-bridge triage and proofreading utility*. It never decides entry, classification or filing. Every packet waits for a licensed broker's recorded sign-off. The engine's result is advisory only.

Decisions, their rationale and open compliance items are tracked in [`TRANSPARENCY_LOG.md`](TRANSPARENCY_LOG.md).

```
WhatsApp ──HMAC──┐                                 ┌─ CFDI XML ─► defusedxml parser (no LLM)
                 ├─► 10 MB limit ─► verify ─► PII ─┤
E-mail relay ────┘   (ASGI)        sig/SPF   tokens└─ PDF/image ─► Claude on Bedrock (instructor)
         HMAC + SPF + aligned DKIM                              │
                                                                ▼
         Supabase Postgres (RLS: tenant_id = auth.uid())  ◄── strict Pydantic v2 schemas
                                                                │  kg→lb, pallet tare, line sums
                                                                ▼
                         reconciliation (>3 % weight, bulto count) ─► CrossBorderLoadTriagePacket
                                                                │
                 S3 Object Lock (COMPLIANCE) ◄── SHA-256 hash-chained audit records
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/v1/health` | `{"status": "secure_online", "region": "us-east-1"}`. Reveals no versions or dependencies. |
| `GET` | `/v1/webhooks/whatsapp` | Meta subscription handshake. The verify token is compared in constant time. |
| `POST` | `/v1/webhooks/whatsapp` | WhatsApp Cloud API notifications. Signed with `X-Hub-Signature-256`. |
| `POST` | `/v1/webhooks/email` | Raw RFC 5322 message from your MTA relay. Signed by the relay, then checked with SPF and DKIM. |
| `GET` | `/v1/packets` | The broker's review queue (pending by default). Needs a Supabase Auth bearer token. |
| `GET` | `/v1/packets/{id}` | A packet with its findings, review status and SHA-256. |
| `POST` | `/v1/packets/{id}/reviews` | The broker's decision: `APPROVE`, `REQUEST_CORRECTION` or `REJECT`. |

`/docs`, `/redoc` **and** `/openapi.json` are all disabled, so the schema is not published either.

## How each requirement is met

### 1. Framework and routing
* `docs_url=None`, `redoc_url=None`, `openapi_url=None` (`triage_engine/main.py`).
* Hardening headers on every response: HSTS, `nosniff`, `frame-ancestors 'none'`, `no-store`. The `Server` header is removed and `TrustedHostMiddleware` is on.
* Error responses never echo request input. FastAPI's default 422 body would reflect PII back to the caller.

### 2. Webhook security
* **WhatsApp:** HMAC-SHA256 over the *raw* body, keyed with the Meta App Secret, and compared with `hmac.compare_digest`. A missing secret fails closed (`security/signatures.py`).
* **10 MB limit:** a pure ASGI middleware rejects an oversized `Content-Length` before reading anything. It also counts streamed or chunked bytes, so a missing or false length header can't get past the limit (`security/body_limit.py`).
* **E-mail:**
  1. The relay signs `timestamp | client IP | MAIL FROM | HELO | RCPT TO | body`, with a 5-minute replay window. The SPF inputs are part of the signed data so they can't be forged.
  2. SPF (RFC 7208, `pyspf`) must return `pass`.
  3. DKIM (RFC 6376, `dkimpy`) must verify, **and** its `d=` domain must align with the `From:` domain, as DMARC does. A message with more than one `From:` header is rejected (`security/email_auth.py`).
* **XXE:** every XML file goes through `defusedxml` with `forbid_dtd`, `forbid_entities` and `forbid_external`. Tests cover file XXE, SSRF to `169.254.169.254`, parameter entities, external DTDs and billion-laughs (`security/xml_safe.py`). A malicious CFDI rejects the whole message with 422, and the rejection is audited.
* WhatsApp media downloads only from HTTPS hosts on an allow-list, with no redirects. Downloads stream and stop at the size cap, and the SHA-256 Meta reports is checked (`services/whatsapp.py`).

### 3. Pydantic v2 extraction and math (`schemas/`, `services/extraction.py`)
* `MexicanFacturaDocument`, `BillOfLadenDocument` (the name follows the brief; `BillOfLadingDocument` is an alias) and `CrossBorderLoadTriagePacket`. Unknown fields are forbidden and models are frozen. instructor validates them in strict mode.
* **Weight normalisation:** `Decimal` arithmetic only, using the exact factor 1 lb = 0.45359237 kg and half-even rounding to 0.01 lb. Unit spellings such as `KGM`, `kilos`, `LBR` and `libras` are accepted. Pound values are `computed_field`s, so the model never does arithmetic.
* **Pallet tare:** a factura that states only net weight is grossed up by an estimated wooden-pallet tare before it is compared with the BOL's scale weight. The estimates are GMA 48×40 = 40 lb, ISO 1200×1000 or EUR = 55 lb, and unknown = 45 lb.
* **Line-item sums:**
  * Importe = Cantidad × ValorUnitario.
  * The conceptos' Importes add up to SubTotal.
  * Total = SubTotal − Descuento + traslados − retenciones.
  * Line weights add up to the total weight.
  * BOL line package counts add up to the stated total.

  When Claude transcribes a number wrong, the `ValidationError` goes back to the model and instructor asks it again.
* **Reconciliation** (`services/reconciliation.py`):

  | Check | Result |
  |---|---|
  | Gross weights differ by more than 3 % (relative to the smaller weight, so it is symmetric) | **HOLD** |
  | Carton or bulto counts differ at all | **HOLD** |
  | Pallet counts differ | REVIEW |
  | BOL cites a different invoice | REVIEW |
  | Data missing on either document | REVIEW |

  The packet's own validator refuses a disposition that contradicts its findings.
* **CFDI XML is parsed deterministically and never sent to the LLM.** That covers the Carta Porte 3.x weight and packaging codes and the Comercio Exterior tariff fractions. Driver data in the Carta Porte `FiguraTransporte` is never extracted at all.

### 4. Data privacy and storage
* **RLS:** each unit of work runs in a transaction that sets `request.jwt.claims.sub = tenant` and then runs `SET LOCAL ROLE authenticated`. The `tenant_id = (select auth.uid())` policies therefore bind the backend's own queries, and the backend login role has no table privileges of its own (`storage/db.py`, `migrations/001_init.sql`). Tenant routing goes through a `SECURITY DEFINER` function in a `private` schema that the Data API doesn't expose.
* **Bedrock zero data retention:** all model traffic goes through `AnthropicBedrockMantle` with SigV4 credentials from the task role, optionally over a PrivateLink endpoint. Bedrock does not store prompts or completions unless *model invocation logging* is on. **Startup fails if invocation logging would deliver content anywhere.** The chosen model must be one that Anthropic serves under ZDR. Claude Sonnet 5.5 is; Claude Fable models require 30-day retention.
* **PII ingestion stage** (`services/ingestion.py`, `security/pii.py`):
  * What gets tokenised: CURPs, persona-física RFCs (13 characters), labelled driver licence numbers (state licences and the Licencia Federal), WhatsApp phone numbers and envelope senders.
  * How: they are replaced with `PII:<KIND>:<HMAC-SHA256 truncated>`, using a secret key and scoped to the tenant. A plain SHA-256 of an RFC could be brute-forced.
  * What is kept: company (persona moral) RFCs and the SAT generic RFCs, because customs filings need them.
  * Why it is a pipeline stage and not a body-rewriting ASGI middleware: the HMAC and DKIM checks must see the original bytes.
  * Extra safety: logs pass through a redaction filter, and the triage packet refuses to build if any raw personal ID is left.

### Broker sign-off (human in the loop)
* **Sign-in:** a Supabase Auth access token, checked on every request against JWKS (ES256/RS256) or a legacy HS256 secret. The algorithm list is fixed so tokens can't be downgraded. Issuer, audience, expiry and `role` are all checked. The token's `sub` is the tenant, so RLS applies.
* **Approval rules:**
  * Each WARNING and CRITICAL finding must be acknowledged by code.
  * Approving a HOLD needs a written override of at least 20 characters.
  * Approvals need an MFA session (`aal2`, on by default).
  * Notes and override text are PII-tokenised.
* **Records:** each decision is archived under Object Lock, together with the SHA-256 of the exact packet the broker saw, and is chained into the audit log before the status changes.
* **Database guards** (`migrations/002_broker_review.sql`):
  * Approvals and rejections are final.
  * A status change without a matching review record is refused when the transaction commits.
  * Packet columns other than the review status can't be updated.
  * The reviewer must be the signed-in user.

### 5. Audit trail (`storage/audit.py`)
* Each tenant has its own chain. Each record holds `prev_hash` and a `record_hash` (SHA-256 over canonical JSON). `verify_chain` detects edits, deletions and reordering.
* Each record is its own S3 object, written with:
  * `ObjectLockMode=COMPLIANCE`. The default retention is 1,917 days. 19 CFR 163.4's five years run from the entry date, which comes after triage, so the default adds 90 days. The config refuses anything under 1,827 days.
  * `IfNoneMatch: *`, so a record can never be overwritten.
  * An S3-verified `ChecksumSHA256`.
  * SSE-KMS encryption.
* Every triage packet and every broker review is also archived in full under `records/v1/`. Its SHA-256 sits in a chained event, so the archive is covered by the chain.
* The chain head lives in Postgres under `SELECT … FOR UPDATE`, which serialises writers across replicas. If a record reached S3 but the database commit failed, the next writer adopts it after checking it.
* A database mirror (`audit_events`) is append-only, enforced by triggers, and the head can only move forward.
* For the NIST AI RMF (*Measure*/*Manage*), every model call records the model ID, prompt version, SHA-256 of the input and output, token counts, latency and the schema-validation result. It never records content.

## Running locally

```bash
cd services/rgv_triage_engine
pip install -r requirements-dev.txt
python -m pytest                      # unit + API tests (no AWS / DB needed)

# Optional: RLS + migration tests against a disposable Postgres 16
TRIAGE_TEST_PG_ADMIN_URL=postgresql://postgres@127.0.0.1:5432/postgres python -m pytest tests/integration

cp .env.example .env                  # fill in secrets
uvicorn --factory triage_engine.main:create_app --port 8080
```

## Deploying

1. **Supabase:**
   * Create the backend role: `CREATE ROLE triage_backend LOGIN PASSWORD '…' NOINHERIT; GRANT authenticated TO triage_backend;`
   * Apply `migrations/001_init.sql` and then `migrations/002_broker_review.sql`.
   * Require MFA (TOTP) for broker users. Set `TRIAGE_SUPABASE_JWT_ISSUER` and `TRIAGE_SUPABASE_JWKS_URL`.
   * Add rows to `private.channel_routes`, mapping each WhatsApp `phone_number_id` or inbound mailbox (lower case) to a tenant's `auth.users.id`.
   * Connect with TLS verification using Supabase's CA certificate (`TRIAGE_DATABASE_SSL_ROOT_CERT`). If you use the transaction pooler, set `TRIAGE_DATABASE_USES_TRANSACTION_POOLER=true`.
2. **Audit bucket:** Object Lock can only be turned on when the bucket is created.
   ```bash
   aws s3api create-bucket --bucket rgv-triage-audit --object-lock-enabled-for-bucket
   aws s3api put-bucket-encryption --bucket rgv-triage-audit --server-side-encryption-configuration \
     '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms","KMSMasterKeyID":"alias/rgv-audit"}}]}'
   aws s3api put-public-access-block --bucket rgv-triage-audit --public-access-block-configuration \
     BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
   ```
   The task role needs `s3:PutObject`, `s3:PutObjectRetention`, `s3:GetObject`, `s3:GetBucketObjectLockConfiguration` and `s3:GetBucketVersioning`, and nothing that deletes.
3. **Bedrock:**
   * Enable model access for `anthropic.claude-sonnet-5-5` in `us-east-1`.
   * Keep model invocation logging **off**.
   * Grant the task role `bedrock:GetModelInvocationLoggingConfiguration` (for the startup check) plus Bedrock Mantle invoke permissions.
4. **WhatsApp:** subscribe the app to the `messages` field. Point the callback at `/v1/webhooks/whatsapp` and set the App Secret, verify token and system-user access token.
5. **E-mail relay** (SES receipt rule → Lambda, or Postfix):
   * POST the raw message with these headers: `X-Relay-Timestamp`, `X-Envelope-Client-IP`, `X-Envelope-Mail-From`, `X-Envelope-Helo` and `X-Envelope-Rcpt-To`.
   * Also send `X-Relay-Signature-256: sha256=HMAC(secret, "ts\nip\nmail_from\nhelo\nrcpt_to\n" + body)`.
6. Run the container (`Dockerfile`, non-root, no writes to disk) on AWS App Runner or ECS Fargate in `us-east-1`, behind TLS and AWS WAF. Prefer these to Render: they give the service a short-lived IAM role, so no long-lived AWS keys are stored on a third-party platform. Startup refuses any non-US region.

## Design notes and limitations

* **Model:** the brief named Claude 3.5 Sonnet, which was retired on 2025-10-28. The default is its current successor, `anthropic.claude-sonnet-5-5`, and you can change it with `TRIAGE_BEDROCK_MODEL_ID`.
* **instructor:** `instructor.from_anthropic` doesn't accept the Bedrock Mantle client. Its default mode also forces `tool_choice={"type":"tool"}`, which current Claude models reject. The service therefore wraps the Mantle client with instructor's own patcher and uses `tool_choice: auto` (`build_instructor`).
* **Background work:** this uses FastAPI `BackgroundTasks`, which runs in the same process. For production volume, put SQS between the webhook and the pipeline so a restart can't drop work. The receipt is already recorded and audited before the webhook returns.
* **Pallet tares are estimates.** The 3 % threshold absorbs the leftover error. Turn the estimates into per-tenant settings if you know your pallet stock.
* **DKIM alignment** uses an organisational-domain heuristic that handles `.com.mx`. Use the Public Suffix List if you need exact DMARC semantics.
* A CFDI has no standard bulto field. The package count comes from Carta Porte packaging unit codes (`XBX`, `XCT`, …). If none are present, the load goes to REVIEW.
