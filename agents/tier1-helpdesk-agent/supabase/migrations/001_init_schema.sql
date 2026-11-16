-- =============================================================================
-- Tier 1 IT Help Desk Agent: initial schema (all service tiers)
--
-- Apply with `supabase db push`, or paste into the Supabase SQL editor.
--
-- Isolation model (defense in depth):
--   1. Every row carries tenant_id. Every function takes p_tenant_id and filters
--      on it inside SQL; application code never filters rows itself.
--   2. Cross-table links use composite (tenant_id, id) foreign keys, so a row can
--      never point at another tenant's row.
--   3. RLS is enabled on every table. The agent uses the service role, which
--      bypasses RLS. Any other caller (a client dashboard, n8n with a user JWT)
--      sees only rows whose tenant_id matches the tenant_id in its JWT's
--      app_metadata, and is read-only.
--   4. EXECUTE on functions is revoked from anon/authenticated (Supabase grants it
--      by default) and granted back only where it's safe.
--
-- Vector search runs as an exact scan inside one tenant's rows (a MATERIALIZED
-- CTE keeps the planner off an ANN index). An ANN index with a tenant filter can
-- silently return fewer than k rows, because it filters after the index scan. An
-- exact scan is fast to roughly 100k chunks per tenant. Past that, add HNSW with
-- pgvector >= 0.8 iterative scans.
-- =============================================================================

create schema if not exists extensions;
create extension if not exists vector with schema extensions;
create extension if not exists pgcrypto with schema extensions;

set search_path = public, extensions;

-- -----------------------------------------------------------------------------
-- Helpers
-- -----------------------------------------------------------------------------

-- Tenant of the calling JWT. app_metadata is set server-side only, unlike
-- user_metadata, which the end user can edit.
create or replace function public.request_tenant_id()
returns text
language sql stable
set search_path = public
as $$
    select nullif(coalesce(auth.jwt() -> 'app_metadata' ->> 'tenant_id', ''), '');
$$;

create or replace function public.set_updated_at()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    new.updated_at := now();
    return new;
end;
$$;

-- Month start (UTC) used by budget and reporting functions.
create or replace function public.utc_month_start()
returns timestamptz
language sql stable
as $$
    select date_trunc('month', now() at time zone 'utc') at time zone 'utc';
$$;

-- =============================================================================
-- 1. Knowledge base: SOP chunks (RAG)
-- =============================================================================

create table if not exists public.sop_chunks (
    id                 text primary key,                 -- "<tenant>:<doc_id>#<chunk_index>"
    tenant_id          text not null check (tenant_id ~ '^[a-z0-9-]{2,64}$'),
    doc_id             text not null check (length(doc_id) between 1 and 128),
    title              text not null,
    system_name        text,                             -- e.g. "GlobalProtect", "Entra ID"
    os_platform        text not null default 'any'
                       check (os_platform in ('any','windows','macos','linux','ios','android','chromeos')),
    source             text not null default 'ingest' check (source in ('ingest','flywheel')),
    chunk_index        int  not null check (chunk_index >= 0),
    content            text not null,
    content_hash       text not null,
    embedding          vector(1024) not null,            -- must match EMBEDDING_DIMENSIONS
    last_verified_date date,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    unique (tenant_id, doc_id, chunk_index)
);

create index if not exists sop_chunks_tenant_platform_idx on public.sop_chunks (tenant_id, os_platform);
create index if not exists sop_chunks_tenant_doc_idx on public.sop_chunks (tenant_id, doc_id);

drop trigger if exists sop_chunks_updated_at on public.sop_chunks;
create trigger sop_chunks_updated_at before update on public.sop_chunks
    for each row execute function public.set_updated_at();

-- p_os_platform (Tier 1 context enrichment) is a hard metadata filter.
-- Platform-agnostic SOPs ('any') always qualify.
create or replace function public.match_sop_chunks(
    p_tenant_id       text,
    p_query_embedding vector(1024),
    p_match_count     int   default 4,
    p_min_similarity  float default 0.55,
    p_os_platform     text  default null
)
returns table (
    doc_id      text,
    title       text,
    system_name text,
    os_platform text,
    content     text,
    similarity  float
)
language sql stable
set search_path = public, extensions
as $$
    with candidates as materialized (
        select c.doc_id, c.title, c.system_name, c.os_platform, c.content,
               1 - (c.embedding <=> p_query_embedding) as similarity
        from public.sop_chunks c
        where c.tenant_id = p_tenant_id
          and (p_os_platform is null or c.os_platform in ('any', p_os_platform))
    )
    select doc_id, title, system_name, os_platform, content, similarity
    from candidates
    where similarity >= p_min_similarity
    order by similarity desc
    limit least(greatest(p_match_count, 1), 20);
$$;

-- =============================================================================
-- 2. Application / hardware catalog (Tier 1 RBAC approvals)
--    Structured on purpose: whether an item needs approval is decided by this
--    table, never by the model's reading of a document.
-- =============================================================================

