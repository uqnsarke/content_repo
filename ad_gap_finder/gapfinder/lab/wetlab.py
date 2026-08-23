"""Wet-lab recommendation — ranked, costed, and advisory only.

Nothing here orders reagents, books instrument time or commits spend. The stage
produces proposals; the final gate refuses to close a run until a person has
signed off, and the pipeline never sets that flag itself.

The ranking encodes one methodological rule that is easy to state and constantly
broken: **validation must be orthogonal to discovery.** Confirming a snRNA-seq
finding with more snRNA-seq confirms the pipeline, not the biology. An
experiment sharing the discovery modality is scored down hard, however cheap and
convenient it is — and it is always the cheapest and most convenient option,
which is why it needs to be penalised rather than left to judgement.

The catalogue is deliberately conservative about what each assay can settle.
`discriminates` names the specific alternative each experiment rules out; an
experiment that cannot rule anything out does not belong on the list regardless
of how well it would illustrate the finding.
"""

from __future__ import annotations

COST_WEIGHT = {"low": 1.0, "medium": 1.6, "high": 2.6, "very_high": 4.0}

# mechanism class -> candidate experiments
CATALOGUE: dict[str, list[dict]] = {
    "regulatory_wiring": [
        {"name": "CUT&RUN / CUT&Tag for the candidate TF in sorted nuclei",
         "modality": "chromatin_binding",
         "discriminates": "whether the TF actually occupies the predicted target loci, "
                          "versus a motif-enrichment artefact of the inference method",
         "controls": ["IgG control", "a TF with no predicted role in the module",
                      "spike-in normalisation"],
         "cost_band": "medium", "turnaround": "4-6 weeks",
         "sample_requirement": "fresh-frozen tissue, 50k-100k sorted nuclei per reaction",
         "discriminative_value": 0.90, "feasibility": 0.75,
         "limitations": "Occupancy is not regulation; pair with a perturbation before "
                        "claiming the TF drives the module."},
        {"name": "CRISPRi knockdown of the TF in iPSC-derived cells of the target type",
         "modality": "perturbation",
         "discriminates": "whether the regulon is downstream of the TF at all, versus "
                          "co-varying with it",
         "controls": ["non-targeting guide", "two independent guides per target",
                      "rescue by TF re-expression"],
         "cost_band": "high", "turnaround": "3-4 months",
         "sample_requirement": "established iPSC line with inducible differentiation",
         "discriminative_value": 0.95, "feasibility": 0.55,
         "limitations": "iPSC-derived cells are developmentally immature; a null result "
                        "may reflect the model rather than the hypothesis."},
        {"name": "ATAC footprinting at predicted sites in existing chromatin data",
         "modality": "chromatin_accessibility",
         "discriminates": "whether accessibility at the predicted sites changes with "
                          "disease state",
         "controls": ["matched background regions", "GC and depth matching"],
         "cost_band": "low", "turnaround": "2-3 weeks",
         "sample_requirement": "none — existing snATAC data",
         "discriminative_value": 0.55, "feasibility": 0.95,
         "limitations": "Computational, not independent of the discovery data if the "
                        "same samples are used."},
    ],
    "cell_state": [
        {"name": "RNAscope / smFISH for marker genes in situ",
         "modality": "in_situ_rna",
         "discriminates": "whether the state exists in intact tissue with its spatial "
                          "context, versus being a dissociation or ambient-RNA artefact",
         "controls": ["positive and negative probe controls", "a second cohort's sections",
                      "region-matched sampling"],
         "cost_band": "medium", "turnaround": "6-8 weeks",
         "sample_requirement": "FFPE or fixed-frozen sections, >= 8 donors per group",
         "discriminative_value": 0.85, "feasibility": 0.80,
         "limitations": "Quantification is sensitive to section depth and autofluorescence "
                        "in aged brain; lipofuscin controls are not optional."},
        {"name": "Flow cytometry / CyTOF on the protein markers of the state",
         "modality": "protein_single_cell",
         "discriminates": "whether the transcriptional state reaches the protein level",
         "controls": ["FMO controls", "isotype controls", "matched processing batches"],
         "cost_band": "medium", "turnaround": "4-6 weeks",
         "sample_requirement": "fresh tissue or viably frozen cells",
         "discriminative_value": 0.75, "feasibility": 0.65,
         "limitations": "Post-mortem brain dissociates poorly; feasible mainly for "
                        "peripheral or biopsy material."},
    ],
    "composition": [
        {"name": "Unbiased stereology or IF cell counting on tissue sections",
         "modality": "histology",
         "discriminates": "whether cell numbers truly differ, versus differential "
                          "recovery during dissociation — which produces the same "
                          "single-cell result",
         "controls": ["region-matched sampling frames", "blinded counting",
                      "a cell type with no expected change"],
         "cost_band": "medium", "turnaround": "8-12 weeks",
         "sample_requirement": "matched sections, >= 10 donors per group",
         "discriminative_value": 0.95, "feasibility": 0.70,
         "limitations": "Slow and labour-intensive; the blinding is what makes it worth "
                        "the time, so it cannot be dropped to save weeks."},
    ],
    "peripheral_biomarker": [
        {"name": "Orthogonal platform re-measurement (targeted MS against affinity assay)",
         "modality": "targeted_proteomics",
         "discriminates": "whether the signal is the analyte or the assay — affinity "
                          "reagents cross-react, and epitope effects mimic abundance change",
         "controls": ["shared reference samples across platforms",
                      "spike-in recovery", "randomised run order"],
         "cost_band": "medium", "turnaround": "6-10 weeks",
         "sample_requirement": "banked plasma/serum aliquots, never freeze-thawed twice",
         "discriminative_value": 0.85, "feasibility": 0.85,
         "limitations": "Agreement between platforms is often poor even for real analytes; "
                        "pre-specify what level of concordance counts as validation."},
        {"name": "Prospective longitudinal sampling in an independent cohort",
         "modality": "longitudinal_clinical",
         "discriminates": "whether the marker moves before diagnosis, versus reflecting "
                          "established disease",
         "controls": ["matched stable participants", "pre-registered time-to-event model"],
         "cost_band": "very_high", "turnaround": "2-5 years",
         "sample_requirement": "cohort access with repeat draws and conversion follow-up",
         "discriminative_value": 0.98, "feasibility": 0.30,
         "limitations": "The definitive design and the one nobody funds on a first pass; "
                        "propose it alongside a cheaper interim experiment, not instead."},
    ],
    "causal": [
        {"name": "Isogenic knock-in/knock-out iPSC lines with the variant of interest",
         "modality": "perturbation",
         "discriminates": "whether the genetic variant causes the molecular phenotype",
         "controls": ["isogenic parental line", "two independent clones",
                      "off-target sequencing"],
         "cost_band": "high", "turnaround": "6-9 months",
         "sample_requirement": "iPSC line, editing capability",
         "discriminative_value": 0.95, "feasibility": 0.50,
         "limitations": "Clone-to-clone variability is large; a single clone per genotype "
                        "cannot support a causal claim."},
        {"name": "Targeted perturbation in an animal model with the matched readout",
         "modality": "in_vivo",
         "discriminates": "whether the mechanism operates in intact tissue over time",
         "controls": ["littermate controls", "both sexes", "pre-registered endpoints"],
         "cost_band": "very_high", "turnaround": "9-18 months",
         "sample_requirement": "IACUC approval, colony",
         "discriminative_value": 0.80, "feasibility": 0.35,
         "limitations": "Mouse models reproduce amyloid and tau pathology but not "
                        "sporadic AD; a positive result constrains mechanism, not "
                        "clinical relevance."},
    ],
}

