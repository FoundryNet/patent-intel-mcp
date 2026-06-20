-- Patent Intelligence — schema for patent_aggregator + patent-intel-mcp.
-- Standalone Supabase project. Idempotent. Uses pgvector for prior-art search.

create extension if not exists vector;
create extension if not exists pg_trgm;

-- ── patents ──────────────────────────────────────────────────────────────────
create table if not exists patents (
  id                 uuid primary key default gen_random_uuid(),
  patent_number      text,
  application_number text,
  title              text,
  abstract           text,
  filing_date        date,
  grant_date         date,
  publication_date   date,
  assignee_name      text,
  assignee_country   text,
  inventors          jsonb,          -- [{name, city, state, country}]
  cpc_codes          jsonb,          -- [codes]
  cpc_primary        text,
  claims_count       integer,
  citation_count     integer,
  status             text,           -- granted | pending | abandoned
  patent_type        text,           -- utility | design | plant
  source_url         text,
  embedding          vector(384),    -- bge-small-en-v1.5 (fastembed) on abstract
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (patent_number)
);

create index if not exists idx_patents_grant   on patents (grant_date desc nulls last);
create index if not exists idx_patents_filing  on patents (filing_date desc nulls last);
create index if not exists idx_patents_assignee on patents (assignee_name);
create index if not exists idx_patents_cpc_primary on patents (cpc_primary);
create index if not exists idx_patents_type    on patents (patent_type);
create index if not exists idx_patents_title_trgm on patents using gin (title gin_trgm_ops);
-- cosine similarity index for prior-art search
create index if not exists idx_patents_embedding on patents
  using hnsw (embedding vector_cosine_ops);

-- ── patent_assignees (rolling summary) ───────────────────────────────────────
create table if not exists patent_assignees (
  assignee_name      text primary key,
  patent_count       integer not null default 0,
  latest_filing_date date,
  primary_cpc_codes  jsonb,
  filing_velocity_90d integer not null default 0,  -- filings in last 90 days
  updated_at         timestamptz not null default now()
);

-- ── prior-art semantic search RPC ────────────────────────────────────────────
-- query_embedding passed as a text literal ('[0.1,0.2,...]') and cast to vector —
-- the reliable shape for PostgREST RPC + pgvector.
create or replace function match_patents(
  query_embedding text,
  match_count int default 10
)
returns table (
  patent_number text, title text, abstract text, assignee_name text,
  cpc_primary text, grant_date date, source_url text, similarity float
)
language sql stable as $$
  select p.patent_number, p.title, p.abstract, p.assignee_name, p.cpc_primary,
         p.grant_date, p.source_url,
         1 - (p.embedding <=> query_embedding::vector) as similarity
  from patents p
  where p.embedding is not null
  order by p.embedding <=> query_embedding::vector
  limit match_count;
$$;

-- Recompute the patent_assignees rolling summary from the patents table.
create or replace function refresh_patent_assignees()
returns void language sql as $$
  insert into patent_assignees
    (assignee_name, patent_count, latest_filing_date, primary_cpc_codes, filing_velocity_90d, updated_at)
  select p.assignee_name,
         count(*),
         max(p.filing_date),
         (select jsonb_agg(c) from (
            select cpc_primary c from patents p2
            where p2.assignee_name = p.assignee_name and p2.cpc_primary is not null
            group by cpc_primary order by count(*) desc limit 5) t),
         count(*) filter (where p.filing_date >= current_date - interval '90 days'),
         now()
  from patents p
  where p.assignee_name is not null and p.assignee_name <> ''
  group by p.assignee_name
  on conflict (assignee_name) do update set
    patent_count        = excluded.patent_count,
    latest_filing_date  = excluded.latest_filing_date,
    primary_cpc_codes   = excluded.primary_cpc_codes,
    filing_velocity_90d = excluded.filing_velocity_90d,
    updated_at          = now();
$$;

-- ── free-tier counter ────────────────────────────────────────────────────────
create table if not exists patent_query_usage (
  agent_key  text not null,
  day        date not null,
  count      integer not null default 0,
  updated_at timestamptz not null default now(),
  primary key (agent_key, day)
);

create or replace function patent_claim_free_query(p_agent_key text, p_day date, p_cap integer)
returns jsonb language plpgsql as $$
declare cur integer; ok boolean;
begin
  insert into patent_query_usage (agent_key, day, count, updated_at)
  values (p_agent_key, p_day, 0, now())
  on conflict (agent_key, day) do nothing;
  select count into cur from patent_query_usage
    where agent_key = p_agent_key and day = p_day for update;
  if cur < p_cap then
    update patent_query_usage set count = count + 1, updated_at = now()
      where agent_key = p_agent_key and day = p_day;
    ok := true; cur := cur + 1;
  else ok := false; end if;
  return jsonb_build_object('allowed', ok, 'count', cur, 'cap', p_cap);
end; $$;

-- ── x402 payment ledger ──────────────────────────────────────────────────────
create table if not exists patent_payments (
  tx_signature text primary key,
  intent       text,
  agent_key    text,
  tool         text,
  amount_usdc  numeric,
  payer_wallet text,
  recipient    text,
  status       text,
  block_time   bigint,
  created_at   timestamptz not null default now()
);