create table if not exists public.app_catalog (
    tenant_id                 text not null,
    item_key                  text not null check (item_key ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
    item_type                 text not null check (item_type in ('software','hardware')),
    display_name              text not null,
    aliases                   text[] not null default '{}',
    requires_manager_approval boolean not null default true,
    approver_role             text not null default 'direct_manager'
                              check (approver_role in ('direct_manager','app_owner','it_manager')),
    app_owner_email           text check (app_owner_email is null or app_owner_email = lower(app_owner_email)),
    provisioning_group        text,                      -- Entra ID / Okta group granted on approval
    license_cost_usd          numeric(10,2) check (license_cost_usd is null or license_cost_usd >= 0),
    doc_id                    text,                      -- SOP describing the item
    active                    boolean not null default true,
    created_at                timestamptz not null default now(),
    updated_at                timestamptz not null default now(),
    primary key (tenant_id, item_key)
);

create index if not exists app_catalog_aliases_idx on public.app_catalog using gin (aliases);

drop trigger if exists app_catalog_updated_at on public.app_catalog;
create trigger app_catalog_updated_at before update on public.app_catalog
    for each row execute function public.set_updated_at();

-- =============================================================================
-- 3. Cached identity / MDM directory (Tier 1 context enrichment)
-- =============================================================================

create table if not exists public.tenant_directory (
    tenant_id            text not null,
    email                text not null check (email = lower(email)),
    display_name         text,
    department           text,
    manager_email        text check (manager_email is null or manager_email = lower(manager_email)),
    os_platform          text not null default 'unknown'
                         check (os_platform in ('unknown','windows','macos','linux','ios','android','chromeos')),
    vip_status           boolean not null default false,
    assigned_hardware    jsonb not null default '[]'::jsonb check (jsonb_typeof(assigned_hardware) = 'array'),
    recent_auth_failures int not null default 0 check (recent_auth_failures >= 0),
    source               text not null default 'manual' check (source in ('manual','microsoft_graph','okta')),
    synced_at            timestamptz not null default now(),
    updated_at           timestamptz not null default now(),
    primary key (tenant_id, email)
);

drop trigger if exists tenant_directory_updated_at on public.tenant_directory;
create trigger tenant_directory_updated_at before update on public.tenant_directory
    for each row execute function public.set_updated_at();

-- =============================================================================
-- 4. Outage burst detection (Tier 0)
-- =============================================================================

create table if not exists public.outage_incidents (
    incident_id       uuid not null default gen_random_uuid() primary key,
    tenant_id         text not null,
    title             text not null,
    status            text not null default 'active' check (status in ('active','resolved')),
    ticket_count      int  not null default 0 check (ticket_count >= 0),
    broadcast_message text,
    first_seen_at     timestamptz not null default now(),
    last_seen_at      timestamptz not null default now(),
    resolved_at       timestamptz,
    -- Guardrail #2 exemption control: outage broadcasts skip per-ticket escalation
    -- only because the parent incident itself goes to Tier 2. Set once the Tier 2
    -- notification is delivered; the app retries while it is null.
    tier2_notified_at timestamptz,
    unique (tenant_id, incident_id)
);

-- Upgrades: when the column is new, incidents that already exist were handled
-- under the old flow. Mark them notified once, so the retry loop doesn't page
-- Tier 2 for all of history.
do $$
begin
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'outage_incidents'
                     and column_name = 'tier2_notified_at') then
        alter table public.outage_incidents add column tier2_notified_at timestamptz;
        update public.outage_incidents set tier2_notified_at = last_seen_at;
    end if;
end;
$$;
-- The cluster's triaged category (so a retried notice can still say what it is),
-- and a short lease so concurrent tickets and workers send one notice, not several.
alter table public.outage_incidents add column if not exists category text;
alter table public.outage_incidents add column if not exists tier2_notify_claimed_at timestamptz;

create index if not exists outage_incidents_active_idx
    on public.outage_incidents (tenant_id, last_seen_at desc) where status = 'active';
create index if not exists outage_incidents_unnotified_idx
    on public.outage_incidents (tenant_id, first_seen_at) where tier2_notified_at is null;

-- Short-lived ticket embeddings. Kept 7 days (see run_housekeeping); detection
-- only looks back 30 minutes.
create table if not exists public.recent_tickets (
    id                 uuid not null default gen_random_uuid() primary key,
    tenant_id          text not null,
    ticket_id          text not null,
    thread_id          text,
    reporter_email     text,
    category           text,
    summary            text not null check (length(summary) <= 500),
    embedding          vector(1024) not null,
    parent_incident_id uuid,
    created_at         timestamptz not null default now(),
    unique (tenant_id, ticket_id),
    foreign key (tenant_id, parent_incident_id)
        references public.outage_incidents (tenant_id, incident_id) on delete set null (parent_incident_id)
);

alter table public.recent_tickets add column if not exists reporter_email text;

create index if not exists recent_tickets_window_idx on public.recent_tickets (tenant_id, created_at desc);

-- Tickets from the same tenant in the last p_window_minutes that look alike.
-- category is the earlier triage result, so a cluster's nature (e.g. a phishing
-- wave) carries over to tickets that skip the model.
drop function if exists public.match_recent_tickets(text, vector, int, float, text, int);
create function public.match_recent_tickets(
    p_tenant_id         text,
    p_query_embedding   vector(1024),
    p_window_minutes    int   default 30,
    p_min_similarity    float default 0.88,
    p_exclude_ticket_id text  default null,
    p_limit             int   default 50
)
returns table (
    ticket_id          text,
    summary            text,
    category           text,
    parent_incident_id uuid,
    similarity         float,
    created_at         timestamptz,
    reporter_email     text
)
language sql stable
set search_path = public, extensions
as $$
    with window_rows as materialized (
        select t.ticket_id, t.summary, t.category, t.parent_incident_id, t.created_at, t.reporter_email,
               1 - (t.embedding <=> p_query_embedding) as similarity
        from public.recent_tickets t
        where t.tenant_id = p_tenant_id
          and t.created_at >= now() - make_interval(mins => greatest(p_window_minutes, 1))
          and (p_exclude_ticket_id is null or t.ticket_id <> p_exclude_ticket_id)
    )
    select ticket_id, summary, category, parent_incident_id, similarity, created_at, reporter_email
    from window_rows
    where similarity >= p_min_similarity
    order by similarity desc
    limit least(greatest(p_limit, 1), 200);
$$;

