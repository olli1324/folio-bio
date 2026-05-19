"""Backfill the `authors` column on `biolit_briefing_papers`.

When the schema migration added the `authors jsonb` column, paper rows that
had already been persisted by older runs were left with NULL / empty
`authors`. This script:

  1. Reads every paper row missing authors.
  2. Re-fetches each unique PMID from PubMed.
  3. Patches the row with the parsed author list.

Safe to re-run; it only touches rows where authors are currently empty.

Run from the repo root:

    python scripts/backfill_authors.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

# Allow `from engine.pubmed import ...` when invoked as `python scripts/...`.
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from dotenv import load_dotenv

from engine.pubmed import PubMedClient

_TABLE = "biolit_briefing_papers"
_PAGE = 1000  # rows per Supabase GET; PostgREST default cap is high


async def main() -> int:
    load_dotenv()
    url = os.getenv("SUPABASE_URL", "").rstrip("/")
    key = os.getenv("SUPABASE_SECRET_KEY", "")
    if not url or not key:
        print("Set SUPABASE_URL + SUPABASE_SECRET_KEY in .env.", file=sys.stderr)
        return 1

    base = f"{url}/rest/v1"
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }

    # --- 1. Pull every paper row + its current authors (paginated) ---
    print("Reading paper rows from Supabase...")
    rows: list[dict] = []
    async with httpx.AsyncClient(timeout=60.0) as client:
        offset = 0
        while True:
            range_hdr = {**headers, "Range-Unit": "items",
                        "Range": f"{offset}-{offset + _PAGE - 1}"}
            resp = await client.get(
                f"{base}/{_TABLE}",
                params={"select": "id,pmid,authors", "order": "created_at.asc"},
                headers=range_hdr,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            rows.extend(batch)
            if len(batch) < _PAGE:
                break
            offset += _PAGE

    missing = [r for r in rows if not r.get("authors") and r.get("pmid")]
    print(f"  {len(rows)} paper rows total; {len(missing)} need backfill.")
    if not missing:
        return 0

    # --- 2. Fetch authors for each unique PMID (one EFetch per batch) ---
    pmids = sorted({r["pmid"] for r in missing})
    print(f"  Fetching authors for {len(pmids)} unique PMIDs from PubMed...")

    pm = PubMedClient()
    pmid_to_authors: dict[str, list[str]] = {}
    batch_size = 100
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        papers = await pm.fetch(batch)
        for paper in papers:
            pmid_to_authors[paper.pmid] = paper.authors
        done = min(i + batch_size, len(pmids))
        print(f"    fetched {done}/{len(pmids)}")

    # --- 3. PATCH each row that we now have authors for ---
    print(f"  Updating Supabase rows...")
    updated = skipped = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        for row in missing:
            authors = pmid_to_authors.get(row["pmid"], [])
            if not authors:
                skipped += 1
                continue
            resp = await client.patch(
                f"{base}/{_TABLE}",
                params={"id": f"eq.{row['id']}"},
                json={"authors": authors},
                headers=headers,
            )
            if resp.status_code in (200, 204):
                updated += 1
            else:
                print(f"    row {row['id']}: HTTP {resp.status_code} {resp.text[:120]}")
                skipped += 1

    print(f"\nDone. Updated {updated} rows; skipped {skipped} "
          f"(no author data returned for those PMIDs).")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
