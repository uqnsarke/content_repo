"""OpenAlex adapter — literature.

OpenAlex is the right starting point: no key, no quota beyond politeness, and
it carries the full citation graph, which later feeds replication counting.
Set `OPENALEX_MAILTO` to enter the polite pool (faster, more stable).

Abstracts arrive as an inverted index (`{token: [positions]}`) and are
reconstructed here; without that step most records would carry title text only
and abstract-level axis matching would silently vanish.
"""

from __future__ import annotations

import os
import time
from typing import Iterator

import requests

BASE = "https://api.openalex.org/works"
DEFAULT_FILTER = (
    "title_and_abstract.search:alzheimer OR title_and_abstract.search:"
    '"alzheimer\'s disease"'
)


def _abstract_from_index(index: dict[str, list[int]] | None) -> str:
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for token, idxs in index.items():
        positions.extend((i, token) for i in idxs)
    positions.sort()
    return " ".join(tok for _, tok in positions)


def fetch(query: str = "alzheimer", limit: int = 500, per_page: int = 200,
          from_year: int | None = 2015, session: requests.Session | None = None) -> Iterator[dict]:
    """Yield record dicts for works matching `query`.

    Uses cursor paging, which is the only paging mode OpenAlex supports past
    10,000 results.
    """
    session = session or requests.Session()
    mailto = os.environ.get("OPENALEX_MAILTO")
    filters = [f"title_and_abstract.search:{query}"]
    if from_year:
        filters.append(f"from_publication_date:{from_year}-01-01")

    cursor, seen = "*", 0
    while cursor and seen < limit:
        params = {
            "filter": ",".join(filters),
            "per-page": min(per_page, limit - seen),
            "cursor": cursor,
        }
        if mailto:
            params["mailto"] = mailto
        resp = session.get(BASE, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        for work in payload.get("results", []):
            seen += 1
            yield to_record(work)
        cursor = payload.get("meta", {}).get("next_cursor")
        time.sleep(0.1)


def to_record(work: dict) -> dict:
    ids = work.get("ids") or {}
    pmid = (ids.get("pmid") or "").rsplit("/", 1)[-1] or None
    location = work.get("primary_location") or {}
    source = location.get("source") or {}
    return {
        "kind": "work",
        "source": "openalex",
        "external_id": (work.get("id") or "").rsplit("/", 1)[-1],
        "doi": (work.get("doi") or "").replace("https://doi.org/", "") or None,
        "pmid": pmid,
        "title": work.get("display_name") or "",
        "abstract": _abstract_from_index(work.get("abstract_inverted_index")),
        "year": work.get("publication_year"),
        "venue": source.get("display_name"),
        "url": work.get("doi") or work.get("id"),
        "cited_by": work.get("cited_by_count"),
        "raw": {"type": work.get("type"), "concepts": [
            c.get("display_name") for c in (work.get("concepts") or [])[:8]
        ]},
    }
