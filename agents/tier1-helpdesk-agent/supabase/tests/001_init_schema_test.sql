-- Behavioural checks for 001_init_schema.sql. Run against a scratch database
-- (never production) after applying the migration:
--   psql "$SCRATCH_DB_URL" -v ON_ERROR_STOP=1 -f supabase/tests/001_init_schema_test.sql
-- Everything runs in one transaction that is rolled back at the end.

begin;
set search_path = public, extensions;

-- Unit vectors along axis i (1-based) and a blend of axes 1 and 2.
create function pg_temp.axis(i int) returns vector language sql as $$
    select ('[' || array_to_string(
        array(select case when g = i then 1.0 else 0.0 end from generate_series(1, 1024) g), ',') || ']')::vector;
$$;
create function pg_temp.blend(w float) returns vector language sql as $$
    select ('[' || array_to_string(
        array(select case when g = 1 then w when g = 2 then sqrt(1 - w * w) else 0.0 end
              from generate_series(1, 1024) g), ',') || ']')::vector;
$$;

-- ---------------------------------------------------------------------------
-- SOP retrieval: tenant isolation + os_platform filter
-- ---------------------------------------------------------------------------
insert into sop_chunks (id, tenant_id, doc_id, title, os_platform, chunk_index, content, content_hash, embedding) values
 ('acme:SOP-VPN-WIN#0', 'acme', 'SOP-VPN-WIN', 'VPN on Windows', 'windows', 0, 'win steps', 'h1', pg_temp.axis(1)),
 ('acme:SOP-VPN-MAC#0', 'acme', 'SOP-VPN-MAC', 'VPN on macOS',   'macos',   0, 'mac steps', 'h2', pg_temp.axis(1)),
 ('acme:SOP-GEN#0',     'acme', 'SOP-GEN',     'General',        'any',     0, 'any steps', 'h3', pg_temp.axis(1)),
 ('globex:SOP-X#0',     'globex','SOP-X',      'Other tenant',   'any',     0, 'secret',    'h4', pg_temp.axis(1));

do $$
declare ids text[];
begin
    select array_agg(doc_id order by doc_id) into ids
    from match_sop_chunks('acme', pg_temp.axis(1), 10, 0.5, 'macos');
    assert ids = array['SOP-GEN','SOP-VPN-MAC'], format('os filter wrong: %s', ids);

    select array_agg(doc_id order by doc_id) into ids from match_sop_chunks('acme', pg_temp.axis(1), 10, 0.5, null);
    assert ids = array['SOP-GEN','SOP-VPN-MAC','SOP-VPN-WIN'], format('no-filter wrong: %s', ids);
    assert not ('SOP-X' = any(ids)), 'cross-tenant leak';

    assert (select count(*) from match_sop_chunks('acme', pg_temp.axis(2), 10, 0.5, null)) = 0,
        'similarity threshold not applied';
end $$;

-- ---------------------------------------------------------------------------
-- Outage burst: 5 similar tickets in window -> one incident for the cluster
-- ---------------------------------------------------------------------------
insert into recent_tickets (tenant_id, ticket_id, summary, embedding, created_at)
select 'acme', 'T' || g, 'VPN down', pg_temp.blend(0.95 + g * 0.005), now() - interval '5 minutes'
from generate_series(1, 5) g;
-- Old ticket outside the 30-minute window, and an unrelated one.
insert into recent_tickets (tenant_id, ticket_id, summary, embedding, created_at) values
 ('acme', 'OLD', 'VPN down', pg_temp.axis(1), now() - interval '45 minutes'),
 ('acme', 'PRN', 'printer',  pg_temp.axis(3), now()),
 ('acme', 'NEW', 'VPN down', pg_temp.axis(1), now()),
 ('globex', 'G1', 'VPN down', pg_temp.axis(1), now());

do $$
declare
    matched text[];
    r1 record; r2 record;