-- Open (or join) an outage for a cluster of matching tickets. A per-tenant
-- advisory lock means a burst of simultaneous tickets yields one incident.
-- needs_tier2_notification is true for the one caller that should send the
-- notice: it claims a 2-minute lease, so the other tickets of the same burst
-- don't each send their own. mark_outage_tier2_notified ends it for good.
drop function if exists public.register_outage_cluster(text, text, text[], text);
drop function if exists public.register_outage_cluster(text, text, text[], text, text);
create function public.register_outage_cluster(
    p_tenant_id          text,
    p_ticket_id          text,
    p_matched_ticket_ids text[],
    p_title              text,
    p_category           text default null
)
returns table (incident_id uuid, is_new boolean, needs_tier2_notification boolean)
language plpgsql
set search_path = public
as $$
#variable_conflict use_column
declare
    v_incident uuid;
    v_new      boolean := false;
    v_claimed  boolean := false;
    v_ids      text[] := array_append(coalesce(p_matched_ticket_ids, '{}'), p_ticket_id);
begin
    perform pg_advisory_xact_lock(hashtextextended('outage:' || p_tenant_id, 0));

    select t.parent_incident_id into v_incident
    from public.recent_tickets t
    join public.outage_incidents i
      on i.tenant_id = t.tenant_id and i.incident_id = t.parent_incident_id
    where t.tenant_id = p_tenant_id
      and t.ticket_id = any (v_ids)
      and i.status = 'active'
    order by i.last_seen_at desc
    limit 1;

    if v_incident is null then
        insert into public.outage_incidents (tenant_id, title, category)
        values (p_tenant_id, left(coalesce(nullif(btrim(p_title), ''), 'Possible service outage'), 200),
                nullif(btrim(p_category), ''))
        returning outage_incidents.incident_id into v_incident;
        v_new := true;
    end if;

    update public.recent_tickets
       set parent_incident_id = v_incident
     where tenant_id = p_tenant_id
       and ticket_id = any (v_ids)
       and parent_incident_id is distinct from v_incident;

    update public.outage_incidents i
       set ticket_count = (select count(*) from public.recent_tickets t
                            where t.tenant_id = p_tenant_id and t.parent_incident_id = v_incident),
           last_seen_at = now(),
           category = coalesce(i.category, nullif(btrim(p_category), ''))
     where i.tenant_id = p_tenant_id and i.incident_id = v_incident;

    with claim as (
        update public.outage_incidents i
           set tier2_notify_claimed_at = now()
         where i.tenant_id = p_tenant_id and i.incident_id = v_incident
           and i.tier2_notified_at is null
           and (i.tier2_notify_claimed_at is null or i.tier2_notify_claimed_at < now() - interval '2 minutes')
        returning 1
    )
    select exists (select 1 from claim) into v_claimed;

    return query select v_incident, v_new, v_claimed;
end;
$$;

-- Incidents still owed a Tier 2 notice, claimed for p_lease_seconds so that
-- several workers running the retry loop don't all send it.
create or replace function public.claim_pending_outage_notifications(
    p_tenant_id     text,
    p_lease_seconds int default 120
)
returns table (incident_id uuid, title text, ticket_count int, category text)
language sql
set search_path = public
as $$
    update public.outage_incidents i
       set tier2_notify_claimed_at = now()
     where i.tenant_id = p_tenant_id
       and i.tier2_notified_at is null
       and (i.tier2_notify_claimed_at is null
            or i.tier2_notify_claimed_at < now() - make_interval(secs => greatest(p_lease_seconds, 10)))
    returning i.incident_id, i.title, i.ticket_count, i.category;
$$;

create or replace function public.mark_outage_tier2_notified(p_tenant_id text, p_incident_id uuid)
returns boolean
language sql
set search_path = public
as $$
    with marked as (
        update public.outage_incidents
           set tier2_notified_at = now()
         where tenant_id = p_tenant_id and incident_id = p_incident_id and tier2_notified_at is null
        returning 1
    )
    select exists (select 1 from marked);
$$;

-- =============================================================================
-- 5. Multi-turn conversation state (Tier 1 slot-filling)
-- =============================================================================

create table if not exists public.conversation_threads (
    tenant_id           text not null,
    thread_id           text not null check (length(thread_id) between 1 and 200),
    ticket_id           text not null,
    reporter_email      text not null,
    source_system       text not null,
    status              text not null default 'active'
                        check (status in ('active','awaiting_user','awaiting_approval','resolved','escalated','expired')),
    -- Hard cap of 2 clarifying questions, enforced by the database as well as the app.
    clarification_count int  not null default 0 check (clarification_count between 0 and 2),
    slots               jsonb not null default '{}'::jsonb check (jsonb_typeof(slots) = 'object'),
    last_action         text check (last_action in
                        ('RESOLVE','ASK_CLARIFICATION','AWAIT_MANAGER_APPROVAL','ESCALATE_TIER_2')),
    user_context        jsonb check (user_context is null or jsonb_typeof(user_context) = 'object'),
    escalation_reason   text check (escalation_reason is null or length(escalation_reason) <= 200),
    -- Set once Tier 2 has been told about an escalated thread; the app retries while null.
    escalation_notified_at timestamptz,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    expires_at          timestamptz not null default now() + interval '72 hours',
    primary key (tenant_id, thread_id)
);

alter table public.conversation_threads add column if not exists escalation_reason text;
do $$
begin
    -- Upgrades: threads escalated before this column existed were handled under
    -- the old flow. Mark them notified once; don't page Tier 2 for all of history.
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'conversation_threads'
                     and column_name = 'escalation_notified_at') then
        alter table public.conversation_threads add column escalation_notified_at timestamptz;
        update public.conversation_threads set escalation_notified_at = updated_at where status = 'escalated';
    end if;
    -- "add column" can't carry the length rule the table definition has.
    if not exists (select 1 from pg_constraint
                   where conrelid = 'public.conversation_threads'::regclass
                     and conname = 'conversation_threads_escalation_reason_check') then
        alter table public.conversation_threads
            add constraint conversation_threads_escalation_reason_check
            check (escalation_reason is null or length(escalation_reason) <= 200) not valid;
    end if;
