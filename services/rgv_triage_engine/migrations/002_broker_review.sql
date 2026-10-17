-- Human-in-the-loop broker sign-off.
--
-- The engine only proofreads. A packet stays PENDING_BROKER_REVIEW until a
-- licensed broker records a decision; approvals and rejections are final.

BEGIN;

ALTER TABLE public.triage_packets
    ADD COLUMN review_status text NOT NULL DEFAULT 'PENDING_BROKER_REVIEW'
        CHECK (review_status IN ('PENDING_BROKER_REVIEW', 'CORRECTION_REQUESTED',
                                 'APPROVED_BY_BROKER', 'REJECTED_BY_BROKER')),
    ADD COLUMN reviewed_at timestamptz;

CREATE INDEX triage_packets_review_queue_idx
    ON public.triage_packets (tenant_id, review_status, created_at DESC);

-- Only the review columns may ever be updated on a packet.
GRANT UPDATE (review_status, reviewed_at) ON public.triage_packets TO authenticated;

CREATE TABLE public.packet_reviews (
    id                     uuid PRIMARY KEY,
    tenant_id              uuid NOT NULL DEFAULT auth.uid() REFERENCES auth.users (id),
    packet_id              uuid NOT NULL REFERENCES public.triage_packets (id),
    reviewer_id            uuid NOT NULL REFERENCES auth.users (id),
    decision               text NOT NULL CHECK (decision IN ('APPROVE', 'REQUEST_CORRECTION', 'REJECT')),
    resulting_status       text NOT NULL,
    engine_disposition     text NOT NULL CHECK (engine_disposition IN ('CLEAR', 'REVIEW', 'HOLD')),
    packet_sha256          char(64) NOT NULL,
    acknowledged_findings  text[] NOT NULL DEFAULT '{}',
    override_reason        text,
    notes                  text,
    archive_key            text NOT NULL,
    created_at             timestamptz NOT NULL DEFAULT now(),
    CHECK (decision <> 'APPROVE' OR engine_disposition <> 'HOLD'
           OR length(coalesce(override_reason, '')) >= 20)
);
CREATE INDEX packet_reviews_packet_idx ON public.packet_reviews (packet_id, created_at);

CREATE TRIGGER packet_reviews_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON public.packet_reviews
    FOR EACH STATEMENT EXECUTE FUNCTION private.reject_mutation();

ALTER TABLE public.packet_reviews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.packet_reviews FORCE ROW LEVEL SECURITY;
REVOKE ALL ON public.packet_reviews FROM PUBLIC, anon;
CREATE POLICY tenant_isolation ON public.packet_reviews FOR ALL TO authenticated
    USING (tenant_id = (SELECT auth.uid()))
    WITH CHECK (tenant_id = (SELECT auth.uid()) AND reviewer_id = (SELECT auth.uid()));
GRANT SELECT, INSERT ON public.packet_reviews TO authenticated;

-- Approved / rejected packets are final.
CREATE OR REPLACE FUNCTION private.enforce_review_transition()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF OLD.review_status IN ('APPROVED_BY_BROKER', 'REJECTED_BY_BROKER')
       AND NEW.review_status IS DISTINCT FROM OLD.review_status THEN
        RAISE EXCEPTION 'packet review is final (%)', OLD.review_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER triage_packets_review_transition
    BEFORE UPDATE OF review_status ON public.triage_packets
    FOR EACH ROW EXECUTE FUNCTION private.enforce_review_transition();

-- A status change must be backed by a review row written in the same
-- transaction (checked at commit, so the UPDATE may come first).
CREATE OR REPLACE FUNCTION private.require_review_row()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
    IF NEW.review_status IS DISTINCT FROM OLD.review_status AND NOT EXISTS (
        SELECT 1 FROM public.packet_reviews r
        WHERE r.packet_id = NEW.id
          AND r.resulting_status = NEW.review_status
          AND r.created_at = now()
    ) THEN
        RAISE EXCEPTION 'review_status changed without a packet_reviews record'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$;

CREATE CONSTRAINT TRIGGER triage_packets_review_backed
    AFTER UPDATE OF review_status ON public.triage_packets
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION private.require_review_row();

COMMIT;
