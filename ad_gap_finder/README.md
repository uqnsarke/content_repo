# ad_gap_finder

Computes research gaps in the Alzheimer's literature instead of asking a language model to imagine them.

## The problem with "AI, what's missing in AD research?"

Ask any model that question and you get fluent, plausible, unfalsifiable prose. It cannot know what is absent from a literature it only ever saw in summary, and absence is precisely the thing a language model will confabulate most confidently.

So this tool never asks. It normalises papers and datasets onto a fixed set of axes, materialises the cross-product as a coverage matrix, and finds the empty cells with SQL. A gap is a query result with a record count attached, and every one of them can be argued with.

## How a gap is computed

Every record is projected onto nine axes:

`cell_type × region × modality × stage × design × tissue_state × sex × ancestry × species`

For each cell of the matrix we hold what the corpus contains and what it *should* contain if the axes were sampled independently:

```
expected   E = N × Π p(termᵢ)          p from the corpus's own marginals
depletion  d = log2((E + 0.5) / (O + 0.5))
```

The residual against the marginal product is the whole point. A raw count only rediscovers that everyone works on prefrontal cortex and that rare cell types are rare. The residual asks a better question: *given how much the field studies microglia, and how much it studies entorhinal cortex, is the intersection emptier than it should be?* Those cells are missing **combinations**, not missing topics.

Then four things happen that matter more than the arithmetic:

**Impossible cells are removed.** Microglia have never been profiled in blood because microglia are not in blood. Bulk RNA-seq has no cell-type resolution, PET has no cell-type resolution, and post-mortem brain cannot be sampled longitudinally. Thirty-odd such constraints are declared in `gapfinder/axes.py` with a stated reason, and cells matching them never reach the output.

**Absences are verified before they are reported.** An empty cell is a hypothesis about the index, not a fact about the world. `verify_absence` tries three ways to disprove it: near misses on a single axis, latent coverage, and an independent full-text pass that bypasses the annotation table entirely. Any hit on that last check is a vocabulary failure, and the cell is dropped rather than reported.

**Tractability is scored.** The output distinguishes a gap you can close by re-analysing public data this week from one that needs a new cohort. An unbiased snRNA-seq study of prefrontal cortex assayed microglia whether or not the paper mentions them — those annotations are stored, flagged `inferred`, excluded from coverage, and used only to answer "could existing data close this?".

**Everything is multiplied together, visibly:**

```
priority = depletion_n × (1 − depth) × (0.25 + 0.75 × tractability) × feasibility
```

`(1 − depth)` stops a well-studied cell from scoring just for being smaller than predicted. `tractability` is what makes the output a project list rather than a wish list.

## Five detectors

| Detector | Finds |
| --- | --- |
| `coverage` | Axis combinations the field has never assayed |
| `design` | Whole evidence bases sharing one blind spot — every record on a topic cross-sectional, post-mortem, single-sex or single-cohort |
| `method_transfer` | Methods proven in a comparator field and never applied here |
| `abc` | Swanson linking: A→B and B→C published, A→C never tested |
| `contradiction` | Sources that disagree on direction, and highly-cited claims with exactly one source |

`design` is the one that surprises people. It does not count papers — it asks what a body of work *cannot conclude*. A topic where forty records all carry `cross_sectional_only` is not a thin literature; it is a confident literature that is structurally unable to answer a causal question. No citation-count view will ever show you that.

## Quick start

```bash
pip install -r requirements.txt
python cli.py demo --out report.html      # full offline run, no network, no API key
open report.html
```

`demo` builds a synthetic corpus, five coverage matrices, all five detectors and a report — in about ten seconds. **Every record in it is fabricated**; identifiers are prefixed `DEMO-`, and any report built from it carries a warning banner. Its purpose is to let you read the gap logic against a corpus whose structure you know. Its axis marginals mirror the documented skew of the AD single-cell field (cortex-heavy, snRNA-heavy, post-mortem, cross-sectional) because a detector that only works on a uniform corpus has not been tested at all.

## Against real data

```bash
python cli.py init
python cli.py ingest --source openalex --query alzheimer --limit 5000
python cli.py ingest --source geo --limit 2000
python cli.py ingest --source openalex --query parkinson --tag pd    # comparator corpus
python cli.py build
python cli.py gaps --limit 25
python cli.py report --out report.html
```

