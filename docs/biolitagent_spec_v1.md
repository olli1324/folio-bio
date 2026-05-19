# BioLitAgent — Technical Spec for the Featherless Engine

_Oliver's component · AI Agent Olympics Hackathon · 15 May 2026_

## What this component is

The research engine: an async pipeline that takes a target protein or disease
query and produces a structured drug-discovery briefing. Every model call goes
through Featherless open-source models. It runs as a background job with
visible step-by-step progress, not as a blocking chat call.

This is the core of BioLitAgent. cambeni's voice layer and dummetts' web app
and Vultr deployment wrap around it.

## A skeleton already exists worth copying

The Featherless Kraken agent that came with the hackathon material
(`github.com/Stephen-Kimoi/featherless-kraken-agent`) is close to a drop-in
template. It is a multi-model agent on Featherless with the exact shape needed:

| Kraken agent | BioLitAgent equivalent |
|---|---|
| Kraken CLI feeds live market data | PubMed E-utilities feed paper metadata and abstracts |
| Qwen2.5-72B analyses the data | extraction model pulls compounds, mechanisms, findings |
| DeepSeek-V3.2 makes the decision | synthesis model writes the briefing |
| Flask + SSE dashboard streams each step | same pattern, streams pipeline progress |
| Models swappable via `.env`, no code change | keep this |

Its structure — `agent.py` for core logic and the model router, `app.py` for
the Flask server with SSE streaming, a data wrapper module, `.env` for model
selection, MIT licence — is a reasonable starting layout. Worth reading it
before writing anything.

## Featherless setup essentials

From the hackathon setup guide:

- **API is OpenAI-compatible.** Base URL `https://api.featherless.ai/v1`,
  endpoint `/chat/completions`. The OpenAI Python SDK works unchanged — just
  point `base_url` at Featherless and pass the Featherless key.
- **Feather Premium is needed and free for the hackathon.** Sign-up code
  `LABLABMILAN` applies automatically. Premium unlocks DeepSeek, Kimi-K2,
  GLM 4.6, up to 4 concurrent connections, and up to 32K context. Oliver has
  already created the account; the remaining step is generating an API key
  under account settings → API Keys.
- **Two endpoints.** `/chat/completions` for the role-structured calls,
  `/completions` for raw single-prompt extraction. Most nodes here use chat
  completions.

## The concurrency constraint — this shapes the architecture

Featherless bills by capacity reservation, not tokens. Premium gives **4
concurrent connection slots**, and model size consumes slots:

- 7B–15B models → 1 slot each (so 4 can run in parallel)
- 24B–34B models → 2 slots each (2 in parallel)
- 70B–72B and DeepSeek / Kimi → 4 slots each (only 1 in flight at a time)

This matters because the pipeline fans out — a query can pull 15–30 papers and
each one needs an extraction call. If those go through a 72B model they
serialise completely and the run takes minutes. The fix:

- **Extraction / per-paper summarisation** → a small fast model
  (Mistral-Nemo-Instruct, or a Qwen 7B). 1 slot each, so 4 papers process at
  once. High volume, modest reasoning needs.
- **Final synthesis / briefing generation** → a strong model (DeepSeek-V3.2
  for reasoning, or GLM-5.1 which the guide flags as good at long-horizon
  tasks). Runs once per query, so the 4-slot cost is fine.

Put both model IDs in `.env` so the team can tune them without touching code,
exactly as the Kraken repo does.

## Pipeline design

A linear graph with a fan-out in the middle. If using LangGraph, these are the
nodes; the same shape works as plain async Python functions if LangGraph adds
friction under time pressure.

1. **plan** — take the raw query, expand it into PubMed search terms (protein
   synonyms, related disease terms, MeSH-style expansion). One small-model call.
2. **search** — hit PubMed ESearch with the terms, get back PMIDs. Cap the
   result set (e.g. top 25 by relevance) so the run stays bounded.
3. **retrieve** — EFetch / ESummary for those PMIDs, pull title, abstract,
   authors, journal, year. No model call, just the API.
