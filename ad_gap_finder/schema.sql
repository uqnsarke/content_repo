-- ============================================================================
-- ad_gap_finder — coverage-matrix schema
--
-- Written for SQLite (zero-setup, ships with Python). It is deliberately
-- portable to Postgres/Supabase; the only changes needed are noted inline:
--
--   INTEGER PRIMARY KEY  -> BIGSERIAL PRIMARY KEY
--   TEXT (json columns)  -> JSONB
--   strftime('%s','now') -> now()
--
-- Nothing here uses SQLite-only syntax beyond those three points.
-- ============================================================================

-- ── Controlled vocabulary ───────────────────────────────────────────────────
-- One row per (axis, term). `synonyms` is a JSON array of lowercase surface
-- forms used for matching. `curie` is an ontology identifier (CL:, UBERON:,
-- EFO:, MONDO:) and is NULL until `gapfinder.ontology` resolves it against
-- OLS4 — matching never depends on it, it exists so exports interoperate.
CREATE TABLE IF NOT EXISTS axis_terms (
    axis        TEXT NOT NULL,
    term        TEXT NOT NULL,
    label       TEXT NOT NULL,
    synonyms    TEXT NOT NULL DEFAULT '[]',
    curie       TEXT,
    parent      TEXT,
    PRIMARY KEY (axis, term)
);

-- ── Records: literature + datasets in one table ─────────────────────────────
-- Coverage is computed over both, so they share a table and are separated by
-- `kind`. `external_id` is the source's own identifier (OpenAlex work ID, GEO
-- accession, ...); (source, external_id) is the natural key.
CREATE TABLE IF NOT EXISTS records (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL CHECK (kind IN ('work', 'dataset')),
    source        TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    doi           TEXT,
    pmid          TEXT,
    title         TEXT NOT NULL,
    abstract      TEXT,
    year          INTEGER,
    venue         TEXT,
    url           TEXT,
    n_donors      INTEGER,          -- NULL when not confidently extractable
    n_donors_src  TEXT,             -- 'reported' | 'regex' | NULL
    cohort        TEXT,             -- ROSMAP / MSBB / SEA-AD / ... when named
    cited_by      INTEGER,
    retrieved_at  INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    raw           TEXT,             -- source payload, JSON
    UNIQUE (source, external_id)
);

CREATE INDEX IF NOT EXISTS idx_records_kind_year ON records (kind, year);
CREATE INDEX IF NOT EXISTS idx_records_cohort    ON records (cohort);

-- A dataset and the paper that generated it are separate records; this links
-- them so an accession is not double-counted as independent evidence.
CREATE TABLE IF NOT EXISTS record_links (
    work_id     INTEGER NOT NULL REFERENCES records (id) ON DELETE CASCADE,
    dataset_id  INTEGER NOT NULL REFERENCES records (id) ON DELETE CASCADE,
    relation    TEXT NOT NULL DEFAULT 'generated',
    PRIMARY KEY (work_id, dataset_id)
);

-- ── Axis annotations (many-to-many) ─────────────────────────────────────────
-- One study covers many cell types, often several regions. Storing this long
-- rather than wide is what makes the coverage matrix computable over an
-- arbitrary subset of axes.
--
-- `evidence` keeps the matched span so an annotation can be audited, and
-- `confidence` records how it was assigned:
--   1.00  curated / structured source field
--   0.75  title match
--   0.55  abstract match
--   0.40  inferred (e.g. a snRNA-seq brain study necessarily assayed glia)
CREATE TABLE IF NOT EXISTS record_axes (
    record_id   INTEGER NOT NULL REFERENCES records (id) ON DELETE CASCADE,
    axis        TEXT NOT NULL,
    term        TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 0.5,
    inferred    INTEGER NOT NULL DEFAULT 0,   -- 1 = not stated, deduced
    evidence    TEXT,
    PRIMARY KEY (record_id, axis, term)
);

CREATE INDEX IF NOT EXISTS idx_record_axes_axis ON record_axes (axis, term);