# Experiment modalities that share a discovery modality's failure modes.
#
# Orthogonality is about failure modes, not analytes. RNAscope measures RNA just
# as snRNA-seq does, but it does not share dissociation loss, ambient RNA or
# amplification bias — which is exactly why it validates. CUT&RUN and ATAC both
# touch chromatin and answer different questions. So the non-orthogonal pairs are
# listed explicitly rather than guessed from name similarity; edit this map when
# adding assays, and the default for anything unlisted is "orthogonal".
SHARED_FAILURE_MODES: dict[str, set[str]] = {
    # Dissociation-based single-cell discovery: anything else that starts by
    # dissociating tissue and building libraries inherits the same artefacts.
    "snrna": {"protein_single_cell"},
    "scrna": {"protein_single_cell"},
    "snatac": {"chromatin_accessibility"},
    "multiome": {"chromatin_accessibility"},
    "chromatin_accessibility": {"chromatin_accessibility"},
    "chromatin_binding": {"chromatin_binding"},
    # Affinity-based discovery cannot be validated by another affinity assay:
    # cross-reactivity and epitope effects reproduce across both.
    "proteomics": {"protein_single_cell", "targeted_proteomics"},
    "spatial_rna": {"in_situ_rna"},
    "histology": {"histology"},
    "perturbation": {"perturbation"},
    "in_vivo": {"in_vivo"},
}