4. **extract** (fan-out) — per paper, a small-model call that pulls compound
   candidates, mechanism of action, key finding, and a relevance score. Run
   these 4 at a time to respect the slot budget.
5. **synthesise** — one strong-model call over all the extractions: cluster by
   mechanism, rank compounds, flag contradictions, note gaps.
6. **format** — assemble the structured briefing (sections: query summary,
   top compound candidates, mechanisms map, key papers with citations, open
   questions). Emit as JSON plus rendered Markdown.

Pipeline state to carry through the graph: `query`, `search_terms`, `pmids`,
`papers[]`, `extractions[]`, `briefing`. Keep it one typed object.

## Async-first, because Featherless rewards it

The Featherless challenge explicitly wants background / pipeline architecture
over a chatbot. Concretely:

- The web layer kicks off a job and returns a job ID immediately.
- The pipeline runs in the background (a task queue, or just an async task for
  hackathon scope).
- Progress streams back over SSE — the same mechanism the Kraken dashboard
  uses. Each node completion pushes an event: "searched, found 24 papers",
  "extracted 12 of 24", "briefing ready".
- Briefings persist so they can be reopened. This is also where the Vultr
  "system of record" angle lives — job history and briefings stored on Vultr.

## Repo structure for the shared codebase

One repo, module boundaries that match who owns what:

```
biolitagent/
├── engine/              # Oliver — the Featherless pipeline
│   ├── pipeline.py      # the graph: plan→search→retrieve→extract→synthesise→format
│   ├── featherless.py   # OpenAI-compatible client wrapper, model router, retry logic
│   ├── pubmed.py        # PubMed E-utilities wrapper
│   └── schema.py        # pipeline state + briefing data model
├── voice/               # cambeni — Speechmatics layer
├── web/                 # dummetts — Flask/web app + SSE, Vultr deploy
│   └── templates/
├── .env.example         # model IDs, API keys
├── requirements.txt
├── LICENSE              # MIT or Apache 2.0 — needed for the Featherless track
└── README.md            # reproducible setup — also a Featherless judging criterion
```

The seam between `engine/` and `web/` is one function: `run_pipeline(query)`
that yields progress events. The seam between `voice/` and `web/` is the query
string going in and the briefing text coming out. Agree those two interfaces
with cambeni and dummetts early and the integration on Monday is small.

## Gotchas to plan around

- **403 on a model** means it is gated — unlock it on its model page and accept
  the licence before the run, not during the demo.
- **503** means a cold model. The guide says retry up to 3 times. Build retry
  with backoff into `featherless.py` from the start; cold-start stalls during a
  live pitch look bad.
- **32K context cap.** Do not stuff 25 full abstracts into one synthesis call.
  The per-paper extraction step exists partly to compress each paper to a few
  fields before synthesis sees them.
- **PubMed rate limits.** Without an API key it is 3 requests/second; with a
  free NCBI key it is 10/second. Get the NCBI key — it is free and instant.
- **Don't over-invest in LangGraph.** If the graph library fights you on
  Saturday, the six nodes are plain async functions in sequence. The judges
  care that the agent plans and executes multi-step work, not which library
  drew the diagram.

## Build plan

**Friday 15 May (today)** — generate the Featherless API key, get an NCBI key,
read the Kraken repo. Get one Featherless chat completion call working and one
PubMed search returning PMIDs. Agree the shared repo and the two integration
interfaces with the team.

**Saturday 16 May** — build the pipeline end to end with stub-quality prompts:
plan → search → retrieve → extract → synthesise → format. Goal by end of day:
a query produces a rough briefing, even if the prompts are crude.

**Sunday 17 May** — tune prompts so the extraction and synthesis output is
actually good. Add the fan-out concurrency control. Wire SSE progress events.
Hand the `run_pipeline` interface to dummetts for the web layer.

**Monday 18 May** — error handling, retry/backoff, the LICENSE, the README with
reproducible setup. Integrate with cambeni's voice input and dummetts' deploy.
Afternoon is buffer.

**Tuesday 19 May** — submission day, deadline 17:00. Should be polish and demo
rehearsal only.