end;
$$;

-- A Tier 2 notice is owed while escalation_reason is set and escalation_notified_at
-- is null (whatever the status: a security report can be escalated while the
-- agent is still asking a question). Threads escalated under the older rule
-- (status only) get a reason so they stay covered.
update public.conversation_threads
   set escalation_reason = 'escalated'
 where status = 'escalated' and escalation_reason is null and escalation_notified_at is null;

create index if not exists conversation_threads_ticket_idx on public.conversation_threads (tenant_id, ticket_id);
drop index if exists public.conversation_threads_unnotified_idx;
create index if not exists conversation_threads_owed_notice_idx
    on public.conversation_threads (tenant_id, updated_at)
    where escalation_reason is not null and escalation_notified_at is null;

drop trigger if exists conversation_threads_updated_at on public.conversation_threads;
create trigger conversation_threads_updated_at before update on public.conversation_threads
    for each row execute function public.set_updated_at();

create table if not exists public.conversation_messages (
    id              bigint generated always as identity primary key,
    tenant_id       text not null,
    thread_id       text not null,
    role            text not null check (role in ('user','assistant','system_note')),
    content         text not null check (length(content) <= 20000),
    attachment_urls text[] not null default '{}',
    created_at      timestamptz not null default now(),
    foreign key (tenant_id, thread_id)
        references public.conversation_threads (tenant_id, thread_id) on delete cascade
);

create index if not exists conversation_messages_thread_idx
    on public.conversation_messages (tenant_id, thread_id, created_at);

-- Atomically spend one clarifying question. Returns the new count, or NULL when
-- the thread is already at p_max (so the caller must resolve or escalate).
create or replace function public.increment_clarification(
    p_tenant_id text,
    p_thread_id text,
    p_max       int default 2
)
returns int
language sql
set search_path = public
as $$
    update public.conversation_threads
       set clarification_count = clarification_count + 1,
           status = 'awaiting_user',
           last_action = 'ASK_CLARIFICATION'
     where tenant_id = p_tenant_id
       and thread_id = p_thread_id
       and clarification_count < least(greatest(p_max, 0), 2)
    returning clarification_count;
$$;

-- =============================================================================
-- 6. Manager approval requests (Tier 1 RBAC via n8n)
-- =============================================================================

create table if not exists public.approval_requests (
    approval_id       uuid not null default gen_random_uuid() primary key,
    tenant_id         text not null,
    thread_id         text not null,
    ticket_id         text not null,
    requester_email   text not null check (requester_email = lower(requester_email)),
    approver_email    text not null check (approver_email = lower(approver_email)),
    item_type         text not null check (item_type in ('software','hardware')),
    item_key          text not null,
    item_display_name text not null,
    justification     text check (justification is null or length(justification) <= 2000),
    status            text not null default 'pending'
                      check (status in ('pending','approved','denied','expired','cancelled')),
    -- Random value sent in the signed n8n payload. The callback must echo it,
    -- so knowing an approval_id alone isn't enough to decide it.
    callback_nonce    text not null default encode(gen_random_bytes(24), 'hex'),
    decided_by        text,
    decision_note     text check (decision_note is null or length(decision_note) <= 2000),
    created_at        timestamptz not null default now(),
    expires_at        timestamptz not null default now() + interval '72 hours',
    decided_at        timestamptz,
    unique (tenant_id, approval_id),
    foreign key (tenant_id, thread_id)
        references public.conversation_threads (tenant_id, thread_id) on delete cascade,
    foreign key (tenant_id, item_key)
        references public.app_catalog (tenant_id, item_key),
    check ((status in ('approved','denied')) = (decided_at is not null))
);

-- At most one open request per item per thread.
create unique index if not exists approval_requests_one_pending_idx
    on public.approval_requests (tenant_id, thread_id, item_key) where status = 'pending';
create index if not exists approval_requests_tenant_status_idx
    on public.approval_requests (tenant_id, status, created_at desc);

-- Decide a pending approval exactly once. Only the designated approver, holding
-- the nonce, before expiry. Returns the updated row, or no row if any check fails
-- (already decided, expired, wrong approver, wrong nonce, wrong tenant).
create or replace function public.decide_approval(
    p_tenant_id   text,
    p_approval_id uuid,
    p_nonce       text,
    p_decision    text,
    p_decided_by  text,
    p_note        text default null
)
returns setof public.approval_requests
language plpgsql
set search_path = public
as $$
begin
    if p_decision not in ('approved','denied') then
        raise exception 'invalid decision %', p_decision using errcode = '22023';
    end if;

    return query
    update public.approval_requests
       set status        = p_decision,
           decided_by    = lower(p_decided_by),
           decision_note = left(p_note, 2000),
           decided_at    = now()
     where tenant_id      = p_tenant_id
       and approval_id    = p_approval_id
       and callback_nonce = p_nonce
       and status         = 'pending'
       and expires_at     > now()
       and approver_email = lower(p_decided_by)
    returning *;
end;
$$;

-- =============================================================================
-- 7. SOP drafts from resolved tickets (Tier 2 knowledge flywheel)
-- =============================================================================

