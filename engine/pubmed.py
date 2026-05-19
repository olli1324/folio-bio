"""PubMed access via the NCBI E-utilities API.

Two calls matter here: ESearch (query -> PMIDs) and EFetch (PMIDs -> records
with abstracts). No model calls happen in this module.

Rate limits: NCBI allows 3 requests/second without an API key and 10/second
with one. The key is free. A light throttle keeps calls under whichever limit
applies.
"""

from __future__ import annotations

import asyncio
import os
import time
import xml.etree.ElementTree as ET

import httpx

from engine.schema import Paper

_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


class PubMedError(RuntimeError):
    """Raised on an E-utilities request or parsing failure."""


class PubMedClient:
    """Async wrapper around NCBI ESearch and EFetch."""

    def __init__(
        self,
        api_key: str | None = None,
        email: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.getenv("NCBI_API_KEY") or None
        self.email = email or os.getenv("NCBI_EMAIL") or None
        # 10 req/s with a key, 3 without. Leave a little headroom.
        self._min_interval = 1.0 / (9.0 if self.api_key else 2.5)
        self._timeout = timeout
        self._lock = asyncio.Lock()
        self._last_call = 0.0

    def _common_params(self) -> dict:
        params = {"tool": "biolitagent"}
        if self.api_key:
            params["api_key"] = self.api_key
        if self.email:
            params["email"] = self.email
        return params

    async def _throttle(self) -> None:
        """Space requests out to stay under the NCBI rate limit."""
        async with self._lock:
            wait = self._min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    async def search(self, terms: list[str], retmax: int = 25) -> list[str]:
        """Run ESearch over the terms (OR-joined). Returns PMIDs by relevance."""
        if not terms:
            return []
        query = " OR ".join(f"({t})" for t in terms)
        params = {
            **self._common_params(),
            "db": "pubmed",
            "term": query,
            "retmax": str(retmax),
            "retmode": "json",
            "sort": "relevance",
        }
        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{_BASE}/esearch.fcgi", params=params)
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise PubMedError(f"ESearch failed: {exc}") from exc
        return data.get("esearchresult", {}).get("idlist", [])

    async def fetch(self, pmids: list[str]) -> list[Paper]:
        """Run EFetch for the PMIDs and parse them into Paper records."""
        if not pmids:
            return []
        params = {
            **self._common_params(),
            "db": "pubmed",
            "id": ",".join(pmids),
            "rettype": "abstract",
            "retmode": "xml",
        }
        await self._throttle()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{_BASE}/efetch.fcgi", params=params)
                resp.raise_for_status()
                xml = resp.text
        except httpx.HTTPError as exc:
            raise PubMedError(f"EFetch failed: {exc}") from exc

        try:
            root = ET.fromstring(xml)
        except ET.ParseError as exc:
            raise PubMedError(f"EFetch returned unparseable XML: {exc}") from exc

        papers = [
            _parse_article(node) for node in root.findall(".//PubmedArticle")
        ]
        # Preserve the relevance order ESearch gave us.
        by_pmid = {p.pmid: p for p in papers}
        return [by_pmid[pid] for pid in pmids if pid in by_pmid]


def _text(node: ET.Element | None) -> str:
    """Flatten an element's text, including any inline markup children."""
    if node is None:
        return ""
    return "".join(node.itertext()).strip()


def _parse_article(article: ET.Element) -> Paper:
    """Turn one <PubmedArticle> element into a Paper."""
    pmid = _text(article.find(".//MedlineCitation/PMID"))

    title = _text(article.find(".//Article/ArticleTitle"))

    # An abstract may be split into several labelled sections.
    sections = []
    for chunk in article.findall(".//Article/Abstract/AbstractText"):
        label = chunk.get("Label")
        body = _text(chunk)
        if not body:
            continue
        sections.append(f"{label}: {body}" if label else body)
    abstract = "\n".join(sections)

    authors = []
    for author in article.findall(".//Article/AuthorList/Author"):
        last = _text(author.find("LastName"))
        fore = _text(author.find("ForeName"))
        collective = _text(author.find("CollectiveName"))
        if last:
            authors.append(f"{last} {fore}".strip())
        elif collective:
            authors.append(collective)

    journal = _text(article.find(".//Article/Journal/Title"))

    year = _text(article.find(".//Article/Journal/JournalIssue/PubDate/Year"))
    if not year:
        # Some records only carry a free-text MedlineDate like "2023 Jan-Feb".
        medline = _text(
            article.find(".//Article/Journal/JournalIssue/PubDate/MedlineDate")
        )
        year = medline.split(" ")[0] if medline else ""

    return Paper(
        pmid=pmid,
        title=title,
        abstract=abstract,
        authors=authors,
        journal=journal,
        year=year,
    )
