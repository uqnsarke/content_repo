"""Render gaps as Markdown or a self-contained HTML page."""

from __future__ import annotations

import html
import json
import re
import sqlite3
from datetime import datetime, timezone

from .axes import label
from .propose import propose

KIND_TITLES = {
    "coverage": "Coverage gaps — combinations the field has never assayed",
    "design": "Design gaps — evidence bases with one shared blind spot",
    "method_transfer": "Method transfer — proven elsewhere, untried here",
    "abc": "Untested implications — A→B and B→C published, A→C never tested",
    "contradiction": "Contradictions and unreplicated singletons",
}


def _synthetic(conn: sqlite3.Connection) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM records WHERE source = 'demo' OR source LIKE 'demo:%'"
    ).fetchone()[0]


def load_gaps(conn: sqlite3.Connection, status: str | None = None,
              kinds: list[str] | None = None, limit: int = 40) -> list[dict]:
    """Top gaps, taken per kind rather than globally.

    Priorities are not comparable across detectors — a design gap is a
    prevalence, a coverage gap is a log-ratio — so a global top-N is really a
    ranking of which detector happens to produce larger numbers. Allocating the
    budget across kinds keeps every detector visible.
    """
    sql = "SELECT * FROM gaps"
    where, params = [], []
    if status:
        where.append("status = ?")
        params.append(status)
    if kinds:
        where.append(f"kind IN ({','.join('?' * len(kinds))})")
        params.extend(kinds)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY priority DESC"

    rows = conn.execute(sql, params).fetchall()
    present = {r["kind"] for r in rows}
    per_kind = max(1, limit // max(1, len(present)))
    taken: dict[str, int] = {}
    selected = []
    for row in rows:
        if taken.get(row["kind"], 0) >= per_kind:
            continue
        taken[row["kind"]] = taken.get(row["kind"], 0) + 1
        selected.append(row)
    # Spend any leftover budget on the highest-priority gaps regardless of kind.
    for row in rows:
        if len(selected) >= limit:
            break
        if row not in selected:
            selected.append(row)

    gaps = []
    for row in selected[:limit]:
        evidence = conn.execute(
            "SELECT r.id, r.kind, r.source, r.external_id, r.title, r.url, r.year, "
            "r.n_donors, ge.role, ge.note FROM gap_evidence ge "
            "JOIN records r ON r.id = ge.record_id WHERE ge.gap_id = ? "
            "ORDER BY ge.role, r.year DESC", (row["id"],)).fetchall()
        gaps.append({
            "id": row["id"], "kind": row["kind"], "key": json.loads(row["key"]),
            "title": row["title"], "detail": row["detail"],
            "scores": json.loads(row["scores"]), "priority": row["priority"],
            "status": row["status"], "notes": row["notes"],
            "evidence": [dict(e) for e in evidence],
        })
    return gaps


def corpus_summary(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) n, SUM(kind='work') works, SUM(kind='dataset') datasets, "
        "MIN(year) y0, MAX(year) y1 FROM records").fetchone()
    sources = [f"{r['source']} ({r['n']})" for r in conn.execute(
        "SELECT source, COUNT(*) n FROM records GROUP BY source ORDER BY n DESC")]
    return {"records": row["n"], "works": row["works"] or 0,
            "datasets": row["datasets"] or 0, "years": (row["y0"], row["y1"]),
            "sources": sources, "synthetic": _synthetic(conn)}


def to_markdown(conn: sqlite3.Connection, gaps: list[dict], with_proposals: bool = True) -> str:
    summary = corpus_summary(conn)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = ["# AD research gap report", "", f"Generated {now}.", ""]

    if summary["synthetic"]:
        out += [
            "> ⚠️ **This report is built on synthetic data.** "
            f"{summary['synthetic']} of {summary['records']} records come from the bundled "
            "demo corpus. Every identifier, title and donor count in those records is "
            "fabricated. The gap *logic* is real; the gaps are not claims about the "
            "published literature. Run `cli.py ingest --source openalex` and "
            "`--source geo` against the live APIs for a report about the actual field.",
            "",
        ]

    out += [
        "## Corpus", "",
        f"- {summary['records']} records — {summary['works']} works, {summary['datasets']} datasets",
        f"- Years {summary['years'][0]}–{summary['years'][1]}",
        f"- Sources: {', '.join(summary['sources'])}",
        "",
    ]

    by_kind: dict[str, list[dict]] = {}
    for gap in gaps:
        by_kind.setdefault(gap["kind"], []).append(gap)

    for kind, title in KIND_TITLES.items():
        items = by_kind.get(kind)
        if not items:
            continue
        out += [f"## {title}", ""]
        for i, gap in enumerate(items, 1):
            out += [f"### {i}. {gap['title']}", "",
                    f"**Priority {gap['priority']:.3f}** · status `{gap['status']}` · "
                    f"gap id `{gap['id']}`", "", gap["detail"] or "", ""]
            if gap["scores"]:
                out += ["| metric | value |", "| --- | --- |"]
                out += [f"| {k} | {v} |" for k, v in gap["scores"].items() if v is not None]
                out.append("")
            if gap["evidence"]:
                out += ["**Evidence**", ""]
                for e in gap["evidence"][:8]:
                    ident = f"[{e['external_id']}]({e['url']})" if e["url"] else e["external_id"]
                    donors = f", n={e['n_donors']}" if e["n_donors"] else ""
                    note = f" — _{e['note']}_" if e["note"] else ""
                    out.append(f"- `{e['role']}` {ident} — {e['title']} "
                               f"({e['year']}{donors}){note}")
                out.append("")
            if with_proposals:
                p = propose(gap)
                out += [
                    "**Hypothesis.** " + p["hypothesis"], "",
                    f"**Route:** {p['route']} · **Effort:** {p['effort']}", "",
                    "**Workflow**", "",
                ]
                out += [f"{n}. {step}" for n, step in enumerate(p["workflow"], 1)]
                out += ["", "**Falsifier.** " + p["falsifier"], ""]
    return "\n".join(out)