-- ── Design flags ────────────────────────────────────────────────────────────
-- Per-record methodological weaknesses, assigned by the design auditor.
-- These are what turn "a claim exists" into "a claim rests on n=6 post-mortem
-- male brains from one cohort".
CREATE TABLE IF NOT EXISTS record_flags (
    record_id   INTEGER NOT NULL REFERENCES records (id) ON DELETE CASCADE,
    flag        TEXT NOT NULL,
    detail      TEXT,
    PRIMARY KEY (record_id, flag)
);

-- ── Infeasible / uninformative axis combinations ────────────────────────────
-- A cell that is empty because the experiment cannot be done is not a gap.
-- Patterns use '*' as a wildcard on any axis. `severity`:
--   'impossible'  — cannot be done (physics, ethics)
--   'uninformative' — can be done, cannot answer the question (e.g. PET has
--                     no cell-type resolution)
--   'hard'        — feasible but costly; downweighted, not excluded
CREATE TABLE IF NOT EXISTS constraints (
    id        INTEGER PRIMARY KEY,
    pattern   TEXT NOT NULL,      -- JSON object: {axis: term|'*'}
    severity  TEXT NOT NULL CHECK (severity IN ('impossible','uninformative','hard')),
    reason    TEXT NOT NULL
);

-- ── Claim graph (for contradiction + ABC linking) ───────────────────────────
CREATE TABLE IF NOT EXISTS claims (
    id          INTEGER PRIMARY KEY,
    subject     TEXT NOT NULL,
    relation    TEXT NOT NULL,     -- increases | decreases | associated_with | ...
    object      TEXT NOT NULL,
    direction   INTEGER NOT NULL DEFAULT 0,   -- +1 / -1 / 0
    context     TEXT NOT NULL DEFAULT '{}',   -- JSON: axis values the claim holds under
    record_id   INTEGER REFERENCES records (id) ON DELETE CASCADE,
    confidence  REAL NOT NULL DEFAULT 0.5
);

CREATE INDEX IF NOT EXISTS idx_claims_subject ON claims (subject);
CREATE INDEX IF NOT EXISTS idx_claims_object  ON claims (object);

-- ── Materialised coverage matrix ────────────────────────────────────────────
-- Rebuilt by `cli.py build`. `key` is a JSON object of the axis values that
-- define the cell; `axes` is the sorted axis list so several matrices over
-- different axis subsets can coexist in one table.
CREATE TABLE IF NOT EXISTS coverage_cells (
    id             INTEGER PRIMARY KEY,
    axes           TEXT NOT NULL,
    key            TEXT NOT NULL,
    n_works        INTEGER NOT NULL DEFAULT 0,
    n_datasets     INTEGER NOT NULL DEFAULT 0,
    n_cohorts      INTEGER NOT NULL DEFAULT 0,
    max_donors     INTEGER,
    total_donors   INTEGER,
    year_last      INTEGER,
    expected       REAL NOT NULL DEFAULT 0,
    depletion      REAL NOT NULL DEFAULT 0,
    UNIQUE (axes, key)
);

-- ── Gaps + human triage ─────────────────────────────────────────────────────
-- `status` is the whole point of persisting gaps: accept/reject decisions by a
-- domain expert are the labelled data that a ranking model is trained on later.
CREATE TABLE IF NOT EXISTS gaps (
    id            INTEGER PRIMARY KEY,
    kind          TEXT NOT NULL,   -- coverage | design | method_transfer | abc | contradiction
    key           TEXT NOT NULL,   -- JSON, identity of the gap
    title         TEXT NOT NULL,
    detail        TEXT,
    scores        TEXT NOT NULL DEFAULT '{}',   -- JSON
    priority      REAL NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'new'
                  CHECK (status IN ('new','accepted','rejected','known','parked')),
    notes         TEXT,
    created_at    INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    UNIQUE (kind, key)
);

CREATE TABLE IF NOT EXISTS gap_evidence (
    gap_id    INTEGER NOT NULL REFERENCES gaps (id) ON DELETE CASCADE,
    record_id INTEGER NOT NULL REFERENCES records (id) ON DELETE CASCADE,
    role      TEXT NOT NULL,   -- near_miss | addressable | latent | supporting | contradicting
    note      TEXT,
    PRIMARY KEY (gap_id, record_id, role)
);