create table if not exists public.sop_drafts (
    draft_id           uuid not null default gen_random_uuid() primary key,
    tenant_id          text not null,
    source_ticket_id   text not null,
    doc_id             text not null check (length(doc_id) between 1 and 128),
    title              text not null,
    system_name        text,
    os_platform        text not null default 'any'
                       check (os_platform in ('any','windows','macos','linux','ios','android','chromeos')),
    markdown           text not null check (length(markdown) <= 50000),
    frontmatter        jsonb not null check (jsonb_typeof(frontmatter) = 'object'),
    status             text not null default 'draft_pending_review'
                       check (status in ('draft_pending_review','approved','rejected','ingested')),
    nearest_doc_id     text,                              -- closest existing SOP at synthesis time
    nearest_similarity float check (nearest_similarity is null or nearest_similarity between -1 and 1),
    reviewed_by        text,
    reviewed_at        timestamptz,
    ingested_at        timestamptz,
    -- Set once the review notice reached n8n; the agent retries while null.
    review_notified_at timestamptz,
    created_at         timestamptz not null default now(),
    unique (tenant_id, source_ticket_id),
    unique (tenant_id, doc_id),
    check (frontmatter ? 'doc_id' and frontmatter ->> 'doc_id' = doc_id)
);

do $$
begin
    -- Upgrades: drafts that already exist were sent for review under the old flow.
    if not exists (select 1 from information_schema.columns
                   where table_schema = 'public' and table_name = 'sop_drafts'
                     and column_name = 'review_notified_at') then
        alter table public.sop_drafts add column review_notified_at timestamptz;
        update public.sop_drafts set review_notified_at = created_at;
    end if;
end;
$$;

create index if not exists sop_drafts_unnotified_idx
    on public.sop_drafts (tenant_id, created_at) where status = 'draft_pending_review' and review_notified_at is null;

create index if not exists sop_drafts_review_idx
    on public.sop_drafts (tenant_id, created_at desc) where status = 'draft_pending_review';

-- =============================================================================
-- 8. ROI & deflection telemetry (Tier 0)
-- =============================================================================

create table if not exists public.ticket_telemetry (
    id                      bigint generated always as identity primary key,
    tenant_id               text not null,
    ticket_id               text not null,
    thread_id               text,
    service_tier            text not null
                            check (service_tier in ('TIER_0_INCENTIVE','TIER_1_PRODUCTION','TIER_2_MANAGED_OPS')),
    category                text not null,
    action                  text not null check (action in
                            ('RESOLVE','ASK_CLARIFICATION','AWAIT_MANAGER_APPROVAL','ESCALATE_TIER_2')),
    scope                   text not null check (scope in ('in_scope','beyond_tier1','not_it_support')),
    confidence_score        numeric(4,3) not null check (confidence_score between 0 and 1),
    deflected_bool          boolean not null,
    estimated_minutes_saved int not null default 0 check (estimated_minutes_saved between 0 and 480),
    missing_sop_topic       text check (missing_sop_topic is null or length(missing_sop_topic) <= 200),
    token_cost_usd          numeric(12,6) not null default 0 check (token_cost_usd >= 0),
    outage_detected         boolean not null default false,
    parent_incident_id      uuid,
    model                   text,
    latency_ms              int check (latency_ms is null or latency_ms >= 0),
    created_at              timestamptz not null default now(),
    -- Minutes are only credited for deflected tickets.
    check (deflected_bool or estimated_minutes_saved = 0)
);

create index if not exists ticket_telemetry_tenant_time_idx on public.ticket_telemetry (tenant_id, created_at desc);
create index if not exists ticket_telemetry_gap_idx
    on public.ticket_telemetry (tenant_id, created_at) where missing_sop_topic is not null;

-- A ticket can be triaged several times (multi-turn). ROI counts each ticket once:
-- deflected if any execution deflected it, credited with its largest saving.
create or replace function public.tenant_roi_summary(
    p_tenant_id text,
    p_since     timestamptz default now() - interval '30 days',
    p_until     timestamptz default null          -- null = open-ended; window is [p_since, p_until)
)
returns table (
    unique_tickets      bigint,
    total_triages       bigint,
    deflected_tickets   bigint,
    deflection_rate_pct numeric,
    hours_saved         numeric,
    token_cost_usd      numeric,
    outage_tickets      bigint
)
language sql stable
set search_path = public
as $$
    with per_ticket as (
        select ticket_id,
               count(*)                     as triages,
               bool_or(deflected_bool)      as deflected,
               max(estimated_minutes_saved) as minutes_saved,
               sum(token_cost_usd)          as cost,
               bool_or(outage_detected)     as outage
        from public.ticket_telemetry
        where tenant_id = p_tenant_id
          and created_at >= p_since
          and (p_until is null or created_at < p_until)
        group by ticket_id
    )
    select count(*)::bigint,
           coalesce(sum(triages), 0)::bigint,
           count(*) filter (where deflected)::bigint,
           case when count(*) = 0 then 0
                else round(100.0 * count(*) filter (where deflected) / count(*), 1) end,
           round(coalesce(sum(minutes_saved), 0) / 60.0, 2),
           round(coalesce(sum(cost), 0), 4),
           count(*) filter (where outage)::bigint
    from per_ticket;
$$;

create or replace function public.tenant_knowledge_gaps(
    p_tenant_id text,
    p_since     timestamptz default now() - interval '30 days',
    p_limit     int default 5
)
returns table (topic text, ticket_count bigint, last_seen_at timestamptz)
language sql stable
set search_path = public
as $$
    select lower(btrim(missing_sop_topic)) as topic,
           count(distinct ticket_id)::bigint,
           max(created_at)
    from public.ticket_telemetry
    where tenant_id = p_tenant_id
      and created_at >= p_since
      and missing_sop_topic is not null
      and btrim(missing_sop_topic) <> ''
    group by lower(btrim(missing_sop_topic))
    order by count(distinct ticket_id) desc, max(created_at) desc
    limit least(greatest(p_limit, 1), 50);
$$;

