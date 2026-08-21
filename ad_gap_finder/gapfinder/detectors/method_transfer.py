"""Methods established in a comparator field but never applied here.

Requires two corpora in the same database, separated by `records.source`:
the AD corpus, and a comparator ingested under a tagged source
(`cli.py ingest --source openalex --query parkinson --tag pd` stores
`source = 'openalex:pd'`).

Without a comparator this detector returns nothing rather than guessing —
"method X has never been applied to AD" is a claim about the whole literature
and cannot be made from an AD-only index.

The transfer signal is deliberately conditioned on context, not just method:
a modality that exists in AD but has never been paired with a given tissue
state or stage is the transferable opportunity, and a bare method count would
miss it.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from ..axes import label

CONTEXT_AXES = ["tissue_state", "stage", "design"]


def _sources(conn: sqlite3.Connection) -> list[str]:
    return [r["source"] for r in conn.execute("SELECT DISTINCT source FROM records")]


def detect(conn: sqlite3.Connection, comparator_sources: list[str] | None = None,
           min_comparator: int = 3, limit: int = 20) -> list[dict]:
    all_sources = _sources(conn)
    comparator = comparator_sources or [s for s in all_sources if ":" in s]
    primary = [s for s in all_sources if s not in comparator]
    if not comparator or not primary:
        return []

    def modality_contexts(sources: list[str]) -> dict[tuple[str, str, str], set[int]]:
        placeholders = ",".join("?" * len(sources))
        rows = conn.execute(
            "SELECT ra.record_id, ra.axis, ra.term FROM record_axes ra "
            "JOIN records r ON r.id = ra.record_id "
            f"WHERE r.source IN ({placeholders}) AND ra.inferred = 0", sources).fetchall()
        by_record: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        for r in rows:
            by_record[r["record_id"]][r["axis"]].add(r["term"])
        out: dict[tuple[str, str, str], set[int]] = defaultdict(set)
        for rid, ax in by_record.items():
            for modality in ax.get("modality", ()):
                for caxis in CONTEXT_AXES:
                    for term in ax.get(caxis, ()):
                        out[(modality, caxis, term)].add(rid)
        return out

    here = modality_contexts(primary)
    there = modality_contexts(comparator)

    gaps: list[dict] = []
    for combo, records in there.items():
        if len(records) < min_comparator or combo in here:
            continue
        modality, caxis, term = combo
        # Only interesting if the primary corpus uses this method at all and
        # works in this context at all — otherwise it is an unrelated field,
        # not an untried transfer.
        method_here = sum(len(v) for k, v in here.items() if k[0] == modality)
        context_here = sum(len(v) for k, v in here.items() if k[1:] == (caxis, term))
        if not method_here or not context_here:
            continue
        priority = round(min(1.0, len(records) / 10) *
                         min(1.0, method_here / 10) *
                         min(1.0, context_here / 10), 4)
        gaps.append({
            "kind": "method_transfer",
            "key": {"modality": modality, "context_axis": caxis, "context_term": term},
            "title": (f"{label('modality', modality)} has never been applied to "
                      f"{label(caxis, term)} here"),
            "detail": (
                f"The comparator corpus has {len(records)} record(s) pairing "
                f"{label('modality', modality)} with {label(caxis, term)}. The primary "
                f"corpus uses {label('modality', modality)} ({method_here} records) and "
                f"works on {label(caxis, term)} ({context_here} records) but has never "
                f"combined them — the method is proven and the context is active, only "
                f"the pairing is missing."),
            "scores": {"comparator_records": len(records), "method_here": method_here,
                       "context_here": context_here},
            "priority": priority,
            "evidence": [(rid, "supporting", "comparator corpus") for rid in sorted(records)[:8]],
        })

    gaps.sort(key=lambda g: -g["priority"])
    return gaps[:limit]
