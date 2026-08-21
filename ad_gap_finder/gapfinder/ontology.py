"""Attach ontology CURIEs to vocabulary terms via OLS4.

Matching never uses these. They exist so the coverage matrix can be exported
and joined against CellxGene, Open Targets or anything else keyed on Cell
Ontology / Uberon / EFO / MONDO.

They are resolved rather than hardcoded on purpose: a wrong CURIE written from
memory is indistinguishable from a right one until someone joins on it and gets
silence, so every identifier here comes from a lookup with the matched label
stored beside it for inspection.

    python -m gapfinder.ontology --db data/gapfinder.db
    python -m gapfinder.ontology --db data/gapfinder.db --axis cell_type
"""

from __future__ import annotations

import argparse
import sys

import requests

OLS4 = "https://www.ebi.ac.uk/ols4/api/search"

# Which ontology to search per axis, and how confident a hit has to be.
AXIS_ONTOLOGY = {
    "cell_type": "cl",
    "region": "uberon",
    "stage": "mondo",
    "modality": "efo",
    "tissue_state": "uberon",
    "species": "ncbitaxon",
}


def resolve(label: str, ontology: str, session: requests.Session | None = None) -> tuple[str, str] | None:
    """(curie, matched_label) for the best exact-ish hit, or None."""
    session = session or requests.Session()
    resp = session.get(OLS4, params={"q": label, "ontology": ontology, "rows": 5,
                                     "exact": "false", "fieldList": "obo_id,label"},
                       timeout=30)
    resp.raise_for_status()
    docs = resp.json().get("response", {}).get("docs", [])
    target = label.strip().lower()
    for doc in docs:
        if (doc.get("label") or "").strip().lower() == target and doc.get("obo_id"):
            return doc["obo_id"], doc["label"]
    return None


def sync(conn, axis: str | None = None, verbose: bool = True) -> int:
    """Fill `axis_terms.curie` where a confident exact match exists."""
    session = requests.Session()
    axes = [axis] if axis else list(AXIS_ONTOLOGY)
    n = 0
    for ax in axes:
        ontology = AXIS_ONTOLOGY.get(ax)
        if not ontology:
            continue
        rows = conn.execute(
            "SELECT term, label FROM axis_terms WHERE axis = ? AND curie IS NULL", (ax,)
        ).fetchall()
        for row in rows:
            try:
                hit = resolve(row["label"], ontology, session)
            except requests.RequestException as exc:
                print(f"  {ax}/{row['term']}: lookup failed ({exc})", file=sys.stderr)
                continue
            if not hit:
                continue
            curie, matched = hit
            conn.execute("UPDATE axis_terms SET curie = ? WHERE axis = ? AND term = ?",
                         (curie, ax, row["term"]))
            n += 1
            if verbose:
                print(f"  {ax}/{row['term']:20s} -> {curie}  ({matched})")
    conn.commit()
    return n


def main(argv=None):
    from . import db

    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default="data/gapfinder.db")
    p.add_argument("--axis", choices=sorted(AXIS_ONTOLOGY))
    args = p.parse_args(argv)

    conn = db.connect(args.db)
    db.init(conn)
    print(f"resolved {sync(conn, args.axis)} CURIEs")


if __name__ == "__main__":
    main()
