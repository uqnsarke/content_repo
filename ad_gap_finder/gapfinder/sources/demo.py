"""Synthetic corpus for offline development.

⚠️  EVERY RECORD PRODUCED HERE IS FABRICATED. No title, accession, cohort or
donor count corresponds to a real study. Identifiers are prefixed `DEMO-` so a
demo record can never be mistaken for a real one, and `cli.py report` labels
any report built from this source.

Its purpose is to exercise the gap logic without a network, so the arithmetic
can be tested against a corpus whose true structure is known. The axis
*marginals* are set to mirror the well-documented skew of the AD single-cell
literature — cortex-heavy, snRNA-heavy, post-mortem, cross-sectional,
case-vs-control — because a gap detector that only works on a uniform corpus
has not been tested at all. The gaps it surfaces are therefore structural
(which axis combinations the field's own sampling leaves empty) and not
claims about specific published work.

Generation is seeded, so the corpus is identical on every machine.
"""

from __future__ import annotations

import random
from typing import Iterator

from ..axes import label

# (term, weight) per axis — weights are relative, not probabilities.
REGIONS = [
    ("prefrontal_cortex", 34), ("temporal_cortex", 16), ("hippocampus", 12),
    ("entorhinal_cortex", 9), ("blood", 8), ("cingulate_cortex", 5),
    ("occipital_cortex", 4), ("parietal_cortex", 3), ("cerebellum", 3),
    ("amygdala", 2), ("striatum", 2), ("white_matter", 2), ("csf", 2),
    ("substantia_nigra", 1), ("locus_coeruleus", 1), ("meninges", 1),
    ("thalamus", 1), ("retina", 1), ("hypothalamus", 1), ("gut", 1),
]
MODALITIES = [
    ("snrna", 40), ("bulk_rna", 15), ("scrna", 9), ("snatac", 6),
    ("spatial_rna", 6), ("proteomics", 5), ("multiome", 5), ("methylation", 4),
    ("gwas", 3), ("cite_seq", 2), ("imaging_pet", 2), ("perturbation", 1),
    ("tcr_bcr", 1), ("hic", 1), ("spatial_prot", 1), ("electrophys", 1),
]
STAGES = [
    ("ad_dementia", 40), ("control", 34), ("mci", 7), ("adrd_other", 6),
    ("eoad", 4), ("preclinical", 4), ("resilient", 3), ("converter", 2),
]
TISSUE = [
    ("post_mortem", 66), ("ipsc", 10), ("in_vivo_model", 10),
    ("peripheral", 8), ("organoid", 4), ("primary_cult", 2), ("biopsy", 1),
]
DESIGNS = [("cross_sectional", 84), ("case_control", 8), ("longitudinal", 5),
           ("cohort_incident", 2), ("interventional", 1)]
SPECIES = [("human", 74), ("mouse", 22), ("nhp", 2), ("rat", 2)]
SEX = [(("female", "male"), 52), ((), 36), (("female",), 6), (("male",), 6)]
ANCESTRY = [(("european",), 46), ((), 44), (("multi_ancestry",), 4),
            (("african",), 3), (("admixed_american",), 2), (("east_asian",), 1)]

CELL_TYPES = [
    ("microglia", 26), ("astrocyte", 20), ("excitatory_neuron", 18),
    ("oligodendrocyte", 14), ("inhibitory_neuron", 10), ("opc", 6),
    ("endothelial", 5), ("pericyte", 3), ("t_cell", 3), ("monocyte", 2),
    ("cam", 2), ("fibroblast", 1), ("smooth_muscle", 1), ("b_cell", 1),
    ("nk_cell", 1), ("dendritic_cell", 1), ("ependymal", 1),
]

SINGLE_CELL = {"snrna", "scrna", "snatac", "multiome", "cite_seq", "spatial_rna"}
BRAIN = {r for r, _ in REGIONS} - {"blood", "csf", "gut", "retina"}

COHORTS = [("ROSMAP", 22), ("MSBB", 12), (None, 40), ("Mayo", 8), ("SEA-AD", 6),
           ("ADNI", 5), ("BANNER", 3), ("DIAN", 2), ("UKB", 2)]


def _pick(weighted, rng):
    population = [x for x, _ in weighted]
    weights = [w for _, w in weighted]
    return rng.choices(population, weights=weights, k=1)[0]


