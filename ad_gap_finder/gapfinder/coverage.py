"""The coverage matrix — where gaps are computed rather than opined.

A cell is one combination of axis values, e.g.
`{cell_type: microglia, region: entorhinal_cortex, stage: preclinical}`.
For each cell we hold what the corpus actually contains and what it *should*
contain if the axes were sampled independently:

    expected  E = N × Π p(term_i)        p from the corpus's own marginals
    depletion d = log2((E + α) / (O + α))

Using the corpus's own marginals is the point. A raw count rediscovers that
rare cell types are rare and that everyone works on prefrontal cortex; the
residual against the marginal product instead asks *given how much the field
studies microglia, and how much it studies entorhinal cortex, is the
intersection emptier than it should be?* Those are the cells that represent a
missing combination rather than a missing topic.

Two safeguards sit on top, and both matter more than the arithmetic:

`constraint_for` removes cells that are empty because the experiment cannot be
done, so the output is not padded with impossibilities.

`verify_absence` treats an empty cell as a hypothesis about the index, not a
fact about the world, and tries three ways to disprove it before it is allowed
to become a gap.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from itertools import product

from .axes import CONSTRAINTS, VOCAB

ALPHA = 0.5          # Laplace-style smoothing on both counts
MAX_CELLS = 200_000  # refuse to enumerate an unusably large matrix


def cell_key(key: dict[str, str]) -> str:
    return json.dumps(key, sort_keys=True)


def primary_sources(conn: sqlite3.Connection) -> list[str]:
    """Sources belonging to the field under study.

    A tagged source (`openalex:pd`) is a comparator corpus ingested for method
    transfer. Letting it into the coverage matrix would be a straightforward
    error: a Parkinson's paper is not evidence that an AD combination has been
    studied, and including it silently fills in exactly the cells the tool
    exists to find.
    """
    return [r["source"] for r in conn.execute("SELECT DISTINCT source FROM records")
            if ":" not in r["source"]]


def _annotations(conn: sqlite3.Connection, min_confidence: float, include_inferred: bool,
                 sources: list[str] | None = None):
    # `confidence` grades how good a *match* is; `inferred` says whether the
    # annotation was stated at all. They are orthogonal, so the confidence floor
    # must not silently drop inferred rows — inference is gated by
    # include_inferred alone. Conflating the two disables latent coverage
    # entirely, since inferred annotations sit below any useful floor.
    sql = "SELECT ra.record_id, ra.axis, ra.term FROM record_axes ra " \
          "JOIN records r ON r.id = ra.record_id WHERE "
    sql += "(ra.confidence >= ? OR ra.inferred = 1)" if include_inferred \
        else "ra.confidence >= ? AND ra.inferred = 0"
    params: list = [min_confidence]
    if sources:
        sql += f" AND r.source IN ({','.join('?' * len(sources))})"
        params.extend(sources)
    by_record: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for r in conn.execute(sql, params):
        by_record[r["record_id"]][r["axis"]].add(r["term"])
    return by_record


def marginals(conn: sqlite3.Connection, axes: list[str], min_confidence: float = 0.5,
              include_inferred: bool = False,
              sources: list[str] | None = None) -> dict[str, dict[str, float]]:
    """p(term | axis), over records that annotate that axis at all.

    Conditioning on "annotates the axis" rather than on the whole corpus keeps
    an axis that is rarely reported (ancestry) from dragging every expectation
    that involves it to zero.
    """
    by_record = _annotations(conn, min_confidence, include_inferred, sources)
    out: dict[str, dict[str, float]] = {}
    for axis in axes:
        counts: dict[str, float] = defaultdict(float)
        denom = 0
        for _rid, ax in by_record.items():
            terms = ax.get(axis)
            if not terms:
                continue
            denom += 1
            # A record covering three cell types contributes 1/3 to each, so
            # every record carries unit weight no matter how many terms it spans
            # and a single broad atlas cannot outvote the rest of the corpus.
            share = 1.0 / len(terms)
            for t in terms:
                counts[t] += share
        out[axis] = {t: c / denom for t, c in counts.items()} if denom else {}
        out[axis]["__n__"] = float(denom)
    return out


def build(conn: sqlite3.Connection, axes: list[str], min_confidence: float = 0.5,
          include_inferred: bool = False, sources: list[str] | None = None) -> int:
    """Materialise the coverage matrix for `axes`. Returns cell count."""
    axes = sorted(axes)
    axes_key = json.dumps(axes)
    sources = sources or primary_sources(conn)
    by_record = _annotations(conn, min_confidence, include_inferred, sources)
    meta = {r["id"]: r for r in conn.execute(
        "SELECT id, kind, cohort, n_donors, year FROM records")}

    observed: dict[tuple[str, ...], dict] = defaultdict(
        lambda: {"works": 0, "datasets": 0, "cohorts": set(), "donors": [], "years": []})

    for rid, ax in by_record.items():
        if not all(ax.get(a) for a in axes):
            continue  # a record silent on any axis cannot be placed in this matrix
        for combo in product(*(sorted(ax[a]) for a in axes)):
            slot = observed[combo]
            row = meta.get(rid)
            if row is None:
                continue
            slot["works" if row["kind"] == "work" else "datasets"] += 1
            if row["cohort"]:
                slot["cohorts"].add(row["cohort"])
            if row["n_donors"]:
                slot["donors"].append(row["n_donors"])
            if row["year"]:
                slot["years"].append(row["year"])

    marg = marginals(conn, axes, min_confidence, include_inferred, sources)
    # N for the expectation is the number of records placeable in this matrix.
    n_eff = sum(1 for ax in by_record.values() if all(ax.get(a) for a in axes))

    # Enumerate the full grid over terms that occur at least once, so empty
    # cells exist as rows. A term the corpus never mentions is left out: its
    # absence is a vocabulary question, not a research gap.
    grids = [sorted(t for t in marg[a] if t != "__n__") or [] for a in axes]
    size = math.prod(len(g) for g in grids) if all(grids) else 0
    if size > MAX_CELLS:
        raise ValueError(
            f"matrix over {axes} would have {size:,} cells (limit {MAX_CELLS:,}); "
            "use fewer axes or restrict the vocabulary")

    conn.execute("DELETE FROM coverage_cells WHERE axes = ?", (axes_key,))
    rows = []
    for combo in product(*grids):
        key = dict(zip(axes, combo))
        slot = observed.get(combo)
        o_works = slot["works"] if slot else 0
        o_datasets = slot["datasets"] if slot else 0
        observed_n = o_works + o_datasets
        expected = n_eff * math.prod(marg[a].get(t, 0.0) for a, t in key.items())
        depletion = math.log2((expected + ALPHA) / (observed_n + ALPHA))
        rows.append((
            axes_key, cell_key(key), o_works, o_datasets,
            len(slot["cohorts"]) if slot else 0,
            max(slot["donors"]) if slot and slot["donors"] else None,
            sum(slot["donors"]) if slot and slot["donors"] else None,
            max(slot["years"]) if slot and slot["years"] else None,
            round(expected, 4), round(depletion, 4),
        ))

    conn.executemany(
        "INSERT INTO coverage_cells (axes, key, n_works, n_datasets, n_cohorts, "
        "max_donors, total_donors, year_last, expected, depletion) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return len(rows)


# ── feasibility ─────────────────────────────────────────────────────────────

def constraint_for(key: dict[str, str]) -> tuple[str, str] | None:
    """(severity, reason) if any constraint pattern matches this cell."""
    for pattern, severity, reason in CONSTRAINTS:
        if all(key.get(axis) == term or (term == "*" and axis in key)
               for axis, term in pattern.items()):
            return severity, reason
    return None


# ── absence verification ────────────────────────────────────────────────────

def verify_absence(conn: sqlite3.Connection, key: dict[str, str],
                   min_confidence: float = 0.5,
                   sources: list[str] | None = None) -> dict:
    """Try to disprove an empty cell before it is reported as a gap.

    Three independent ways an empty cell can be an artefact rather than a
    finding, checked in increasing order of how badly they undermine it:

    `near_misses`   records matching every axis but one. Many near misses on
                    the same axis usually mean the vocabulary splits one real
                    concept in two, not that the combination is unstudied.
    `latent`        records that match once inferred annotations are allowed —
                    the data exists and contains the cells, nobody analysed
                    them. Still a gap, but a re-analysis, not a new cohort.
    `text_hits`     records whose raw text contains a surface form of every
                    term but which were not annotated. Any hit here is a
                    matcher failure and the cell must not be reported until
                    the vocabulary is fixed.
    """
    sources = sources or primary_sources(conn)
    strict = _annotations(conn, min_confidence, include_inferred=False, sources=sources)
    loose = _annotations(conn, min_confidence, include_inferred=True, sources=sources)

    def matches(ax_map, k, skip=None):
        return all(term in ax_map.get(axis, set())
                   for axis, term in k.items() if axis != skip)

    near: list[dict] = []
    for axis in key:
        hits = [rid for rid, ax in strict.items() if matches(ax, key, skip=axis)]
        if hits:
            near.append({"relaxed_axis": axis, "n": len(hits), "record_ids": hits[:10]})

    latent = [rid for rid, ax in loose.items() if matches(ax, key)]

    # Independent text check: bypass the annotation table entirely.
    synonym_sets = [
        [s.lower() for s in VOCAB[axis][term][1]] + [VOCAB[axis][term][0].lower()]
        for axis, term in key.items() if axis in VOCAB and term in VOCAB[axis]
    ]
    text_hits: list[int] = []
    for r in conn.execute(
        f"SELECT id, title, abstract FROM records WHERE source IN "
        f"({','.join('?' * len(sources))})", sources):
        blob = f"{r['title'] or ''} {r['abstract'] or ''}".lower()
        if all(any(s in blob for s in syns) for syns in synonym_sets):
            text_hits.append(r["id"])

    annotated = set(strict) | set(loose)
    unannotated_hits = [rid for rid in text_hits if rid not in annotated or
                        not matches(strict.get(rid, {}), key)]

    return {
        "near_misses": sorted(near, key=lambda d: -d["n"]),
        "latent_record_ids": latent,
        "n_latent": len(latent),
        "text_only_record_ids": unannotated_hits[:10],
        "n_text_only": len(unannotated_hits),
        # A cell survives verification only if no text-level evidence contradicts it.
        "confirmed_absent": len(unannotated_hits) == 0,
    }
