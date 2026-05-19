"""Folio web app.

Serves a small, scientist-facing UI on top of `engine.pipeline.run_pipeline`.
The pipeline emits `ProgressEvent`s as it runs; this layer turns them into a
Server-Sent Events stream so the browser can render the live trace, the
papers panel, and the final briefing without any custom polling.

The engine is the source of truth. This layer adds no logic; it only:

  1. Serves the static HTML shell.
  2. Exposes `GET /api/run?query=...` as an SSE stream of pipeline events.
  3. Renders the briefing's Markdown to HTML on completion.

Run with:

    python app.py
    # -> http://127.0.0.1:8000

The engine still works without these deps; FastAPI / uvicorn / Jinja2 are
only loaded when this file is executed.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator

# WeasyPrint needs to find Pango/Cairo at dlopen time. On macOS Homebrew
# installs them under /opt/homebrew/lib; on Apple Silicon machines they
# aren't on the default library path. Set the env var *before* importing
# weasyprint so cffi.dlopen can find them. Safe no-op on Linux/Docker.
if os.name == "posix" and "DYLD_FALLBACK_LIBRARY_PATH" not in os.environ:
    os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = "/opt/homebrew/lib:/usr/local/lib"

try:
    from weasyprint import HTML as WeasyHTML  # noqa: E402
    _WEASYPRINT_OK = True
except Exception as _weasy_err:  # noqa: BLE001 -- defensive import
    WeasyHTML = None  # type: ignore[assignment]
    _WEASYPRINT_OK = False
    _WEASY_IMPORT_ERROR = _weasy_err

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from engine.auth import resolve_auth
from engine.pipeline import get_default_clients, run_pipeline
from engine.render import briefing_to_markdown
from engine.schema import Stage
from engine.storage import SupabaseStorage

load_dotenv()

logger = logging.getLogger("biolitagent.app")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE = Path(__file__).parent
app = FastAPI(title="Folio", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# Storage is optional: when SUPABASE_URL / SUPABASE_SECRET_KEY are unset,
# `from_env()` returns None and the app still serves the live pipeline,
# just without history.
storage: SupabaseStorage | None = None


@app.on_event("startup")
async def _warm_clients() -> None:
    """Construct the shared singleton clients at boot so the first query is
    not slowed down by lazy initialisation."""
    global storage
    get_default_clients()
    storage = SupabaseStorage.from_env()
    if storage is None:
        logger.info("Folio ready (no Supabase configured -- history disabled)")
    else:
        logger.info("Folio ready with Supabase history persistence")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    # Expose the public Supabase URL + anon (publishable) key to the frontend
    # so it can call Supabase Auth directly (signup / login / refresh) without
    # routing those requests through us. The secret service-role key stays
    # server-side. Both values are empty strings if not configured -- the UI
    # then hides the auth controls.
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "supabase_url": os.getenv("SUPABASE_URL", ""),
            "supabase_anon_key": os.getenv("SUPABASE_ANON_KEY", ""),
        },
    )


@app.get("/healthz")
async def healthz() -> JSONResponse:
    """Liveness probe. Returns 200 once the app process is responsive."""
    return JSONResponse({"status": "ok", "history": storage is not None})


@app.get("/api/history")
async def api_history(request: Request, limit: int = 30) -> JSONResponse:
    """Recent briefings, newest first.

    Auth semantics:
      - Logged-in users see their own briefings plus any curated
        is_demo=true rows.
      - Anonymous visitors see only the curated demo briefings.
      - Returns [] if Supabase isn't configured.
    """
    if storage is None:
        return JSONResponse([])
    auth = await resolve_auth(request.headers.get("Authorization"))
    rows = await storage.list_briefings(
        limit=limit,
        user_id=auth.user_id,
        include_demo=True,
    )
    return JSONResponse(rows)


@app.get("/api/briefing/{briefing_id}")
async def api_briefing(briefing_id: str, request: Request) -> JSONResponse:
    """Full past briefing + papers.

    Access rules:
      - Owner (briefing.user_id == caller.user_id): always allowed.
      - Demo (briefing.is_demo == true): allowed for anyone.
      - Anonymous briefing (user_id is null): allowed for anyone with the id
        (link-share model — same as before auth landed).
      - Someone else's owned briefing: 404 (not 403, to avoid disclosing existence).
    """
    if storage is None:
        return JSONResponse({"error": "history disabled"}, status_code=404)
    auth = await resolve_auth(request.headers.get("Authorization"))
    briefing = await storage.get_briefing(briefing_id)
    if not briefing:
        return JSONResponse({"error": "not found"}, status_code=404)

    owner_id = briefing.get("user_id")
    is_demo = bool(briefing.get("is_demo"))
    if owner_id and owner_id != auth.user_id and not is_demo:
        return JSONResponse({"error": "not found"}, status_code=404)

    papers = await storage.get_papers(briefing_id)
    return JSONResponse({"briefing": briefing, "papers": papers})


# --- PDF rendering helpers ------------------------------------------------

_PHASE_LABEL = {
    "EARLY_PHASE1": "Early Phase I", "PHASE1": "Phase I",
    "PHASE1_PHASE2": "Phase I/II", "PHASE2": "Phase II",
    "PHASE2_PHASE3": "Phase II/III", "PHASE3": "Phase III",
    "PHASE4": "Phase IV",
}


def _logo_data_uri() -> str | None:
    """Embed the logo as a data URI so WeasyPrint can render it without
    fetching from a network URL during the render pass."""
    logo_path = BASE / "static" / "logo-tagline.png"
    if not logo_path.exists():
        logo_path = BASE / "static" / "logo.png"
    if not logo_path.exists():
        return None
    data = logo_path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def _enrich_briefing_for_pdf(briefing: dict) -> dict:
    """Add phase labels to trial_info so the Jinja template can show them
    as 'Phase III' rather than the raw 'PHASE3' enum string."""
    for compound in briefing.get("top_compounds", []) or []:
        info = compound.get("trial_info") or {}
        if info:
            info["phase_label"] = _PHASE_LABEL.get(info.get("highest_phase") or "", "")
    return briefing


@app.get("/api/briefing/{briefing_id}/pdf")
async def api_briefing_pdf(briefing_id: str, request: Request) -> Response:
    """Render a past briefing as a real PDF using WeasyPrint.

    Bypasses the browser print dialog entirely, so margins are guaranteed
    consistent across every device. Returns 503 if weasyprint failed to
    import (missing system libs); the UI falls back to browser print in
    that case. Same auth rules as `GET /api/briefing/{id}`: owner, demo,
    or anonymous-link briefings are readable; someone else's owned
    briefing returns 404.
    """
    if not _WEASYPRINT_OK:
        return JSONResponse(
            {"error": f"server PDF unavailable: {_WEASY_IMPORT_ERROR}"},
            status_code=503,
        )
    if storage is None:
        return JSONResponse({"error": "history disabled"}, status_code=404)

    auth = await resolve_auth(request.headers.get("Authorization"))
    briefing_row = await storage.get_briefing(briefing_id)
    if not briefing_row:
        return JSONResponse({"error": "not found"}, status_code=404)
    owner_id = briefing_row.get("user_id")
    if owner_id and owner_id != auth.user_id and not briefing_row.get("is_demo"):
        return JSONResponse({"error": "not found"}, status_code=404)
    briefing = _enrich_briefing_for_pdf(briefing_row.get("briefing") or {})

    html = templates.get_template("briefing_print.html").render({
        "briefing": briefing,
        "notes": briefing_row.get("notes") or "",
        "paper_count": briefing_row.get("paper_count") or 0,
        "generated_on": datetime.utcnow().strftime("%-d %B %Y"),
        "logo_data_uri": _logo_data_uri(),
    })

    # Render to PDF. WeasyPrint can be slow on first call (~1s); subsequent
    # calls warm-cache faster. Do it off-thread so the event loop isn't
    # blocked while rendering.
    def _render() -> bytes:
        return WeasyHTML(string=html, base_url=str(BASE)).write_pdf()

    pdf_bytes = await asyncio.to_thread(_render)

    slug = "".join(
        c if c.isalnum() or c in "-_" else "-"
        for c in (briefing.get("query") or "briefing").lower()
    ).strip("-")[:64] or "briefing"

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="folio-{slug}.pdf"',
            "Cache-Control": "no-cache",
        },
    )


@app.patch("/api/briefing/{briefing_id}/notes")
async def api_update_notes(
    briefing_id: str, payload: dict, request: Request
) -> JSONResponse:
    """Save free-text notes a scientist attaches to a briefing.

    Body: {"notes": "string"}. Always overwrites — the UI keeps the
    authoritative copy in the textarea and PATCHes after a debounce.

    Auth: only the briefing's owner can edit. Anonymous briefings
    (user_id null) are writable by anyone with the link, matching read
    semantics. Demo briefings are read-only — no notes editing allowed.
    """
    if storage is None:
        return JSONResponse({"error": "history disabled"}, status_code=404)

    auth = await resolve_auth(request.headers.get("Authorization"))
    existing = await storage.get_briefing(briefing_id)
    if not existing:
        return JSONResponse({"error": "not found"}, status_code=404)
    owner_id = existing.get("user_id")
    if existing.get("is_demo"):
        return JSONResponse({"error": "demo is read-only"}, status_code=403)
    if owner_id and owner_id != auth.user_id:
        return JSONResponse({"error": "not found"}, status_code=404)

    notes = ""
    if isinstance(payload, dict):
        raw = payload.get("notes", "")
        if isinstance(raw, str):
            notes = raw
    ok = await storage.update_notes(briefing_id, notes)
    if not ok:
        return JSONResponse({"error": "update failed"}, status_code=500)
    return JSONResponse({"ok": True})


@app.get("/api/run")
async def api_run(
    query: str, request: Request, papers: int = 25
) -> StreamingResponse:
    """Stream pipeline progress events as Server-Sent Events.

    `papers` caps the PubMed search size; clamped to [5, 50] to keep both the
    UI responsive and Featherless slot budget bounded.

    Each event is a single `data: {...JSON...}\\n\\n` frame matching the
    `ProgressEvent` schema, with one additional synthetic frame on completion
    carrying the rendered Markdown of the briefing.
    """
    query = (query or "").strip()
    if not query:
        return JSONResponse({"error": "missing query"}, status_code=400)
    # Defensive clamp -- a UI bug must not be able to flood Featherless.
    papers = max(5, min(50, papers))

    # Resolve auth once, up-front. EventSource can't set custom headers, so
    # the SSE endpoint also accepts `?token=<jwt>` as a fallback. Anonymous
    # runs keep user_id=None on the persisted briefing.
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        token_qs = request.query_params.get("token")
        if token_qs:
            auth_header = f"Bearer {token_qs}"
    auth = await resolve_auth(auth_header)

    async def event_source() -> AsyncIterator[bytes]:
        # Accumulators for the persistence write on DONE.
        papers_by_pmid: dict[str, dict] = {}
        extraction_count = 0
        start = asyncio.get_event_loop().time()

        try:
            async for event in run_pipeline(query, max_papers=papers):
                # If the client has disconnected, abandon the stream cleanly.
                if await request.is_disconnected():
                    logger.info("Client disconnected; abandoning stream.")
                    return

                # Accumulate the data we need to persist on DONE.
                if event.stage is Stage.RETRIEVE and event.detail.get("papers"):
                    for p in event.detail["papers"]:
                        if p.get("pmid"):
                            papers_by_pmid[p["pmid"]] = {**p, "extraction": None}
                elif event.stage is Stage.EXTRACT and event.detail.get("pmid"):
                    pmid = event.detail["pmid"]
                    if pmid in papers_by_pmid:
                        papers_by_pmid[pmid]["extraction"] = event.detail.get("extraction")
                    if event.detail.get("ok"):
                        extraction_count += 1

                payload = {
                    "stage": event.stage.value,
                    "message": event.message,
                    "detail": event.detail,
                }
                yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")

                # On DONE, persist the briefing + papers and emit a synthetic
                # `render` event carrying the rendered Markdown plus the
                # briefing_id (used by the UI for permalinks / sharing).
                if event.stage is Stage.DONE:
                    briefing = event.detail.get("briefing") or {}
                    markdown = event.detail.get("briefing_markdown") or ""
                    elapsed = asyncio.get_event_loop().time() - start

                    briefing_id: str | None = None
                    if storage is not None and briefing:
                        try:
                            briefing_id = await storage.save_briefing(
                                query=query,
                                briefing=briefing,
                                briefing_markdown=markdown,
                                paper_count=len(papers_by_pmid),
                                extraction_count=extraction_count,
                                elapsed_seconds=elapsed,
                                status="ok",
                                user_id=auth.user_id,
                            )
                            if briefing_id:
                                await storage.save_papers(
                                    briefing_id, list(papers_by_pmid.values())
                                )
                        except Exception:
                            logger.exception("Persistence failed; continuing without history.")

                    extra = {
                        "stage": "render",
                        "message": "rendered",
                        "detail": {
                            "markdown": markdown,
                            "briefing": briefing,
                            "briefing_id": briefing_id,
                            "elapsed_seconds": elapsed,
                        },
                    }
                    yield f"data: {json.dumps(extra)}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            # Browser closed mid-stream. Don't propagate as an error.
            raise
        except Exception as exc:  # noqa: BLE001 -- surface to client as event
            logger.exception("Unhandled pipeline error")
            error = {
                "stage": "error",
                "message": f"Pipeline crashed: {exc}",
                "detail": {},
            }
            yield f"data: {json.dumps(error)}\n\n".encode("utf-8")

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            # Disable buffering on common reverse proxies so SSE actually streams.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    # In production (Render / Docker), bind 0.0.0.0 so the platform's reverse
    # proxy can reach the app. Locally, default to 127.0.0.1 for safety.
    host = os.getenv("HOST", "0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
    uvicorn.run("app:app", host=host, port=port, log_level="info", reload=False)


if __name__ == "__main__":
    main()
