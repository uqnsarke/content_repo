"""Turn a gap into a testable hypothesis and a workflow that could falsify it.

Deliberately template-driven and deterministic. The agent layer can write a
better-read version, but the structure below is the part that must not vary:
every proposal states what would have to be true, what design separates it
from the alternative, and what result would kill it. A hypothesis with no
stated failure condition is a press release.

The templates branch on axis values because the right design genuinely differs
— a missing longitudinal contrast and a missing cell type in an existing
dataset are not the same kind of work, and proposing a new cohort for a
re-analysis problem is the most common way this sort of tool wastes people's
time.
"""

from __future__ import annotations

from .axes import label

TIME_RESOLVED = {"longitudinal", "cohort_incident"}
LIVING = {"peripheral", "biopsy"}


def _phrase(key: dict[str, str]) -> str:
    return ", ".join(f"{label(a, t)}" for a, t in sorted(key.items()) if a != "axes")


def propose(gap: dict) -> dict:
    """Return {hypothesis, rationale, workflow: [...], falsifier, effort}."""
    if gap["kind"] != "coverage":
        return _generic(gap)

    key = {a: t for a, t in gap["key"].items() if a != "axes"}
    scores = gap.get("scores", {})
    latent = scores.get("n_latent") or 0
    stage = key.get("stage")
    design = key.get("design")
    tissue = key.get("tissue_state")
    cell = key.get("cell_type")
    region = key.get("region")
    modality = key.get("modality")

    subject = _phrase(key)

    if latent:
        route = "reanalysis"
        effort = "low — data already public"
    elif design in TIME_RESOLVED or tissue in LIVING:
        route = "new_longitudinal"
        effort = "high — requires prospective sampling"
    else:
        route = "new_crosssectional"
        effort = "medium — requires new tissue or new assay on banked tissue"

    hypothesis = (
        f"There is a measurable, reproducible difference in {subject} that the current "
        f"evidence base cannot detect, because no study has assayed this combination."
    )
    if stage in ("preclinical", "converter", "mci"):
        hypothesis = (
            f"A signal in {subject} is present before diagnosis and changes with proximity "
            f"to conversion, rather than appearing as a consequence of established disease."
        )
    elif stage == "resilient":
        hypothesis = (
            f"In {subject}, individuals with high pathology and intact cognition differ "
            f"systematically from both matched controls and matched cases — i.e. resilience "
            f"is an active state, not simply less pathology."
        )
    elif cell and modality in ("multiome", "snatac"):
        hypothesis = (
            f"In {subject}, the disease-associated change is a shift in regulatory wiring "
            f"(TF→target coupling) rather than a shift in mean expression, which is why "
            f"expression-only studies of this compartment have found little."
        )

    workflow = _workflow(route, key, scores)
    falsifier = _falsifier(route, key, stage)

    return {
        "hypothesis": hypothesis,
        "rationale": gap.get("detail", ""),
        "route": route,
        "effort": effort,
        "workflow": workflow,
        "falsifier": falsifier,
    }


def _workflow(route: str, key: dict, scores: dict) -> list[str]:
    subject = _phrase(key)
    cell = key.get("cell_type")
    modality = key.get("modality")
    stage = key.get("stage")

    if route == "reanalysis":
        steps = [
            f"Pull the {scores.get('n_latent')} record(s) flagged as latent coverage; confirm "
            f"from their metadata that the target condition is actually represented, not just "
            f"the assay — the index can only see what the record states.",
            "Harmonise: one annotation pass over the pooled object, so cell labels are not "
            "each study's own vocabulary.",
            "Model with donor as a random effect and study as a fixed effect. Pooling "
            "public datasets without a study term recovers batch, not biology.",
        ]
    elif route == "new_longitudinal":
        steps = [
            "Sample within-subject across timepoints; each participant is their own control, "
            "which is the only way to separate trajectory from between-person variation.",
            "Realign every sample to time-to-event rather than to diagnosis label, so the "
            "axis is trajectory and not group membership.",
            "Power on the number of *converters*, not the number of participants — the "
            "informative contrast is converters vs. matched stable, and conversion rates are "
            "low enough that this is usually the binding constraint.",
        ]
    else:
        steps = [
            "Select banked tissue matched on age, sex, PMI and (where available) pathology "
            "stage before assaying, not after — post-hoc matching on a small n cannot recover "
            "a confounded design.",
            "Assay the missing combination directly rather than inferring it from a proxy "
            "compartment.",
        ]

    if cell:
        steps.append(
            f"Quantify {label('cell_type', cell)} specifically, and report its abundance "
            f"separately from its per-cell state — a compositional shift and a state shift "
            f"produce the same pseudobulk signal and mean different things.")
    if modality in ("multiome", "snatac"):
        steps.append(
            "Infer regulatory networks per compartment (SCENIC+ / EpiRegulon) and test "
            "regulon activity, not just differential expression — the hypothesis is about "
            "wiring, so a DE-only readout cannot address it.")
    if stage in ("preclinical", "converter", "mci"):
        steps.append(
            "Mediation analysis against amyloid/tau burden: the informative result is the "
            "component of the trajectory that is *not* mediated by pathology load.")
    steps.append(
        "Replicate in an independent cohort before interpreting. One cohort cannot "
        "distinguish a finding from a cohort-specific artefact, however large it is.")
    return steps


