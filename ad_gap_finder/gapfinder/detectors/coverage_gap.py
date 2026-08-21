"""Empty and thin cells in the coverage matrix.

Scoring, in full, so it can be argued with rather than trusted:

    depletion_n  min(1, max(0, log2((E+α)/(O+α))) / 4)   how much emptier than
                                                          the field's own
                                                          marginals predict
    depth        evidence sufficiency where the cell is not empty — donor
                 count, independent cohorts, number of records
    tractability can this be closed with data that already exists?
                 0.9  latent: matching data exists, the cells were never
                      analysed → a re-analysis, not a new cohort
                 0.5  near miss on exactly one axis → extendable
                 0.2  nothing close → new data collection required
    feasibility  1.0 normally, 0.6 for 'hard' constraints; 'impossible' and
                 'uninformative' cells are dropped entirely

    priority = depletion_n × (1 − depth) × (0.25 + 0.75 × tractability) × feasibility

`(1 − depth)` is what stops a well-studied cell from scoring merely because it
is smaller than the marginal product predicts, and `tractability` is what makes
the output a project list rather than a wish list.

Verification is expensive (a full-text pass per cell), so cells are ranked by
depletion first and only the top `verify_top` are verified. Anything not
verified is not emitted — an unverified absence is not a finding.
"""

from __future__ import annotations

import json
import sqlite3

from ..axes import describe
from ..coverage import constraint_for, verify_absence

FEASIBILITY = {"hard": 0.6}
DROP_SEVERITIES = {"impossible", "uninformative"}


def _depth(row) -> float:
    donors = row["max_donors"] or 0
    cohorts = row["n_cohorts"] or 0
    records = (row["n_works"] or 0) + (row["n_datasets"] or 0)
    if records == 0:
        return 0.0
    return min(1.0, 0.5 * min(1.0, donors / 30) +
                    0.3 * min(1.0, cohorts / 2) +
                    0.2 * min(1.0, records / 5))


def detect(conn: sqlite3.Connection, axes: list[str] | None = None,
           verify_top: int = 60, limit: int = 25, min_expected: float = 0.75,
           max_siblings: int = 2) -> list[dict]:
    axes_key = json.dumps(sorted(axes)) if axes else None
    sql = "SELECT * FROM coverage_cells"
    params: tuple = ()
    if axes_key:
        sql += " WHERE axes = ?"
        params = (axes_key,)
    sql += " ORDER BY depletion DESC"

    candidates = []
    for row in conn.execute(sql, params):
        key = json.loads(row["key"])
        # A cell the corpus never had any reason to fill is not evidence of
        # anything; requiring E >= min_expected keeps the output to
        # combinations the field's own sampling should already have produced.
        if row["expected"] < min_expected:
            continue
        constraint = constraint_for(key)
        if constraint and constraint[0] in DROP_SEVERITIES:
            continue
        candidates.append((row, key, constraint))
        if len(candidates) >= verify_top:
            break

    gaps: list[dict] = []
    for row, key, constraint in candidates:
        records = (row["n_works"] or 0) + (row["n_datasets"] or 0)
        verdict = verify_absence(conn, key) if records == 0 else None

        if verdict and not verdict["confirmed_absent"]:
            continue  # text-level evidence contradicts the index: fix the matcher, not the field

        if verdict:
            tractability = 0.9 if verdict["n_latent"] else (
                0.5 if verdict["near_misses"] else 0.2)
        else:
            tractability = 0.6

        depth = _depth(row)
        depletion_n = min(1.0, max(0.0, row["depletion"]) / 4.0)
        feasibility = FEASIBILITY.get(constraint[0], 1.0) if constraint else 1.0
        priority = depletion_n * (1 - depth) * (0.25 + 0.75 * tractability) * feasibility

        evidence = []
        if verdict:
            for rid in verdict["latent_record_ids"][:8]:
                evidence.append((rid, "latent",
                                 "assayed these cells; never analysed this contrast"))
            for near in verdict["near_misses"][:3]:
                for rid in near["record_ids"][:4]:
                    evidence.append((rid, "near_miss",
                                     f"matches except {near['relaxed_axis']}"))

        gaps.append({
            "kind": "coverage",
            "key": {"axes": sorted(key), **key},
            "title": describe(key),
            "detail": _detail(row, key, verdict, constraint),
            "scores": {
                "observed": records,
                "expected": round(row["expected"], 2),
                "depletion_log2": row["depletion"],
                "depth": round(depth, 3),
                "tractability": tractability,
                "feasibility": feasibility,
                "n_latent": verdict["n_latent"] if verdict else None,
                "confirmed_absent": bool(verdict["confirmed_absent"]) if verdict else None,
            },
            "priority": round(priority, 4),
            "evidence": evidence,
        })

    gaps.sort(key=lambda g: -g["priority"])
    return _diversify(gaps, limit, args_max_siblings=max_siblings)


def _diversify(gaps: list[dict], limit: int, args_max_siblings: int = 2) -> list[dict]:
    """Drop near-duplicate cells that differ on a single axis.

    "Microglia × bulk × control" and "astrocytes × bulk × control" are the same
    finding stated twice; a reader who acts on one has acted on both. Keeping a
    couple of siblings preserves the signal that the hole is wide, without
    spending the whole report on one neighbourhood of the matrix.
    """
    kept: list[dict] = []
    for gap in gaps:
        key = {a: t for a, t in gap["key"].items() if a != "axes"}
        siblings = 0
        for other in kept:
            okey = {a: t for a, t in other["key"].items() if a != "axes"}
            if set(okey) != set(key):
                continue
            if sum(1 for a in key if key[a] != okey[a]) <= 1:
                siblings += 1
        if siblings >= args_max_siblings:
            continue
        kept.append(gap)
        if len(kept) >= limit:
            break
    return kept


def _detail(row, key: dict, verdict: dict | None, constraint) -> str:
    records = (row["n_works"] or 0) + (row["n_datasets"] or 0)
    lines = [
        f"{records} record(s) in the corpus; {row['expected']:.1f} expected from the "
        f"marginal distributions of {', '.join(sorted(key))} "
        f"(depletion {row['depletion']:+.2f} log2).",
    ]
    if records:
        lines.append(
            f"Largest study n = {row['max_donors'] or 'unreported'} donors across "
            f"{row['n_cohorts']} named cohort(s); most recent {row['year_last']}.")
    if verdict:
        if verdict["n_latent"]:
            lines.append(
                f"{verdict['n_latent']} existing record(s) already assayed these cells "
                f"under these conditions without analysing them — addressable by "
                f"re-analysis rather than new collection.")
        if verdict["near_misses"]:
            top = verdict["near_misses"][0]
            lines.append(
                f"Nearest existing work relaxes {top['relaxed_axis']} "
                f"({top['n']} record(s)).")
        if not verdict["n_latent"] and not verdict["near_misses"]:
            lines.append("No near-miss or latent coverage: closing this requires new data.")
    if constraint:
        lines.append(f"Feasibility caveat ({constraint[0]}): {constraint[1]}")
    return " ".join(lines)
