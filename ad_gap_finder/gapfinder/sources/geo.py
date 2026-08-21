"""NCBI GEO adapter — datasets.

Two-step E-utilities flow: `esearch` against db=gds for UIDs, then `esummary`
for metadata. An NCBI_API_KEY raises the rate limit from 3 to 10 requests per
second; without one this adapter throttles itself to stay inside the limit.

Only GSE series entries are kept. GDS/GPL rows describe curated subsets and
platforms, and counting them as datasets inflates coverage of exactly the
well-studied regions that already dominate the matrix.
"""

from __future__ import annotations

import os
import time
from typing import Iterator

import requests

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
DEFAULT_TERM = (
    '(alzheimer[All Fields] OR "alzheimer disease"[All Fields]) '
    'AND ("expression profiling by high throughput sequencing"[DataSet Type] '
    'OR "genome variation profiling by high throughput sequencing"[DataSet Type])'
)


def _params(extra: dict) -> dict:
    p = {"retmode": "json", "tool": "ad_gap_finder", **extra}
    key = os.environ.get("NCBI_API_KEY")
    if key:
        p["api_key"] = key
    email = os.environ.get("OPENALEX_MAILTO")
    if email:
        p["email"] = email
    return p


def _delay() -> float:
    return 0.11 if os.environ.get("NCBI_API_KEY") else 0.34


def fetch(term: str = DEFAULT_TERM, limit: int = 500,
          session: requests.Session | None = None) -> Iterator[dict]:
    session = session or requests.Session()
    resp = session.get(f"{EUTILS}/esearch.fcgi",
                       params=_params({"db": "gds", "term": term, "retmax": limit}),
                       timeout=60)
    resp.raise_for_status()
    uids = resp.json().get("esearchresult", {}).get("idlist", [])
    time.sleep(_delay())

    for i in range(0, len(uids), 200):
        batch = uids[i:i + 200]
        summary = session.get(f"{EUTILS}/esummary.fcgi",
                              params=_params({"db": "gds", "id": ",".join(batch)}),
                              timeout=60)
        summary.raise_for_status()
        result = summary.json().get("result", {})
        for uid in result.get("uids", []):
            entry = result.get(uid) or {}
            if (entry.get("entrytype") or "").upper() != "GSE":
                continue
            yield to_record(entry)
        time.sleep(_delay())


TAXON_TO_SPECIES = {
    "homo sapiens": "human",
    "mus musculus": "mouse",
    "rattus norvegicus": "rat",
    "macaca mulatta": "nhp",
    "danio rerio": "other_model",
    "drosophila melanogaster": "other_model",
}


def to_record(entry: dict) -> dict:
    accession = entry.get("accession") or f"GDS-UID-{entry.get('uid')}"
    taxa = [t.strip().lower() for t in (entry.get("taxon") or "").split(";") if t.strip()]
    species = [TAXON_TO_SPECIES[t] for t in taxa if t in TAXON_TO_SPECIES]
    year = None
    pdat = entry.get("PDAT") or entry.get("pdat") or ""
    if pdat[:4].isdigit():
        year = int(pdat[:4])
    return {
        "kind": "dataset",
        "source": "geo",
        "external_id": accession,
        "title": entry.get("title") or "",
        "abstract": entry.get("summary") or "",
        "year": year,
        "url": f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={accession}",
        "raw": {"gdstype": entry.get("gdsType"), "n_samples": entry.get("n_samples"),
                "taxon": entry.get("taxon"), "pubmed": entry.get("pubmedids")},
        "curated": {"species": species} if species else {},
        # GEO reports samples, not donors; conflating the two would overstate
        # cohort size wherever a donor contributed several libraries.
        "n_samples": entry.get("n_samples"),
        "pmid": str((entry.get("pubmedids") or [None])[0]) if entry.get("pubmedids") else None,
    }