begin
    select array_agg(ticket_id order by ticket_id) into matched
    from match_recent_tickets('acme', pg_temp.axis(1), 30, 0.88, 'NEW');
    assert matched = array['T1','T2','T3','T4','T5'], format('burst match wrong: %s', matched);
    update recent_tickets set category = 'vpn_network' where tenant_id = 'acme' and ticket_id = 'T3';
    assert (select array_agg(category) filter (where category is not null)
            from match_recent_tickets('acme', pg_temp.axis(1), 30, 0.88, 'NEW')) = array['vpn_network'],
        'category not returned for cluster inheritance';

    select * into r1 from register_outage_cluster('acme', 'NEW', matched, 'VPN outage');
    assert r1.incident_id is not null and r1.is_new and r1.needs_tier2_notification;
    assert (select ticket_count from outage_incidents where incident_id = r1.incident_id) = 6, 'ticket_count wrong';

    -- A later ticket joins the same active incident instead of opening a new one.
    insert into recent_tickets (tenant_id, ticket_id, summary, embedding) values ('acme', 'NEW2', 'VPN', pg_temp.axis(1));
    select * into r2 from register_outage_cluster('acme', 'NEW2', array['T1','NEW'], 'VPN outage again');
    assert r2.incident_id = r1.incident_id and not r2.is_new, 'second burst opened a duplicate incident';
    assert r2.needs_tier2_notification, 'notification flag cleared before delivery';
    assert (select count(*) from outage_incidents where tenant_id = 'acme') = 1;

    -- Exemption control: Tier 2 is told exactly once; other tenants can't mark it.
    assert not mark_outage_tier2_notified('globex', r1.incident_id), 'cross-tenant mark';
    assert mark_outage_tier2_notified('acme', r1.incident_id);
    assert not mark_outage_tier2_notified('acme', r1.incident_id), 'marked twice';
    select * into r2 from register_outage_cluster('acme', 'NEW2', array['T1'], 'x');
    assert not r2.needs_tier2_notification;
end $$;

-- ---------------------------------------------------------------------------
-- Budget: atomic reservations enforce the hard cap
-- ---------------------------------------------------------------------------
do $$
declare r1 uuid; r2 uuid; r3 uuid;
begin
    r1 := try_reserve_llm_budget('acme', 'T1', 'triage', 0.40, 1.00);
    r2 := try_reserve_llm_budget('acme', 'T2', 'triage', 0.40, 1.00);
    r3 := try_reserve_llm_budget('acme', 'T3', 'triage', 0.40, 1.00);   -- 1.20 > 1.00
    assert r1 is not null and r2 is not null and r3 is null, 'cap not enforced on reservations';

    assert settle_llm_budget('acme', r1, 'claude-haiku-5-5', 0.01, 1000, 200);
    assert not settle_llm_budget('acme', r1, 'claude-haiku-5-5', 0.01), 'double settle allowed';
    assert not settle_llm_budget('globex', r2, 'x', 0), 'cross-tenant settle allowed';
    assert tenant_month_spend('acme') = 0.41, format('spend wrong: %s', tenant_month_spend('acme'));

    -- Freed headroom is reusable.
    assert try_reserve_llm_budget('acme', 'T3', 'triage', 0.40, 1.00) is not null;
    -- Stale reservations (crashed task) stop counting after 15 minutes.
    update llm_usage set created_at = now() - interval '20 minutes' where id = r2;
    assert tenant_month_spend('acme') = 0.41, format('stale reservation still counted: %s', tenant_month_spend('acme'));
end $$;

-- ---------------------------------------------------------------------------
-- Conversation: clarifying questions capped at 2 (function + CHECK)
-- ---------------------------------------------------------------------------
insert into conversation_threads (tenant_id, thread_id, ticket_id, reporter_email, source_system)
values ('acme', 'TH1', 'T1', 'pat@acme.com', 'slack');

do $$
begin
    assert increment_clarification('acme', 'TH1', 2) = 1;
    assert increment_clarification('acme', 'TH1', 2) = 2;
    assert increment_clarification('acme', 'TH1', 2) is null, 'third clarification allowed';
    assert increment_clarification('acme', 'TH1', 99) is null, 'p_max above 2 bypassed the cap';
    begin
        update conversation_threads set clarification_count = 3 where thread_id = 'TH1';
        assert false, 'CHECK allowed clarification_count = 3';
    exception when check_violation then null;
    end;
end $$;