def _sentence(rng, region, modality, stage, tissue, design, species, sexes, cells, n):
    stage_txt = label("stage", stage)
    parts = [
        f"We profiled {label('tissue_state', tissue).lower()} {label('region', region).lower()} "
        f"from {n} {label('species', species).lower()} donors using {label('modality', modality)}."
    ]
    parts.append(f"The design was {label('design', design).lower()}, comparing {stage_txt.lower()} "
                 f"against cognitively normal controls.")
    if cells:
        parts.append("Analysis focused on " + ", ".join(label("cell_type", c).lower() for c in cells) + ".")
    if len(sexes) == 2:
        parts.append("Both sexes were included.")
    elif sexes:
        parts.append(f"The cohort was {label('sex', sexes[0]).lower()} only.")
    return " ".join(parts)


def fetch(limit: int = 400, seed: int = 20260821, **_kwargs) -> Iterator[dict]:
    """Yield `limit` synthetic records (~30% datasets, ~70% works)."""
    rng = random.Random(seed)

    for i in range(limit):
        modality = _pick(MODALITIES, rng)
        region = _pick(REGIONS, rng)
        stage = _pick(STAGES, rng)
        species = _pick(SPECIES, rng)
        design = _pick(DESIGNS, rng)
        sexes = _pick(SEX, rng)
        ancestry = _pick(ANCESTRY, rng)

        tissue = _pick(TISSUE, rng)
        if species != "human":
            tissue = "in_vivo_model" if region in BRAIN else "primary_cult"
        elif region in ("blood", "csf"):
            tissue = "peripheral"
        if tissue in ("ipsc", "organoid", "primary_cult", "in_vivo_model"):
            # Clinical stage is not a property of a model system.
            stage = rng.choice(["ad_dementia", "control", "eoad"])
        if tissue == "post_mortem" and design in ("longitudinal", "cohort_incident"):
            design = "cross_sectional"

        cells: list[str] = []
        if modality in SINGLE_CELL:
            k = rng.choice([1, 1, 2, 2, 3])
            pool = CELL_TYPES if region in BRAIN else [
                c for c in CELL_TYPES if c[0] in
                ("t_cell", "monocyte", "b_cell", "nk_cell", "dendritic_cell")
            ]
            while len(cells) < k and len(cells) < len(pool):
                c = _pick(pool, rng)
                if c not in cells:
                    cells.append(c)

        n = max(3, int(rng.lognormvariate(3.0, 0.9)))
        if modality in ("gwas", "bulk_rna", "imaging_pet"):
            n *= rng.choice([4, 8, 20])
        cohort = _pick(COHORTS, rng)
        year = rng.choices([2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025],
                           weights=[3, 5, 8, 11, 14, 17, 21, 21], k=1)[0]

        is_dataset = rng.random() < 0.3
        title = (f"[DEMO] {label('modality', modality)} of {label('region', region).lower()} "
                 f"in {label('stage', stage).lower()}")
        abstract = _sentence(rng, region, modality, stage, tissue, design, species, sexes, cells, n)
        if cohort:
            abstract += f" Samples were drawn from the {cohort} cohort."

        yield {
            "kind": "dataset" if is_dataset else "work",
            "source": "demo",
            "external_id": f"DEMO-{'D' if is_dataset else 'W'}-{i:04d}",
            "title": title,
            "abstract": abstract,
            "year": year,
            "venue": None if is_dataset else "[DEMO] Journal of Synthetic Neuroscience",
            "url": None,
            "n_donors": n,
            "n_donors_src": "reported",
            "cohort": cohort,
            "cited_by": rng.randint(0, 300) if not is_dataset else None,
            "raw": {"synthetic": True},
            "curated": {
                "region": [region], "modality": [modality], "stage": [stage],
                "tissue_state": [tissue], "design": [design], "species": [species],
                "sex": list(sexes), "ancestry": list(ancestry),
                **({"cell_type": cells} if cells else {}),
            },
        }


# ── comparator corpus ───────────────────────────────────────────────────────
# A synthetic non-AD neurodegeneration corpus, ingested under a tagged source
# so `method_transfer` has something to compare against offline. Its modality
# mix is deliberately different: more living-tissue longitudinal work, more
# perturbation screens — the shape a comparator field needs to have for the
# transfer detector to say anything at all.

COMP_MODALITIES = [
    ("snrna", 22), ("perturbation", 14), ("cite_seq", 12), ("spatial_prot", 10),
    ("multiome", 10), ("tcr_bcr", 8), ("proteomics", 8), ("hic", 6),
    ("scrna", 6), ("electrophys", 4),
]
COMP_TISSUE = [("peripheral", 34), ("ipsc", 20), ("post_mortem", 18),
               ("organoid", 14), ("biopsy", 8), ("in_vivo_model", 6)]
COMP_DESIGN = [("longitudinal", 40), ("cohort_incident", 20),
               ("interventional", 18), ("cross_sectional", 22)]
