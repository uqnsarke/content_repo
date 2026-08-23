"""Typed artifacts and the provenance graph that links them.

Every stage produces exactly one artifact, and every artifact records what it
came from: the artifact before it, the gap it addresses, and the record ids that
justify it. That chain is the difference between a system that recommends a
wet-lab experiment and a system that can tell you *why* — walk `derived_from`
backwards from a recommendation and you land on accessions.

Bodies are hashed. Re-running a stage on unchanged inputs produces an identical
digest, which is how a rerun is distinguished from a genuinely new conclusion.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from typing import Any

STAGES = ["synthesis", "hypothesis", "analysis", "insilico", "wetlab"]

KIND_FOR_STAGE = {
    "synthesis": "brief",
    "hypothesis": "hypothesis",
    "analysis": "plan",
    "insilico": "simulation",
    "wetlab": "recommendation",
}


@dataclass
class Artifact:
    stage: str
    title: str
    body: dict
    parents: list[int] = field(default_factory=list)
    records: list[int] = field(default_factory=list)
    gap_id: int | None = None
    id: int | None = None

    @property
    def kind(self) -> str:
        return KIND_FOR_STAGE[self.stage]

    @property
    def digest(self) -> str:
        return hashlib.sha256(
            json.dumps(self.body, sort_keys=True, default=str).encode()).hexdigest()


def create_run(conn: sqlite3.Connection, goal: str, mode: str, trace_id: str | None = None) -> int:
    cur = conn.execute("INSERT INTO runs (goal, mode, trace_id) VALUES (?, ?, ?)",
                       (goal, mode, trace_id))
    conn.commit()
    return cur.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, stage: str | None = None,
               halt_reason: str | None = None) -> None:
    conn.execute("UPDATE runs SET status = ?, stage = ?, halt_reason = ?, "
                 "ended_at = strftime('%s','now') WHERE id = ?",
                 (status, stage, halt_reason, run_id))
    conn.commit()


def save(conn: sqlite3.Connection, run_id: int, artifact: Artifact) -> int:
    cur = conn.execute(
        "INSERT INTO artifacts (run_id, stage, kind, title, body, digest) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, artifact.stage, artifact.kind, artifact.title,
         json.dumps(artifact.body, default=str), artifact.digest))
    artifact.id = cur.lastrowid

    links = [(artifact.id, p, None, None, "derived_from") for p in artifact.parents]
    links += [(artifact.id, None, r, None, "evidence") for r in artifact.records]
    if artifact.gap_id is not None:
        links.append((artifact.id, None, None, artifact.gap_id, "addresses"))
    conn.executemany(
        "INSERT INTO artifact_links (artifact_id, parent_artifact, record_id, gap_id, role) "
        "VALUES (?, ?, ?, ?, ?)", links)
    conn.commit()
    return artifact.id


def load(conn: sqlite3.Connection, artifact_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM artifacts WHERE id = ?", (artifact_id,)).fetchone()
    if row is None:
        return None
    out = dict(row)
    out["body"] = json.loads(row["body"])
    return out


def for_run(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    return [{**dict(r), "body": json.loads(r["body"])} for r in conn.execute(
        "SELECT * FROM artifacts WHERE run_id = ? ORDER BY id", (run_id,))]


def provenance(conn: sqlite3.Connection, artifact_id: int) -> dict:
    """Walk `derived_from` back to the roots, collecting evidence on the way.

    This is the query that answers "why are you telling me to do this
    experiment?" — the answer is a chain of stages ending in accessions, not a
    paragraph of prose.
    """
    seen: set[int] = set()
    chain: list[dict] = []
    records: set[int] = set()
    gaps: set[int] = set()

    frontier = [artifact_id]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        node = load(conn, current)
        if node is None:
            continue
        chain.append({"id": node["id"], "stage": node["stage"], "title": node["title"],
                      "digest": node["digest"][:12]})
        for link in conn.execute(
            "SELECT parent_artifact, record_id, gap_id, role FROM artifact_links "
            "WHERE artifact_id = ?", (current,)
        ):
            if link["parent_artifact"]:
                frontier.append(link["parent_artifact"])
            if link["record_id"]:
                records.add(link["record_id"])
            if link["gap_id"]:
                gaps.add(link["gap_id"])

    evidence = [dict(r) for r in conn.execute(
        f"SELECT id, kind, source, external_id, title, url, year FROM records "
        f"WHERE id IN ({','.join('?' * len(records))})", sorted(records))] if records else []

    chain.sort(key=lambda n: STAGES.index(n["stage"]) if n["stage"] in STAGES else 99)
    return {"artifact_id": artifact_id, "chain": chain,
            "gap_ids": sorted(gaps), "evidence": evidence}
