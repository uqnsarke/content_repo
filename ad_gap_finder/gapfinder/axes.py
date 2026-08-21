"""Controlled vocabulary for the coverage matrix.

Every record is projected onto these axes. The vocabulary is intentionally
local and explicit rather than derived from an ontology at match time: matching
must be deterministic and auditable, because a gap is an *absence*, and an
absence produced by a flaky matcher is not a finding.

Ontology CURIEs (Cell Ontology, Uberon, EFO, MONDO) hang off each term but are
resolved separately by `gapfinder.ontology` and are never used for matching.
They exist so the coverage matrix can be exported and joined against other
resources.

To extend: add a term with its surface forms. Keep synonyms lowercase; the
matcher lowercases input and matches on word boundaries.
"""

from __future__ import annotations

# Axis order matters only for display; the matrix works over any subset.
AXES = [
    "cell_type",
    "region",
    "modality",
    "stage",
    "design",
    "tissue_state",
    "sex",
    "ancestry",
    "species",
]

# Axes where a record legitimately carries several values at once (a study
# covers many cell types). Axes not listed here are still multi-valued in the
# schema, but a record with >1 value on them is flagged as ambiguous.
MULTI_VALUED = {"cell_type", "region", "modality", "sex", "ancestry", "species"}

# term -> (label, [synonyms])
VOCAB: dict[str, dict[str, tuple[str, list[str]]]] = {
    "cell_type": {
        "microglia":         ("Microglia", ["microglia", "microglial", "myeloid cells of the brain", "iba1+"]),
        "astrocyte":         ("Astrocyte", ["astrocyte", "astrocytic", "astroglia", "gfap+"]),
        "oligodendrocyte":   ("Oligodendrocyte", ["oligodendrocyte", "oligodendroglia", "mature oligodendrocyte"]),
        "opc":               ("OPC", ["opc", "oligodendrocyte precursor", "oligodendrocyte progenitor", "ng2 glia"]),
        "excitatory_neuron": ("Excitatory neuron", ["excitatory neuron", "pyramidal neuron", "glutamatergic neuron", "principal neuron"]),
        "inhibitory_neuron": ("Inhibitory neuron", ["inhibitory neuron", "interneuron", "gabaergic neuron"]),
        "endothelial":       ("Endothelial cell", ["endothelial", "endothelium", "brain endothelial"]),
        "pericyte":          ("Pericyte", ["pericyte", "mural cell"]),
        "smooth_muscle":     ("Vascular smooth muscle", ["smooth muscle cell", "vsmc"]),
        "fibroblast":        ("Fibroblast", ["fibroblast", "perivascular fibroblast"]),
        "ependymal":         ("Ependymal cell", ["ependymal", "ependyma"]),
        "t_cell":            ("T cell", ["t cell", "t-cell", "t lymphocyte", "cd8+ t", "cd4+ t"]),
        "b_cell":            ("B cell", ["b cell", "b-cell", "b lymphocyte"]),
        "nk_cell":           ("NK cell", ["nk cell", "natural killer"]),
        "monocyte":          ("Monocyte", ["monocyte", "cd14+ monocyte", "classical monocyte"]),
        "dendritic_cell":    ("Dendritic cell", ["dendritic cell", "pdc", "cdc2"]),
        "neutrophil":        ("Neutrophil", ["neutrophil", "granulocyte"]),
        "cam":               ("CNS-associated macrophage", ["perivascular macrophage", "border-associated macrophage", "cns-associated macrophage", "bam"]),
    },
    "region": {
        "prefrontal_cortex":  ("Prefrontal cortex", ["prefrontal cortex", "pfc", "dorsolateral prefrontal", "dlpfc", "brodmann area 9", "ba9", "ba46"]),
        "entorhinal_cortex":  ("Entorhinal cortex", ["entorhinal cortex", "entorhinal", "brodmann area 28"]),
        "hippocampus":        ("Hippocampus", ["hippocampus", "hippocampal", "dentate gyrus", "ca1", "subiculum"]),
        "temporal_cortex":    ("Temporal cortex", ["temporal cortex", "superior temporal gyrus", "middle temporal gyrus", "ba20", "ba21", "ba22", "ba38"]),
        "occipital_cortex":   ("Occipital cortex", ["occipital cortex", "visual cortex", "ba17"]),
        "cingulate_cortex":   ("Cingulate cortex", ["cingulate", "anterior cingulate", "ba24"]),
        "parietal_cortex":    ("Parietal cortex", ["parietal cortex", "ba7", "ba39", "ba40"]),
        "amygdala":           ("Amygdala", ["amygdala", "amygdalar"]),
        "thalamus":           ("Thalamus", ["thalamus", "thalamic"]),
        "striatum":           ("Striatum", ["striatum", "caudate", "putamen", "nucleus accumbens"]),
        "cerebellum":         ("Cerebellum", ["cerebellum", "cerebellar"]),
        "substantia_nigra":   ("Substantia nigra", ["substantia nigra"]),
        "locus_coeruleus":    ("Locus coeruleus", ["locus coeruleus", "locus ceruleus"]),
        "hypothalamus":       ("Hypothalamus", ["hypothalamus", "hypothalamic"]),
        "white_matter":       ("White matter", ["white matter", "corpus callosum"]),
        "blood":              ("Peripheral blood", ["peripheral blood", "pbmc", "whole blood", "blood mononuclear"]),
        "csf":                ("CSF", ["cerebrospinal fluid", "csf"]),
        "meninges":           ("Meninges / dura", ["meninges", "meningeal", "dura mater", "leptomeninges"]),
        "retina":             ("Retina", ["retina", "retinal"]),
        "gut":                ("Gut", ["gut", "intestinal", "colon", "gut microbiome"]),
    },
    "modality": {
        "snrna":        ("snRNA-seq", ["snrna-seq", "snrnaseq", "single-nucleus rna", "single nucleus rna", "snseq"]),
        "scrna":        ("scRNA-seq", ["scrna-seq", "scrnaseq", "single-cell rna", "single cell rna"]),
        "snatac":       ("snATAC-seq", ["snatac", "single-nucleus atac", "single cell atac", "scatac"]),
        "multiome":     ("Paired multiome (RNA+ATAC)", ["multiome", "paired rna and atac", "joint rna-atac", "snare-seq", "share-seq"]),
        "spatial_rna":  ("Spatial transcriptomics", ["spatial transcriptomic", "merfish", "visium", "xenium", "slide-seq", "cosmx", "in situ sequencing"]),
        "spatial_prot": ("Spatial proteomics", ["imaging mass cytometry", "codex", "spatial proteomic", "mibi"]),
        "cite_seq":     ("CITE-seq", ["cite-seq", "citeseq", "abseq", "surface protein multiplex"]),
        "tcr_bcr":      ("TCR/BCR repertoire", ["tcr sequencing", "bcr sequencing", "immune repertoire", "vdj sequencing"]),
        "bulk_rna":     ("Bulk RNA-seq", ["bulk rna-seq", "bulk transcriptom", "rna microarray"]),
        "bulk_atac":    ("Bulk ATAC-seq", ["bulk atac"]),
        "methylation":  ("DNA methylation", ["dna methylation", "methylome", "epigenome-wide association", "ewas", "bisulfite"]),
        "hic":          ("3D genome (Hi-C)", ["hi-c", "hic", "chromatin conformation", "micro-c"]),
        "proteomics":   ("Proteomics", ["proteomic", "mass spectrometry proteom", "tmt proteom", "olink", "somascan"]),
        "metabolomics": ("Metabolomics / lipidomics", ["metabolomic", "lipidomic"]),
        "gwas":         ("GWAS / genetics", ["genome-wide association", "gwas", "polygenic risk", "whole-genome sequencing", "whole-exome"]),
        "perturbation": ("Perturbation screen", ["crispr screen", "perturb-seq", "crispri", "knockdown screen"]),
        "imaging_pet":  ("PET imaging", ["pet imaging", "amyloid pet", "tau pet", "pib-pet", "flortaucipir"]),
        "imaging_mri":  ("MRI", ["mri", "structural imaging", "diffusion tensor"]),
        "electrophys":  ("Electrophysiology", ["patch clamp", "electrophysiolog", "multielectrode array"]),
    },
    "stage": {
        "control":     ("Cognitively normal control", ["cognitively normal", "healthy control", "non-demented control", "control brain"]),
        "preclinical": ("Preclinical AD", ["preclinical alzheimer", "preclinical ad", "asymptomatic alzheimer", "presymptomatic", "amyloid-positive cognitively normal"]),
        "mci":         ("MCI", ["mild cognitive impairment", "mci", "prodromal alzheimer"]),
        "ad_dementia": ("AD dementia", ["alzheimer's disease dementia", "ad dementia", "symptomatic alzheimer", "late-stage alzheimer", "clinical alzheimer"]),
        "resilient":   ("Resilient / high-pathology-normal", ["cognitive resilience", "resilient", "high pathology normal", "asymptomatic ad neuropathology", "cognitive reserve"]),
        "converter":   ("Converter (pre → post diagnosis)", ["converter", "progressor", "converted to dementia", "incident dementia"]),
        "eoad":        ("Early-onset / autosomal dominant AD", ["early-onset alzheimer", "eoad", "autosomal dominant alzheimer", "adad", "presenilin carrier", "down syndrome ad"]),
        "adrd_other":  ("Other dementia (LBD/FTD/PART/VaD)", ["lewy body dementia", "frontotemporal", "ftd", "part", "vascular dementia", "primary age-related tauopathy"]),
    },
    "design": {
        "cross_sectional": ("Cross-sectional", ["cross-sectional", "cross sectional"]),
        "longitudinal":    ("Longitudinal / repeated-measures", ["longitudinal", "repeated measures", "serial sampling", "within-subject", "time course", "follow-up visits"]),
        "case_control":    ("Case-control", ["case-control", "case control", "cases and controls"]),
        "cohort_incident": ("Prospective incident-event cohort", ["prospective cohort", "incident", "time-to-event", "survival analysis"]),
        "interventional":  ("Interventional / trial", ["randomized controlled trial", "clinical trial", "placebo-controlled", "treatment arm"]),
        "mendelian_rand":  ("Mendelian randomisation", ["mendelian randomization", "mendelian randomisation"]),
    },
    "tissue_state": {
        "post_mortem":   ("Post-mortem tissue", ["post-mortem", "postmortem", "autopsy", "brain bank"]),
        "biopsy":        ("Living-brain biopsy", ["brain biopsy", "surgical resection", "neurosurgical specimen"]),
        "peripheral":    ("Living peripheral sample", ["peripheral blood", "pbmc", "plasma", "serum", "lumbar puncture", "csf sample"]),
        "ipsc":          ("iPSC-derived", ["ipsc", "induced pluripotent", "ipsc-derived"]),
        "organoid":      ("Organoid / assembloid", ["organoid", "assembloid", "cerebral organoid"]),
        "primary_cult":  ("Primary culture", ["primary culture", "primary neuron culture"]),
        "in_vivo_model": ("In-vivo animal", ["in vivo", "5xfad", "app/ps1", "appnl-g-f", "tau p301s", "3xtg"]),
    },
    "sex": {
        "female": ("Female", ["female", "women", "in women"]),
        "male":   ("Male", ["male", "men", "in men"]),
    },
    "ancestry": {
        "european":         ("European", ["european ancestry", "non-hispanic white", "caucasian"]),
        "african":          ("African / African-American", ["african ancestry", "african american", "black participants"]),
        "admixed_american": ("Hispanic / Latino", ["hispanic", "latino", "latinx", "admixed american"]),
        "east_asian":       ("East Asian", ["east asian", "chinese cohort", "japanese cohort", "korean cohort"]),
        "south_asian":      ("South Asian", ["south asian", "indian cohort"]),
        "multi_ancestry":   ("Multi-ancestry", ["multi-ancestry", "trans-ethnic", "multiethnic"]),
    },
    "species": {
        "human":   ("Human", ["human", "patients", "participants", "donors"]),
        "mouse":   ("Mouse", ["mouse", "mice", "murine"]),
        "rat":     ("Rat", ["rat ", "rats"]),
        "nhp":     ("Non-human primate", ["macaque", "rhesus", "marmoset", "non-human primate"]),
        "other_model": ("Other model organism", ["zebrafish", "drosophila", "c. elegans", "caenorhabditis"]),
    },
}

