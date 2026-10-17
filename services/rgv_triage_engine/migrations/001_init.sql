-- RGV Cross-Border Logistics Triage Engine: initial schema for Supabase Postgres.
--
-- Tenancy model: one tenant = one Supabase auth user (a brokerage / carrier
-- service account). Every tenant-owned row carries tenant_id, and RLS
-- restricts it to tenant_id = auth.uid(). The backend runs each unit of work
-- as role "authenticated" with request.jwt.claims.sub set to the tenant, so
-- these policies bind the service itself, not only end users.

BEGIN;

CREATE SCHEMA IF NOT EXISTS private;          -- not exposed through the Data API
REVOKE ALL ON SCHEMA private FROM PUBLIC, anon, authenticated;

-- ---------------------------------------------------------------------------
-- Backend login role (create it once per environment; set the password
-- out-of-band, e.g. from AWS Secrets Manager):
--
--   CREATE ROLE triage_backend LOGIN PASSWORD '...' NOINHERIT;
--   GRANT authenticated TO triage_backend;   -- allows SET LOCAL ROLE authenticated
--
-- triage_backend has no table privileges of its own and never BYPASSRLS.
-- ---------------------------------------------------------------------------

-- Channel routing: which tenant owns a WhatsApp number / inbound mailbox.
CREATE TABLE private.channel_routes (
    channel      text NOT NULL CHECK (channel IN ('whatsapp', 'email')),
    external_id  text NOT NULL,
    tenant_id    uuid NOT NULL REFERENCES auth.users (id) ON DELETE CASCADE,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (channel, external_id)
);

CREATE OR REPLACE FUNCTION private.resolve_channel_tenant(p_channel text, p_external_id text)
RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT tenant_id FROM private.channel_routes
    WHERE channel = p_channel AND external_id = lower(p_external_id)
$$;
REVOKE ALL ON FUNCTION private.resolve_channel_tenant(text, text) FROM PUBLIC, anon, authenticated;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'triage_backend') THEN
        GRANT USAGE ON SCHEMA private TO triage_backend;
        GRANT EXECUTE ON FUNCTION private.resolve_channel_tenant(text, text) TO triage_backend;
    END IF;
END $$;

-- ---------------------------------------------------------------------------
-- Tenant-owned tables
-- ---------------------------------------------------------------------------

CREATE TABLE public.inbound_messages (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id),
    channel              text NOT NULL CHECK (channel IN ('whatsapp', 'email')),
    external_message_id  text NOT NULL,
    payload_sha256       char(64) NOT NULL,
    sender_token         text CHECK (sender_token IS NULL OR sender_token ~ '^PII:[A-Z]+:[0-9a-f]{16}$'),
    status               text NOT NULL DEFAULT 'received'
                         CHECK (status IN ('received', 'processed', 'failed', 'rejected')),
    received_at          timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, channel, external_message_id)        -- idempotent redelivery
);

CREATE TABLE public.triage_packets (
    id                   uuid PRIMARY KEY,
    tenant_id            uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id),
    load_reference       text NOT NULL,
    disposition          text NOT NULL CHECK (disposition IN ('CLEAR', 'REVIEW', 'HOLD')),
    weight_variance_pct  numeric(9, 2),
    packet               jsonb NOT NULL,
    created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX triage_packets_tenant_created_idx ON public.triage_packets (tenant_id, created_at DESC);

CREATE TABLE public.shipping_documents (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id            uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id),
    inbound_message_id   uuid NOT NULL REFERENCES public.inbound_messages (id),
    doc_type             text NOT NULL CHECK (doc_type IN ('FACTURA', 'BILL_OF_LADING')),
    source_sha256        char(64) NOT NULL,
    doc_references       text[] NOT NULL DEFAULT '{}',
    extracted            jsonb NOT NULL,
    packet_id            uuid REFERENCES public.triage_packets (id),
    created_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX shipping_documents_refs_idx ON public.shipping_documents USING gin (doc_references);
CREATE INDEX shipping_documents_unpaired_idx
    ON public.shipping_documents (tenant_id, doc_type, created_at DESC) WHERE packet_id IS NULL;

-- Audit chain head (one row per tenant), locked FOR UPDATE by writers.
CREATE TABLE public.audit_chain_heads (
    tenant_id      uuid PRIMARY KEY DEFAULT auth.uid() REFERENCES auth.users (id),
    last_sequence  bigint NOT NULL CHECK (last_sequence >= 0),
    last_hash      char(64) NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Queryable index of the S3 audit chain. S3 Object Lock is the system of
-- record; this mirror is append-only as well.
CREATE TABLE public.audit_events (
    tenant_id    uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id),
    sequence     bigint NOT NULL CHECK (sequence > 0),
    record_hash  char(64) NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, sequence)
);

CREATE OR REPLACE FUNCTION private.reject_mutation()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    RAISE EXCEPTION 'audit records are append-only (% on %)', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'insufficient_privilege';
END;
$$;

CREATE TRIGGER audit_events_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON public.audit_events
    FOR EACH STATEMENT EXECUTE FUNCTION private.reject_mutation();

-- The chain head may only move forward by contiguous sequence numbers.
CREATE OR REPLACE FUNCTION private.enforce_head_forward()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.tenant_id <> OLD.tenant_id OR NEW.last_sequence <= OLD.last_sequence THEN
        RAISE EXCEPTION 'audit chain head can only advance' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER audit_chain_heads_forward_only
    BEFORE UPDATE ON public.audit_chain_heads
    FOR EACH ROW EXECUTE FUNCTION private.enforce_head_forward();

CREATE TRIGGER audit_chain_heads_no_delete
    BEFORE DELETE OR TRUNCATE ON public.audit_chain_heads
    FOR EACH STATEMENT EXECUTE FUNCTION private.reject_mutation();

-- ---------------------------------------------------------------------------
-- Row-Level Security
-- ---------------------------------------------------------------------------

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'inbound_messages', 'shipping_documents', 'triage_packets',
        'audit_chain_heads', 'audit_events'
    ] LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('REVOKE ALL ON public.%I FROM PUBLIC, anon', t);
        -- (select auth.uid()) is evaluated once per statement, not per row.
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON public.%I FOR ALL TO authenticated '
            'USING (tenant_id = (SELECT auth.uid())) '
            'WITH CHECK (tenant_id = (SELECT auth.uid()))', t);
    END LOOP;
END $$;

GRANT SELECT, INSERT, UPDATE ON public.inbound_messages   TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.shipping_documents TO authenticated;
GRANT SELECT, INSERT         ON public.triage_packets     TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.audit_chain_heads  TO authenticated;
GRANT SELECT, INSERT         ON public.audit_events       TO authenticated;

COMMIT;
