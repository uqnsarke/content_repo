"""Tests for the autonomous pipeline layer.

Weighted towards the properties that make autonomy safe rather than the ones
that make it work: that gates fail closed, that a run cannot sign off on its own,
that a model's narration cannot rewrite a computed number, and that a
recommendation can be walked back to the records underneath it.

Everything runs in `dry` mode — no API key, no network, no tracing backend.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gapfinder import coverage, db, detectors  # noqa: E402
from gapfinder.lab import artifacts, gates, insilico, pipeline, stages, viewer, wetlab  # noqa: E402
from gapfinder.lab.tracing import Tracer, mantis_status  # noqa: E402
from gapfinder.pipeline import ingest  # noqa: E402
from gapfinder.sources import demo  # noqa: E402


def seeded_db(path=":memory:"):
    conn = db.connect(path)
    db.init(conn)
    ingest(conn, demo.fetch(limit=220))
    for axes in (["cell_type", "region", "stage"], ["modality", "stage", "tissue_state"]):
        coverage.build(conn, axes)
    for kind in ("coverage", "design"):
        detectors.persist(conn, detectors.DETECTORS[kind](conn, limit=10))
    return conn


class TestInSilico(unittest.TestCase):
    def test_power_matches_known_values(self):
        # d=0.5 at 80% power is ~64 per group in every standard table.
        self.assertAlmostEqual(insilico.required_n(0.5, 0.8), 63, delta=2)
        self.assertAlmostEqual(insilico.two_sample_power(64, 0.5), 0.80, delta=0.02)
        # Power rises monotonically with n.
        powers = [insilico.two_sample_power(n, 0.5) for n in (10, 20, 40, 80)]
        self.assertEqual(powers, sorted(powers))

    def test_detectable_effect_is_inverse_of_required_n(self):
        d = insilico.detectable_effect(64)
        self.assertAlmostEqual(d, 0.5, delta=0.03)

    def test_longitudinal_power_is_bounded_by_subjects_not_timepoints(self):
        """More timepoints cannot rescue a design limited by between-subject slope variance."""
        few = insilico.longitudinal_power(10, 4, 0.5, sd_random_slope=1.0)
        many = insilico.longitudinal_power(10, 40, 0.5, sd_random_slope=1.0)
        more_subjects = insilico.longitudinal_power(40, 4, 0.5, sd_random_slope=1.0)
        self.assertLess(many["achieved"] - few["achieved"], 0.15)
        self.assertGreater(more_subjects["achieved"], few["achieved"] + 0.2)
        self.assertIn("participants", many["note"])

    def test_compositional_confound_produces_false_positives(self):
        """A pure abundance shift with zero state change must still be detected."""
        out = insilico.compositional_confound(n_per_group=25, state_effect=0.0, sims=400)
        self.assertGreater(out["false_positive_rate"], 0.5)
        self.assertGreater(out["mean_pseudobulk_shift"], 0)

    def test_no_confound_when_abundance_is_unchanged(self):
        out = insilico.compositional_confound(n_per_group=25, base_fraction=0.10,
                                              case_fraction=0.10, state_effect=0.0, sims=400)
        self.assertLess(out["false_positive_rate"], 0.15)

    def test_simulation_is_deterministic(self):
        plan = {"expected_effect_size": 0.5, "n_available": 40}
        a = insilico.run_simulations(plan)
        b = insilico.run_simulations(plan)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_projected_power_is_labelled(self):
        plan = {"expected_effect_size": 0.5, "n_available": 3000,
                "n_available_source": "field_pool"}
        out = insilico.run_simulations(plan)
        self.assertTrue(out["assumptions"]["power_is_projected"])
        self.assertIn("not currently available", out["power"]["conditional_on"])

    def test_reanalysis_reports_largest_alone_as_well_as_pooled(self):
        out = insilico.reanalysis_feasibility([40, 10, 10], effect_d=0.5)
        self.assertGreater(out["power_pooled"], out["power_largest_alone"])
        self.assertIn("study fixed effect", out["caveat"])


class TestGates(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        db.init(self.conn)
        self.run_id = artifacts.create_run(self.conn, "test", "dry")

    def test_missing_falsifier_blocks_analysis(self):
        passed, checks = gates.evaluate(self.conn, self.run_id, "hypothesis", "analysis",
                                        {"falsifier": "", "primary_outcome": "x"})
        self.assertFalse(passed)
        self.assertIn("has_falsifier", gates.failures(checks))

    def test_cell_level_unit_of_analysis_blocks_simulation(self):
        body = {"unit_of_analysis": "cell", "statistical_test": "wilcoxon",
                "n_available": 40, "predicted_direction": "up", "covariates": ["age"]}
        passed, checks = gates.evaluate(self.conn, self.run_id, "analysis", "insilico", body)
        self.assertFalse(passed)
        self.assertIn("unit_of_analysis_is_donor", gates.failures(checks))

    def test_donor_unit_passes(self):
        body = {"unit_of_analysis": "donor", "statistical_test": "mixed model",
                "n_available": 40, "predicted_direction": "up", "covariates": ["age", "sex"]}
        passed, _ = gates.evaluate(self.conn, self.run_id, "analysis", "insilico", body)
        self.assertTrue(passed)

    def test_described_simulation_is_not_an_executed_one(self):
        passed, checks = gates.evaluate(self.conn, self.run_id, "insilico", "wetlab",
                                        {"results": {}, "underpowered_acknowledged": True})
        self.assertFalse(passed)
        self.assertIn("simulation_executed", gates.failures(checks))

    def test_missing_negative_control_blocks_wetlab(self):
        body = {"results": {"power": {"achieved": 0.95}}, "underpowered_acknowledged": False}
        passed, checks = gates.evaluate(self.conn, self.run_id, "insilico", "wetlab", body)
        self.assertFalse(passed)
        self.assertIn("ran_negative_control", gates.failures(checks))

    def test_gate_fails_closed_on_malformed_body(self):
        # A body of the wrong shape must not pass by accident.
        passed, checks = gates.evaluate(self.conn, self.run_id, "insilico", "wetlab",
                                        {"results": "not a dict"})
        self.assertFalse(passed)

    def test_unknown_transition_fails(self):
        passed, _ = gates.evaluate(self.conn, self.run_id, "synthesis", "wetlab", {})
        self.assertFalse(passed)

    def test_gate_outcome_is_recorded_either_way(self):
        gates.evaluate(self.conn, self.run_id, "hypothesis", "analysis", {"falsifier": ""})
        row = self.conn.execute("SELECT * FROM gate_results WHERE run_id = ?",
                                (self.run_id,)).fetchone()
        self.assertEqual(row["passed"], 0)
        self.assertTrue(json.loads(row["checks"]))


class TestNarrationGuard(unittest.TestCase):
    def test_protected_fields_survive_model_output(self):
        body = {"n_available": 42, "summary": "computed", "unit_of_analysis": "donor"}
        hostile = {"n_available": 9999, "summary": "rewritten", "unit_of_analysis": "cell"}
        merged = stages.merge_narration(body, hostile, stages.PROTECTED["analysis"])
        self.assertEqual(merged["n_available"], 42)
        self.assertEqual(merged["unit_of_analysis"], "donor")
        self.assertEqual(merged["summary"], "rewritten")  # narration may change

    def test_every_stage_protects_something(self):
        for stage in artifacts.STAGES:
            self.assertTrue(stages.PROTECTED.get(stage),
                            f"{stage} declares no protected fields")


class TestWetLab(unittest.TestCase):
    def test_same_failure_modes_are_penalised(self):
        h = {"statement": "regulatory wiring", "key": {"modality": "multiome"}}
        ranked = wetlab.recommend(h, discovery_modality="multiome")["experiments"]
        atac = next(e for e in ranked if "ATAC footprinting" in e["name"])
        self.assertFalse(atac["orthogonal_to_discovery"])

    def test_orthogonal_assay_is_not_penalised(self):
        h = {"statement": "regulatory wiring", "key": {"modality": "multiome"}}
        ranked = wetlab.recommend(h, discovery_modality="multiome")["experiments"]
        cutrun = next(e for e in ranked if "CUT&RUN" in e["name"])
        self.assertTrue(cutrun["orthogonal_to_discovery"])
        self.assertGreater(cutrun["score"],
                           next(e for e in ranked if "ATAC footprinting" in e["name"])["score"])

    def test_every_experiment_is_costed_and_controlled(self):
        for mechanism in wetlab.CATALOGUE.values():
            for exp in mechanism:
                self.assertTrue(exp["controls"], exp["name"])
                self.assertIn(exp["cost_band"], wetlab.COST_WEIGHT)
                self.assertTrue(exp["turnaround"])
                self.assertTrue(exp["limitations"])

    def test_never_self_approves(self):
        out = wetlab.recommend({"statement": "x", "key": {}})
        self.assertIsNone(out["human_signoff"])
        self.assertIn("advisory", out)

    def test_underpowered_discovery_raises_a_note(self):
        out = wetlab.recommend({"statement": "x", "key": {}},
                               simulation={"power": {"achieved": 0.3}})
        self.assertTrue(any("underpowered" in n for n in out["notes"]))


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.conn = seeded_db()

    def test_dry_run_reaches_wetlab_and_waits_for_a_human(self):
        result = pipeline.run_pipeline(self.conn, "microglia", mode="dry", enable_mantis=False)
        self.assertEqual(result["reached_stage"], "wetlab")
        self.assertEqual(result["status"], "awaiting_signoff")
        self.assertTrue(result["awaiting_human_signoff"])

    def test_run_never_signs_itself_off(self):
        result = pipeline.run_pipeline(self.conn, "microglia", mode="dry", enable_mantis=False)
        body = artifacts.load(self.conn, result["artifacts"]["wetlab"])["body"]
        self.assertIsNot(body.get("human_signoff"), True)

    def test_every_stage_emits_spans(self):
        result = pipeline.run_pipeline(self.conn, "microglia", mode="dry", enable_mantis=False)
        kinds = {r["kind"] for r in self.conn.execute(
            "SELECT kind FROM trace_events WHERE run_id = ?", (result["run_id"],))}
        self.assertTrue({"stage", "tool", "gate"} <= kinds)

    def test_provenance_reaches_records(self):
        result = pipeline.run_pipeline(self.conn, "microglia", mode="dry", enable_mantis=False)
        prov = artifacts.provenance(self.conn, result["artifacts"]["wetlab"])
        stages_seen = [n["stage"] for n in prov["chain"]]
        self.assertIn("synthesis", stages_seen)
        self.assertIn("wetlab", stages_seen)
        self.assertTrue(prov["evidence"], "recommendation is not traceable to any record")

    def test_viewer_renders_without_a_backend(self):
        result = pipeline.run_pipeline(self.conn, "microglia", mode="dry", enable_mantis=False)
        html = viewer.render(self.conn, result["run_id"])
        self.assertIn("<title>", html)
        self.assertIn("reasoning trace", html)
        self.assertIn("awaiting human sign-off", html)

    def test_halted_run_records_its_reason(self):
        # No gaps at all: synthesis cannot select one and the run fails loudly.
        blank = db.connect(":memory:")
        db.init(blank)
        with self.assertRaises(RuntimeError):
            pipeline.run_pipeline(blank, "anything", mode="dry", enable_mantis=False)
        row = blank.execute("SELECT status, halt_reason FROM runs").fetchone()
        self.assertEqual(row["status"], "failed")
        self.assertTrue(row["halt_reason"])


class TestTracing(unittest.TestCase):
    def test_status_reports_why_mantis_is_inactive(self):
        status = mantis_status()
        self.assertIn("available", status)
        if not status["available"]:
            self.assertTrue(status["reason"], "must say why it is unavailable")

    def test_spans_persist_locally_with_no_backend(self):
        conn = db.connect(":memory:")
        db.init(conn)
        run_id = artifacts.create_run(conn, "t", "dry")
        tracer = Tracer(conn, run_id, enable_mantis=False)
        with tracer.span("outer", kind="stage") as outer:
            outer.set_attribute("k", "v")
            with tracer.span("inner", kind="tool") as inner:
                inner.set_output({"n": 1})
        rows = {r["name"]: r for r in conn.execute("SELECT * FROM trace_events")}
        self.assertEqual(rows["inner"]["parent_span"], rows["outer"]["span_id"])
        self.assertEqual(json.loads(rows["inner"]["output"]), {"n": 1})

    def test_failing_span_records_the_error_and_reraises(self):
        conn = db.connect(":memory:")
        db.init(conn)
        run_id = artifacts.create_run(conn, "t", "dry")
        tracer = Tracer(conn, run_id, enable_mantis=False)
        with self.assertRaises(ValueError):
            with tracer.span("boom", kind="tool"):
                raise ValueError("nope")
        row = conn.execute("SELECT status, attributes FROM trace_events").fetchone()
        self.assertEqual(row["status"], "error")
        self.assertIn("nope", row["attributes"])


if __name__ == "__main__":
    unittest.main()