# Named cohorts, matched separately from the axes. Knowing which cohort a
# record came from is what makes "n independent cohorts" countable — three
# papers on ROSMAP are one cohort, not three replications.
COHORTS = {
    "ROSMAP":  ["rosmap", "religious orders study", "rush memory and aging"],
    "MSBB":    ["msbb", "mount sinai brain bank"],
    "Mayo":    ["mayo clinic brain bank", "mayo rnaseq"],
    "SEA-AD":  ["sea-ad", "seattle alzheimer"],
    "ADNI":    ["adni", "alzheimer's disease neuroimaging initiative"],
    "UKB":     ["uk biobank"],
    "A4":      ["a4 study", "anti-amyloid treatment in asymptomatic"],
    "DIAN":    ["dian", "dominantly inherited alzheimer network"],
    "BANNER":  ["banner sun health", "arizona study of aging"],
    "HBCC":    ["human brain collection core"],
}

# Infeasible or uninformative axis combinations. '*' matches any term.
# Severity drives what the gap detector does with the cell:
#   impossible / uninformative -> excluded from gap output entirely
#   hard                       -> kept, priority multiplied by 0.6
CONSTRAINTS: list[tuple[dict[str, str], str, str]] = [
    ({"tissue_state": "peripheral", "cell_type": "microglia"},
     "impossible", "Microglia are CNS-resident; they are not present in blood or CSF."),
    ({"tissue_state": "peripheral", "cell_type": "astrocyte"},
     "impossible", "Astrocytes are CNS-resident; not obtainable from a peripheral sample."),
    ({"tissue_state": "peripheral", "cell_type": "oligodendrocyte"},
     "impossible", "Oligodendrocytes are CNS-resident; not obtainable from a peripheral sample."),
    ({"tissue_state": "peripheral", "cell_type": "excitatory_neuron"},
     "impossible", "Neurons are not obtainable from a peripheral sample."),
    ({"tissue_state": "peripheral", "cell_type": "inhibitory_neuron"},
     "impossible", "Neurons are not obtainable from a peripheral sample."),
    # The same impossibility keyed on `region`, because a matrix built over
    # region rather than tissue_state would otherwise report "microglia have
    # never been profiled in blood" as an opportunity.
    ({"region": "blood", "cell_type": "microglia"},
     "impossible", "Microglia are CNS-resident and do not circulate."),
    ({"region": "blood", "cell_type": "astrocyte"},
     "impossible", "Astrocytes are CNS-resident and do not circulate."),
    ({"region": "blood", "cell_type": "oligodendrocyte"},
     "impossible", "Oligodendrocytes are CNS-resident and do not circulate."),
    ({"region": "blood", "cell_type": "opc"},
     "impossible", "OPCs are CNS-resident and do not circulate."),
    ({"region": "blood", "cell_type": "excitatory_neuron"},
     "impossible", "Neurons are not present in peripheral blood."),
    ({"region": "blood", "cell_type": "inhibitory_neuron"},
     "impossible", "Neurons are not present in peripheral blood."),
    ({"region": "blood", "cell_type": "pericyte"},
     "impossible", "Pericytes are tissue-resident mural cells and do not circulate."),
    ({"region": "blood", "cell_type": "ependymal"},
     "impossible", "Ependymal cells line the ventricles and do not circulate."),
    ({"region": "blood", "cell_type": "cam"},
     "impossible", "CNS-associated macrophages are defined by their CNS niche."),
    ({"region": "csf", "cell_type": "microglia"},
     "impossible", "CSF contains no parenchymal microglia."),
    ({"region": "csf", "cell_type": "astrocyte"},
     "impossible", "CSF contains no parenchymal astrocytes."),
    ({"region": "csf", "cell_type": "excitatory_neuron"},
     "impossible", "CSF contains no neurons."),
    ({"region": "csf", "cell_type": "inhibitory_neuron"},
     "impossible", "CSF contains no neurons."),
    ({"region": "blood", "modality": "spatial_rna"},
     "uninformative", "Spatial context is not defined in a circulating cell suspension."),
    ({"region": "csf", "modality": "spatial_rna"},
     "uninformative", "Spatial context is not defined in a cell-free fluid compartment."),
    ({"tissue_state": "post_mortem", "design": "longitudinal"},
     "impossible", "Post-mortem brain gives one timepoint per donor; repeated within-subject "
                   "sampling of the same brain is not possible."),
    ({"modality": "imaging_pet", "cell_type": "*"},
     "uninformative", "PET has no cell-type resolution; a cell-type-specific PET cell cannot "
                      "be filled by any current tracer."),
    ({"modality": "imaging_mri", "cell_type": "*"},
     "uninformative", "MRI has no cell-type resolution."),
    ({"modality": "bulk_rna", "cell_type": "*"},
     "uninformative", "Bulk RNA-seq has no cell-type resolution. Deconvolution estimates a "
                      "proportion from a mixture; it does not assay the cell type, so this "
                      "cell cannot be filled by doing the experiment."),
    ({"modality": "bulk_atac", "cell_type": "*"},
     "uninformative", "Bulk ATAC-seq has no cell-type resolution."),
    ({"modality": "methylation", "cell_type": "*"},
     "uninformative", "Array/bisulfite methylation is run on bulk tissue; cell-type estimates "
                      "are deconvolved rather than measured."),
    ({"modality": "metabolomics", "cell_type": "*"},
     "uninformative", "Metabolomics is a bulk measurement with no cell-type resolution."),
    ({"modality": "gwas", "cell_type": "*"},
     "uninformative", "Germline genotype is not cell-type-specific; cell-type effects require "
                      "a molecular readout to map onto."),
    ({"tissue_state": "ipsc", "stage": "preclinical"},
     "uninformative", "Clinical stage is a property of a living participant, not of a "
                      "reprogrammed line; encode donor stage on the source donor instead."),
    ({"tissue_state": "organoid", "stage": "preclinical"},
     "uninformative", "Clinical stage is not a property of an organoid."),
    ({"species": "mouse", "stage": "mci"},
     "uninformative", "MCI is a clinical construct with no validated mouse equivalent."),
    ({"species": "mouse", "stage": "preclinical"},
     "uninformative", "Preclinical AD is defined by human biomarker-positive cognitive normality."),
    ({"tissue_state": "biopsy", "region": "locus_coeruleus"},
     "impossible", "The locus coeruleus is not accessible by any accepted living-brain biopsy route."),
    ({"tissue_state": "biopsy", "region": "entorhinal_cortex"},
     "hard", "Entorhinal biopsy occurs only incidentally in epilepsy surgery; cohorts are tiny "
             "and not representative of AD."),
    ({"modality": "multiome", "tissue_state": "biopsy"},
     "hard", "Feasible but constrained by tissue quality and the rarity of consented resections."),
    ({"modality": "hic", "region": "locus_coeruleus"},
     "hard", "Input requirements are high relative to the yield from a small nucleus."),
]


def terms(axis: str) -> list[str]:
    return list(VOCAB[axis].keys())


def label(axis: str, term: str) -> str:
    entry = VOCAB.get(axis, {}).get(term)
    return entry[0] if entry else term


def describe(key: dict[str, str]) -> str:
    """Human-readable one-line description of a coverage cell."""
    return " × ".join(label(a, t) for a, t in sorted(key.items()))
