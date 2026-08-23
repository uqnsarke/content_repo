"""Self-contained HTML view of a run: stages, gates, span tree, provenance.

Mantis is the right place to look across many runs. This is the view of one run
that works with no backend, no credentials and no network — which matters
because the moment you most need to see why a run halted is usually the moment
the hosted thing is not set up yet.

Every gate check is shown with its detail, passed and failed alike. A system
that only displays its successes is not transparent, it is decorated.
"""

from __future__ import annotations

import html
import json
import sqlite3

from .artifacts import for_run, provenance

BADGE = {"stage": "#4c6ef5", "tool": "#0ca678", "llm": "#ae3ec9", "gate": "#f08c00"}


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def render(conn: sqlite3.Connection, run_id: int) -> str:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"no run {run_id}")

    artifacts = for_run(conn, run_id)
    gate_rows = conn.execute(
        "SELECT * FROM gate_results WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    gates_by_stage = {g["from_stage"]: g for g in gate_rows}
    spans = conn.execute(
        "SELECT * FROM trace_events WHERE run_id = ? ORDER BY started_at", (run_id,)).fetchall()

    parts = [_header(run), _summary(run, artifacts, gate_rows)]
    for artifact in artifacts:
        parts.append(_stage_block(artifact, gates_by_stage.get(artifact["stage"])))
    if artifacts:
        parts.append(_provenance_block(conn, artifacts[-1]["id"]))
    parts.append(_trace_block(spans))
    return _PAGE.format(run_id=run_id, body="".join(parts))


def _header(run) -> str:
    status_class = {"completed": "ok", "halted": "warn", "failed": "bad"}.get(run["status"], "")
    return (f'<h1>Run {run["id"]}</h1>'
            f'<p class="goal">{_esc(run["goal"])}</p>'
            f'<p class="meta"><span class="pill {status_class}">{_esc(run["status"])}</span>'
            f'<span class="pill">mode: {_esc(run["mode"])}</span>'
            f'<span class="pill">reached: {_esc(run["stage"])}</span>'
            f'<span class="pill">trace {_esc((run["trace_id"] or "")[:12])}</span></p>'
            + (f'<blockquote>{_esc(run["halt_reason"])}</blockquote>'
               if run["halt_reason"] else ""))


def _summary(run, artifacts, gate_rows) -> str:
    passed = sum(1 for g in gate_rows if g["passed"])
    return (f'<table class="kv"><tr><td>artifacts</td><td>{len(artifacts)}</td></tr>'
            f'<tr><td>gates evaluated</td><td>{len(gate_rows)} '
            f'({passed} passed)</td></tr></table>')


def _stage_block(artifact: dict, gate) -> str:
    body = artifact["body"]
    out = [f'<h2>{_esc(artifact["stage"])} <span class="digest">'
           f'{_esc(artifact["digest"][:12])}</span></h2>',
           f'<p class="title">{_esc(artifact["title"])}</p>']

    for key in ("summary", "statement", "falsifier", "primary_outcome", "interpretation",
                "decision", "rationale"):
        if body.get(key):
            out.append(f'<p><b>{_esc(key)}.</b> {_esc(body[key])}</p>')

    for key in ("what_is_established", "what_rests_on_thin_evidence",
                "alternative_explanations", "covariates", "notes"):
        items = body.get(key)
        if isinstance(items, list) and items:
            out.append(f'<p class="label">{_esc(key)}</p><ul>'
                       + "".join(f"<li>{_esc(i)}</li>" for i in items) + "</ul>")

    if artifact["stage"] == "insilico":
        out.append(_results_table(body.get("results") or {}))
    if artifact["stage"] == "wetlab":
        out.append(_experiments_table(body.get("experiments") or []))
        if body.get("advisory"):
            out.append(f'<blockquote>{_esc(body["advisory"])}</blockquote>')

    if gate is not None:
        out.append(_gate_block(gate))
    return "".join(out)


def _results_table(results: dict) -> str:
    power = results.get("power") or {}
    negative = results.get("negative_control") or {}
    rows = [
        ("power (achieved)", power.get("achieved")),
        ("required n per group", results.get("required_n_per_group")),
        ("detectable effect at available n", results.get("detectable_effect_at_available_n")),
        ("negative control false-positive rate", negative.get("false_positive_rate")),
    ]
    if power.get("note"):
        rows.append(("variance note", power["note"]))
    if negative.get("verdict"):
        rows.append(("negative control verdict", negative["verdict"]))
    reanalysis = results.get("reanalysis")
    if reanalysis and reanalysis.get("feasible"):
        rows.append(("pooled donors (existing data)", reanalysis["pooled_donors"]))
        rows.append(("power if pooled", reanalysis["power_pooled"]))

    curve = results.get("power_curve") or []
    table = ('<table class="kv">'
             + "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in rows)
             + "</table>")
    if curve:
        table += ('<p class="label">power curve</p><table class="grid"><tr>'
                  + "".join(f"<th>{_esc(p['n_per_group'])}</th>" for p in curve)
                  + "</tr><tr>"
                  + "".join(f"<td>{_esc(p['power'])}</td>" for p in curve) + "</tr></table>")
    return table


def _experiments_table(experiments: list) -> str:
    if not experiments:
        return ""
    rows = ["<table class='grid'><tr><th>score</th><th>experiment</th><th>discriminates</th>"
            "<th>cost</th><th>turnaround</th><th>orthogonal</th></tr>"]
    for e in experiments:
        rows.append(
            f"<tr><td>{_esc(e.get('score'))}</td><td>{_esc(e.get('name'))}</td>"
            f"<td>{_esc(e.get('discriminates'))}</td><td>{_esc(e.get('cost_band'))}</td>"
            f"<td>{_esc(e.get('turnaround'))}</td>"
            f"<td>{'yes' if e.get('orthogonal_to_discovery') else 'NO'}</td></tr>")
        if e.get("controls"):
            rows.append(f"<tr class='sub'><td></td><td colspan='5'>controls: "
                        f"{_esc(', '.join(e['controls']))}</td></tr>")
        if e.get("limitations"):
            rows.append(f"<tr class='sub'><td></td><td colspan='5'>limits: "
                        f"{_esc(e['limitations'])}</td></tr>")
    return "".join(rows) + "</table>"


def _gate_block(gate) -> str:
    checks = json.loads(gate["checks"])
    cls = "ok" if gate["passed"] else "bad"
    out = [f'<div class="gate {cls}"><p class="label">gate {_esc(gate["gate"])} — '
           f'{"passed" if gate["passed"] else "FAILED"}</p><ul>']
    for c in checks:
        mark = "✓" if c["passed"] else "✗"
        out.append(f'<li class="{"ok" if c["passed"] else "bad"}">{mark} '
                   f'<b>{_esc(c["name"])}</b> — {_esc(c["detail"])}</li>')
    return "".join(out) + "</ul></div>"


def _provenance_block(conn, artifact_id: int) -> str:
    prov = provenance(conn, artifact_id)
    out = ['<h2>provenance</h2>',
           '<p>Walking <code>derived_from</code> back from the final artifact.</p><ol>']
    for node in prov["chain"]:
        out.append(f'<li>{_esc(node["stage"])}: {_esc(node["title"])} '
                   f'<span class="digest">{_esc(node["digest"])}</span></li>')
    out.append("</ol>")
    if prov["evidence"]:
        out.append('<p class="label">grounded in</p><ul>')
        for e in prov["evidence"]:
            ident = (f'<a href="{_esc(e["url"])}" rel="noopener">{_esc(e["external_id"])}</a>'
                     if e["url"] else _esc(e["external_id"]))
            out.append(f'<li>{ident} — {_esc(e["title"])} ({_esc(e["year"])})</li>')
        out.append("</ul>")
    return "".join(out)


def _trace_block(spans) -> str:
    by_parent: dict[str | None, list] = {}
    for s in spans:
        by_parent.setdefault(s["parent_span"], []).append(s)

    out = ['<h2>reasoning trace</h2>',
           '<p>Every span below is also exported to Mantis when Insight credentials are '
           'configured; this table is the local mirror.</p><div class="trace">']

    def walk(parent, depth):
        for s in by_parent.get(parent, []):
            duration = ((s["ended_at"] or s["started_at"]) - s["started_at"]) * 1000
            colour = BADGE.get(s["kind"], "#868e96")
            out.append(
                f'<div class="span" style="margin-left:{depth * 1.25}rem">'
                f'<span class="kind" style="background:{colour}">{_esc(s["kind"])}</span>'
                f'<span class="name">{_esc(s["name"])}</span>'
                f'<span class="dur">{duration:.0f} ms</span>'
                + (f'<span class="err">{_esc(s["status"])}</span>'
                   if s["status"] != "ok" else "")
                + (f'<div class="io">{_esc((s["output"] or "")[:300])}</div>'
                   if s["output"] else "")
                + "</div>")
            walk(s["span_id"], depth + 1)

    walk(None, 0)
    return "".join(out) + "</div>"


_PAGE = """<!doctype html>
<meta charset="utf-8">
<title>Pipeline run {run_id}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ --bg:#fff; --fg:#1a1a1a; --muted:#5b6472; --rule:#e3e6ea; --accent:#8c2f39;
           --ok:#0ca678; --bad:#e03131; --warn:#f08c00; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#14171a; --fg:#e8eaed; --muted:#9aa4b2; --rule:#2a2f36; --accent:#e08d97;
             --ok:#38d9a9; --bad:#ff8787; --warn:#ffc078; }}
  }}
  body {{ background:var(--bg); color:var(--fg); margin:0 auto; max-width:60rem;
          padding:2.5rem 1.25rem 6rem; line-height:1.6;
          font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif; }}
  h1 {{ font-size:1.8rem; margin:0 0 .35rem; }}
  h2 {{ font-size:1.15rem; margin:2.5rem 0 .5rem; padding-bottom:.3rem;
        border-bottom:1px solid var(--rule); text-transform:lowercase;
        letter-spacing:.02em; color:var(--accent); }}
  .goal {{ font-size:1.05rem; color:var(--muted); margin:.2rem 0 .8rem; }}
  .title {{ font-weight:600; margin:.2rem 0 .6rem; }}
  .label {{ color:var(--muted); font-size:.82rem; text-transform:uppercase;
            letter-spacing:.05em; margin:.9rem 0 .2rem; }}
  .pill {{ display:inline-block; border:1px solid var(--rule); border-radius:999px;
           padding:.1rem .6rem; margin-right:.4rem; font-size:.8rem; color:var(--muted); }}
  .pill.ok {{ color:var(--ok); border-color:var(--ok); }}
  .pill.bad {{ color:var(--bad); border-color:var(--bad); }}
  .pill.warn {{ color:var(--warn); border-color:var(--warn); }}
  .digest {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.75rem;
             color:var(--muted); }}
  blockquote {{ border-left:3px solid var(--warn); margin:1rem 0; padding:.6rem 1rem;
                background:color-mix(in srgb, var(--warn) 10%, transparent); }}
  table {{ border-collapse:collapse; width:100%; margin:.7rem 0; font-size:.88rem;
           display:block; overflow-x:auto; }}
  td, th {{ border:1px solid var(--rule); padding:.3rem .55rem; text-align:left;
            vertical-align:top; }}
  table.kv tr td:first-child {{ color:var(--muted); width:18rem; }}
  tr.sub td {{ color:var(--muted); font-size:.84rem; }}
  .gate {{ border:1px solid var(--rule); border-left-width:3px; border-radius:4px;
           padding:.4rem .9rem; margin:1rem 0; }}
  .gate.ok {{ border-left-color:var(--ok); }}
  .gate.bad {{ border-left-color:var(--bad); }}
  li.ok {{ color:var(--fg); }}
  li.bad {{ color:var(--bad); }}
  ul, ol {{ margin:.3rem 0 .6rem; padding-left:1.3rem; }}
  .trace {{ font-size:.85rem; }}
  .span {{ padding:.15rem 0; border-bottom:1px dotted var(--rule); }}
  .kind {{ display:inline-block; color:#fff; border-radius:3px; padding:0 .35rem;
           font-size:.7rem; margin-right:.5rem; text-transform:uppercase; }}
  .name {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  .dur {{ color:var(--muted); margin-left:.5rem; font-size:.78rem; }}
  .err {{ color:var(--bad); margin-left:.5rem; }}
  .io {{ color:var(--muted); font-size:.78rem; margin:.1rem 0 .2rem 2.5rem;
         overflow-wrap:anywhere; }}
  a {{ color:var(--accent); }}
  code {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.85em; }}
</style>
{body}
"""
