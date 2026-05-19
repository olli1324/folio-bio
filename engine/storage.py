"""Supabase persistence for briefing history.

Thin async wrapper over the Supabase REST API (PostgREST). Persists each
completed briefing plus the papers used to produce it, so the UI can offer a
history view and shareable permalinks.

Configured via environment:
  - SUPABASE_URL          project URL, e.g. https://xxxxx.supabase.co
  - SUPABASE_SECRET_KEY   server-side service-role / secret key

If either is missing, `from_env()` returns None and the rest of the app
treats persistence as a no-op. The engine and the CLI continue to work
without Supabase configured.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger("biolitagent.storage")


_BRIEFINGS_TABLE = "biolit_briefings"
_PAPERS_TABLE = "biolit_briefing_papers"


class SupabaseStorage:
    """Async client for the briefings + papers tables.

    All methods are safe to call when Supabase is unreachable: errors are
    logged and the methods return None or empty results. The pipeline never
    has to know whether persistence is available.
    """

    def __init__(self, url: str, secret_key: str, *, timeout: float = 10.0) -> None:
        self._base = url.rstrip("/") + "/rest/v1"
        self._headers = {
            "apikey": secret_key,
            "Authorization": f"Bearer {secret_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    @classmethod
    def from_env(cls) -> "SupabaseStorage | None":
        """Build from SUPABASE_URL + SUPABASE_SECRET_KEY env vars, or None."""
        url = os.getenv("SUPABASE_URL", "").strip()
        key = os.getenv("SUPABASE_SECRET_KEY", "").strip()
        if not url or not key:
            return None
        return cls(url=url, secret_key=key)

    # --- writes ----------------------------------------------------------

    async def save_briefing(
        self,
        *,
        query: str,
        briefing: dict,
        briefing_markdown: str,
        paper_count: int,
        extraction_count: int,
        elapsed_seconds: float,
        status: str = "ok",
        user_id: str | None = None,
    ) -> str | None:
        """Insert a briefing row, return its id, or None on failure.

        `user_id` ties the briefing to a logged-in Supabase user. Pass None
        for anonymous demo briefings; those can still be reopened via
        permalink but won't appear in any user's history sidebar.
        """
        body = {
            "query": query,
            "summary": briefing.get("summary", ""),
            "briefing": briefing,
            "briefing_markdown": briefing_markdown,
            "paper_count": paper_count,
            "extraction_count": extraction_count,
            "elapsed_seconds": elapsed_seconds,
            "status": status,
        }
        if user_id is not None:
            body["user_id"] = user_id
        row = await self._insert(_BRIEFINGS_TABLE, body)
        return row.get("id") if row else None

    async def save_papers(
        self, briefing_id: str, papers: list[dict]
    ) -> None:
        """Bulk-insert the papers used in a briefing. Best-effort."""
        if not papers:
            return
        rows = [
            {
                "briefing_id": briefing_id,
                "pmid": p.get("pmid", ""),
                "title": p.get("title", ""),
                "authors": p.get("authors") or [],
                "journal": p.get("journal", ""),
                "year": p.get("year", ""),
                "abstract": p.get("abstract", ""),
                "extraction": p.get("extraction"),
                "relevance_score": (
                    p.get("extraction") or {}
                ).get("relevance_score") if p.get("extraction") else None,
            }
            for p in papers
        ]
        await self._insert(_PAPERS_TABLE, rows)

    # --- reads -----------------------------------------------------------

    async def list_briefings(
        self,
        limit: int = 30,
        *,
        user_id: str | None = None,
        include_demo: bool = False,
    ) -> list[dict]:
        """Return the most recent briefings, newest first.

        Filter semantics:
          - logged-in (user_id set):      that user's briefings only
          - anonymous (user_id None):     returns []  (no history sidebar)
          - include_demo=True:            also includes is_demo=true rows
        """
        params = {
            "select": "id,query,summary,paper_count,elapsed_seconds,status,created_at,is_demo",
            "order": "created_at.desc",
            "limit": str(limit),
        }
        if user_id is not None and include_demo:
            # PostgREST OR: (user_id = X) OR (is_demo = true)
            params["or"] = f"(user_id.eq.{user_id},is_demo.eq.true)"
        elif user_id is not None:
            params["user_id"] = f"eq.{user_id}"
        elif include_demo:
            params["is_demo"] = "eq.true"
        else:
            # No filter would leak every briefing; explicitly return nothing.
            return []
        return await self._select(_BRIEFINGS_TABLE, params) or []

    async def get_briefing(self, briefing_id: str) -> dict | None:
        """Return one briefing by id, or None if not found / on error."""
        params = {"id": f"eq.{briefing_id}", "select": "*", "limit": "1"}
        rows = await self._select(_BRIEFINGS_TABLE, params)
        return rows[0] if rows else None

    async def get_papers(self, briefing_id: str) -> list[dict]:
        """Return the papers attached to one briefing."""
        params = {
            "briefing_id": f"eq.{briefing_id}",
            "select": "*",
            "order": "relevance_score.desc.nullslast,created_at.asc",
        }
        return await self._select(_PAPERS_TABLE, params) or []

    async def update_notes(self, briefing_id: str, notes: str) -> bool:
        """Patch the notes field on one briefing. Returns True on success.

        Notes are free-text per-briefing annotations the scientist scratches
        out while reading. Persisted independently of the briefing body so a
        re-run doesn't clobber them.
        """
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.patch(
                    f"{self._base}/{_BRIEFINGS_TABLE}",
                    params={"id": f"eq.{briefing_id}"},
                    json={"notes": notes},
                    headers=self._headers,
                )
                resp.raise_for_status()
            return True
        except httpx.HTTPError as exc:
            logger.warning("Supabase notes update for %s failed: %s", briefing_id, exc)
            return False

    # --- HTTP plumbing ---------------------------------------------------

    async def _insert(self, table: str, body: Any) -> dict | None:
        """POST to /rest/v1/{table} with `Prefer: return=representation`."""
        headers = {**self._headers, "Prefer": "return=representation"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base}/{table}", json=body, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            logger.warning("Supabase insert into %s failed: %s", table, exc)
            return None
        # POSTgREST returns an array even when inserting one row.
        if isinstance(data, list):
            return data[0] if data else None
        return data if isinstance(data, dict) else None

    async def _select(self, table: str, params: dict) -> list[dict] | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base}/{table}", params=params, headers=self._headers
                )
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPError as exc:
            logger.warning("Supabase select from %s failed: %s", table, exc)
            return None
