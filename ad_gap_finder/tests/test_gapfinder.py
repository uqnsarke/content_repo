"""Tests for the gap-finder engine.

Run:  python -m unittest discover -s tests -v

Stdlib unittest, no fixtures beyond an in-memory database, so this runs
anywhere Python does. The tests that matter most are the ones asserting what
the tool must *not* report: an impossible combination, a cell that is only
empty because the matcher missed it, and a rank order that would bury the
scarce findings under the common ones.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gapfinder import coverage, db, normalize, propose  # noqa: E402
from gapfinder.detectors import abc_linking, contradiction, coverage_gap, design_audit  # noqa: E402
from gapfinder.pipeline import design_flags, ingest  # noqa: E402
from gapfinder.sources import demo  # noqa: E402


def fresh_db():
    conn = db.connect(":memory:")
    db.init(conn)
    return conn


class TestNormalize(unittest.TestCase):
    def test_matches_stated_terms_with_provenance(self):
        annos = normalize.annotate(
            "snRNA-seq of entorhinal cortex",
            "Microglia were profiled in postmortem tissue from 30 donors.")
        by = {(a["axis"], a["term"]): a for a in annos}
        self.assertEqual(by[("modality", "snrna")]["confidence"], normalize.CONF_TITLE)
        self.assertEqual(by[("cell_type", "microglia")]["confidence"], normalize.CONF_ABSTRACT)
        self.assertFalse(by[("region", "entorhinal_cortex")]["inferred"])

    def test_inferred_cell_types_are_marked(self):
        annos = normalize.annotate("snRNA-seq of prefrontal cortex in Alzheimer's disease dementia",
                                   "Postmortem tissue from 20 donors.")
        astro = next(a for a in annos if a["term"] == "astrocyte")
        self.assertTrue(astro["inferred"],
                        "cell types nobody analysed must never count as coverage")

    def test_female_does_not_match_male(self):
        annos = normalize.annotate("A study in female participants", "")
        terms = {a["term"] for a in annos if a["axis"] == "sex"}
        self.assertEqual(terms, {"female"})

    def test_donor_extraction_is_conservative(self):
        self.assertEqual(normalize.extract_n_donors("we studied n = 48 donors"), 48)
        self.assertEqual(normalize.extract_n_donors("brains from 24 individuals"), 24)
        self.assertIsNone(normalize.extract_n_donors("in 1 donor"))
        self.assertIsNone(normalize.extract_n_donors("no counts stated here"))

    def test_cohort_detection(self):
        self.assertEqual(normalize.detect_cohort("samples from the rosmap cohort"), "ROSMAP")


class TestDesignFlags(unittest.TestCase):
    def test_cross_sectional_post_mortem_single_sex(self):
        annos = normalize.annotate(
            "Postmortem prefrontal cortex in Alzheimer's disease dementia",
            "Male donors only. Cognitively normal controls were included.")
        flags = dict(design_flags({"n_donors": 6, "cohort": "ROSMAP"}, annos))
        for expected in ("cross_sectional_only", "post_mortem_only", "single_sex",
                         "small_n", "single_named_cohort", "end_stage_contrast_only"):
            self.assertIn(expected, flags)

    def test_longitudinal_record_is_not_flagged_cross_sectional(self):
        annos = normalize.annotate(
            "Longitudinal peripheral blood profiling in converters",
            "Repeated measures from 60 participants across visits, both sexes.")
        flags = dict(design_flags({"n_donors": 60, "cohort": None}, annos))
        self.assertNotIn("cross_sectional_only", flags)
        self.assertNotIn("end_stage_contrast_only", flags)


class TestReannotation(unittest.TestCase):
    def test_curated_annotations_survive_a_reannotate(self):
        conn = fresh_db()
        # Curated axis values that the text does not mention at all.
        ingest(conn, [{"kind": "dataset", "source": "test", "external_id": "R1",
                       "title": "An uninformative title", "abstract": "No axis terms here.",
                       "curated": {"modality": ["multiome"], "region": ["locus_coeruleus"]}}])
        before = {(r["axis"], r["term"]) for r in
                  conn.execute("SELECT axis, term FROM record_axes")}
        self.assertIn(("modality", "multiome"), before)

        rows = conn.execute("SELECT * FROM records").fetchall()
        ingest(conn, ({k: r[k] for k in r.keys() if k not in ("id", "retrieved_at")}
                      for r in rows))
        after = {(r["axis"], r["term"]) for r in
                 conn.execute("SELECT axis, term FROM record_axes")}
        self.assertIn(("modality", "multiome"), after)
        self.assertIn(("region", "locus_coeruleus"), after)


class TestCoverage(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        ingest(self.conn, demo.fetch(limit=250))

    def test_marginals_are_a_distribution(self):
        marg = coverage.marginals(self.conn, ["region"])
        mass = sum(v for k, v in marg["region"].items() if k != "__n__")
        self.assertAlmostEqual(mass, 1.0, places=6)

    def test_build_populates_cells_and_signs_depletion(self):
        n = coverage.build(self.conn, ["modality", "stage"])
        self.assertGreater(n, 0)
        rows = self.conn.execute(
            "SELECT key, n_works + n_datasets AS obs, expected, depletion "
            "FROM coverage_cells").fetchall()
        for r in rows:
            # depletion must be positive exactly when observed is below expected
            self.assertEqual(r["depletion"] > 0, r["expected"] > r["obs"],
                             f"sign disagrees for {r['key']}")

    def test_comparator_corpus_excluded_from_matrix(self):
        ingest(self.conn, demo.fetch_comparator(limit=60))
        primary = coverage.primary_sources(self.conn)
        self.assertIn("demo", primary)
        self.assertNotIn("demo:comparator", primary)
        before = coverage.marginals(self.conn, ["design"])["design"]["__n__"]
        coverage.build(self.conn, ["design", "modality"])
        after = coverage.marginals(self.conn, ["design"])["design"]["__n__"]
        self.assertEqual(before, after)


class TestConstraints(unittest.TestCase):
    def test_exact_pattern(self):
        got = coverage.constraint_for(
            {"cell_type": "microglia", "region": "blood", "stage": "control"})
        self.assertIsNotNone(got)
        self.assertEqual(got[0], "impossible")

    def test_wildcard_pattern(self):
        got = coverage.constraint_for({"modality": "bulk_rna", "cell_type": "astrocyte"})
        self.assertEqual(got[0], "uninformative")

    def test_wildcard_requires_axis_present(self):
        # bulk RNA-seq alone is fine; only the cell-type pairing is uninformative
        self.assertIsNone(coverage.constraint_for({"modality": "bulk_rna", "stage": "mci"}))

    def test_feasible_combination_is_unconstrained(self):
        self.assertIsNone(coverage.constraint_for(
            {"cell_type": "microglia", "region": "entorhinal_cortex", "stage": "preclinical"}))


class TestAbsenceVerification(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()

    def test_matcher_failure_is_caught(self):
        # A record whose text covers the combination, stored with no annotations
        # at all — exactly what a vocabulary miss looks like.
        rid = db.upsert_record(self.conn, {
            "kind": "work", "source": "test", "external_id": "T1",
            "title": "Microglia in the entorhinal cortex",
            "abstract": "A study of preclinical Alzheimer's disease."})
        db.set_axes(self.conn, rid, [])
        self.conn.commit()

        verdict = coverage.verify_absence(
            self.conn, {"cell_type": "microglia", "region": "entorhinal_cortex"})
        self.assertFalse(verdict["confirmed_absent"])
        self.assertIn(rid, verdict["text_only_record_ids"])

    def test_genuine_absence_confirms(self):
        db.upsert_record(self.conn, {
            "kind": "work", "source": "test", "external_id": "T2",
            "title": "Astrocytes in the cerebellum", "abstract": "Nothing relevant."})
        self.conn.commit()
        verdict = coverage.verify_absence(
            self.conn, {"cell_type": "microglia", "region": "locus_coeruleus"})
        self.assertTrue(verdict["confirmed_absent"])

    def test_latent_coverage_detected(self):
        rid = db.upsert_record(self.conn, {
            "kind": "dataset", "source": "test", "external_id": "T3",
            "title": "snRNA-seq of hippocampus",
            "abstract": "Postmortem tissue from 40 donors with AD dementia."})
        annos = normalize.annotate(
            "snRNA-seq of hippocampus",
            "Postmortem tissue from 40 donors with AD dementia.")
        db.set_axes(self.conn, rid, annos)
        self.conn.commit()
        verdict = coverage.verify_absence(
            self.conn, {"cell_type": "astrocyte", "region": "hippocampus"})
        self.assertIn(rid, verdict["latent_record_ids"])


class TestDetectors(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        ingest(self.conn, demo.fetch(limit=300))
        for axes in (["cell_type", "region", "stage"], ["modality", "stage", "tissue_state"]):
            coverage.build(self.conn, axes)

    def test_coverage_gaps_exclude_impossible_cells(self):
        gaps = coverage_gap.detect(self.conn, limit=25)
        self.assertTrue(gaps)
        for gap in gaps:
            key = {a: t for a, t in gap["key"].items() if a != "axes"}
            got = coverage.constraint_for(key)
            self.assertNotIn(got[0] if got else "", ("impossible", "uninformative"),
                             f"reported an unfillable cell: {gap['title']}")

    def test_coverage_gaps_are_verified_absent(self):
        for gap in coverage_gap.detect(self.conn, limit=25):
            if gap["scores"]["observed"] == 0:
                self.assertTrue(gap["scores"]["confirmed_absent"])

    def test_coverage_gaps_are_diversified(self):
        gaps = coverage_gap.detect(self.conn, limit=25, max_siblings=2)
        for gap in gaps:
            key = {a: t for a, t in gap["key"].items() if a != "axes"}
            siblings = sum(
                1 for other in gaps if other is not gap
                and set(other["key"]) == set(gap["key"])
                and sum(1 for a in key if key[a] != other["key"][a]) <= 1)
            self.assertLessEqual(siblings, 2, f"too many variations on {gap['title']}")

    def test_design_audit_caps_repeats_of_one_flag(self):
        gaps = design_audit.detect(self.conn, limit=25, per_flag=3)
        counts: dict[str, int] = {}
        for gap in gaps:
            counts[gap["key"]["flag"]] = counts.get(gap["key"]["flag"], 0) + 1
        self.assertTrue(gaps)
        self.assertLessEqual(max(counts.values()), 3)

    def test_design_audit_finds_the_cross_sectional_monoculture(self):
        gaps = design_audit.detect(self.conn, limit=25)
        self.assertTrue(any(g["key"]["flag"] == "cross_sectional_only" for g in gaps))


class TestClaimDetectors(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        for c in demo.claims():
            self.conn.execute(
                "INSERT INTO claims (subject, relation, object, direction, context, confidence) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (c["subject"], c["relation"], c["object"], c["direction"],
                 json.dumps(c["context"], sort_keys=True), c["confidence"]))
        self.conn.commit()

    def test_abc_finds_the_planted_chain(self):
        gaps = abc_linking.detect(self.conn)
        keys = {(g["key"]["a"], g["key"]["c"]) for g in gaps}
        self.assertIn(("DEMOGENE1", "DEMOPHENO1"), keys)

    def test_abc_ranks_same_context_above_cross_context(self):
        gaps = {(g["key"]["a"], g["key"]["c"]): g for g in abc_linking.detect(self.conn)}
        same = gaps[("DEMOGENE1", "DEMOPHENO1")]
        cross = gaps[("DEMOGENE2", "DEMOPHENO2")]
        self.assertGreater(same["priority"], cross["priority"])

    def test_abc_never_proposes_an_existing_edge(self):
        edges = {(r["subject"], r["object"]) for r in
                 self.conn.execute("SELECT subject, object FROM claims")}
        for gap in abc_linking.detect(self.conn):
            self.assertNotIn((gap["key"]["a"], gap["key"]["c"]), edges)

    def test_contradiction_ranks_same_context_conflict_highest(self):
        gaps = contradiction.detect(self.conn)
        conflicts = [g for g in gaps if g["key"].get("mode") != "singleton"]
        self.assertTrue(conflicts)
        top = max(conflicts, key=lambda g: g["priority"])
        self.assertEqual(top["key"]["subject"], "DEMOGENE3")
        self.assertTrue(top["scores"]["same_context"])

    def test_singletons_never_outrank_conflicts(self):
        gaps = contradiction.detect(self.conn)
        conflicts = [g["priority"] for g in gaps if g["key"].get("mode") != "singleton"]
        singles = [g["priority"] for g in gaps if g["key"].get("mode") == "singleton"]
        if conflicts and singles:
            self.assertGreater(max(conflicts), max(singles))


class TestPropose(unittest.TestCase):
    def test_latent_coverage_proposes_reanalysis_not_a_new_cohort(self):
        gap = {"kind": "coverage", "detail": "",
               "key": {"axes": ["cell_type", "region"], "cell_type": "microglia",
                       "region": "entorhinal_cortex"},
               "scores": {"n_latent": 4}}
        out = propose.propose(gap)
        self.assertEqual(out["route"], "reanalysis")
        self.assertIn("study", out["falsifier"])

    def test_preclinical_gap_gets_a_mediation_step_and_a_falsifier(self):
        gap = {"kind": "coverage", "detail": "",
               "key": {"axes": ["stage"], "stage": "preclinical"},
               "scores": {"n_latent": 0}}
        out = propose.propose(gap)
        self.assertTrue(any("ediation" in step for step in out["workflow"]))
        self.assertIn("amyloid", out["falsifier"])

    def test_every_gap_kind_yields_a_falsifier(self):
        for kind in ("coverage", "design", "abc", "contradiction", "method_transfer"):
            out = propose.propose({"kind": kind, "key": {"a": "X", "c": "Y"},
                                   "detail": "", "scores": {}})
            self.assertTrue(out["falsifier"].strip(), f"{kind} produced no falsifier")


if __name__ == "__main__":
    unittest.main()
