"""Project free text onto the coverage axes.

Two things happen here, and keeping them apart matters:

**Matching** finds axis terms that a record *states*. It is a deterministic
word-boundary match against the controlled vocabulary. Confidence reflects
where the match was found, not how sure we are that the biology is right.

**Inference** adds axis terms that a record *contains but does not analyse* —
a snRNA-seq study of prefrontal cortex assayed microglia whether or not the
paper ever mentions them. Inferred annotations are stored with `inferred = 1`
and are excluded from the coverage matrix by default. They are used for one
thing only: deciding whether an empty cell is addressable by re-analysing data
that already exists. Letting them count as coverage would erase the very gaps
this tool is built to find.
"""

from __future__ import annotations

import re

from .axes import COHORTS, VOCAB

# Confidence by where a term was found.
CONF_CURATED = 1.00
CONF_TITLE = 0.75
CONF_ABSTRACT = 0.55
CONF_INFERRED = 0.40

# Cell classes that any unbiased single-cell assay of brain tissue captures.
# Used only for latent (inferred) coverage.
BRAIN_RESIDENT = [
    "microglia", "astrocyte", "oligodendrocyte", "opc",
    "excitatory_neuron", "inhibitory_neuron", "endothelial",
]
BLOOD_RESIDENT = ["t_cell", "b_cell", "nk_cell", "monocyte", "dendritic_cell"]

SINGLE_CELL_MODALITIES = {"snrna", "scrna", "snatac", "multiome", "cite_seq", "spatial_rna"}
BRAIN_REGIONS = {
    "prefrontal_cortex", "entorhinal_cortex", "hippocampus", "temporal_cortex",
    "occipital_cortex", "cingulate_cortex", "parietal_cortex", "amygdala",
    "thalamus", "striatum", "cerebellum", "substantia_nigra", "locus_coeruleus",
    "hypothalamus", "white_matter",
}


def _compile(vocab) -> dict[str, list[tuple[str, re.Pattern]]]:
    out: dict[str, list[tuple[str, re.Pattern]]] = {}
    for axis, entries in vocab.items():
        pats = []
        for term, (_label, synonyms) in entries.items():
            for syn in synonyms:
                syn = syn.strip()
                if not syn:
                    continue
                pats.append((term, re.compile(r"\b" + re.escape(syn).replace(r"\ ", r"\s+") + r"\b")))
        out[axis] = pats
    return out


_PATTERNS = _compile(VOCAB)
_COHORT_PATTERNS = [
    (name, re.compile(r"\b" + re.escape(s).replace(r"\ ", r"\s+") + r"\b"))
    for name, syns in COHORTS.items()
    for s in syns
]

# "n = 84", "84 donors", "brains from 24 individuals", "a cohort of 312 participants"
_DONOR_PATTERNS = [
    re.compile(r"\bn\s*=\s*(\d{1,6})\s*(?:human\s+)?(?:donors?|brains?|individuals?|participants?|subjects?|patients?|cases?)\b"),
    re.compile(r"\b(\d{1,6})\s*(?:human\s+)?(?:post-?mortem\s+)?(?:donors?|brains?|individuals?|participants?|subjects?)\b"),
    re.compile(r"\bcohort of\s+(\d{1,6})\b"),
]
_SEX_BOTH = re.compile(r"\b(both sexes|sex-balanced|male and female|females and males|males and females)\b")


def _matches(text: str, weight: float) -> dict[tuple[str, str], tuple[float, str]]:
    found: dict[tuple[str, str], tuple[float, str]] = {}
    for axis, pats in _PATTERNS.items():
        for term, pat in pats:
            m = pat.search(text)
            if not m:
                continue
            key = (axis, term)
            if key not in found or found[key][0] < weight:
                found[key] = (weight, m.group(0))
    return found


def extract_n_donors(text: str) -> int | None:
    """Largest plausible donor count stated in the text, or None.

    Deliberately conservative: a wrong n silently corrupts every depth score
    downstream, so anything outside 2–20,000 is discarded rather than guessed.
    """
    best = None
    for pat in _DONOR_PATTERNS:
        for m in pat.finditer(text):
            n = int(m.group(1))
            if 2 <= n <= 20000 and (best is None or n > best):
                best = n
    return best


