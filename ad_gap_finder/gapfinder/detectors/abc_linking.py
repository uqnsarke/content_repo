"""Swanson ABC linking: A→B and B→C are published, A→C has never been tested.

Operates on the `claims` table, which is populated either by the agent's
`extract_claims` tool or by any other extractor. The traversal itself is plain
graph code — the LLM's job is turning prose into triples, not deciding what is
missing, because "what is missing" is exactly the judgement a language model
will confabulate if asked for it directly.

Two filters keep the output from degenerating into every path in the graph:

`min_bridges`  a single shared intermediate is usually coincidence; requiring
               several independent B terms is what made Swanson's original
               fish-oil/Raynaud link hold up.
`context`      A→B and B→C observed in unrelated contexts (different tissue,
               different species) do not compose. Overlapping context is
               scored, not required, so cross-context links still surface but
               rank below within-context ones.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict


def _load(conn: sqlite3.Connection):
    out = []
    for r in conn.execute("SELECT id, subject, relation, object, direction, context, "
                          "record_id, confidence FROM claims"):
        out.append({
            "id": r["id"], "subject": r["subject"], "relation": r["relation"],
            "object": r["object"], "direction": r["direction"],
            "context": json.loads(r["context"] or "{}"),
            "record_id": r["record_id"], "confidence": r["confidence"],
        })
    return out


def _context_overlap(a: dict, b: dict) -> float:
    keys = set(a) & set(b)
    if not keys:
        return 0.0
    return sum(1 for k in keys if a[k] == b[k]) / len(keys)


def detect(conn: sqlite3.Connection, min_bridges: int = 2, limit: int = 20) -> list[dict]:
    claims = _load(conn)
    if not claims:
        return []

    out_edges: dict[str, list[dict]] = defaultdict(list)
    in_edges: dict[str, list[dict]] = defaultdict(list)
    direct: set[tuple[str, str]] = set()
    for c in claims:
        out_edges[c["subject"]].append(c)
        in_edges[c["object"]].append(c)
        direct.add((c["subject"], c["object"]))
        direct.add((c["object"], c["subject"]))

    bridges: dict[tuple[str, str], list[tuple[str, dict, dict]]] = defaultdict(list)
    for b in set(out_edges) & set(in_edges):
        for ab in in_edges[b]:            # A → B
            for bc in out_edges[b]:       # B → C
                a, c = ab["subject"], bc["object"]
                if a == c or (a, c) in direct:
                    continue
                bridges[(a, c)].append((b, ab, bc))

    gaps: list[dict] = []
    for (a, c), paths in bridges.items():
        distinct_b = {b for b, _, _ in paths}
        if len(distinct_b) < min_bridges:
            continue
        overlap = max(_context_overlap(ab["context"], bc["context"]) for _, ab, bc in paths)
        # Sign of the composed relation, when every path agrees on it.
        signs = {ab["direction"] * bc["direction"] for _, ab, bc in paths
                 if ab["direction"] and bc["direction"]}
        predicted = {1: "positive", -1: "negative"}.get(
            next(iter(signs)) if len(signs) == 1 else 0, "unsigned")
        support = sum(min(ab["confidence"], bc["confidence"]) for _, ab, bc in paths)
        priority = round(min(1.0, len(distinct_b) / 4) *
                         (0.5 + 0.5 * overlap) *
                         min(1.0, support / 4), 4)
        gaps.append({
            "kind": "abc",
            "key": {"a": a, "c": c},
            "title": f"{a} → {c} is implied by {len(distinct_b)} intermediates but never tested",
            "detail": (
                f"Bridging terms: {', '.join(sorted(distinct_b)[:6])}"
                f"{'…' if len(distinct_b) > 6 else ''}. No claim in the graph links "
                f"{a} to {c} directly. Predicted direction of effect: {predicted}. "
                f"Context overlap between the two halves: {overlap:.0%} — "
                + ("the halves were observed under the same conditions, so they compose."
                   if overlap >= 0.5 else
                   "the halves come from different conditions, so the composition may not hold.")
            ),
            "scores": {"n_bridges": len(distinct_b), "n_paths": len(paths),
                       "context_overlap": round(overlap, 3), "predicted": predicted,
                       "support": round(support, 2)},
            "priority": priority,
            "evidence": [(p[1]["record_id"], "supporting", f"{a} → {p[0]}") for p in paths[:5]
                         if p[1]["record_id"]] +
                        [(p[2]["record_id"], "supporting", f"{p[0]} → {c}") for p in paths[:5]
                         if p[2]["record_id"]],
        })

    gaps.sort(key=lambda g: -g["priority"])
    return gaps[:limit]