-- =============================================================================
-- 9. LLM spend ledger with atomic reservations (hard monthly cap)
--
-- Every model call first reserves its worst-case cost under a per-tenant lock,
-- then settles to the real cost. Concurrent requests therefore can't jointly
-- overshoot MONTHLY_TOKEN_BUDGET_USD. Reservations not settled within 15 minutes
-- (crashed task) stop counting.
-- =============================================================================

create table if not exists public.llm_usage (
    id                 uuid not null default gen_random_uuid() primary key,
    tenant_id          text not null,
    ticket_id          text not null,
    purpose            text not null default 'triage'
                       check (purpose in ('vision','triage','reasoning','flywheel','report','embedding')),
    -- (the public website chat spends under its own tenant_id, like any other tenant)
    model              text not null default 'unknown',
    status             text not null default 'settled' check (status in ('reserved','settled')),
    reserved_usd       numeric(12,6) not null default 0 check (reserved_usd >= 0),
    cost_usd           numeric(12,6) not null default 0 check (cost_usd >= 0),
    input_tokens       int not null default 0,
    output_tokens      int not null default 0,
    cache_read_tokens  int not null default 0,
    cache_write_tokens int not null default 0,
    created_at         timestamptz not null default now(),
    settled_at         timestamptz
);

create index if not exists llm_usage_tenant_month_idx on public.llm_usage (tenant_id, created_at);

create or replace function public.tenant_month_spend(p_tenant_id text)
returns numeric
language sql stable
set search_path = public
as $$
    -- A reservation that was never settled (the call is still running, or the
    -- app lost the database right after it) keeps counting at its worst-case
    -- estimate: the ledger may over-count, but can never under-count the cap.
    select coalesce(sum(case when status = 'settled' then cost_usd else reserved_usd end), 0)
    from public.llm_usage
    where tenant_id = p_tenant_id
      and created_at >= public.utc_month_start();
$$;

-- Returns a reservation id, or NULL when the call would exceed the cap.
create or replace function public.try_reserve_llm_budget(
    p_tenant_id     text,
    p_ticket_id     text,
    p_purpose       text,
    p_estimated_usd numeric,
    p_cap_usd       numeric
)
returns uuid
language plpgsql
set search_path = public
as $$
declare
    v_id uuid;
begin
    if p_estimated_usd < 0 or p_cap_usd < 0 then
        raise exception 'budget amounts must be non-negative' using errcode = '22023';
    end if;
    perform pg_advisory_xact_lock(hashtextextended('budget:' || p_tenant_id, 0));
    if public.tenant_month_spend(p_tenant_id) + p_estimated_usd > p_cap_usd then
        return null;
    end if;
    insert into public.llm_usage (tenant_id, ticket_id, purpose, status, reserved_usd)
    values (p_tenant_id, p_ticket_id, p_purpose, 'reserved', p_estimated_usd)
    returning id into v_id;
    return v_id;
end;
$$;

-- Settle a reservation to its real cost (0 if the call never happened).
create or replace function public.settle_llm_budget(
    p_tenant_id          text,
    p_reservation_id     uuid,
    p_model              text,
    p_cost_usd           numeric,
    p_input_tokens       int default 0,
    p_output_tokens      int default 0,
    p_cache_read_tokens  int default 0,
    p_cache_write_tokens int default 0
)
returns boolean
language sql
set search_path = public
as $$
    with settled as (
        update public.llm_usage
           set status = 'settled',
               model = coalesce(p_model, model),
               cost_usd = greatest(p_cost_usd, 0),
               input_tokens = p_input_tokens,
               output_tokens = p_output_tokens,
               cache_read_tokens = p_cache_read_tokens,
               cache_write_tokens = p_cache_write_tokens,
               settled_at = now()
         where tenant_id = p_tenant_id
           and id = p_reservation_id
           and status = 'reserved'
        returning 1
    )
    select exists (select 1 from settled);
$$;

-- =============================================================================
-- 10. Housekeeping (schedule with pg_cron, e.g. every 15 minutes):
--     select cron.schedule('tier1-housekeeping', '*/15 * * * *', 'select public.run_housekeeping()');
-- =============================================================================

create or replace function public.run_housekeeping()
returns void
language plpgsql
set search_path = public
as $$
begin
    delete from public.recent_tickets where created_at < now() - interval '7 days';

    -- An incident Tier 2 was never told about stays open (and keeps being
    -- retried): closing it would silently drop the only escalation its tickets got.
    update public.outage_incidents
       set status = 'resolved', resolved_at = now()
     where status = 'active' and last_seen_at < now() - interval '6 hours'
       and tier2_notified_at is not null;

    -- Guardrail #2 exemption control: an approval wait that times out is no
    -- longer covered, so its thread escalates to Tier 2.
    with expired as (
        update public.approval_requests
           set status = 'expired'
         where status = 'pending' and expires_at <= now()
        returning tenant_id, thread_id
    )
    update public.conversation_threads t
       set status = 'escalated',
           last_action = 'ESCALATE_TIER_2',
           escalation_reason = 'approval_expired',
           escalation_notified_at = null  -- a new notice is owed, even on a thread notified before
      from expired e
     where t.tenant_id = e.tenant_id and t.thread_id = e.thread_id
       and t.status = 'awaiting_approval';

    update public.conversation_threads
       set status = 'expired'
     where status in ('active','awaiting_user') and expires_at <= now();

    delete from public.webchat_ip_usage where day < (now() at time zone 'utc')::date - 7;
    delete from public.webchat_visitors where last_seen_at < now() - interval '180 days';
    delete from public.maintenance_leases where expires_at < now() - interval '1 day';
end;
$$;

-- =============================================================================
-- 10a. Maintenance lease: every worker of every task runs the same retry loop.
--      One of them takes the lease for a tick; the rest skip it, so owed
--      notifications are retried once, not once per worker.
-- =============================================================================

create table if not exists public.maintenance_leases (
    name       text primary key check (length(name) between 1 and 64),
    expires_at timestamptz not null
);

