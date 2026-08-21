"""Tools the agent is allowed to use, as plain functions.

The division of labour matters more than the code here. The model is not asked
what the gaps are — that is the question it will answer fluently and wrongly.
It is given tools that *compute* absence and asked to interpret, verify and
write up what comes back. Everything a proposal asserts about the corpus has to
come through one of these calls.

`SCHEMAS` + `dispatch` are transport-agnostic on purpose: the same layer backs
the Python tool runner in `agent.py` and any HTTP handler (a Next.js route, a
FastAPI endpoint) that wants to expose the engine to a hosted agent.
"""

from __future__ import annotations

import json
import sqlite3

from . import coverage, report
from .axes import AXES, VOCAB, describe
from .propose import propose

MAX_ROWS = 50


# ── tools ───────────────────────────────────────────────────────────────────

def list_axes() -> dict:
    """The controlled vocabulary: which axes exist and which terms are legal."""
    return {axis: sorted(VOCAB[axis]) for axis in AXES}


def query_coverage(conn: sqlite3.Connection, axes: list[str], where: dict | None = None,
                   order: str = "depletion", limit: int = 20) -> dict:
    """Cells of a materialised coverage matrix, optionally filtered by axis value."""
    axes_key = json.dumps(sorted(axes))
    rows = conn.execute("SELECT * FROM coverage_cells WHERE axes = ?", (axes_key,)).fetchall()
    if not rows:
        built = [r["axes"] for r in conn.execute(
            "SELECT DISTINCT axes FROM coverage_cells")]
        return {"error": f"no matrix built over {sorted(axes)}", "available": built}

    out = []
    for row in rows:
        key = json.loads(row["key"])
        if where and any(key.get(a) != v for a, v in where.items()):
            continue
        constraint = coverage.constraint_for(key)
        out.append({
            "key": key, "label": describe(key),
            "n_works": row["n_works"], "n_datasets": row["n_datasets"],
            "n_cohorts": row["n_cohorts"], "max_donors": row["max_donors"],
            "expected": row["expected"], "depletion_log2": row["depletion"],
            "year_last": row["year_last"],
            "infeasible": {"severity": constraint[0], "reason": constraint[1]} if constraint else None,
        })
    reverse = order != "observed"
    out.sort(key=lambda c: (c["depletion_log2"] if order == "depletion"
                            else c["n_works"] + c["n_datasets"]), reverse=reverse)
    return {"axes": sorted(axes), "n_cells": len(out), "cells": out[:min(limit, MAX_ROWS)]}


def verify_absence(conn: sqlite3.Connection, key: dict) -> dict:
    """Test whether an empty cell is genuinely unstudied or an indexing artefact.

    Call this before asserting that anything has never been done. A non-empty
    `text_only_record_ids` means the matcher missed records that do cover the
    combination, and the cell must not be reported as a gap.
    """
    verdict = coverage.verify_absence(conn, key)
    verdict["interpretation"] = (
        "confirmed absent: no annotated or text-level evidence for this combination"
        if verdict["confirmed_absent"] else
        "NOT absent: records mention every term but were not annotated — this is a "
        "matcher failure, not a research gap")
    return verdict


def search_records(conn: sqlite3.Connection, text: str | None = None,
                   axis_filters: dict | None = None, kind: str | None = None,
                   min_donors: int | None = None, limit: int = 15) -> dict:
    """Find records by free text and/or axis annotations."""
    sql = ["SELECT DISTINCT r.* FROM records r"]
    params: list = []
    for i, (axis, term) in enumerate((axis_filters or {}).items()):
        sql.append(f"JOIN record_axes a{i} ON a{i}.record_id = r.id "
                   f"AND a{i}.axis = ? AND a{i}.term = ? AND a{i}.inferred = 0")
        params += [axis, term]
    conds = []
    if text:
        conds.append("(r.title LIKE ? OR r.abstract LIKE ?)")
        params += [f"%{text}%", f"%{text}%"]
    if kind:
        conds.append("r.kind = ?")
        params.append(kind)
    if min_donors:
        conds.append("r.n_donors >= ?")
        params.append(min_donors)
    if conds:
        sql.append("WHERE " + " AND ".join(conds))
    sql.append("ORDER BY r.year DESC, r.cited_by DESC LIMIT ?")
    params.append(min(limit, MAX_ROWS))

    rows = conn.execute(" ".join(sql), params).fetchall()
    return {"n": len(rows), "records": [
        {"id": r["id"], "kind": r["kind"], "source": r["source"],
         "external_id": r["external_id"], "title": r["title"], "year": r["year"],
         "n_donors": r["n_donors"], "cohort": r["cohort"], "url": r["url"],
         "flags": [f["flag"] for f in conn.execute(
             "SELECT flag FROM record_flags WHERE record_id = ?", (r["id"],))]}
        for r in rows]}


