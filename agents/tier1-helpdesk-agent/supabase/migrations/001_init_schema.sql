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
    unique (tenant_id, incident_id)
);

create index if not exists outage_incidents_active_idx
    on public.outage_incidents (tenant_id, last_seen_at desc) where status = 'active';

-- Short-lived ticket embeddings. Kept 7 days (see run_housekeeping); detection
-- only looks back 30 minutes.
create table if not exists public.recent_tickets (
    id                 uuid not null default gen_random_uuid() primary key,
    tenant_id          text not null,
    ticket_id          text not null,
    thread_id          text,
    category           text,
    summary            text not null check (length(summary) <= 500),
    embedding          vector(1024) not null,
    parent_incident_id uuid,
    created_at         timestamptz not null default now(),
    unique (tenant_id, ticket_id),
    foreign key (tenant_id, parent_incident_id)
        references public.outage_incidents (tenant_id, incident_id) on delete set null (parent_incident_id)
);

create index if not exists recent_tickets_window_idx on public.recent_tickets (tenant_id, created_at desc);

-- Tickets from the same tenant in the last p_window_minutes that look alike.
create or replace function public.match_recent_tickets(
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
    parent_incident_id uuid,
    similarity         float,
    created_at         timestamptz
)
language sql stable
set search_path = public, extensions
as $$
    with window_rows as materialized (
        select t.ticket_id, t.summary, t.parent_incident_id, t.created_at,
               1 - (t.embedding <=> p_query_embedding) as similarity
        from public.recent_tickets t
        where t.tenant_id = p_tenant_id
          and t.created_at >= now() - make_interval(mins => greatest(p_window_minutes, 1))
          and (p_exclude_ticket_id is null or t.ticket_id <> p_exclude_ticket_id)
    )
    select ticket_id, summary, parent_incident_id, similarity, created_at
    from window_rows
    where similarity >= p_min_similarity
    order by similarity desc
    limit least(greatest(p_limit, 1), 200);
$$;

-- Open (or join) an outage for a cluster of matching tickets. A per-tenant
-- advisory lock means a burst of simultaneous tickets yields one incident.
create or replace function public.register_outage_cluster(
    p_tenant_id          text,
    p_ticket_id          text,
    p_matched_ticket_ids text[],
    p_title              text
)
returns uuid
language plpgsql
set search_path = public
as $$
declare
    v_incident uuid;
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
        insert into public.outage_incidents (tenant_id, title)
        values (p_tenant_id, left(coalesce(nullif(btrim(p_title), ''), 'Possible service outage'), 200))
        returning incident_id into v_incident;
    end if;

    update public.recent_tickets
       set parent_incident_id = v_incident
     where tenant_id = p_tenant_id
       and ticket_id = any (v_ids)
       and parent_incident_id is distinct from v_incident;

    update public.outage_incidents i
       set ticket_count = (select count(*) from public.recent_tickets t
                            where t.tenant_id = p_tenant_id and t.parent_incident_id = v_incident),
           last_seen_at = now()
     where i.tenant_id = p_tenant_id and i.incident_id = v_incident;

    return v_incident;
end;
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
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now(),
    expires_at          timestamptz not null default now() + interval '72 hours',
    primary key (tenant_id, thread_id)
);

create index if not exists conversation_threads_ticket_idx on public.conversation_threads (tenant_id, ticket_id);

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
    created_at         timestamptz not null default now(),
    unique (tenant_id, source_ticket_id),
    unique (tenant_id, doc_id),
    check (frontmatter ? 'doc_id' and frontmatter ->> 'doc_id' = doc_id)
);

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
    select coalesce(sum(case
               when status = 'settled' then cost_usd
               when status = 'reserved' and created_at > now() - interval '15 minutes' then reserved_usd
               else 0 end), 0)
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

    update public.outage_incidents
       set status = 'resolved', resolved_at = now()
     where status = 'active' and last_seen_at < now() - interval '6 hours';

    update public.approval_requests
       set status = 'expired'
     where status = 'pending' and expires_at <= now();

    update public.conversation_threads
       set status = 'expired'
     where status in ('active','awaiting_user') and expires_at <= now();
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
              'match_recent_tickets','register_outage_cluster','increment_clarification',
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