create or replace function public.try_acquire_maintenance_lease(p_name text, p_ttl_seconds int default 50)
returns boolean
language sql
set search_path = public
as $$
    with taken as (
        insert into public.maintenance_leases as l (name, expires_at)
        values (p_name, now() + make_interval(secs => least(greatest(p_ttl_seconds, 5), 3600)))
        on conflict (name) do update
            set expires_at = excluded.expires_at
          where l.expires_at <= now()
        returning 1
    )
    select exists (select 1 from taken);
$$;

-- =============================================================================
-- 10b. Public website chat: free-trial metering, plans and credits.
--      Anonymous visitors get a fixed number of questions, then must sign up.
--      Accounts get a monthly allowance by plan, then spend purchased credits.
--      All counting happens here, atomically, before any model call.
-- =============================================================================

create table if not exists public.webchat_visitors (
    visitor_id    text primary key check (visitor_id ~ '^[A-Za-z0-9_-]{16,64}$'),
    uses          int not null default 0 check (uses >= 0),
    first_seen_at timestamptz not null default now(),
    last_seen_at  timestamptz not null default now()
);

-- Anonymous use per network per day (a keyed hash, never the address itself):
-- clearing cookies doesn't buy another free trial.
create table if not exists public.webchat_ip_usage (
    ip_hash text not null check (ip_hash ~ '^[0-9a-f]{64}$'),
    day     date not null,
    uses    int  not null default 0 check (uses >= 0),
    primary key (ip_hash, day)
);

create table if not exists public.webchat_accounts (
    email        text primary key check (email = lower(email) and length(email) between 3 and 320),
    plan         text not null default 'free' check (plan ~ '^[a-z0-9_]{1,32}$'),
    credits      int  not null default 0 check (credits >= 0),
    period_start date not null default (date_trunc('month', now() at time zone 'utc'))::date,
    period_uses  int  not null default 0 check (period_uses >= 0),
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);

drop trigger if exists webchat_accounts_updated_at on public.webchat_accounts;
create trigger webchat_accounts_updated_at before update on public.webchat_accounts
    for each row execute function public.set_updated_at();

-- One row per plan change or credit purchase, keyed by the payment's own ID,
-- so a retried payment webhook can never grant twice.
create table if not exists public.webchat_grants (
    grant_id    text primary key check (length(grant_id) between 8 and 200),
    email       text not null,
    plan        text,
    add_credits int  not null default 0 check (add_credits >= 0),
    created_at  timestamptz not null default now()
);

-- Count one question, or say why not. p_plan_limits maps plan -> questions per
-- calendar month (UTC), e.g. {"free": 20, "starter": 250}; an unknown plan gets 0.
-- charged: 'trial' | 'allowance' | 'credit' (what to give back if the answer fails).
create or replace function public.webchat_consume(
    p_visitor_id     text,
    p_ip_hash        text,
    p_account_email  text,
    p_visitor_limit  int,
    p_ip_daily_limit int,
    p_plan_limits    jsonb
)
returns table (allowed boolean, reason text, charged text, plan text, used int, allowance int, credits int)
language plpgsql
set search_path = public
as $$
#variable_conflict use_column
declare
    v_month  date := (date_trunc('month', now() at time zone 'utc'))::date;
    v_today  date := (now() at time zone 'utc')::date;
    v_acct   public.webchat_accounts%rowtype;
    v_limit  int;
    v_uses   int;
    v_ip     int;
begin
    if p_account_email is not null then
        insert into public.webchat_accounts (email) values (lower(p_account_email)) on conflict do nothing;
        select * into v_acct from public.webchat_accounts a where a.email = lower(p_account_email) for update;
        if v_acct.period_start < v_month then
            update public.webchat_accounts a set period_start = v_month, period_uses = 0
             where a.email = v_acct.email;
            v_acct.period_uses := 0;
        end if;
        v_limit := greatest(coalesce((p_plan_limits ->> v_acct.plan)::int, 0), 0);
        if v_acct.period_uses < v_limit then
            update public.webchat_accounts a set period_uses = a.period_uses + 1 where a.email = v_acct.email;
            return query select true, null::text, 'allowance', v_acct.plan, v_acct.period_uses + 1, v_limit, v_acct.credits;
        elsif v_acct.credits > 0 then
            update public.webchat_accounts a set credits = a.credits - 1 where a.email = v_acct.email;
            return query select true, null::text, 'credit', v_acct.plan, v_acct.period_uses, v_limit, v_acct.credits - 1;
        else
            return query select false, 'plan_limit', null::text, v_acct.plan, v_acct.period_uses, v_limit, 0;
        end if;
        return;
    end if;

    -- Anonymous visitor. Lock per visitor and per network so parallel requests can't overspend.
    perform pg_advisory_xact_lock(hashtextextended('webchat:v:' || p_visitor_id, 0));
    perform pg_advisory_xact_lock(hashtextextended('webchat:ip:' || p_ip_hash, 0));
    select v.uses into v_uses from public.webchat_visitors v where v.visitor_id = p_visitor_id;
    v_uses := coalesce(v_uses, 0);
    select u.uses into v_ip from public.webchat_ip_usage u where u.ip_hash = p_ip_hash and u.day = v_today;
    v_ip := coalesce(v_ip, 0);
    if v_uses >= p_visitor_limit or v_ip >= p_ip_daily_limit then
        return query select false, 'signup_required', null::text, null::text, v_uses, greatest(p_visitor_limit, 0), 0;
        return;
    end if;
    insert into public.webchat_visitors as v (visitor_id, uses) values (p_visitor_id, 1)
        on conflict (visitor_id) do update set uses = v.uses + 1, last_seen_at = now();
    insert into public.webchat_ip_usage as u (ip_hash, day, uses) values (p_ip_hash, v_today, 1)
        on conflict (ip_hash, day) do update set uses = u.uses + 1;
    return query select true, null::text, 'trial', null::text, v_uses + 1, p_visitor_limit, 0;
