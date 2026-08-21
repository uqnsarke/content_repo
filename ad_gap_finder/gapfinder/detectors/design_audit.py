"""Whole evidence bases that share one methodological limitation.

The coverage detector asks what has never been looked at. This asks a
different and often sharper question: of the things that *have* been looked at,
which are supported entirely by one kind of evidence?

A topic where 40 records all carry `cross_sectional_only` is not a thin
literature — it is a confident literature that cannot, structurally, answer a
causal question. That is a gap, and it is invisible to any method that counts
publications.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict

from ..axes import label
from ..coverage import primary_sources

# Flags that limit what a body of work can conclude, with how badly.
LIMITING = {
    "cross_sectional_only":   (1.00, "cannot separate within-person trajectory from between-person variation"),
    "post_mortem_only":       (0.95, "one terminal timepoint per donor; cause and consequence are confounded"),
    "end_stage_contrast_only":(0.85, "diagnosed-vs-control contrast only; nothing about initiation"),
    "single_sex":             (0.75, "no test of the sex dimorphism that defines two-thirds of cases"),
    "single_named_cohort":    (0.70, "one cohort; replication and cohort-specific artefact are not separable"),
    "small_n":                (0.65, "donor counts below any reasonable power for between-group inference"),
    "single_ancestry":        (0.60, "findings not transportable beyond European-ancestry participants"),
    "model_system_only":      (0.55, "no human tissue anywhere in the evidence base"),
    "sex_unreported":         (0.35, "sex composition never stated, so imbalance cannot be ruled out"),
    "ancestry_unreported":    (0.30, "ancestry composition never stated"),
}

# Topic axes worth auditing. cell_type × stage is where the interesting
# monocultures live; region and modality are audited on their own.
TOPIC_AXES = ["cell_type", "stage", "region", "modality"]


def detect(conn: sqlite3.Connection, min_records: int = 5, min_prevalence: float = 0.85,
           limit: int = 25, per_flag: int = 3) -> list[dict]:
    sources = primary_sources(conn)
    src_sql = f" AND r.source IN ({','.join('?' * len(sources))})"

    flags: dict[int, set[str]] = defaultdict(set)
    for r in conn.execute(
        "SELECT rf.record_id, rf.flag FROM record_flags rf "
        "JOIN records r ON r.id = rf.record_id WHERE 1=1" + src_sql, sources):
        flags[r["record_id"]].add(r["flag"])

    topics: dict[tuple[str, str], set[int]] = defaultdict(set)
    for r in conn.execute(
        "SELECT ra.record_id, ra.axis, ra.term FROM record_axes ra "
        "JOIN records r ON r.id = ra.record_id "
        f"WHERE ra.inferred = 0 AND ra.axis IN ({','.join('?' * len(TOPIC_AXES))})" + src_sql,
        TOPIC_AXES + sources
    ):
        topics[(r["axis"], r["term"])].add(r["record_id"])

    gaps: list[dict] = []
    for (axis, term), record_ids in topics.items():
        n = len(record_ids)
        if n < min_records:
            continue
        for flag, (severity, consequence) in LIMITING.items():
            hits = [rid for rid in record_ids if flag in flags.get(rid, ())]
            prevalence = len(hits) / n
            if prevalence < min_prevalence:
                continue
            # Weight by how large the affected literature is: an entire field
            # built one way is worth more than five papers built one way.
            breadth = min(1.0, n / 40)
            priority = round(severity * prevalence * (0.4 + 0.6 * breadth), 4)
            gaps.append({
                "kind": "design",
                "key": {"axis": axis, "term": term, "flag": flag},
                "title": f"{label(axis, term)}: entire evidence base is {flag.replace('_', ' ')}",
                "detail": (
                    f"{len(hits)} of {n} records mentioning {label(axis, term)} carry "
                    f"'{flag}' ({prevalence:.0%}). Consequence: {consequence}. "
                    f"Any claim about {label(axis, term)} that requires the opposite "
                    f"design is currently unsupported, however many papers agree."
                ),
                "scores": {"n_records": n, "n_flagged": len(hits),
                           "prevalence": round(prevalence, 3), "severity": severity},
                "priority": priority,
                "evidence": [(rid, "supporting", flag) for rid in sorted(hits)[:10]],
            })

    # One flag can be true of a hundred topics at once, and a report that is
    # fifteen restatements of "this field is cross-sectional" carries the same
    # information as one. Keep the worst few topics per flag so the output
    # spans the distinct weaknesses instead of the most common one.
    gaps.sort(key=lambda g: -g["priority"])
    kept: list[dict] = []
    seen: dict[str, int] = defaultdict(int)
    for gap in gaps:
        flag = gap["key"]["flag"]
        if seen[flag] >= per_flag:
            continue
        seen[flag] += 1
        kept.append(gap)
    return kept[:limit]