def get_record_flags(conn: sqlite3.Connection, record_ids: list[int]) -> dict:
    """Design weaknesses recorded for specific records."""
    out = {}
    for rid in record_ids[:MAX_ROWS]:
        out[rid] = [{"flag": f["flag"], "detail": f["detail"]} for f in conn.execute(
            "SELECT flag, detail FROM record_flags WHERE record_id = ?", (rid,))]
    return out


def get_claim_graph(conn: sqlite3.Connection, entity: str, limit: int = 30) -> dict:
    """Claims where `entity` is the subject or the object."""
    rows = conn.execute(
        "SELECT subject, relation, object, direction, context, record_id, confidence "
        "FROM claims WHERE subject = ? OR object = ? LIMIT ?",
        (entity, entity, min(limit, MAX_ROWS))).fetchall()
    return {"entity": entity, "n": len(rows),
            "claims": [dict(r) for r in rows]}


def list_gaps(conn: sqlite3.Connection, kind: str | None = None,
              status: str | None = None, limit: int = 15) -> dict:
    """Stored gaps, highest priority first."""
    gaps = report.load_gaps(conn, status=status, kinds=[kind] if kind else None,
                            limit=min(limit, MAX_ROWS))
    return {"n": len(gaps), "gaps": [
        {"id": g["id"], "kind": g["kind"], "title": g["title"], "priority": g["priority"],
         "status": g["status"], "detail": g["detail"], "scores": g["scores"],
         "evidence": [{"external_id": e["external_id"], "role": e["role"],
                       "title": e["title"], "url": e["url"]} for e in g["evidence"][:6]]}
        for g in gaps]}


def propose_workflow(conn: sqlite3.Connection, gap_id: int) -> dict:
    """Template hypothesis, workflow and falsifier for a stored gap.

    Use this as the skeleton to argue with, not as the answer: it encodes which
    design a given gap needs, which is the part that is easy to get wrong and
    cheap to check.
    """
    row = conn.execute("SELECT * FROM gaps WHERE id = ?", (gap_id,)).fetchone()
    if row is None:
        return {"error": f"no gap with id {gap_id}"}
    gap = {"kind": row["kind"], "key": json.loads(row["key"]), "title": row["title"],
           "detail": row["detail"], "scores": json.loads(row["scores"])}
    return {"gap": gap, **propose(gap)}


def save_finding(conn: sqlite3.Connection, title: str, detail: str,
                 key: dict, priority: float = 0.5, evidence_ids: list[int] | None = None) -> dict:
    """Persist an agent-authored gap for human triage.

    Written with kind='agent' and status='new'. It never overwrites a computed
    gap, and a human's accept/reject on it is what later trains ranking.
    """
    key_json = json.dumps(key, sort_keys=True)
    conn.execute(
        "INSERT INTO gaps (kind, key, title, detail, scores, priority) "
        "VALUES ('agent', ?, ?, ?, '{}', ?) "
        "ON CONFLICT (kind, key) DO UPDATE SET title = excluded.title, "
        "detail = excluded.detail, priority = excluded.priority",
        (key_json, title, detail, priority))
    gap_id = conn.execute("SELECT id FROM gaps WHERE kind = 'agent' AND key = ?",
                          (key_json,)).fetchone()["id"]
    for rid in (evidence_ids or [])[:MAX_ROWS]:
        conn.execute("INSERT OR REPLACE INTO gap_evidence (gap_id, record_id, role, note) "
                     "VALUES (?, ?, 'supporting', 'cited by agent')", (gap_id, rid))
    conn.commit()
    return {"gap_id": gap_id, "status": "new"}


# ── transport-agnostic schema + dispatch ────────────────────────────────────

