# Folio — Supabase persistence (optional)

Supabase backs the briefing history sidebar, permalinks, and the per-briefing
notes scratchpad. Folio runs fine without it — the engine, CLI, and web UI
all degrade gracefully when `SUPABASE_*` env vars are unset; you just lose
history and refreshing the page loses the current briefing.

## 1. Create the two tables

In your Supabase project's SQL Editor:

```sql
create table if not exists biolit_briefings (
    id uuid primary key default gen_random_uuid(),
    query text not null,
    summary text,
    briefing jsonb not null,
    briefing_markdown text,
    paper_count int not null default 0,
    extraction_count int not null default 0,
    elapsed_seconds real,
    status text not null default 'ok',
    created_at timestamptz not null default now()
);
create index if not exists biolit_briefings_created_at_idx
    on biolit_briefings (created_at desc);

create table if not exists biolit_briefing_papers (
    id uuid primary key default gen_random_uuid(),
    briefing_id uuid not null references biolit_briefings(id) on delete cascade,
    pmid text not null, title text, journal text, year text, abstract text,
    extraction jsonb, relevance_score real,
    created_at timestamptz not null default now()
);
create index if not exists biolit_papers_briefing_id_idx
    on biolit_briefing_papers (briefing_id);
```

## 2. Wire up the env vars

In `.env`:

```
SUPABASE_URL=https://<project>.supabase.co
SUPABASE_SECRET_KEY=sb_secret_…   # service-role key, NOT the publishable / anon key
```

The server uses the service-role key directly — there is no row-level
security setup required for this minimal schema. If you want to expose
read-only access to the front end later, add RLS policies and switch to the
anon key from the client.

## 3. What you get

- A **Recent** drawer in the top bar listing past briefings
- Permalinks per briefing at `/?briefing=<uuid>`
- Per-briefing notes scratchpad that autosaves 800ms after the last
  keystroke

Restart `python app.py` after setting the env vars.
