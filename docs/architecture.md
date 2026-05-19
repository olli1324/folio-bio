# Folio — architecture

The deep-dive companion to the [README](../README.md). Covers concurrency,
resilience, the integration seam, and the module layout. Read the README
first.

## Concurrency model

Featherless Premium reserves 4 concurrent connection slots. Model size
consumes the slot budget: 7B-15B models cost 1 slot, 24B-34B cost 2, 70B+
and DeepSeek / Kimi cost 4. The pipeline is shaped around that.

- **Extraction fan-out** uses a small model (default
  `mistralai/Mistral-Nemo-Instruct-2407`, 12B, 32K context, 1 slot). One
  semaphore in `engine/featherless.py` caps in-flight calls at 4. For 25
  papers, ~6 waves of 4 finish the extraction phase in roughly 60-70s.
- **Synthesis** uses a strong model (default `deepseek-ai/DeepSeek-V3.2`,
  4 slots). Runs alone after the fan-out drains; the strong model's slot
  cost doesn't block extraction.
- **ClinicalTrials.gov enrichment** runs the top compounds in parallel
  through a separate per-host semaphore (default 5). No Featherless slots
  used here; this is a public API call.

End-to-end wall time is ~75-100 seconds for a typical 25-paper query when
the models are warm; first call of the day can stretch to several minutes
on a Featherless cold-start (handled by the retry/backoff in
`engine/featherless.py`).

A process-wide singleton `FeatherlessClient` is exposed via
`engine.pipeline.get_default_clients()`. The web layer uses it so two
concurrent in-flight pipelines actually share the 4-slot budget instead of
each opening their own 4.

## Resilience

- **Cold-start retries.** Featherless calls retry with exponential backoff
  (2 → 4 → 8 → 16 → 32s) up to 5 attempts. `APIStatusError`,
  `APIConnectionError`, `APITimeoutError`, and `httpx.TimeoutException` all
  go through retry. Total backoff budget covers the 30-60s cold-start
  window the platform documents.
- **Per-paper extraction failures** are isolated: one malformed model
  response is logged and that paper is marked `skipped`; the other 24
  continue.
- **Synthesis fallback.** If the strong model returns an empty string or
  unparseable JSON (we have observed this with `zai-org/GLM-5.1` against a
  strict-JSON prompt), the briefing is rebuilt directly from the
  extractions — most-mentioned compounds, most-frequent mechanisms — so
  the demo never hard-fails on stage.
- **Cancellation-safe fan-out.** Both `node_extract` and `node_enrich_trials`
  cancel and drain in-flight tasks in a `finally` block, so a browser
  disconnect mid-stream does not leak tasks.

## Programmatic use

```python
from engine.pipeline import run_pipeline

async for event in run_pipeline("BRAF V600E melanoma"):
    print(event.stage, event.message)
    if event.stage == "done":
        briefing = event.detail["briefing"]
        markdown = event.detail["briefing_markdown"]
```

For concurrent callers, share one client set so the Featherless 4-slot
budget is respected globally:

```python
from engine.pipeline import get_default_clients, run_pipeline

fl, pm, ct = get_default_clients()
async for event in run_pipeline("EGFR NSCLC", fl=fl, pm=pm, ct=ct):
    ...
```

## Integration seam

```python
run_pipeline(query: str) -> AsyncIterator[ProgressEvent]
```

`ProgressEvent.stage` is a typed `Stage` enum
(`plan`, `search`, `retrieve`, `extract`, `synthesise`, `enrich`, `done`,
`error`). `ProgressEvent.detail` carries structured payloads:

- `plan` → `{"search_terms": [...]}`
- `retrieve` → `{"paper_count": N, "papers": [{pmid, title, abstract, ...}]}`
- `extract` (per paper) → `{"pmid", "completed", "total", "ok", "extraction": {...}}`
- `enrich` (per compound) → `{"compound", "trial_info": {...}}`
- `done` → `{"briefing": {...}, "briefing_markdown": "..."}`

The voice and web layers consume this stream. Schema additions are additive;
no field has ever been renamed or removed.

## Models

Both model IDs are configured via `.env`; any Featherless catalogue model
fits the matching slot cost. No code change required to swap.

| Role | Default | Slot cost | Verified alternatives |
|---|---|---|---|
| Extraction (small, fan-out) | `mistralai/Mistral-Nemo-Instruct-2407` | 1 | `Qwen/Qwen2.5-7B-Instruct` (ran 25/25 papers cleanly) |
| Synthesis (strong, one call) | `deepseek-ai/DeepSeek-V3.2` | 4 | `moonshotai/Kimi-K2-Instruct` (full BRAF V600E briefing produced) |

`zai-org/GLM-5.1` returns an empty string against our strict-JSON synthesis
prompt; the synthesis-fallback handler catches this so the briefing still
ships, but it would need a prompt variant to be a first-class option.

## Module layout

```
biolitagent/
├── engine/
│   ├── schema.py              data models + pipeline state (pydantic)
│   ├── prompts.py             plan / extract / synthesise prompts
│   ├── featherless.py         OpenAI-compatible client, retry/backoff, slot semaphore
│   ├── pubmed.py              PubMed E-utilities wrapper (ESearch + EFetch)
│   ├── clinicaltrials.py      ClinicalTrials.gov v2 API wrapper
│   ├── pipeline.py            seven nodes + run_pipeline() + alias collapse + fallback
│   ├── storage.py             Supabase REST persistence (optional)
│   └── render.py              briefing → Markdown (PMID + NCT links)
├── app.py                     FastAPI app: SSE + history + notes + PDF endpoints
├── templates/
│   ├── index.html             scientist UI shell (sidebar + workspace)
│   └── briefing_print.html    Jinja template WeasyPrint renders into a PDF
├── static/                    UI styles, JS, logo, brand assets
├── scripts/
│   └── backfill_authors.py    one-off: re-fetch authors for legacy paper rows
├── run.py                     headless CLI entrypoint
├── evals.py                   recall benchmark runner
├── evals_quality.py           precision + citation-grounding benchmark
└── docs/                      this folder
```

## Dependencies by layer

| Layer | Packages |
|---|---|
| Engine + CLI | `openai`, `pydantic`, `httpx`, `python-dotenv` |
| Web UI | `fastapi`, `uvicorn[standard]`, `jinja2` |
| Server-side PDF | `weasyprint` (plus the Pango/Cairo system libraries — `brew install pango cairo libffi` on macOS, or the matching apt packages on Debian/Ubuntu) |

The engine and CLI run without the web-UI / PDF packages — uninstall them
if you only want the library. The PDF endpoint also degrades gracefully:
if WeasyPrint can't load its system libraries at import time, the server
disables `/api/briefing/{id}/pdf` with a 503 and the UI falls back to the
browser's "Save as PDF" path.