def detect_cohort(text: str) -> str | None:
    for name, pat in _COHORT_PATTERNS:
        if pat.search(text):
            return name
    return None


def annotate(title: str, abstract: str = "", curated: dict[str, list[str]] | None = None) -> list[dict]:
    """Return axis annotations for one record.

    `curated` carries axis values that came from a structured source field
    (a GEO characteristics field, a CellxGene Census column, a human curator)
    and is trusted above any text match.
    """
    title_l = (title or "").lower()
    abstract_l = (abstract or "").lower()

    scored = _matches(abstract_l, CONF_ABSTRACT)
    scored.update(_matches(title_l, CONF_TITLE))

    annos: dict[tuple[str, str], dict] = {
        (axis, term): {"axis": axis, "term": term, "confidence": conf,
                       "inferred": False, "evidence": ev}
        for (axis, term), (conf, ev) in scored.items()
    }

    for axis, terms_ in (curated or {}).items():
        for term in terms_:
            if term in VOCAB.get(axis, {}):
                annos[(axis, term)] = {"axis": axis, "term": term,
                                       "confidence": CONF_CURATED, "inferred": False,
                                       "evidence": "curated"}

    text = f"{title_l} {abstract_l}"
    if _SEX_BOTH.search(text):
        for term in ("female", "male"):
            annos.setdefault((("sex"), term),
                             {"axis": "sex", "term": term, "confidence": CONF_ABSTRACT,
                              "inferred": False, "evidence": "both sexes"})

    annos.update(_infer(annos, text))
    return list(annos.values())


def _infer(annos: dict[tuple[str, str], dict], text: str) -> dict[tuple[str, str], dict]:
    """Fill axes a record implies but never states. All marked inferred."""
    have: dict[str, set[str]] = {}
    for (axis, term), a in annos.items():
        if not a["inferred"]:
            have.setdefault(axis, set()).add(term)

    add: dict[tuple[str, str], dict] = {}

    def put(axis, term, why):
        if (axis, term) in annos:
            return
        add[(axis, term)] = {"axis": axis, "term": term, "confidence": CONF_INFERRED,
                             "inferred": True, "evidence": why}

    modalities = have.get("modality", set())
    regions = have.get("region", set())
    species = have.get("species", set())
    tissue = have.get("tissue_state", set())

    # Latent cell-type coverage: an unbiased single-cell assay captured these
    # populations regardless of whether the study analysed them.
    if modalities & SINGLE_CELL_MODALITIES:
        if regions & BRAIN_REGIONS:
            for ct in BRAIN_RESIDENT:
                put("cell_type", ct, "captured by unbiased single-cell assay of brain tissue")
        if "blood" in regions:
            for ct in BLOOD_RESIDENT:
                put("cell_type", ct, "captured by unbiased single-cell assay of blood")

    # Species: cohort names and participant language imply human when no
    # model organism is named.
    if not species:
        if detect_cohort(text) or re.search(r"\b(participants?|donors?|patients?|autopsy)\b", text):
            put("species", "human", "human participants/donors referenced, no model organism named")

    # Human brain tissue with no stated tissue state is post-mortem in the
    # overwhelming majority of cases; inferring it makes the post-mortem
    # monoculture visible instead of hiding it as unannotated.
    if not tissue and (regions & BRAIN_REGIONS) and ("human" in species or ("species", "human") in add):
        put("tissue_state", "post_mortem", "human brain tissue, no living-tissue route stated")

    # An observational human study that never says 'longitudinal' is
    # cross-sectional. This is the single most consequential inference in the
    # tool, so it is always marked inferred and always shown as such.
    if "longitudinal" not in have.get("design", set()) and "cohort_incident" not in have.get("design", set()):
        if ("human" in species or ("species", "human") in add) and "interventional" not in have.get("design", set()):
            put("design", "cross_sectional", "no longitudinal or repeated-measures language")

    return add