_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_CODE = re.compile(r"`([^`]+)`")
_EM = re.compile(r"(?<![\w])_([^_]+)_(?![\w])")


def _inline(text: str) -> str:
    """Escape, then restore the small set of inline markdown the report emits.

    Escaping first and re-introducing markup after is the safe order: record
    titles come from external sources and must never reach the page as live
    markup, but the links and code spans this module writes itself should still
    render as links and code spans.
    """
    out = html.escape(text)
    # Only http(s) becomes a live link. Record titles are external text, and a
    # title containing `[click](javascript:…)` must not survive as an anchor.
    out = _LINK.sub(
        lambda m: (f'<a href="{m.group(2)}" rel="noopener">{m.group(1)}</a>'
                   if m.group(2).startswith(("http://", "https://"))
                   else f"{m.group(1)} ({m.group(2)})"),
        out)
    out = _CODE.sub(r"<code>\1</code>", out)
    return _EM.sub(r"<em>\1</em>", out)


def to_html(conn: sqlite3.Connection, gaps: list[dict], with_proposals: bool = True) -> str:
    """Self-contained HTML. No external assets, readable in light and dark."""
    md_like = to_markdown(conn, gaps, with_proposals)
    body = []
    for block in md_like.split("\n\n"):
        b = block.strip()
        if not b:
            continue
        if b.startswith("# "):
            body.append(f"<h1>{_inline(b[2:])}</h1>")
        elif b.startswith("## "):
            body.append(f"<h2>{_inline(b[3:])}</h2>")
        elif b.startswith("### "):
            body.append(f"<h3>{_inline(b[4:])}</h3>")
        elif b.startswith(">"):
            body.append(f"<blockquote>{_inline(b.lstrip('> '))}</blockquote>")
        elif b.startswith("|"):
            rows = [r for r in b.split("\n") if not set(r) <= set("|- ")]
            cells = ["".join(f"<td>{_inline(c.strip())}</td>" for c in r.strip("|").split("|"))
                     for r in rows]
            body.append("<table>" + "".join(f"<tr>{c}</tr>" for c in cells) + "</table>")
        elif b.startswith(("- ", "1. ")):
            items = [f"<li>{_inline(line.split(' ', 1)[1])}</li>"
                     for line in b.split("\n") if " " in line]
            tag = "ul" if b.startswith("- ") else "ol"
            body.append(f"<{tag}>{''.join(items)}</{tag}>")
        else:
            body.append(f"<p>{_inline(b)}</p>")

    return f"""<!doctype html>
<meta charset="utf-8">
<title>AD research gap report</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ --bg:#fff; --fg:#1a1a1a; --muted:#5b6472; --rule:#e3e6ea; --accent:#8c2f39; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#14171a; --fg:#e8eaed; --muted:#9aa4b2; --rule:#2a2f36; --accent:#e08d97; }}
  }}
  body {{ background:var(--bg); color:var(--fg); margin:0 auto; max-width:52rem;
         padding:2.5rem 1.25rem 6rem; line-height:1.6;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
  h1 {{ font-size:1.9rem; margin:0 0 .25rem; }}
  h2 {{ font-size:1.25rem; margin:2.75rem 0 .5rem; padding-bottom:.35rem;
        border-bottom:1px solid var(--rule); }}
  h3 {{ font-size:1.05rem; margin:2rem 0 .35rem; color:var(--accent); }}
  p, li {{ margin:.5rem 0; }}
  blockquote {{ border-left:3px solid var(--accent); margin:1.25rem 0; padding:.65rem 1rem;
                background:color-mix(in srgb, var(--accent) 8%, transparent); }}
  table {{ border-collapse:collapse; margin:.75rem 0; font-size:.9rem; width:100%; }}
  td {{ border:1px solid var(--rule); padding:.3rem .55rem; }}
  tr td:first-child {{ color:var(--muted); width:14rem; }}
  code {{ font-size:.85em; background:color-mix(in srgb, var(--fg) 8%, transparent);
          padding:.1rem .3rem; border-radius:3px; }}
  a {{ color:var(--accent); }}
</style>
{''.join(body)}
"""