def _falsifier(route: str, key: dict, stage: str | None) -> str:
    if stage in ("preclinical", "converter", "mci"):
        return ("The hypothesis fails cleanly if the trajectory is fully mediated by amyloid "
                "or tau burden, or if converters and matched stable participants are "
                "indistinguishable once donor and batch are modelled.")
    if key.get("modality") in ("multiome", "snatac"):
        return ("The hypothesis fails if regulon-level models add no discriminative "
                "information beyond differential expression on the same cells.")
    if route == "reanalysis":
        return ("The hypothesis fails if the effect disappears once study is included as a "
                "fixed effect — that result means the signal was batch.")
    return ("The hypothesis fails if the difference is absent after matching on age, sex and "
            "post-mortem interval, or does not replicate in a second cohort.")


def _generic(gap: dict) -> dict:
    kind = gap["kind"]
    if kind == "design":
        return {
            "hypothesis": ("The consensus in this area is an artefact of its uniform study "
                           "design and will not survive a design that lacks the shared limitation."),
            "rationale": gap.get("detail", ""),
            "route": "design_replication",
            "effort": "medium",
            "workflow": [
                "Identify the specific claim that the shared design cannot support.",
                "Re-test it under a design without that limitation (time-resolved, "
                "sex-stratified, or in a second independent cohort as applicable).",
                "Pre-register the direction of effect predicted by the existing literature "
                "so a null is interpretable rather than filed away.",
            ],
            "falsifier": ("The hypothesis fails if the effect reproduces at similar magnitude "
                          "under the alternative design."),
        }
    if kind == "abc":
        a, c = gap["key"].get("a"), gap["key"].get("c")
        return {
            "hypothesis": f"{a} affects {c}, composed through the bridging terms already published.",
            "rationale": gap.get("detail", ""),
            "route": "direct_test",
            "effort": "low to medium",
            "workflow": [
                f"Confirm each half of the chain holds in one system before composing them — "
                f"published A→B and B→C in different tissues do not imply A→C in either.",
                f"Test {a} → {c} directly with the intermediate measured, so a null "
                f"distinguishes 'no effect' from 'effect not through this route'.",
                "Perturb the bridge: if the composition is real, blocking the intermediate "
                "should abolish the association.",
            ],
            "falsifier": (f"The hypothesis fails if {a} and {c} are unrelated when the "
                          f"intermediate is measured and controlled."),
        }
    if kind == "contradiction":
        return {
            "hypothesis": ("The disagreement is explained by a measurable difference between "
                           "the studies rather than by error in either."),
            "rationale": gap.get("detail", ""),
            "route": "adjudication",
            "effort": "low",
            "workflow": [
                "Tabulate the conflicting studies on every axis this tool tracks; the "
                "explanation is usually an axis on which they differ.",
                "Re-analyse both underlying datasets through one pipeline — a large share of "
                "direction conflicts are pipeline differences, not biology.",
                "If they still conflict, test the candidate moderator explicitly.",
            ],
            "falsifier": ("The hypothesis fails if a single harmonised pipeline reproduces "
                          "both original results on their own data."),
        }
    return {
        "hypothesis": gap.get("title", ""),
        "rationale": gap.get("detail", ""),
        "route": "unspecified",
        "effort": "unknown",
        "workflow": ["Assess feasibility against existing public data before designing new work."],
        "falsifier": "Not automatically derivable for this gap kind.",
    }
