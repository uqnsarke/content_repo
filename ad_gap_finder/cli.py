#!/usr/bin/env python
"""ad_gap_finder — compute research gaps in the Alzheimer's literature.

    python cli.py demo                     # offline end-to-end run on synthetic data
    python cli.py init
    python cli.py ingest --source openalex --query alzheimer --limit 2000
    python cli.py ingest --source geo --limit 1000
    python cli.py ingest --source openalex --query parkinson --tag pd   # comparator
    python cli.py build  --axes cell_type,region,stage
    python cli.py gaps   --limit 25
    python cli.py report --out report.html
    python cli.py triage 14 --status accepted --note "worth a grant"

Part of the Multiome Academy tutorial series — https://multiomeacademy.com
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gapfinder import coverage, db, detectors, report  # noqa: E402
from gapfinder.lab import pipeline as lab_pipeline  # noqa: E402
from gapfinder.lab import artifacts as lab_artifacts  # noqa: E402
from gapfinder.lab import viewer as lab_viewer  # noqa: E402
from gapfinder.lab.stages import agent_mode_available  # noqa: E402
from gapfinder.pipeline import ingest  # noqa: E402
from gapfinder.sources import ADAPTERS  # noqa: E402

DEFAULT_DB = "data/gapfinder.db"

# Axis subsets worth materialising by default. Each is chosen because its empty
# cells mean something specific; adding axes past three makes almost every cell
# empty and the depletion score stops discriminating.
DEFAULT_MATRICES = [
    ["cell_type", "region", "stage"],
    ["modality", "stage", "tissue_state"],
    ["cell_type", "modality", "stage"],
    ["region", "modality", "design"],
    ["stage", "sex", "modality"],
]


def _open(args):
    conn = db.connect(args.db)
    db.init(conn)
    return conn


def cmd_init(args):
    conn = _open(args)
    print(f"initialised {args.db}")
    print(json.dumps(db.counts(conn), indent=2))


def cmd_ingest(args):
    conn = _open(args)
    adapter = ADAPTERS.get(args.source.split(":")[0])
    if adapter is None:
        sys.exit(f"unknown source '{args.source}' (have: {', '.join(ADAPTERS)})")

    kwargs = {"limit": args.limit}
    if args.query:
        kwargs["query" if args.source == "openalex" else "term"] = args.query
    records = adapter.fetch(**kwargs)

    if args.tag:
        records = ({**r, "source": f"{r['source']}:{args.tag}"} for r in records)

    n = ingest(conn, records, limit=args.limit)
    print(f"ingested {n} records from {args.source}")
    print(json.dumps(db.counts(conn), indent=2))


def cmd_annotate(args):
    """Re-run normalisation over stored records, e.g. after a vocabulary change."""
    conn = _open(args)
    rows = conn.execute("SELECT * FROM records").fetchall()
    n = ingest(conn, ({k: r[k] for k in r.keys() if k not in ("id", "retrieved_at")}
                      for r in rows))
    print(f"re-annotated {n} records")


def cmd_build(args):
    conn = _open(args)
    matrices = [a.split(",") for a in args.axes] if args.axes else DEFAULT_MATRICES
    for axes in matrices:
        cells = coverage.build(conn, axes, min_confidence=args.min_confidence)
        filled = conn.execute(
            "SELECT COUNT(*) FROM coverage_cells WHERE axes = ? AND n_works + n_datasets > 0",
            (json.dumps(sorted(axes)),)).fetchone()[0]
        print(f"{'×'.join(axes):48s} {cells:6d} cells, {filled:5d} occupied "
              f"({filled / cells:.1%})" if cells else f"{axes}: empty")


def cmd_gaps(args):
    conn = _open(args)
    kinds = args.detector or list(detectors.DETECTORS)
    total = 0
    for kind in kinds:
        fn = detectors.DETECTORS[kind]
        found = fn(conn, limit=args.limit)
        detectors.persist(conn, found)
        total += len(found)
        print(f"{kind:16s} {len(found):3d} gaps")
    print(f"{total} gaps written to {args.db}")


def cmd_report(args):
    conn = _open(args)
    gaps = report.load_gaps(conn, status=args.status, kinds=args.detector, limit=args.limit)
    if not gaps:
        sys.exit("no gaps stored — run `gaps` first")
    out = Path(args.out)
    text = (report.to_html if out.suffix == ".html" else report.to_markdown)(
        conn, gaps, with_proposals=not args.no_proposals)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f"wrote {out} ({len(gaps)} gaps)")


def cmd_triage(args):
    conn = _open(args)
    cur = conn.execute("UPDATE gaps SET status = ?, notes = ? WHERE id = ?",
                       (args.status, args.note, args.gap_id))
    conn.commit()
    if not cur.rowcount:
        sys.exit(f"no gap with id {args.gap_id}")
    print(f"gap {args.gap_id} -> {args.status}")


def cmd_stats(args):
    conn = _open(args)
    print(json.dumps(db.counts(conn), indent=2))
    print("\nby source:")
    for r in conn.execute("SELECT source, kind, COUNT(*) n FROM records "
                          "GROUP BY source, kind ORDER BY n DESC"):
        print(f"  {r['source']:20s} {r['kind']:8s} {r['n']:5d}")
    print("\ntriage:")
    for r in conn.execute("SELECT status, COUNT(*) n FROM gaps GROUP BY status"):
        print(f"  {r['status']:10s} {r['n']:4d}")


def cmd_demo(args):
    """Full offline run: synthetic corpus -> matrices -> gaps -> report."""
    from gapfinder.sources import demo as demo_src

    conn = _open(args)
    n = ingest(conn, demo_src.fetch(limit=args.limit))
    m = ingest(conn, demo_src.fetch_comparator(limit=max(60, args.limit // 3)))
    print(f"ingested {n} synthetic AD records + {m} comparator records")

    # Attach each synthetic claim to a real row in `records` so the detectors
    # that reason about a claim's provenance — citation count, cohort,
    # replication — have something to read. A claim with no record behind it
    # silently disables half of `contradiction`.
    conn.execute("DELETE FROM claims")
    hosts = [r["id"] for r in conn.execute(
        "SELECT id FROM records WHERE source = 'demo' AND kind = 'work' "
        "ORDER BY cited_by DESC")]
    for i, c in enumerate(demo_src.claims()):
        conn.execute("INSERT INTO claims (subject, relation, object, direction, context, "
                     "confidence, record_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (c["subject"], c["relation"], c["object"], c["direction"],
                      json.dumps(c["context"], sort_keys=True), c["confidence"],
                      hosts[i % len(hosts)] if hosts else None))
    conn.commit()

    for axes in DEFAULT_MATRICES:
        coverage.build(conn, axes)
    print(f"built {len(DEFAULT_MATRICES)} coverage matrices")

    for kind, fn in detectors.DETECTORS.items():
        found = fn(conn, limit=15)
        detectors.persist(conn, found)
        print(f"  {kind:16s} {len(found):3d} gaps")

    gaps = report.load_gaps(conn, limit=40)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report.to_html(conn, gaps) if out.suffix == ".html"
                   else report.to_markdown(conn, gaps))
    print(f"\nwrote {out}")
    print("⚠️  synthetic corpus — the logic is real, the records are not")


def cmd_lab(args):
    """Run the autonomous pipeline: synthesis -> hypothesis -> analysis -> in-silico -> wet-lab."""
    conn = _open(args)
    status = lab_pipeline.mantis_status()
    print(f"mantis: {'configured' if status['configured'] else 'local-only'}"
          + (f" ({status['reason']})" if status.get("reason") else ""))

    if args.mode == "agent" and not agent_mode_available():
        sys.exit("agent mode needs the `anthropic` package and credentials "
                 "(ANTHROPIC_API_KEY or `ant auth login`); use --mode dry otherwise")

    goals = args.goal or ["Find the most tractable gap and take it to a wet-lab recommendation"]

    if len(goals) > 1:
        conn.close()
        results = lab_pipeline.run_many(args.db, goals, args.mode, args.workers, args.model)
        conn = db.connect(args.db)
    else:
        def on_event(kind, **kw):
            if kind == "stage":
                print(f"  {kw['stage']:<11s} -> artifact {kw['artifact'].id}: "
                      f"{kw['artifact'].title[:70]}")
            elif kind == "gate":
                failed = [n for n, ok, _ in kw["checks"] if not ok]
                # Waiting on a person is the designed ending, not a halt.
                mark = ("pass" if kw["passed"]
                        else "WAIT" if failed == ["human_signoff"] else "HALT")
                print(f"  gate {kw['stage']}->{kw['to_stage']}: {mark}")
                for name, ok, detail in kw["checks"]:
                    if not ok:
                        print(f"       - {name}: {detail}")
        results = [lab_pipeline.run_pipeline(conn, goals[0], args.mode, args.model,
                                             on_event=on_event,
                                             enable_mantis=not args.no_mantis)]

    for result in results:
        print(f"\nrun {result.get('run_id')}: {result['status']} "
              f"(reached {result.get('reached_stage')})")
        if result.get("awaiting_human_signoff"):
            print(f"  awaiting sign-off — review, then: "
                  f"python cli.py signoff {result['run_id']}")
        elif result.get("halt_reason"):
            print(f"  halted: {result['halt_reason']}")

    if args.out and results and results[0].get("run_id"):
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(lab_viewer.render(conn, results[0]["run_id"]))
        print(f"\nwrote {out}")


def cmd_runs(args):
    conn = _open(args)
    rows = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT ?", (args.limit,)).fetchall()
    if not rows:
        sys.exit("no runs yet — try `python cli.py lab`")
    for r in rows:
        print(f"{r['id']:4d}  {r['status']:10s} {str(r['stage'] or '-'):10s} "
              f"{r['mode']:6s} {r['goal'][:60]}")
        if r["halt_reason"]:
            print(f"      {r['halt_reason'][:100]}")


def cmd_signoff(args):
    """Record human approval on a wet-lab recommendation and close the run.

    Deliberately a separate command a person runs. The pipeline cannot reach
    this state on its own, which is the whole point of the final gate.
    """
    conn = _open(args)
    row = conn.execute(
        "SELECT * FROM artifacts WHERE run_id = ? AND stage = 'wetlab' ORDER BY id DESC LIMIT 1",
        (args.run_id,)).fetchone()
    if row is None:
        sys.exit(f"run {args.run_id} has no wet-lab recommendation to approve")

    body = json.loads(row["body"])
    body["human_signoff"] = True
    body["signed_off_by"] = args.by
    conn.execute("UPDATE artifacts SET body = ? WHERE id = ?",
                 (json.dumps(body, default=str), row["id"]))
    lab_artifacts.finish_run(conn, args.run_id, "completed", "wetlab", None)
    conn.commit()
    print(f"run {args.run_id} signed off by {args.by}")
    top = (body.get("experiments") or [{}])[0].get("name")
    if top:
        print(f"  approved first experiment: {top}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=DEFAULT_DB)

    # --db is accepted on either side of the subcommand. SUPPRESS is what makes
    # that work: without it the subparser's default would overwrite a --db given
    # before the subcommand, so the global flag would appear to be ignored.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=argparse.SUPPRESS,
                        help=f"database path (default {DEFAULT_DB})")

    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name):
        return sub.add_parser(name, parents=[common])

    add("init").set_defaults(fn=cmd_init)

    s = add("ingest")
    s.add_argument("--source", required=True, choices=sorted(ADAPTERS))
    s.add_argument("--query", help="search term (OpenAlex) or E-utilities term (GEO)")
    s.add_argument("--limit", type=int, default=500)
    s.add_argument("--tag", help="store as source '<source>:<tag>' — use for comparator corpora")
    s.set_defaults(fn=cmd_ingest)

    add("annotate").set_defaults(fn=cmd_annotate)

    s = add("build")
    s.add_argument("--axes", action="append",
                   help="comma-separated axis list; repeatable. Default: five standard matrices")
    s.add_argument("--min-confidence", type=float, default=0.5)
    s.set_defaults(fn=cmd_build)

    s = add("gaps")
    s.add_argument("--detector", action="append", choices=sorted(detectors.DETECTORS))
    s.add_argument("--limit", type=int, default=25)
    s.set_defaults(fn=cmd_gaps)

    s = add("report")
    s.add_argument("--out", default="report.md")
    s.add_argument("--status", choices=["new", "accepted", "rejected", "known", "parked"])
    s.add_argument("--detector", action="append", choices=sorted(detectors.DETECTORS))
    s.add_argument("--limit", type=int, default=40)
    s.add_argument("--no-proposals", action="store_true")
    s.set_defaults(fn=cmd_report)

    s = add("triage")
    s.add_argument("gap_id", type=int)
    s.add_argument("--status", required=True,
                   choices=["new", "accepted", "rejected", "known", "parked"])
    s.add_argument("--note")
    s.set_defaults(fn=cmd_triage)

    add("stats").set_defaults(fn=cmd_stats)

    s = add("lab")
    s.add_argument("--goal", action="append",
                   help="research goal; repeat for a fan-out across goals")
    s.add_argument("--mode", choices=["dry", "agent"], default="dry",
                   help="dry = deterministic, no model calls (default); agent = Claude narrates")
    s.add_argument("--model", default="claude-opus-5")
    s.add_argument("--workers", type=int, default=4)
    s.add_argument("--out", default="run.html", help="write the run view here")
    s.add_argument("--no-mantis", action="store_true",
                   help="never initialise Mantis even if INSIGHT_* is set")
    s.set_defaults(fn=cmd_lab)

    s = add("runs")
    s.add_argument("--limit", type=int, default=20)
    s.set_defaults(fn=cmd_runs)

    s = add("signoff")
    s.add_argument("run_id", type=int)
    s.add_argument("--by", default="unnamed reviewer")
    s.set_defaults(fn=cmd_signoff)

    s = add("demo")
    s.add_argument("--limit", type=int, default=400)
    s.add_argument("--out", default="report.html")
    s.set_defaults(fn=cmd_demo)

    args = p.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        # `cli.py stats | head` is a normal thing to do; don't traceback for it.
        sys.stdout = None