def _shares_failure_modes(experiment_modality: str, discovery_modality: str | None) -> bool:
    if not discovery_modality:
        return False
    if experiment_modality == discovery_modality:
        return True
    return experiment_modality in SHARED_FAILURE_MODES.get(discovery_modality, set())


# Which mechanism class a hypothesis belongs to, inferred from its axes.
def classify(hypothesis: dict) -> str:
    key = {k: v for k, v in (hypothesis.get("key") or {}).items() if k != "axes"}
    text = f"{hypothesis.get('statement', '')} {hypothesis.get('primary_outcome', '')}".lower()
    modality = key.get("modality", "")

    if modality in ("multiome", "snatac", "hic") or "regulon" in text or "wiring" in text \
            or "transcription factor" in text:
        return "regulatory_wiring"
    if "abundance" in text or "proportion" in text or "composition" in text:
        return "composition"
    if key.get("region") in ("blood", "csf") or modality in ("proteomics", "metabolomics"):
        return "peripheral_biomarker"
    if key.get("tissue_state") in ("ipsc", "organoid") or "causal" in text or "variant" in text:
        return "causal"
    return "cell_state"


def recommend(hypothesis: dict, discovery_modality: str | None = None,
              simulation: dict | None = None, limit: int = 4) -> dict:
    """Rank validation experiments for one hypothesis.

    score = discriminative_value × feasibility × orthogonality / cost_weight

    `orthogonality` is 0.35 when the experiment shares the discovery method's
    failure modes (see `SHARED_FAILURE_MODES`).
    That is a heavy penalty and it is meant to be: the resulting ranking will
    often put a slower, more expensive assay first, which is the correct answer
    and the one a convenience-driven process never reaches.
    """
    mechanism = classify(hypothesis)
    candidates = CATALOGUE[mechanism] + (
        CATALOGUE["causal"] if mechanism != "causal" else [])

    scored = []
    for exp in candidates:
        same_modality = _shares_failure_modes(exp["modality"], discovery_modality)
        orthogonality = 0.35 if same_modality else 1.0
        score = (exp["discriminative_value"] * exp["feasibility"] * orthogonality
                 / COST_WEIGHT[exp["cost_band"]])
        scored.append({**exp, "orthogonal_to_discovery": not same_modality,
                       "score": round(score, 4)})

    scored.sort(key=lambda e: -e["score"])
    top = scored[:limit]

    notes = []
    if simulation:
        power = (simulation.get("power") or {}).get("achieved")
        if isinstance(power, (int, float)) and power < 0.8:
            notes.append(
                f"The discovery analysis is powered at {power:.2f}. Validating an "
                "underpowered result risks spending wet-lab budget confirming noise — "
                "resolve power first, or treat the top experiment as exploratory and "
                "size it independently.")
        negative = simulation.get("negative_control") or {}
        if (negative.get("false_positive_rate") or 0) > 0.10:
            notes.append(
                "The negative control shows the discovery design cannot separate a "
                "compositional shift from a state change. Prefer an experiment that "
                "counts cells directly; a state-marker assay will reproduce the "
                "ambiguity rather than resolve it.")

    return {
        "mechanism_class": mechanism,
        "discovery_modality": discovery_modality,
        "experiments": top,
        "notes": notes,
        "advisory": ("Proposals only. Nothing in this run orders reagents, books "
                     "instrument time, commits spend or contacts a vendor. Sample-size "
                     "figures are design inputs, not approvals, and any work involving "
                     "human tissue or animals requires the relevant approvals before "
                     "it starts."),
        "human_signoff": None,
    }