Set `OPENALEX_MAILTO` to enter OpenAlex's polite pool and `NCBI_API_KEY` to raise the E-utilities rate limit from 3 to 10 requests/second. Neither is required.

A comparator corpus is stored under a tagged source (`openalex:pd`) and is deliberately excluded from the AD coverage matrix — a Parkinson's paper is not evidence that an AD combination has been studied. `method_transfer` is the only detector that reads it, and it returns nothing at all when no comparator is present rather than guessing.

## Triage is the point

```bash
python cli.py triage 14 --status accepted --note "worth a grant"
python cli.py triage 19 --status known    --note "tried in 2021, went nowhere"
```

Status survives every re-run. This is the part that compounds: nobody has a labelled dataset of *gaps a domain expert thought were worth pursuing*, and after a few hundred judgements you can train ranking on it. The tool cannot feel the field's tacit knowledge about what has been quietly tried and failed — `known` is where that knowledge accumulates.

## The autonomous pipeline

The gap finder decides *what to look at*. The pipeline takes one gap the whole
way: **literature synthesis → hypothesis → analysis plan → in-silico
experimentation → wet-lab recommendation**, with a gate between every pair of
stages.

```bash
python cli.py lab --goal "microglia in preclinical Alzheimer's" --out run.html
```

```
  synthesis   -> artifact 1: Synthesis: Preclinical AD: entire evidence base is post mortem only
  gate synthesis->hypothesis: pass
  hypothesis  -> artifact 2: ...
  gate hypothesis->analysis: pass
  analysis    -> artifact 3: ...
  gate analysis->insilico: pass
  insilico    -> artifact 4: Simulation: power 1.00
  gate insilico->wetlab: pass
  wetlab      -> artifact 5: RNAscope / smFISH for marker genes in situ
  gate wetlab->done: WAIT
       - human_signoff: awaiting human sign-off before the run can be considered complete

run 1: awaiting_signoff (reached wetlab)
```

It runs with no API key. `--mode dry` (the default) is fully deterministic;
`--mode agent` adds Claude, and what Claude is allowed to do is the point below.

### Code owns the numbers, the model owns the prose

Every stage computes its structured fields from the database and the simulation
modules first. Only then — in agent mode — does Claude get asked to sharpen the
free-text fields. Protected keys are re-applied after the merge, so a stage
cannot revise its own donor count, power estimate or gap id into something more
convenient. `merge_narration` is a public function precisely so that guarantee
is directly testable.

This is the difference between an autonomous system and a fluent one. It is easy
to have a model *describe* a power analysis and produce a paragraph containing
numbers; nothing in the output distinguishes that from a computation. So
`gapfinder/lab/insilico.py` contains no model calls at all, and the gate
downstream refuses to advance a stage whose `results` block is empty.

### Gates, not vibes

An autonomous pipeline's real failure mode is not a wrong answer at stage two.
It is stages three, four and five elaborating, citing and formatting that answer
until it arrives looking like a conclusion. So each transition is gated on
machine-checkable structure:

| Transition | Blocks unless |
| --- | --- |
| synthesis → hypothesis | ≥3 records cited, design census computed, candidate gaps named |
| hypothesis → analysis | falsifier stated, primary outcome named, gap's absence verified rather than assumed |
| analysis → in-silico | **unit of analysis is donor**, test named, available n stated, direction pre-registered, covariates declared |
| in-silico → wet-lab | simulation actually executed, powered or explicitly acknowledged as not, negative control run |
| wet-lab → done | every experiment costed and controlled, marked advisory, **and signed off by a person** |

No check asks whether a hypothesis is *good* — that is not decidable here, and
pretending otherwise would be the same confabulation one layer up. They ask
whether it is the kind of object that can be wrong. Gates fail closed: a check
that cannot be evaluated counts as failed.

The donor-unit check is there because testing across cells rather than donors
inflates n by three orders of magnitude, and is the most common reason a
single-cell result does not replicate.

### In-silico means it ran

`insilico.py` is stdlib-only and deterministic. It computes two-group and
random-slope longitudinal power, the detectable effect at the n you actually
have, a power curve, and — the one that is always skipped — a **negative
control**: whether the most likely confound produces the same signal.

For single-cell AD work that confound is compositional. A shift in how many
microglia there are, with no change in what any microglia is doing, moves every
pseudobulk estimate. The simulation reports how often a purely abundance-driven
shift is called significant, and a design that cannot separate the two gets told
so before anyone spends wet-lab budget on it.

