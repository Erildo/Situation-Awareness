-- Ledger schema. Idempotent: safe to run on every start.
-- Principle: we store confirmed state + pointers to evidence, never raw chat text.

create table if not exists person (
  id           bigserial primary key,
  name         text not null,
  email        text unique,
  slack_id     text unique,
  teams_id     text unique,
  github_login text unique,
  linear_id    text unique,
  opted_in     boolean not null default false
);
alter table person add column if not exists active boolean not null default true;  -- false once they leave

-- What a person can see. scope = 'slack:C123' | 'github:org/repo' | 'linear:INFRA'.
-- Q8: evidence usable in an answer = asker scopes ∩ owner scopes, checked in SQL.
create table if not exists person_scope (
  person_id bigint not null references person on delete cascade,
  scope     text not null,
  primary key (person_id, scope)
);

-- Anchored on a real ID so the AI links to workstreams instead of inventing them.
create table if not exists workstream (
  id          bigserial primary key,
  anchor_kind text not null check (anchor_kind in ('linear', 'github', 'channel')),
  anchor_id   text not null,          -- 'INFRA-123' | 'org/repo#812' | 'slack:C123'
  title       text not null,
  status      text,
  url         text,
  scope       text not null,          -- who may see this workstream
  owner_id    bigint references person,
  updated_at  timestamptz not null default now(),
  unique (anchor_kind, anchor_id)
);

-- Decision / Commitment / Blocker share one shape, so one table.
-- ponytail: one table with `kind`; split if the kinds grow different columns.
create table if not exists state (
  id            bigserial primary key,
  kind          text not null check (kind in ('decision', 'commitment', 'blocker')),
  workstream_id bigint not null references workstream on delete cascade,
  text          text not null,
  owner_id      bigint references person,
  due           date,                 -- commitments only
  status        text not null default 'proposed'
                check (status in ('proposed', 'confirmed', 'superseded')),
  valid_from    timestamptz not null default now(),
  valid_to      timestamptz,
  created_at    timestamptz not null default now()
);

-- Pointer to a source, no raw text. scope says who can see it.
create table if not exists evidence_ref (
  id            bigserial primary key,
  url           text not null unique,
  source        text not null,        -- 'slack' | 'github' | 'linear' | 'teams'
  scope         text not null,
  title         text,
  workstream_id bigint references workstream on delete cascade,
  state_id      bigint references state on delete cascade,
  at            timestamptz not null
);
-- PRs/issues only: who opened it, open|closed|merged, when merged. Feeds the prs() tool.
alter table evidence_ref add column if not exists author_id bigint references person;
alter table evidence_ref add column if not exists status text;
alter table evidence_ref add column if not exists merged_at timestamptz;

-- Owner-approved answers (Q10): asked once, answered forever.
create table if not exists fact (
  id         bigserial primary key,
  owner_id   bigint not null references person,
  question   text not null,
  answer     text not null,
  created_at timestamptz not null default now(),
  valid_to   timestamptz
);

create table if not exists escalation (
  id          bigserial primary key,
  owner_id    bigint not null references person,
  asker_id    bigint references person,
  question    text not null,
  thread_ref  text not null,          -- platform:channel:thread, for the reply
  urgent      boolean not null default false,
  status      text not null default 'open' check (status in ('open', 'answered', 'dropped')),
  answer      text,
  fact_id     bigint references fact,
  created_at  timestamptz not null default now(),
  answered_at timestamptz
);

-- Owner sees who asked their twin what (Q7).
create table if not exists query_log (
  id         bigserial primary key,
  owner_id   bigint not null references person,
  asker_id   bigint references person,
  question   text not null,
  answer     text,
  level      smallint check (level between 1 and 4),
  created_at timestamptz not null default now()
);
alter table query_log add column if not exists thread_ref text;  -- follow-ups in that thread go to the same twin
alter table query_log add column if not exists sources text[];
alter table escalation add column if not exists owner_ref text;  -- the owner's DM message, replies to it answer

-- Job queue, consumed with FOR UPDATE SKIP LOCKED. Rows are deleted when done.
create table if not exists job (
  id         bigserial primary key,
  kind       text not null,
  payload    jsonb not null,
  run_after  timestamptz not null default now(),
  created_at timestamptz not null default now()
);
create index if not exists job_ready on job (run_after);

-- Keyword search for the twin (search tool). ponytail: built-in FTS; ParadeDB BM25 / pgvector when recall falls short.
create index if not exists workstream_fts on workstream using gin (to_tsvector('english', title || ' ' || anchor_id));
create index if not exists state_fts on state using gin (to_tsvector('english', text));
create index if not exists fact_fts on fact using gin (to_tsvector('english', question || ' ' || answer));
