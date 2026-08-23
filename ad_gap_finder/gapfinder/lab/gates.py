"""Stage gates — the part that makes autonomy safe rather than fast.

An autonomous research pipeline's real failure mode is not a wrong answer at one
stage. It is a wrong answer at stage two that stages three, four and five then
elaborate, cite and format until it arrives looking like a conclusion. Each
stage here must pass a machine-checkable gate before the next one runs, and a
failed gate halts the run with its reason recorded.

The checks are deliberately about *structure*, not quality. No check asks
whether a hypothesis is good — that is not decidable here, and pretending
otherwise would be the same confabulation one layer up. They ask whether it is
the kind of object that can be wrong: does it have a falsifier, does it name a
primary outcome, does it state the unit of analysis, does it rest on an absence
that was verified rather than assumed.

Gates fail closed. A check that cannot be evaluated counts as failed, because
the alternative — proceeding on an unevaluated precondition — is exactly the
behaviour these exist to prevent.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Callable

Check = tuple[str, bool, str]

MIN_EVIDENCE_RECORDS = 3
MIN_POWER = 0.8

# The one check whose failure is an expected resting state rather than an error.
SIGNOFF_CHECK = "human_signoff"

# Units of analysis that are actually donors. Testing across cells rather than
# donors inflates n by three orders of magnitude and is the single most common
# way a single-cell result fails to replicate, so the plan has to say which.
DONOR_UNITS = {"donor", "participant", "subject", "sample_donor", "pseudobulk_donor"}


def _fail(name: str, detail: str) -> Check:
    return (name, False, detail)


# ── per-transition checks ───────────────────────────────────────────────────

def gate_synthesis_to_hypothesis(conn, run_id: int, body: dict) -> list[Check]:
    checks: list[Check] = []

    evidence = body.get("evidence_record_ids") or []
    checks.append(("cites_evidence", len(evidence) >= MIN_EVIDENCE_RECORDS,
                   f"{len(evidence)} record(s) cited, need {MIN_EVIDENCE_RECORDS}"))

    flags = body.get("design_census") or {}
    checks.append(("characterises_evidence_quality", bool(flags),
                   "design flag census present" if flags else
                   "no design census: the brief describes findings without describing "
                   "what kind of evidence they rest on"))

    gaps = body.get("candidate_gap_ids") or []
    checks.append(("identifies_candidate_gaps", bool(gaps),
                   f"{len(gaps)} candidate gap(s)" if gaps else "no candidate gaps"))
    return checks


def gate_hypothesis_to_analysis(conn, run_id: int, body: dict) -> list[Check]:
    checks: list[Check] = []

    falsifier = (body.get("falsifier") or "").strip()
    checks.append(("has_falsifier", len(falsifier) > 20,
                   "falsifier stated" if len(falsifier) > 20 else
                   "no falsifier: a hypothesis with no stated failure condition cannot "
                   "be tested, only illustrated"))

    outcome = (body.get("primary_outcome") or "").strip()
    checks.append(("names_primary_outcome", bool(outcome),
                   outcome or "no primary outcome named"))

    gap_id = body.get("gap_id")
    if gap_id is None:
        checks.append(_fail("grounded_in_verified_gap", "hypothesis is not tied to a gap"))
    else:
        row = conn.execute("SELECT scores, kind FROM gaps WHERE id = ?", (gap_id,)).fetchone()
        if row is None:
            checks.append(_fail("grounded_in_verified_gap", f"gap {gap_id} does not exist"))
        else:
            scores = json.loads(row["scores"] or "{}")
            confirmed = scores.get("confirmed_absent")
            # confirmed_absent is None for a cell that is thin rather than empty;
            # that is legitimate. An explicit False is not — it means the text
            # check found records the index missed.
            ok = confirmed is not False
            checks.append(("grounded_in_verified_gap", ok,
                           "absence verified" if ok else
                           "the gap's own verification found records covering this "
                           "combination — the absence is an indexing artefact"))
            checks.append(("feasible", scores.get("feasibility", 1.0) > 0,
                           f"feasibility {scores.get('feasibility', 1.0)}"))
    return checks


def gate_analysis_to_insilico(conn, run_id: int, body: dict) -> list[Check]:
    checks: list[Check] = []

    unit = (body.get("unit_of_analysis") or "").strip().lower()
    checks.append(("unit_of_analysis_is_donor", unit in DONOR_UNITS,
                   f"unit = {unit or 'unstated'}" + ("" if unit in DONOR_UNITS else
                   " — inference across cells rather than donors treats one participant "
                   "as thousands of independent observations")))

    test = (body.get("statistical_test") or "").strip()
    checks.append(("names_test", bool(test), test or "no statistical test named"))

    n = body.get("n_available")
    checks.append(("states_available_n", isinstance(n, int) and n > 0,
                   f"n = {n}" if n else "available n not stated"))

    checks.append(("preregisters_direction", bool((body.get("predicted_direction") or "").strip()),
                   body.get("predicted_direction") or
                   "no predicted direction: without one, any result confirms the hypothesis"))

    covariates = body.get("covariates") or []
    checks.append(("declares_covariates", isinstance(covariates, list) and bool(covariates),
                   f"{len(covariates)} covariate(s)" if covariates else
                   "no covariates declared"))
    return checks


def gate_insilico_to_wetlab(conn, run_id: int, body: dict) -> list[Check]:
    checks: list[Check] = []

    results = body.get("results") or {}
    checks.append(("simulation_executed", bool(results),
                   "simulation results present" if results else
                   "no results: the stage produced a description of a simulation "
                   "rather than a simulation"))

    power = (results.get("power") or {}).get("achieved")
    acknowledged = bool(body.get("underpowered_acknowledged"))
    ok = (isinstance(power, (int, float)) and power >= MIN_POWER) or acknowledged
    checks.append(("adequately_powered_or_acknowledged", ok,
                   f"power = {power}" if isinstance(power, (int, float)) else
                   "power not computed"))

    # A design that cannot distinguish the hypothesis from its most likely
    # confound will produce a positive result either way. Requiring the negative
    # control up front is cheaper than discovering it after the wet-lab spend.
    negative = results.get("negative_control")
    checks.append(("ran_negative_control", bool(negative),
                   "negative control simulated" if negative else
                   "no negative control: nothing rules out the confound producing "
                   "the same signal"))
    return checks


def gate_wetlab_to_done(conn, run_id: int, body: dict) -> list[Check]:
    checks: list[Check] = []

    experiments = body.get("experiments") or []
    checks.append(("has_recommendations", bool(experiments),
                   f"{len(experiments)} experiment(s)"))

    missing_controls = [e.get("name") for e in experiments if not e.get("controls")]
    checks.append(("every_experiment_has_controls", not missing_controls,
                   "all controls specified" if not missing_controls else
                   f"no controls for: {', '.join(str(m) for m in missing_controls)}"))

    missing_cost = [e.get("name") for e in experiments
                    if not e.get("cost_band") or not e.get("turnaround")]
    checks.append(("costed", not missing_cost,
                   "cost and turnaround stated" if not missing_cost else
                   f"uncosted: {', '.join(str(m) for m in missing_cost)}"))

    checks.append(("advisory_only", bool(body.get("advisory")),
                   "recommendations are marked advisory: nothing here orders reagents, "
                   "books instrument time or commits spend"))

    # The pipeline never sets this, and the run does not reach 'done' without it.
    # This is the boundary an autonomous loop should not cross on its own, so it
    # is a required check rather than a note — `cli.py signoff` is a person's
    # command, not a stage.
    checks.append((SIGNOFF_CHECK, body.get("human_signoff") is True,
                   "signed off" if body.get("human_signoff") is True else
                   "awaiting human sign-off before the run can be considered complete"))
    return checks


GATES: dict[tuple[str, str], Callable] = {
    ("synthesis", "hypothesis"): gate_synthesis_to_hypothesis,
    ("hypothesis", "analysis"): gate_hypothesis_to_analysis,
    ("analysis", "insilico"): gate_analysis_to_insilico,
    ("insilico", "wetlab"): gate_insilico_to_wetlab,
    ("wetlab", "done"): gate_wetlab_to_done,
}


def evaluate(conn: sqlite3.Connection, run_id: int, from_stage: str, to_stage: str,
             body: dict) -> tuple[bool, list[Check]]:
    """Run the gate between two stages and record the outcome."""
    fn = GATES.get((from_stage, to_stage))
    if fn is None:
        checks = [_fail("gate_defined", f"no gate for {from_stage} -> {to_stage}")]
    else:
        try:
            checks = fn(conn, run_id, body)
        except Exception as exc:
            # Fail closed: an unevaluable precondition is not a satisfied one.
            checks = [_fail("gate_evaluated", f"{type(exc).__name__}: {exc}")]

    passed = all(c[1] for c in checks)
    conn.execute(
        "INSERT INTO gate_results (run_id, gate, from_stage, to_stage, passed, checks) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, f"{from_stage}->{to_stage}", from_stage, to_stage, int(passed),
         json.dumps([{"name": n, "passed": p, "detail": d} for n, p, d in checks])))
    conn.commit()
    return passed, checks


def failures(checks: list[Check]) -> str:
    return "; ".join(f"{n}: {d}" for n, p, d in checks if not p)