end;
$$;

-- Give a question back when no answer could be produced (model or budget unavailable).
create or replace function public.webchat_refund(
    p_visitor_id    text,
    p_ip_hash       text,
    p_account_email text,
    p_charged       text
)
returns void
language plpgsql
set search_path = public
as $$
begin
    if p_charged = 'trial' then
        update public.webchat_visitors set uses = greatest(uses - 1, 0) where visitor_id = p_visitor_id;
        update public.webchat_ip_usage set uses = greatest(uses - 1, 0)
         where ip_hash = p_ip_hash and day = (now() at time zone 'utc')::date;
    elsif p_charged = 'allowance' then
        update public.webchat_accounts set period_uses = greatest(period_uses - 1, 0)
         where email = lower(p_account_email);
    elsif p_charged = 'credit' then
        update public.webchat_accounts set credits = credits + 1 where email = lower(p_account_email);
    end if;
end;
$$;

-- Apply a plan change and/or a credit purchase exactly once per p_grant_id.
create or replace function public.webchat_apply_grant(
    p_grant_id    text,
    p_email       text,
    p_plan        text default null,
    p_add_credits int  default 0
)
returns table (applied boolean, plan text, credits int)
language plpgsql
set search_path = public
as $$
#variable_conflict use_column
declare
    v_new boolean := false;
begin
    if coalesce(p_add_credits, 0) < 0 or coalesce(p_add_credits, 0) > 100000 then
        raise exception 'add_credits out of range' using errcode = '22023';
    end if;
    with ins as (
        insert into public.webchat_grants (grant_id, email, plan, add_credits)
        values (p_grant_id, lower(p_email), p_plan, coalesce(p_add_credits, 0))
        on conflict (grant_id) do nothing
        returning 1
    )
    select exists (select 1 from ins) into v_new;
    insert into public.webchat_accounts (email) values (lower(p_email)) on conflict do nothing;
    if v_new then
        update public.webchat_accounts a
           set plan = coalesce(p_plan, a.plan),
               credits = a.credits + coalesce(p_add_credits, 0)
         where a.email = lower(p_email);
    end if;
    return query select v_new, a.plan, a.credits from public.webchat_accounts a where a.email = lower(p_email);
end;
$$;

-- =============================================================================
-- 11. Row-level security: service role only, plus read-only tenant-scoped JWTs
-- =============================================================================

do $$
declare
    t text;
begin
    foreach t in array array[
        'sop_chunks','app_catalog','tenant_directory','outage_incidents','recent_tickets',
        'conversation_threads','conversation_messages','approval_requests','sop_drafts',
        'ticket_telemetry','llm_usage'
    ] loop
        execute format('alter table public.%I enable row level security', t);
        execute format('revoke all on public.%I from anon', t);
        execute format('revoke insert, update, delete, truncate on public.%I from authenticated', t);
        execute format('drop policy if exists tenant_read on public.%I', t);
        execute format(
            'create policy tenant_read on public.%I for select to authenticated '
            'using (tenant_id = public.request_tenant_id())', t);
    end loop;
end;
$$;

-- Tables without a tenant: service role only (RLS on, no policies, no grants).
do $$
declare
    t text;
begin
    foreach t in array array[
        'maintenance_leases','webchat_visitors','webchat_ip_usage','webchat_accounts','webchat_grants'
    ] loop
        execute format('alter table public.%I enable row level security', t);
        execute format('revoke all on public.%I from anon, authenticated', t);
    end loop;
end;
$$;

-- Approval nonces stay server-side, even from the owning tenant's JWT. A
-- column-level REVOKE does nothing while a table-level grant exists, so replace
-- the table grant with an explicit column list.
revoke select on public.approval_requests from authenticated;
grant select (approval_id, tenant_id, thread_id, ticket_id, requester_email, approver_email,
              item_type, item_key, item_display_name, justification, status, decided_by,
              decision_note, created_at, expires_at, decided_at)
    on public.approval_requests to authenticated;

-- =============================================================================
-- 12. Function privileges. Supabase grants EXECUTE to anon/authenticated by
--     default; revoke all, then grant back the safe read-only reports.
-- =============================================================================

do $$
declare
    f regprocedure;
begin
    for f in
        select p.oid::regprocedure
        from pg_proc p
        join pg_namespace n on n.oid = p.pronamespace
        where n.nspname = 'public'
          and p.proname in (
              'request_tenant_id','set_updated_at','utc_month_start','match_sop_chunks',
              'match_recent_tickets','register_outage_cluster','mark_outage_tier2_notified',
              'claim_pending_outage_notifications','try_acquire_maintenance_lease',
              'webchat_consume','webchat_refund','webchat_apply_grant',
              'increment_clarification',
              'decide_approval','tenant_roi_summary','tenant_knowledge_gaps','tenant_month_spend',
              'try_reserve_llm_budget','settle_llm_budget','run_housekeeping')
    loop
        execute format('revoke all on function %s from public, anon, authenticated', f);
        execute format('grant execute on function %s to service_role', f);
    end loop;
end;
$$;

-- Read-only reports are safe for tenant dashboards: they run as the caller, so
-- RLS limits them to the JWT's tenant whatever p_tenant_id says.
grant execute on function public.request_tenant_id() to authenticated;
grant execute on function public.tenant_roi_summary(text, timestamptz, timestamptz) to authenticated;
grant execute on function public.tenant_knowledge_gaps(text, timestamptz, int) to authenticated;

-- Tell PostgREST (Supabase's REST layer) to pick up the new tables and functions now.
notify pgrst, 'reload schema';