-- ---------------------------------------------------------------------------
-- Approvals: decided once, only by the approver, only with the nonce
-- ---------------------------------------------------------------------------
insert into app_catalog (tenant_id, item_key, item_type, display_name, requires_manager_approval)
values ('acme', 'figma', 'software', 'Figma', true);
insert into approval_requests (tenant_id, thread_id, ticket_id, requester_email, approver_email,
                               item_type, item_key, item_display_name)
values ('acme', 'TH1', 'T1', 'pat@acme.com', 'boss@acme.com', 'software', 'figma', 'Figma');

do $$
declare a approval_requests;
begin
    select * into a from approval_requests where tenant_id = 'acme' limit 1;
    assert not exists (select 1 from decide_approval('acme', a.approval_id, 'wrong-nonce', 'approved', 'boss@acme.com')),
        'wrong nonce accepted';
    assert not exists (select 1 from decide_approval('acme', a.approval_id, a.callback_nonce, 'approved', 'pat@acme.com')),
        'requester approved own request';
    assert not exists (select 1 from decide_approval('globex', a.approval_id, a.callback_nonce, 'approved', 'boss@acme.com')),
        'cross-tenant decision accepted';
    assert exists (select 1 from decide_approval('acme', a.approval_id, a.callback_nonce, 'approved', 'BOSS@acme.com')),
        'valid decision rejected';
    assert not exists (select 1 from decide_approval('acme', a.approval_id, a.callback_nonce, 'denied', 'boss@acme.com')),
        'decision replay accepted';
    begin
        insert into approval_requests (tenant_id, thread_id, ticket_id, requester_email, approver_email,
                                       item_type, item_key, item_display_name)
        values ('globex', 'TH1', 'T1', 'x@globex.com', 'y@globex.com', 'software', 'figma', 'Figma');
        assert false, 'approval linked to another tenant''s thread';
    exception when foreign_key_violation then null;
    end;
end $$;

-- ---------------------------------------------------------------------------
-- Telemetry: ROI counts each ticket once; gaps are normalised
-- ---------------------------------------------------------------------------
insert into ticket_telemetry (tenant_id, ticket_id, service_tier, category, action, scope, confidence_score,
                              deflected_bool, estimated_minutes_saved, missing_sop_topic, token_cost_usd) values
 ('acme', 'A', 'TIER_0_INCENTIVE', 'vpn_network', 'ASK_CLARIFICATION', 'in_scope', 0.6, false, 0, null, 0.002),
 ('acme', 'A', 'TIER_0_INCENTIVE', 'vpn_network', 'RESOLVE',           'in_scope', 0.9, true, 15, null, 0.002),
 ('acme', 'B', 'TIER_0_INCENTIVE', 'software_access', 'ESCALATE_TIER_2', 'in_scope', 0.5, false, 0, 'Adobe licence', 0.003),
 ('acme', 'C', 'TIER_0_INCENTIVE', 'software_access', 'ESCALATE_TIER_2', 'in_scope', 0.5, false, 0, ' adobe LICENCE ', 0.003),
 ('acme', 'D', 'TIER_0_INCENTIVE', 'unknown', 'ESCALATE_TIER_2', 'in_scope', 0.4, false, 0, 'MFA on new phone', 0.001),
 ('globex', 'Z', 'TIER_0_INCENTIVE', 'unknown', 'RESOLVE', 'in_scope', 0.9, true, 15, 'leak', 1.0);

do $$
declare s record; g record;
begin
    select * into s from tenant_roi_summary('acme');
    assert s.unique_tickets = 4 and s.total_triages = 5, format('counts wrong: %s', s);
    assert s.deflected_tickets = 1 and s.deflection_rate_pct = 25.0, format('deflection wrong: %s', s);
    assert s.hours_saved = 0.25, format('hours wrong: %s', s);
    select * into g from tenant_knowledge_gaps('acme') limit 1;
    assert g.topic = 'adobe licence' and g.ticket_count = 2, format('gap normalisation wrong: %s', g);
    assert (select count(*) from tenant_knowledge_gaps('acme')) = 2;

    begin
        insert into ticket_telemetry (tenant_id, ticket_id, service_tier, category, action, scope,
                                      confidence_score, deflected_bool, estimated_minutes_saved)
        values ('acme', 'E', 'TIER_0_INCENTIVE', 'unknown', 'ESCALATE_TIER_2', 'in_scope', 0.5, false, 15);
        assert false, 'minutes credited to a non-deflected ticket';
    exception when check_violation then null;
    end;
