"""Claims that disagree, and claims nobody ever checked.

Both are gaps, for opposite reasons. A contradiction is a question the field
has answered twice, differently, and usually stopped arguing about rather than
resolved. A singleton is a claim that entered the literature once and has been
cited ever since without independent replication — which reads identically to
an established finding in any citation-count view.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict


def detect(conn: sqlite3.Connection, limit: int = 20,
           singleton_min_citations: int = 25) -> list[dict]:
    pairs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in conn.execute(
        "SELECT c.id, c.subject, c.object, c.relation, c.direction, c.context, "
        "c.record_id, c.confidence, r.cited_by, r.cohort "
        "FROM claims c LEFT JOIN records r ON r.id = c.record_id"
    ):
        pairs[(r["subject"], r["object"])].append({
            "direction": r["direction"], "relation": r["relation"],
            "record_id": r["record_id"], "cited_by": r["cited_by"] or 0,
            "cohort": r["cohort"], "context": json.loads(r["context"] or "{}"),
            "confidence": r["confidence"],
        })

    conflicts: list[dict] = []
    singletons: list[dict] = []
    for (subject, obj), claims in pairs.items():
        directions = {c["direction"] for c in claims if c["direction"]}

        if len(directions) > 1:
            up = [c for c in claims if c["direction"] > 0]
            down = [c for c in claims if c["direction"] < 0]
            contexts = {json.dumps(c["context"], sort_keys=True) for c in claims}
            # Disagreement under identical conditions is a real contradiction;
            # disagreement across conditions may be a genuine context effect,
            # which is a different and more tractable question.
            same_context = len(contexts) == 1
            priority = round(min(1.0, len(claims) / 4) * (1.0 if same_context else 0.6), 4)
            conflicts.append({
                "kind": "contradiction",
                "key": {"subject": subject, "object": obj},
                "title": f"{subject} → {obj}: sources disagree on direction",
                "detail": (
                    f"{len(up)} record(s) report an increase, {len(down)} a decrease. "
                    + ("All were observed under the same annotated context, so this is a "
                       "direct conflict rather than a context effect."
                       if same_context else
                       "The conflicting reports come from different annotated contexts, so "
                       "the disagreement may itself be the finding — an effect that reverses "
                       "with tissue, stage or species.")),
                "scores": {"n_claims": len(claims), "n_up": len(up), "n_down": len(down),
                           "same_context": same_context},
                "priority": priority,
                "evidence": [(c["record_id"], "contradicting",
                              "increase" if c["direction"] > 0 else "decrease")
                             for c in claims if c["record_id"]][:10],
            })
            continue

        if len(claims) == 1:
            only = claims[0]
            if only["cited_by"] < singleton_min_citations:
                continue  # an uncited singleton is noise, not an unreplicated pillar
            # Capped at 0.5 so a well-cited singleton never outranks a direct
            # conflict: "two groups disagree" is a sharper, cheaper piece of
            # work than "nobody has checked this", and both are stored in one
            # table where priority has to mean the same thing.
            priority = round(0.5 * min(1.0, only["cited_by"] / 300), 4)
            singletons.append({
                "kind": "contradiction",
                "key": {"subject": subject, "object": obj, "mode": "singleton"},
                "title": f"{subject} → {obj}: single source, {only['cited_by']} citations, no replication",
                "detail": (
                    f"One record supports this claim and nothing in the corpus tests it "
                    f"again, yet it has accumulated {only['cited_by']} citations"
                    + (f" and comes from a single cohort ({only['cohort']})." if only["cohort"]
                       else ".")
                    + " Independent replication is the cheapest available piece of work here."),
                "scores": {"n_claims": 1, "cited_by": only["cited_by"]},
                "priority": priority,
                "evidence": [(only["record_id"], "supporting", "sole source")]
                            if only["record_id"] else [],
            })

    # Every claim in a sparse graph is a singleton, so unreplicated claims can
    # bury the direct conflicts entirely. Conflicts are the scarcer and more
    # actionable finding, so they take the budget first and singletons fill
    # what is left, up to half the output.
    conflicts.sort(key=lambda g: -g["priority"])
    singletons.sort(key=lambda g: -g["priority"])
    room = max(0, limit - len(conflicts))
    return conflicts[:limit] + singletons[:min(room, limit // 2)]