SCHEMAS = [
    {"name": "list_axes",
     "description": list_axes.__doc__,
     "input_schema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "query_coverage",
     "description": query_coverage.__doc__,
     "input_schema": {"type": "object", "properties": {
         "axes": {"type": "array", "items": {"type": "string"},
                  "description": "axis names defining the matrix, e.g. ['cell_type','region','stage']"},
         "where": {"type": "object", "description": "axis -> term filter"},
         "order": {"type": "string", "enum": ["depletion", "observed"]},
         "limit": {"type": "integer"}},
         "required": ["axes"], "additionalProperties": False}},
    {"name": "verify_absence",
     "description": verify_absence.__doc__,
     "input_schema": {"type": "object", "properties": {
         "key": {"type": "object", "description": "axis -> term describing one cell"}},
         "required": ["key"], "additionalProperties": False}},
    {"name": "search_records",
     "description": search_records.__doc__,
     "input_schema": {"type": "object", "properties": {
         "text": {"type": "string"},
         "axis_filters": {"type": "object"},
         "kind": {"type": "string", "enum": ["work", "dataset"]},
         "min_donors": {"type": "integer"},
         "limit": {"type": "integer"}}, "additionalProperties": False}},
    {"name": "get_record_flags",
     "description": get_record_flags.__doc__,
     "input_schema": {"type": "object", "properties": {
         "record_ids": {"type": "array", "items": {"type": "integer"}}},
         "required": ["record_ids"], "additionalProperties": False}},
    {"name": "get_claim_graph",
     "description": get_claim_graph.__doc__,
     "input_schema": {"type": "object", "properties": {
         "entity": {"type": "string"}, "limit": {"type": "integer"}},
         "required": ["entity"], "additionalProperties": False}},
    {"name": "list_gaps",
     "description": list_gaps.__doc__,
     "input_schema": {"type": "object", "properties": {
         "kind": {"type": "string"}, "status": {"type": "string"},
         "limit": {"type": "integer"}}, "additionalProperties": False}},
    {"name": "propose_workflow",
     "description": propose_workflow.__doc__,
     "input_schema": {"type": "object", "properties": {"gap_id": {"type": "integer"}},
                      "required": ["gap_id"], "additionalProperties": False}},
    {"name": "save_finding",
     "description": save_finding.__doc__,
     "input_schema": {"type": "object", "properties": {
         "title": {"type": "string"}, "detail": {"type": "string"},
         "key": {"type": "object"}, "priority": {"type": "number"},
         "evidence_ids": {"type": "array", "items": {"type": "integer"}}},
         "required": ["title", "detail", "key"], "additionalProperties": False}},
]

_NEEDS_CONN = {"query_coverage", "verify_absence", "search_records", "get_record_flags",
               "get_claim_graph", "list_gaps", "propose_workflow", "save_finding"}


def dispatch(conn: sqlite3.Connection, name: str, payload: dict) -> dict:
    fn = globals().get(name)
    if fn is None or name not in {s["name"] for s in SCHEMAS}:
        return {"error": f"unknown tool '{name}'"}
    try:
        return fn(conn, **payload) if name in _NEEDS_CONN else fn(**payload)
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}


SYSTEM_PROMPT = """\
You find research gaps in the Alzheimer's literature using a coverage-matrix \
database, and you are held to one standard above all others: every claim you \
make about what the field has or has not done must come from a tool call.

How to work:

1. Start from `query_coverage`, not from memory. Your training data contains \
strong priors about AD research; those priors are exactly what this database \
exists to check. Pick a matrix, look at the depleted cells.
2. Before you describe any cell as unstudied, call `verify_absence` on it. If \
`confirmed_absent` is false, the cell is a matcher failure — say so and move on. \
Do not report it as a gap.
3. Discard cells that come back with an `infeasible` block. A combination that \
cannot be assayed is not an opportunity, and listing it costs you the reader's \
trust for the ones that are real.
4. Prefer gaps that existing data can close. `verify_absence` returns latent \
coverage — records that already assayed these cells without analysing them. A \
re-analysis someone can start this week beats a cohort proposal.
5. Use `propose_workflow` for the design skeleton, then improve it. Keep its \
falsifier or write a better one; a hypothesis with no stated failure condition \
is not a hypothesis.
6. Cite records by their `external_id` exactly as the tools return them. Never \
write an accession, DOI or PMID that did not come out of a tool call — a \
plausible-looking wrong accession is worse than no citation.

If the corpus is synthetic (`external_id` values beginning `DEMO-`), say so in \
your first sentence and frame everything you report as a demonstration of the \
method, never as a claim about the real literature.

Be concrete and brief. The reader is a domain expert who will check you."""
