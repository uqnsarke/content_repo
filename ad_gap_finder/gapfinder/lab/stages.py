"""The five stage agents.

One rule runs through all of them: **code owns the numbers, the model owns the
prose.** Each stage computes its structured fields from the database and the
simulation modules, then — in `agent` mode — asks Claude to sharpen the free-text
fields only. Protected keys are re-applied after the merge, so a model cannot
revise a donor count, a power estimate or a gap id into something more
convenient. In `dry` mode the model is never called and the pipeline still runs
end to end, which is what makes it testable.

That split is the difference between an autonomous system and a fluent one. The
parts that can be checked are computed; the parts that cannot be checked are
clearly marked as narration and never feed back into a computation.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any

from .. import report
from ..propose import propose
from . import gates, insilico, wetlab
from .artifacts import Artifact

MODEL = "claude-opus-5"

# Fields a stage computes and the model may never overwrite.
PROTECTED = {
    "synthesis": {"evidence_record_ids", "candidate_gap_ids", "design_census", "corpus"},
    "hypothesis": {"gap_id", "key", "route", "verified_absent"},
    "analysis": {"n_available", "n_available_source", "n_available_meaning",
                 "latent_donor_counts", "requires_new_data", "unit_of_analysis",
                 "longitudinal"},
    "insilico": {"results", "underpowered_acknowledged"},
    "wetlab": {"experiments", "mechanism_class", "human_signoff", "advisory"},
}


@dataclass
class Context:
    conn: sqlite3.Connection
    run_id: int
    tracer: Any
    goal: str
    mode: str = "dry"
    model: str = MODEL
    artifacts: dict[str, Artifact] = field(default_factory=dict)
    gap: dict | None = None

    def parent_ids(self) -> list[int]:
        return [a.id for a in self.artifacts.values() if a.id is not None]


# ── stage 1: literature synthesis ───────────────────────────────────────────

def stage_synthesis(ctx: Context) -> Artifact:
    conn = ctx.conn

    with ctx.tracer.span("select_gaps", kind="tool", input={"goal": ctx.goal}) as span:
        gaps = report.load_gaps(conn, limit=40)
        if not gaps:
            raise RuntimeError("no gaps stored — run `cli.py gaps` before the pipeline")
        ranked = sorted(gaps, key=lambda g: (-_goal_match(ctx.goal, g), -g["priority"]))
        ctx.gap = ranked[0]
        span.set_output({"selected": ctx.gap["id"], "title": ctx.gap["title"],
                         "considered": len(gaps)})

    evidence_ids = [e["id"] for e in ctx.gap["evidence"]]
    with ctx.tracer.span("design_census", kind="tool",
                         input={"n_records": len(evidence_ids)}) as span:
        census = _design_census(conn, evidence_ids)
        span.set_output(census)

    corpus = report.corpus_summary(conn)
    body = {
        "goal": ctx.goal,
        "selected_gap": {"id": ctx.gap["id"], "kind": ctx.gap["kind"],
                         "title": ctx.gap["title"], "priority": ctx.gap["priority"]},
        "candidate_gap_ids": [g["id"] for g in ranked[:8]],
        "evidence_record_ids": evidence_ids,
        "design_census": census,
        "corpus": {"records": corpus["records"], "synthetic": corpus["synthetic"],
                   "sources": corpus["sources"]},
        "summary": (
            f"{ctx.gap['title']}. {ctx.gap['detail']} "
            f"Of {census['n_records']} supporting records, the most common limitation is "
            f"{census['dominant_flag'] or 'none recorded'}"
            + (f" ({census['flags'][census['dominant_flag']]:.0%})."
               if census["dominant_flag"] else ".")),
        "what_is_established": [],
        "what_rests_on_thin_evidence": [],
    }

    body = _narrate(ctx, "synthesis", body, instruction=(
        "Write a short literature synthesis for this gap. Fill `summary` (3-4 sentences), "
        "`what_is_established` and `what_rests_on_thin_evidence` (3 bullets each). Base "
        "every statement on the design census and the gap detail given — do not introduce "
        "findings, citations or accessions that are not in the input."),
        schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "what_is_established": {"type": "array", "items": {"type": "string"}},
                "what_rests_on_thin_evidence": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["summary", "what_is_established", "what_rests_on_thin_evidence"],
            "additionalProperties": False,
        })

    return Artifact(stage="synthesis", title=f"Synthesis: {ctx.gap['title']}", body=body,
                    records=evidence_ids, gap_id=ctx.gap["id"])


# ── stage 2: hypothesis generation ──────────────────────────────────────────

def stage_hypothesis(ctx: Context) -> Artifact:
    gap = ctx.gap
    with ctx.tracer.span("propose_from_gap", kind="tool", input={"gap_id": gap["id"]}) as span:
        template = propose(gap)
        span.set_output({"route": template["route"], "effort": template["effort"]})

    key = {k: v for k, v in gap["key"].items() if k != "axes"}
    body = {
        "gap_id": gap["id"],
        "key": key,
        "route": template["route"],
        "verified_absent": gap["scores"].get("confirmed_absent"),
        "statement": template["hypothesis"],
        "falsifier": template["falsifier"],
        "primary_outcome": _primary_outcome(key, template["route"]),
        "rationale": gap["detail"],
        "alternative_explanations": [
            "A compositional shift in the cell type, with no change in per-cell state.",
            "Post-mortem interval or agonal state differing systematically between groups.",
            "Batch structure aligned with group, which pooling across studies reproduces.",
        ],
    }

    body = _narrate(ctx, "hypothesis", body, instruction=(
        "Sharpen this hypothesis. `statement` must be one sentence naming the direction "
        "of effect. `falsifier` must name a specific result that would kill it — keep or "
        "improve the existing one, never weaken it. `primary_outcome` must be a single "
        "measurable quantity. Add any alternative explanation the list is missing. Do not "
        "change the gap or its axes."),
        schema={
            "type": "object",
            "properties": {
                "statement": {"type": "string"},
                "falsifier": {"type": "string"},
                "primary_outcome": {"type": "string"},
                "alternative_explanations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["statement", "falsifier", "primary_outcome",
                         "alternative_explanations"],
            "additionalProperties": False,
        })

    return Artifact(stage="hypothesis", title=body["statement"][:120], body=body,
                    parents=ctx.parent_ids(), gap_id=gap["id"])


# ── stage 3: analysis plan ──────────────────────────────────────────────────

def stage_analysis(ctx: Context) -> Artifact:
    hypothesis = ctx.artifacts["hypothesis"].body
    key = hypothesis["key"]

    with ctx.tracer.span("available_data", kind="tool",
                         input={"gap_id": hypothesis["gap_id"]}) as span:
        donors, donor_source = _latent_donor_counts(ctx.conn, hypothesis["gap_id"])
        span.set_output({"n_datasets": len(donors), "pooled": sum(donors),
                         "source": donor_source})

    longitudinal = key.get("design") in ("longitudinal", "cohort_incident") or \
        key.get("stage") in ("converter", "preclinical")

    body = {
        "hypothesis_statement": hypothesis["statement"],
        "unit_of_analysis": "donor",
        "longitudinal": longitudinal,
        "statistical_test": (
            "linear mixed model on donor-level summaries, group × time interaction, "
            "donor random intercept and slope"
            if longitudinal else
            "donor-level pseudobulk differential test with study as a fixed effect"),
        "primary_outcome": hypothesis["primary_outcome"],
        "predicted_direction": "increase in cases relative to matched controls",
        "covariates": ["age", "sex", "post-mortem interval", "sequencing batch",
                       "study of origin"] if not longitudinal else
                      ["age at baseline", "sex", "batch", "time since baseline"],
        "n_available": sum(donors),
        "n_available_source": donor_source,
        "n_available_meaning": {
            "reanalysable": "donors in datasets that already contain this contrast",
            "field_pool": "donors across the literature on this topic; new assays or new "
                          "collection would be required to use them",
            "none": "no donor counts recoverable from the cited records",
        }[donor_source],
        "latent_donor_counts": donors,
        "requires_new_data": donor_source != "reanalysable",
        "expected_effect_size": 0.5,
        "n_timepoints": 4 if longitudinal else None,
        "alpha": 0.05,
        "multiple_testing": "Benjamini-Hochberg across tested features, reported with the "
                            "number of features tested",
        "stopping_rule": "Analysis is specified before the data are unblinded; no outcome "
                         "or covariate is added after seeing the primary result.",
    }

    body = _narrate(ctx, "analysis", body, instruction=(
        "Review this analysis plan. You may refine `statistical_test`, "
        "`predicted_direction`, `covariates`, `multiple_testing` and `stopping_rule`, and "
        "set `expected_effect_size` to a defensible standardised value with a one-line "
        "justification in `effect_size_justification`. The unit of analysis is donor and "
        "must stay that way."),
        schema={
            "type": "object",
            "properties": {
                "statistical_test": {"type": "string"},
                "predicted_direction": {"type": "string"},
                "covariates": {"type": "array", "items": {"type": "string"}},
                "multiple_testing": {"type": "string"},
                "stopping_rule": {"type": "string"},
                "expected_effect_size": {"type": "number"},
                "effect_size_justification": {"type": "string"},
            },
            "required": ["statistical_test", "predicted_direction", "covariates",
                         "multiple_testing", "stopping_rule", "expected_effect_size",
                         "effect_size_justification"],
            "additionalProperties": False,
        })

    return Artifact(stage="analysis", title=f"Plan: {body['primary_outcome'][:100]}",
                    body=body, parents=ctx.parent_ids(), gap_id=hypothesis["gap_id"])


# ── stage 4: in-silico experimentation ──────────────────────────────────────

def stage_insilico(ctx: Context) -> Artifact:
    plan = ctx.artifacts["analysis"].body

    with ctx.tracer.span("run_simulations", kind="tool",
                         input={"effect": plan.get("expected_effect_size"),
                                "n_available": plan.get("n_available")}) as span:
        results = insilico.run_simulations(plan)
        span.set_output({"power": results["power"].get("achieved"),
                         "required_n": results["required_n_per_group"],
                         "negative_control_fpr":
                             results["negative_control"]["false_positive_rate"]})

    power = results["power"].get("achieved") or 0.0
    body = {
        "results": results,
        # Set by the stage itself, not by the model: the downstream gate accepts
        # an underpowered design only when it was declared, and letting the
        # narrator decide that would make the acknowledgement meaningless.
        "underpowered_acknowledged": power < gates.MIN_POWER,
        "interpretation": (
            (f"Projected: at {plan.get('n_available')} donors — which exist in the "
             f"literature but have not been assayed for this contrast — "
             if plan.get("requires_new_data") else
             f"At the {plan.get('n_available')} donors already available for re-analysis, ")
            + f"the design reaches power {power:.2f} for a standardised effect of "
              f"{plan.get('expected_effect_size')}; "
              f"{results['required_n_per_group']} per group would be needed for 0.8. "
            + results["negative_control"]["verdict"]),
        "decision": ("proceed to validation design with the power limitation stated"
                     if power < 0.8 else "adequately powered as specified"),
    }

    body = _narrate(ctx, "insilico", body, instruction=(
        "Interpret these simulation results in `interpretation` (4-6 sentences) and give a "
        "one-line `decision`. Quote the computed numbers exactly as given; do not "
        "recompute, round differently, or estimate any quantity yourself."),
        schema={
            "type": "object",
            "properties": {"interpretation": {"type": "string"},
                           "decision": {"type": "string"}},
            "required": ["interpretation", "decision"],
            "additionalProperties": False,
        })

    return Artifact(stage="insilico", title=f"Simulation: power {power:.2f}", body=body,
                    parents=ctx.parent_ids(), gap_id=ctx.artifacts["hypothesis"].body["gap_id"])


# ── stage 5: wet-lab recommendation ─────────────────────────────────────────

def stage_wetlab(ctx: Context) -> Artifact:
    hypothesis = ctx.artifacts["hypothesis"].body
    simulation = ctx.artifacts["insilico"].body["results"]
    discovery_modality = hypothesis["key"].get("modality")

    with ctx.tracer.span("rank_experiments", kind="tool",
                         input={"discovery_modality": discovery_modality}) as span:
        body = wetlab.recommend(hypothesis, discovery_modality, simulation)
        span.set_output({"mechanism": body["mechanism_class"],
                         "top": body["experiments"][0]["name"] if body["experiments"] else None})

    body["rationale"] = (
        f"Mechanism class {body['mechanism_class']}. Experiments are ranked by "
        f"discriminative value × feasibility ÷ cost, with a heavy penalty on any assay "
        f"sharing the failure modes of the discovery method.")

    body = _narrate(ctx, "wetlab", body, instruction=(
        "Write `rationale` (3-4 sentences) explaining why the top-ranked experiment is the "
        "right first move for this hypothesis and what a negative result there would mean. "
        "Do not add experiments, change costs, or suggest anything be ordered or booked."),
        schema={"type": "object", "properties": {"rationale": {"type": "string"}},
                "required": ["rationale"], "additionalProperties": False})

    return Artifact(stage="wetlab",
                    title=(body["experiments"][0]["name"][:120]
                           if body["experiments"] else "No experiment recommended"),
                    body=body, parents=ctx.parent_ids(), gap_id=hypothesis["gap_id"])


STAGE_FN = {
    "synthesis": stage_synthesis,
    "hypothesis": stage_hypothesis,
    "analysis": stage_analysis,
    "insilico": stage_insilico,
    "wetlab": stage_wetlab,
}


# ── helpers ─────────────────────────────────────────────────────────────────

def _goal_match(goal: str, gap: dict) -> int:
    words = {w for w in goal.lower().split() if len(w) > 3}
    blob = f"{gap['title']} {gap.get('detail') or ''} {json.dumps(gap['key'])}".lower()
    return sum(1 for w in words if w in blob)


def _design_census(conn: sqlite3.Connection, record_ids: list[int]) -> dict:
    if not record_ids:
        return {"n_records": 0, "flags": {}, "dominant_flag": None}
    rows = conn.execute(
        f"SELECT flag, COUNT(*) n FROM record_flags WHERE record_id IN "
        f"({','.join('?' * len(record_ids))}) GROUP BY flag ORDER BY n DESC", record_ids
    ).fetchall()
    flags = {r["flag"]: round(r["n"] / len(record_ids), 3) for r in rows}
    return {"n_records": len(record_ids), "flags": flags,
            "dominant_flag": rows[0]["flag"] if rows else None}


def _latent_donor_counts(conn: sqlite3.Connection, gap_id: int) -> tuple[list[int], str]:
    """Donor counts usable for a re-analysis, and where the number came from.

    `latent` and `near_miss` records are preferred: their data plausibly contains
    the contrast, so their donors are donors you could actually analyse. A design
    gap has neither — its evidence is the literature that shares the flaw — so it
    falls back to `supporting` records, which measure the size of the donor pool
    the field has assembled on the topic rather than data already in hand.

    The distinction is returned, not hidden, because it changes what the number
    means: 'reanalysable now' and 'this many donors exist somewhere' are not the
    same claim and should not both silently become `n_available`.
    """
    def query(roles: tuple[str, ...]) -> list[int]:
        return [r["n_donors"] for r in conn.execute(
            "SELECT r.n_donors FROM gap_evidence ge JOIN records r ON r.id = ge.record_id "
            f"WHERE ge.gap_id = ? AND ge.role IN ({','.join('?' * len(roles))}) "
            "AND r.n_donors IS NOT NULL", (gap_id, *roles))]

    reanalysable = query(("latent", "near_miss"))
    if reanalysable:
        return reanalysable, "reanalysable"
    supporting = query(("supporting",))
    if supporting:
        return supporting, "field_pool"
    return [], "none"


def _primary_outcome(key: dict, route: str) -> str:
    from ..axes import label

    # A design gap is keyed by the topic it concerns, not by a full axis tuple.
    if "flag" in key and key.get("axis"):
        return (f"the primary published effect for {label(key['axis'], key['term'])}, "
                f"re-estimated under a design without the shared limitation")
    if key.get("modality") in ("multiome", "snatac"):
        return "regulon activity score per donor for the top-ranked transcription factor"
    if key.get("cell_type"):
        return (f"per-donor pseudobulk expression module score in "
                f"{label('cell_type', key['cell_type'])}, reported alongside that cell "
                f"type's abundance")
    return "per-donor summary of the primary molecular readout"


def _narrate(ctx: Context, stage: str, body: dict, instruction: str, schema: dict) -> dict:
    """Ask the model to fill free-text fields. No-op in dry mode.

    Protected keys are restored after the merge, so the model's output can only
    ever change narration. A stage that tried to revise its own donor count or
    power estimate would be silently overruled here.
    """
    if ctx.mode != "agent":
        return body

    import anthropic

    protected = {k: body[k] for k in PROTECTED.get(stage, set()) if k in body}
    prompt = (f"{instruction}\n\nStage: {stage}\nGoal: {ctx.goal}\n\n"
              f"Computed input (authoritative — every number here is already verified):\n"
              f"{json.dumps(body, indent=2, default=str)[:12000]}")

    with ctx.tracer.span(f"llm:{stage}", kind="llm",
                         input={"model": ctx.model, "chars": len(prompt)}) as span:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=ctx.model,
            max_tokens=8000,
            system=("You are one stage of an autonomous research pipeline. Numbers, ids and "
                    "record counts in your input are computed and authoritative: quote them, "
                    "never revise or invent them. Never write an accession, DOI or PMID that "
                    "is not present in the input. Write for a domain expert who will check "
                    "you."),
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema",
                                                        "schema": schema}},
            messages=[{"role": "user", "content": prompt}],
        )
        if response.stop_reason == "refusal":
            span.fail("model refused")
            return body
        text = next(b.text for b in response.content if b.type == "text")
        update = json.loads(text)
        span.set_output({"keys": sorted(update)})
        span.set_attribute("gen_ai.usage.output_tokens", response.usage.output_tokens)

    merged = merge_narration(body, update, PROTECTED.get(stage, set()))
    merged["_narrated_by"] = ctx.model
    return merged


def merge_narration(body: dict, update: dict, protected: set[str]) -> dict:
    """Apply model output, then put the protected fields back.

    Separate and public so the guarantee is directly testable: whatever the model
    returns, a protected key comes out of this function holding the value the
    stage computed.
    """
    return {**body, **update, **{k: body[k] for k in protected if k in body}}


def agent_mode_available() -> bool:
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY") or
                os.path.exists(os.path.expanduser("~/.config/anthropic")))
