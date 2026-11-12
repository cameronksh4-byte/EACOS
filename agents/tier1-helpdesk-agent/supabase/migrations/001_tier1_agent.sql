-- Tier 1 help desk agent: SOP vector store + LLM usage ledger.
-- Apply with `supabase db push` or paste into the Supabase SQL editor.

create extension if not exists vector;

-- ---------------------------------------------------------------------------
-- SOP chunks (one row per chunk; sop_id groups the chunks of one document)
-- ---------------------------------------------------------------------------
create table if not exists public.sop_chunks (
    id           text primary key,              -- "<tenant>:<sop_id>#<chunk_index>"
    tenant_id    text not null,
    sop_id       text not null,
    title        text not null,
    chunk_index  int  not null,
    content      text not null,
    content_hash text not null,
    embedding    vector(1024) not null,         -- must match EMBEDDING_DIMENSIONS
    updated_at   timestamptz not null default now()
);

create index if not exists sop_chunks_tenant_idx on public.sop_chunks (tenant_id);
create index if not exists sop_chunks_embedding_idx
    on public.sop_chunks using hnsw (embedding vector_cosine_ops);

-- RLS on with no policies: only the service role (used server-side) can read
-- or write. Anon/authenticated keys get nothing.
alter table public.sop_chunks enable row level security;

-- Tenant isolation happens inside the query, never in application code.
create or replace function public.match_sop_chunks(
    p_tenant_id       text,
    p_query_embedding vector(1024),
    p_match_count     int   default 4,
    p_min_similarity  float default 0.55
)
returns table (sop_id text, title text, content text, similarity float)
language sql stable
set search_path = public, extensions
as $$
    select c.sop_id, c.title, c.content,
           1 - (c.embedding <=> p_query_embedding) as similarity
    from public.sop_chunks c
    where c.tenant_id = p_tenant_id
      and 1 - (c.embedding <=> p_query_embedding) >= p_min_similarity
    order by c.embedding <=> p_query_embedding
    limit least(greatest(p_match_count, 1), 20);
$$;

revoke all on function public.match_sop_chunks(text, vector, int, float) from public, anon, authenticated;

-- ---------------------------------------------------------------------------
-- LLM usage ledger (drives MONTHLY_TOKEN_BUDGET_USD)
-- ---------------------------------------------------------------------------
create table if not exists public.llm_usage (
    id                 bigint generated always as identity primary key,
    tenant_id          text not null,
    ticket_id          text not null,
    model              text not null,
    input_tokens       int  not null default 0,
    output_tokens      int  not null default 0,
    cache_read_tokens  int  not null default 0,
    cache_write_tokens int  not null default 0,
    cost_usd           numeric(12, 6) not null default 0,
    created_at         timestamptz not null default now()
);

create index if not exists llm_usage_tenant_month_idx on public.llm_usage (tenant_id, created_at);
alter table public.llm_usage enable row level security;

create or replace function public.tenant_month_spend(p_tenant_id text)
returns numeric
language sql stable
set search_path = public
as $$
    select coalesce(sum(cost_usd), 0)
    from public.llm_usage
    where tenant_id = p_tenant_id
      and created_at >= date_trunc('month', now() at time zone 'utc') at time zone 'utc';
$$;

revoke all on function public.tenant_month_spend(text) from public, anon, authenticated;
