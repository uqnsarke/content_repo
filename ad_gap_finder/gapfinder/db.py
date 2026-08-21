"""Thin SQLite wrapper.

Deliberately thin: the queries in this project are the interesting part, and
hiding them behind an ORM would make the coverage arithmetic harder to audit.
Everything returns `sqlite3.Row`, which indexes by column name.

Porting to Postgres/Supabase: swap `connect()` for a psycopg connection and
change the three constructs noted in schema.sql. No call site changes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text())
    _load_vocab(conn)
    _load_constraints(conn)
    conn.commit()


def _load_vocab(conn: sqlite3.Connection) -> None:
    from .axes import VOCAB

    rows = [
        (axis, term, label, json.dumps(syns))
        for axis, entries in VOCAB.items()
        for term, (label, syns) in entries.items()
    ]
    conn.executemany(
        "INSERT INTO axis_terms (axis, term, label, synonyms) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (axis, term) DO UPDATE SET label = excluded.label, "
        "synonyms = excluded.synonyms",
        rows,
    )


def _load_constraints(conn: sqlite3.Connection) -> None:
    from .axes import CONSTRAINTS

    conn.execute("DELETE FROM constraints")
    conn.executemany(
        "INSERT INTO constraints (pattern, severity, reason) VALUES (?, ?, ?)",
        [(json.dumps(p, sort_keys=True), sev, reason) for p, sev, reason in CONSTRAINTS],
    )


# ── record upsert ───────────────────────────────────────────────────────────

RECORD_FIELDS = (
    "kind", "source", "external_id", "doi", "pmid", "title", "abstract",
    "year", "venue", "url", "n_donors", "n_donors_src", "cohort", "cited_by", "raw",
)


def upsert_record(conn: sqlite3.Connection, rec: dict) -> int:
    """Insert or update one record; returns its row id.

    Re-ingesting is idempotent on (source, external_id). Axis annotations for
    the record are replaced wholesale by `set_axes`, so a vocabulary change
    followed by a re-annotate produces a clean state rather than a union of old
    and new terms.
    """
    values = [rec.get(f) for f in RECORD_FIELDS]
    if isinstance(rec.get("raw"), (dict, list)):
        values[RECORD_FIELDS.index("raw")] = json.dumps(rec["raw"])
    placeholders = ", ".join("?" for _ in RECORD_FIELDS)
    updates = ", ".join(f"{f} = excluded.{f}" for f in RECORD_FIELDS if f not in ("source", "external_id"))
    conn.execute(
        f"INSERT INTO records ({', '.join(RECORD_FIELDS)}) VALUES ({placeholders}) "
        f"ON CONFLICT (source, external_id) DO UPDATE SET {updates}",
        values,
    )
    # lastrowid is unreliable across the UPDATE branch of an upsert, so read the
    # id back by natural key rather than trusting it.
    return conn.execute(
        "SELECT id FROM records WHERE source = ? AND external_id = ?",
        (rec["source"], rec["external_id"]),
    ).fetchone()["id"]


def set_axes(conn: sqlite3.Connection, record_id: int, annotations: list[dict]) -> None:
    conn.execute("DELETE FROM record_axes WHERE record_id = ?", (record_id,))
    conn.executemany(
        "INSERT OR REPLACE INTO record_axes "
        "(record_id, axis, term, confidence, inferred, evidence) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (record_id, a["axis"], a["term"], a.get("confidence", 0.5),
             int(a.get("inferred", False)), a.get("evidence"))
            for a in annotations
        ],
    )


def set_flags(conn: sqlite3.Connection, record_id: int, flags: list[tuple[str, str]]) -> None:
    conn.execute("DELETE FROM record_flags WHERE record_id = ?", (record_id,))
    conn.executemany(
        "INSERT OR REPLACE INTO record_flags (record_id, flag, detail) VALUES (?, ?, ?)",
        [(record_id, f, d) for f, d in flags],
    )


def record_axis_map(conn: sqlite3.Connection, min_confidence: float = 0.0) -> dict[int, dict[str, set[str]]]:
    """{record_id: {axis: {terms}}} for every annotated record."""
    out: dict[int, dict[str, set[str]]] = {}
    for r in conn.execute(
        "SELECT record_id, axis, term FROM record_axes WHERE confidence >= ?",
        (min_confidence,),
    ):
        out.setdefault(r["record_id"], {}).setdefault(r["axis"], set()).add(r["term"])
    return out


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "works": q("SELECT COUNT(*) FROM records WHERE kind = 'work'"),
        "datasets": q("SELECT COUNT(*) FROM records WHERE kind = 'dataset'"),
        "annotations": q("SELECT COUNT(*) FROM record_axes"),
        "claims": q("SELECT COUNT(*) FROM claims"),
        "coverage_cells": q("SELECT COUNT(*) FROM coverage_cells"),
        "gaps": q("SELECT COUNT(*) FROM gaps"),
    }