It also distinguishes *reanalysable* donors from the field's donor pool. A power
figure computed on donors that exist in the literature but have never been
assayed for your contrast is a projection, and it is labelled one — that quiet
substitution is how an autonomous pipeline starts overstating itself.

### Validation must be orthogonal to discovery

The wet-lab stage ranks experiments by
`discriminative_value × feasibility ÷ cost`, with a heavy penalty on any assay
sharing the **failure modes** of the discovery method. Confirming a snRNA-seq
finding with more snRNA-seq confirms the pipeline, not the biology.

Orthogonality is about failure modes, not analytes: RNAscope measures RNA too,
but it does not share dissociation loss, ambient RNA or amplification bias —
which is exactly why it validates. The non-orthogonal pairs are listed
explicitly in `SHARED_FAILURE_MODES` rather than guessed from name similarity.
The penalty routinely promotes a slower, costlier assay to the top, which is the
correct answer and the one a convenience-driven process never reaches.

Every recommendation carries required controls, a cost band, a turnaround and a
stated limitation. Nothing orders reagents, books instrument time or contacts a
vendor, and the run cannot reach `completed` on its own:

```bash
python cli.py runs                       # 1  awaiting_signoff  wetlab ...
python cli.py signoff 1 --by "your name"
```

### Provenance

Every artifact records what it was derived from — the artifact before it, the
gap it addresses, and the record ids that justify it. Walk `derived_from`
backwards from a wet-lab recommendation and you land on accessions. That chain
is rendered in the run view, and it is the answer to "why are you telling me to
do this experiment?".

### At scale

```bash
python cli.py lab --goal "..." --goal "..." --goal "..." --workers 4
```

Fans out across goals with a bounded thread pool, one connection and one trace
per run.

## Mantis

Every stage, tool call, LLM call and gate decision becomes a span. Spans go to
two places at once.

**Mantis** — `mantisdk.tracing` → Metis Insight — when the SDK is installed and
`INSIGHT_HOST`, `INSIGHT_PUBLIC_KEY` and `INSIGHT_SECRET_KEY` are set:

```bash
pip install mantisdk
export INSIGHT_HOST=https://insight.withmetis.ai
export INSIGHT_PUBLIC_KEY=pk-...
export INSIGHT_SECRET_KEY=sk-...
python cli.py lab --goal "..."          # mantis: configured
```

`mantisdk` auto-instruments the Anthropic SDK, so agent-mode model calls are
captured with their Claude-specific attributes without extra wiring.

**The run's own database** — always. A run whose reasoning is only legible when
a hosted backend happens to be reachable is not transparent, so the local mirror
is the record and Mantis is the lens across many runs. `--out run.html` renders
that mirror with no backend, no credentials and no network, showing every gate
check that passed *and* every one that failed. A system that displays only its
successes is not transparent, it is decorated.

Every run prints which mode it is in and, when Mantis is inactive, why.

The tracing import is guarded against `BaseException`, not `ImportError`.
`mantisdk` pulls in litellm, which pulls in native crypto bindings; a broken
system `cryptography` raises a pyo3 `PanicException`, which is not an
`ImportError` and took the process down at import time on the machine this was
built on. An observability layer must never be the thing that kills the run it
is observing.

## The agent layer

```bash
export ANTHROPIC_API_KEY=...
python -m gapfinder.agent --db data/gapfinder.db \
  "Find the three best re-analysis opportunities in microglia and write up one."
```

The model gets tools — `query_coverage`, `verify_absence`, `search_records`, `get_claim_graph`, `propose_workflow`, `save_finding` — and a system prompt that forbids asserting anything about the corpus that did not arrive through a tool result, including any accession, DOI or PMID. Interpretation and write-up are its job; deciding what is missing is not.

`gapfinder/agent_tools.py` holds the same tools as plain functions with JSON schemas, so an HTTP backend can expose them to a hosted agent without importing the SDK.

## Gap → hypothesis → workflow

Every gap carries a hypothesis, a route, a workflow and a falsifier, generated deterministically from its axes (`gapfinder/propose.py`) and improvable by the agent. The templates branch on axis values because the right design genuinely differs — proposing a new cohort for what is really a re-analysis problem is the most common way a tool like this wastes people's time. A preclinical gap gets a mediation step against amyloid burden; a multiome gap gets regulon-level analysis rather than differential expression; every one of them states what result would kill it.

