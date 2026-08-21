"""Gap detectors.

Each detector returns a list of gap dicts:

    {kind, key (dict), title, detail, scores (dict), priority (float),
     evidence: [(record_id, role, note), ...]}

`persist` writes them, preserving any triage status a human already set — an
accept/reject decision must survive a re-run, since those decisions are the
labelled data this whole system accumulates.
"""

from __future__ import annotations

import json
import sqlite3

from . import abc_linking, contradiction, coverage_gap, design_audit, method_transfer  # noqa: F401

DETECTORS = {
    "coverage": coverage_gap.detect,
    "design": design_audit.detect,
    "method_transfer": method_transfer.detect,
    "abc": abc_linking.detect,
    "contradiction": contradiction.detect,
}


def persist(conn: sqlite3.Connection, gaps: list[dict]) -> int:
    for gap in gaps:
        key = json.dumps(gap["key"], sort_keys=True)
        conn.execute(
            "INSERT INTO gaps (kind, key, title, detail, scores, priority) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (kind, key) DO UPDATE SET title = excluded.title, "
            "detail = excluded.detail, scores = excluded.scores, "
            "priority = excluded.priority",
            (gap["kind"], key, gap["title"], gap.get("detail"),
             json.dumps(gap.get("scores", {})), gap.get("priority", 0.0)),
        )
        gap_id = conn.execute("SELECT id FROM gaps WHERE kind = ? AND key = ?",
                              (gap["kind"], key)).fetchone()["id"]
        conn.execute("DELETE FROM gap_evidence WHERE gap_id = ?", (gap_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO gap_evidence (gap_id, record_id, role, note) "
            "VALUES (?, ?, ?, ?)",
            [(gap_id, rid, role, note) for rid, role, note in gap.get("evidence", [])],
        )
    conn.commit()
    return len(gaps)