COMP_STAGE = [("adrd_other", 60), ("control", 30), ("mci", 10)]


def fetch_comparator(limit: int = 120, seed: int = 4242, **_kwargs) -> Iterator[dict]:
    """Synthetic comparator field. Source tag: `demo:comparator`."""
    rng = random.Random(seed)
    for i in range(limit):
        modality = _pick(COMP_MODALITIES, rng)
        tissue = _pick(COMP_TISSUE, rng)
        design = _pick(COMP_DESIGN, rng)
        stage = _pick(COMP_STAGE, rng)
        region = _pick([("blood", 30), ("csf", 14), ("prefrontal_cortex", 14),
                        ("substantia_nigra", 12), ("white_matter", 10),
                        ("meninges", 8), ("hippocampus", 6), ("gut", 6)], rng)
        n = max(4, int(rng.lognormvariate(3.4, 0.8)))
        yield {
            "kind": "work",
            "source": "demo:comparator",
            "external_id": f"DEMO-C-{i:04d}",
            "title": (f"[DEMO comparator] {label('modality', modality)} of "
                      f"{label('region', region).lower()} in {label('stage', stage).lower()}"),
            "abstract": (f"A {label('design', design).lower()} study of "
                         f"{label('tissue_state', tissue).lower()} samples from {n} donors "
                         f"using {label('modality', modality)}. Both sexes were included."),
            "year": rng.choice([2022, 2023, 2024, 2025]),
            "n_donors": n,
            "n_donors_src": "reported",
            "cited_by": rng.randint(0, 120),
            "raw": {"synthetic": True, "corpus": "comparator"},
            "curated": {"modality": [modality], "tissue_state": [tissue],
                        "design": [design], "stage": [stage], "region": [region],
                        "species": ["human"], "sex": ["female", "male"]},
        }


# ── claim graph ─────────────────────────────────────────────────────────────
# Entities are named DEMOGENE*/DEMOPATH*/DEMOPHENO* so no synthetic claim can
# be mistaken for a statement about a real gene or phenotype. The graph is
# constructed to contain one clean ABC chain, one direct contradiction and one
# highly-cited singleton, so the detectors have known ground truth to hit.

def claims(seed: int = 99) -> list[dict]:
    rng = random.Random(seed)
    ctx_brain = {"tissue_state": "post_mortem", "species": "human"}
    ctx_blood = {"tissue_state": "peripheral", "species": "human"}
    rows = [
        # A → B → C with four bridges and no direct A → C.
        ("DEMOGENE1", "increases", "DEMOPATH1", 1, ctx_brain),
        ("DEMOGENE1", "increases", "DEMOPATH2", 1, ctx_brain),
        ("DEMOGENE1", "decreases", "DEMOPATH3", -1, ctx_brain),
        ("DEMOGENE1", "increases", "DEMOPATH4", 1, ctx_brain),
        ("DEMOPATH1", "increases", "DEMOPHENO1", 1, ctx_brain),
        ("DEMOPATH2", "increases", "DEMOPHENO1", 1, ctx_brain),
        ("DEMOPATH3", "decreases", "DEMOPHENO1", -1, ctx_brain),
        ("DEMOPATH4", "increases", "DEMOPHENO1", 1, ctx_brain),
        # Cross-context chain: should rank lower than the one above.
        ("DEMOGENE2", "increases", "DEMOPATH5", 1, ctx_blood),
        ("DEMOGENE2", "increases", "DEMOPATH6", 1, ctx_blood),
        ("DEMOPATH5", "increases", "DEMOPHENO2", 1, ctx_brain),
        ("DEMOPATH6", "increases", "DEMOPHENO2", 1, ctx_brain),
        # Direct contradiction under identical context.
        ("DEMOGENE3", "increases", "DEMOPHENO3", 1, ctx_brain),
        ("DEMOGENE3", "decreases", "DEMOPHENO3", -1, ctx_brain),
        ("DEMOGENE3", "decreases", "DEMOPHENO3", -1, ctx_brain),
        # Context-dependent disagreement: same pair, different tissue.
        ("DEMOGENE4", "increases", "DEMOPHENO4", 1, ctx_brain),
        ("DEMOGENE4", "decreases", "DEMOPHENO4", -1, ctx_blood),
        # Singleton pillar.
        ("DEMOGENE5", "increases", "DEMOPHENO5", 1, ctx_brain),
    ]
    return [
        {"subject": s, "relation": rel, "object": o, "direction": d,
         "context": c, "confidence": round(rng.uniform(0.6, 0.95), 2)}
        for s, rel, o, d, c in rows
    ]