## Commands

| Command | Does |
| --- | --- |
| `init` | Create the database and load the vocabulary |
| `ingest` | Pull from `openalex`, `geo` or `demo`; `--tag` for a comparator corpus |
| `annotate` | Re-run normalisation over stored records after a vocabulary change |
| `build` | Materialise coverage matrices (five useful axis subsets by default) |
| `gaps` | Run detectors and store what they find |
| `report` | Render Markdown or self-contained HTML |
| `triage` | Accept / reject / mark known |
| `stats` | Corpus and triage counts |
| `lab` | Run the autonomous pipeline; repeat `--goal` to fan out |
| `runs` | List pipeline runs and where each stopped |
| `signoff` | Record human approval on a wet-lab recommendation |
| `demo` | Everything above, offline, on synthetic data |

`python -m gapfinder.ontology --db data/gapfinder.db` attaches Cell Ontology / Uberon / EFO / MONDO CURIEs to the vocabulary via OLS4. Matching never uses them; they exist so the matrix can be joined against CellxGene or Open Targets. They are resolved rather than hardcoded because a CURIE written from memory is indistinguishable from a correct one until someone joins on it and gets silence.

## Layout

```
ad_gap_finder/
├── cli.py                    # every command above
├── schema.sql                # SQLite, portable to Postgres/Supabase (3 noted changes)
├── gapfinder/
│   ├── axes.py               # controlled vocabulary + feasibility constraints
│   ├── normalize.py          # text → axes; stated vs inferred, kept strictly apart
│   ├── pipeline.py           # ingest + per-record design flags
│   ├── coverage.py           # the matrix, the marginals, absence verification
│   ├── detectors/            # the five detectors
│   ├── propose.py            # gap → hypothesis → workflow → falsifier
│   ├── report.py             # Markdown / HTML
│   ├── agent_tools.py        # tools as plain functions + JSON schemas
│   ├── agent.py              # Claude tool-runner loop
│   ├── ontology.py           # OLS4 CURIE resolution
│   ├── sources/              # openalex, geo, demo
│   └── lab/                  # the autonomous pipeline
│       ├── pipeline.py       #   stage machine + fan-out
│       ├── stages.py         #   the five stage agents
│       ├── gates.py          #   machine-checkable stage gates
│       ├── insilico.py       #   power, longitudinal, negative control
│       ├── wetlab.py         #   costed, controlled, orthogonal validation
│       ├── artifacts.py      #   provenance graph
│       ├── tracing.py        #   Mantis adapter + local mirror
│       └── viewer.py         #   self-contained run view
└── tests/                    # python -m unittest discover -s tests
```

## What it will not do

The pipeline is autonomous up to the point where autonomy stops being
appropriate, and no further. It will not sign off its own wet-lab
recommendations, commit spend, or contact anyone. Human tissue and animal work
needs the relevant approvals before it starts, and nothing here substitutes for
them.


It surfaces gaps that exist for good reasons — technically blocked, ethically blocked, or quietly tried and abandoned. The constraint table catches the first kind. Nothing catches the third, because that knowledge is in people's heads and not in any index. Treat the output as hypothesis generation with a human curator, which is what the triage loop is for.

It also only knows what its sources say. Credentialed resources (AD Knowledge Portal / Synapse, NIAGADS) should be indexed by metadata and linked out to, never mirrored — and a combination that exists only in a credentialed dataset will read as a gap until that metadata is indexed.

## Porting to Postgres / Supabase

`schema.sql` is written for SQLite but avoids SQLite-only syntax. Three changes: `INTEGER PRIMARY KEY` → `BIGSERIAL PRIMARY KEY`, JSON-bearing `TEXT` columns → `JSONB`, `strftime('%s','now')` → `now()`. Swap `db.connect()` for a psycopg connection; no call site changes. Run ingestion on a always-on worker rather than serverless functions — a full OpenAlex crawl will outlive any function timeout.

## Requirements

`requests` for ingestion; `anthropic` only for the agent layer. The database is stdlib `sqlite3`, the report is hand-rendered HTML, and the tests are stdlib `unittest` — the engine runs with one dependency.

## Part of the Multiome Academy tutorial series

→ [multiomeacademy.com](https://multiomeacademy.com)

## License

MIT