end $$;

-- ---------------------------------------------------------------------------
-- SOP drafts: frontmatter doc_id must match the row; review delivery is tracked
-- ---------------------------------------------------------------------------
do $$
begin
    insert into sop_drafts (tenant_id, source_ticket_id, doc_id, title, markdown, frontmatter)
    values ('acme', 'T-RN', 'SOP-RN-1', 't', '# t', '{"doc_id": "SOP-RN-1"}');
    assert (select review_notified_at from sop_drafts where doc_id = 'SOP-RN-1') is null,
        'new drafts must start un-notified so the agent retries delivery';
    begin
        insert into sop_drafts (tenant_id, source_ticket_id, doc_id, title, markdown, frontmatter)
        values ('acme', 'B', 'SOP-NEW-001', 't', '# t', '{"doc_id": "SOP-OTHER"}');
        assert false, 'mismatched frontmatter accepted';
    exception when check_violation then null;
    end;
end $$;

-- ---------------------------------------------------------------------------
-- RLS: tenant JWTs read only their rows; anon reads nothing; nonce hidden
-- ---------------------------------------------------------------------------
set local role authenticated;
set local request.jwt.claims = '{"app_metadata": {"tenant_id": "acme"}}';
do $$
begin
    assert (select count(*) from sop_chunks) = 3, 'authenticated saw another tenant''s SOPs';
    assert (select count(distinct tenant_id) from ticket_telemetry) = 1;
    assert (select unique_tickets from tenant_roi_summary('globex')) = 0, 'report leaked another tenant';
    begin
        perform callback_nonce from approval_requests;
        assert false, 'nonce readable by tenant JWT';
    exception when insufficient_privilege then null;
    end;
    begin
        insert into ticket_telemetry (tenant_id, ticket_id, service_tier, category, action, scope,
                                      confidence_score, deflected_bool)
        values ('acme', 'X', 'TIER_0_INCENTIVE', 'unknown', 'RESOLVE', 'in_scope', 0.9, true);
        assert false, 'tenant JWT could write telemetry';
    exception when insufficient_privilege then null;
    end;
    begin
        perform try_reserve_llm_budget('acme', 'X', 'triage', 0, 100);
        assert false, 'tenant JWT could call budget function';
    exception when insufficient_privilege then null;
    end;
end $$;

-- user_metadata is user-editable, so it must not grant access.
set local request.jwt.claims = '{"user_metadata": {"tenant_id": "acme"}}';
do $$ begin assert (select count(*) from sop_chunks) = 0, 'user_metadata granted tenant access'; end $$;

reset role;
set local role anon;
do $$
begin
    begin
        perform 1 from sop_chunks;
        assert false, 'anon can read sop_chunks';
    exception when insufficient_privilege then null;
    end;
    begin
        perform * from tenant_roi_summary('acme');
        assert false, 'anon can run reports';
    exception when insufficient_privilege then null;
    end;
end $$;
reset role;

-- ---------------------------------------------------------------------------
-- Housekeeping: an unanswered approval expires AND escalates its thread
-- (the approval-wait exemption from guardrail #2 ends with the approval)
-- ---------------------------------------------------------------------------
update conversation_threads set status = 'awaiting_approval', last_action = 'AWAIT_MANAGER_APPROVAL'
 where tenant_id = 'acme' and thread_id = 'TH1';
update approval_requests set status = 'pending', decided_at = null, decided_by = null,
       expires_at = now() - interval '1 minute';
select run_housekeeping();
do $$
declare t conversation_threads;
begin
    assert (select status from approval_requests limit 1) = 'expired';
    select * into t from conversation_threads where tenant_id = 'acme' and thread_id = 'TH1';
    assert t.status = 'escalated' and t.last_action = 'ESCALATE_TIER_2', format('thread not escalated: %s', t.status);
    assert t.escalation_reason = 'approval_expired' and t.escalation_notified_at is null;
end $$;

\echo 'ALL SCHEMA CHECKS PASSED'
rollback;
