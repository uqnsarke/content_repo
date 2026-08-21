"""Ingest → normalise → flag. The path every record takes into the database."""

from __future__ import annotations

import json
import sqlite3
from typing import Iterable

from . import db
from .normalize import annotate, detect_cohort, extract_n_donors

LIVING_TISSUE = {"peripheral", "biopsy"}
MODEL_TISSUE = {"ipsc", "organoid", "primary_cult", "in_vivo_model"}
TIME_RESOLVED = {"longitudinal", "cohort_incident"}
EARLY_STAGES = {"preclinical", "mci", "converter"}


def ingest(conn: sqlite3.Connection, records: Iterable[dict], limit: int | None = None) -> int:
    """Persist records and their axis annotations. Idempotent per record."""
    n = 0
    for rec in records:
        if limit is not None and n >= limit:
            break
        rec = dict(rec)
        curated = rec.pop("curated", None) or {}
        rec.pop("n_samples", None)

        # Curated axis values come from structured source fields and cannot be
        # recovered from the text, so they are stashed in `raw` and read back on
        # re-annotation. Without this, `cli.py annotate` after a vocabulary
        # change would silently downgrade every GEO/curated annotation to
        # whatever the abstract happens to say.
        raw = rec.get("raw")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        raw = dict(raw) if isinstance(raw, dict) else {}
        if curated:
            raw["_curated"] = curated
        elif isinstance(raw.get("_curated"), dict):
            curated = raw["_curated"]
        rec["raw"] = raw

        text = f"{rec.get('title') or ''} {rec.get('abstract') or ''}"
        if rec.get("n_donors") is None:
            found = extract_n_donors(text.lower())
            if found is not None:
                rec["n_donors"], rec["n_donors_src"] = found, "regex"
        if rec.get("cohort") is None:
            rec["cohort"] = detect_cohort(text.lower())

        rec.setdefault("kind", "work")
        rid = db.upsert_record(conn, rec)
        annotations = annotate(rec.get("title") or "", rec.get("abstract") or "", curated)
        db.set_axes(conn, rid, annotations)
        db.set_flags(conn, rid, design_flags(rec, annotations))
        n += 1
        if n % 200 == 0:
            conn.commit()
    conn.commit()
    return n


def design_flags(rec: dict, annotations: list[dict]) -> list[tuple[str, str]]:
    """Methodological weaknesses of a single record.

    This is the automated version of the reasoning a reviewer does by hand:
    not *what* was found but *what kind of evidence it rests on*. Aggregated
    over the records supporting a claim, it is what separates "established"
    from "asserted once, cross-sectionally, in six male brains".
    """
    stated: dict[str, set[str]] = {}
    for a in annotations:
        if not a["inferred"]:
            stated.setdefault(a["axis"], set()).add(a["term"])
    inferred: dict[str, set[str]] = {}
    for a in annotations:
        if a["inferred"]:
            inferred.setdefault(a["axis"], set()).add(a["term"])

    design = stated.get("design", set()) | inferred.get("design", set())
    tissue = stated.get("tissue_state", set())
    stages = stated.get("stage", set())
    sexes = stated.get("sex", set())
    species = stated.get("species", set()) | inferred.get("species", set())
    ancestry = stated.get("ancestry", set())

    flags: list[tuple[str, str]] = []

    if design and not (design & TIME_RESOLVED):
        flags.append(("cross_sectional_only",
                      "no repeated-measures or time-to-event design; within-person "
                      "trajectory is not separable from between-person variation"))
    if "post_mortem" in tissue and not (tissue & LIVING_TISSUE):
        flags.append(("post_mortem_only",
                      "single terminal timepoint per donor; cause and consequence "
                      "are not distinguishable"))
    if len(sexes) == 1:
        flags.append(("single_sex", f"{next(iter(sexes))} participants only"))
    elif not sexes:
        flags.append(("sex_unreported", "no sex composition stated"))

    n = rec.get("n_donors")
    if n is None:
        flags.append(("n_unreported", "donor count not extractable from the record"))
    elif n < 10:
        flags.append(("small_n", f"n = {n} donors"))

    if rec.get("cohort"):
        flags.append(("single_named_cohort",
                      f"all samples from {rec['cohort']}; not independent of other "
                      f"{rec['cohort']} studies"))
    if ancestry == {"european"}:
        flags.append(("single_ancestry", "European-ancestry participants only"))
    elif not ancestry:
        flags.append(("ancestry_unreported", "no ancestry composition stated"))

    if stages and not (stages & EARLY_STAGES):
        flags.append(("end_stage_contrast_only",
                      "compares diagnosed disease to controls only; no preclinical, "
                      "MCI or converter arm"))
    if species and "human" not in species:
        flags.append(("model_only", "no human tissue in this record"))
    if tissue & MODEL_TISSUE and not (tissue - MODEL_TISSUE):
        flags.append(("model_system_only", "findings come from a model system only"))

    return flags
