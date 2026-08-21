"""Agent loop over the gap-finder tools.

Run:  python -m gapfinder.agent --db data/gapfinder.db \\
          "Find the three best re-analysis opportunities in microglia."

Requires `pip install anthropic` and credentials (ANTHROPIC_API_KEY, or an
`ant auth login` profile — the zero-arg client picks either up).

The model is deliberately given no freedom about where facts come from: the
system prompt in `agent_tools` forbids asserting anything about the corpus that
did not arrive through a tool result, and `verify_absence` exists so "nobody has
studied X" is a query result rather than a recollection.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from . import agent_tools, db
from .agent_tools import SYSTEM_PROMPT

MODEL = "claude-opus-5"

_CONN: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    if _CONN is None:
        raise RuntimeError("call run() first — the tools need a database handle")
    return _CONN


def _build_tools():
    """Wrap the plain functions as SDK tools.

    Schemas come from the type hints and docstrings, so the description the
    model reads is the same text a developer reads in `agent_tools`. Keeping
    one source for both is what stops the two from drifting.
    """
    from anthropic import beta_tool

    @beta_tool
    def list_axes() -> str:
        """List the coverage axes and every legal term on each. Call this first."""
        return json.dumps(agent_tools.list_axes())

    @beta_tool
    def query_coverage(axes: list[str], where: dict | None = None,
                       order: str = "depletion", limit: int = 20) -> str:
        """Read cells of a materialised coverage matrix.

        Args:
            axes: Axis names defining the matrix, e.g. ["cell_type", "region", "stage"].
            where: Optional axis -> term filter restricting which cells are returned.
            order: "depletion" (most under-represented first) or "observed" (most studied first).
            limit: Maximum cells to return.
        """
        return json.dumps(agent_tools.query_coverage(_conn(), axes, where, order, limit))

    @beta_tool
    def verify_absence(key: dict) -> str:
        """Check whether an empty cell is genuinely unstudied or an indexing artefact.

        Always call this before describing anything as never done. If the result
        reports that it is NOT absent, the vocabulary missed records that cover the
        combination and it must not be reported as a gap.

        Args:
            key: One cell, as axis -> term, e.g. {"cell_type": "microglia", "stage": "preclinical"}.
        """
        return json.dumps(agent_tools.verify_absence(_conn(), key))

    @beta_tool
    def search_records(text: str | None = None, axis_filters: dict | None = None,
                       kind: str | None = None, min_donors: int | None = None,
                       limit: int = 15) -> str:
        """Find works and datasets by free text and/or axis annotations.

        Args:
            text: Substring to match in title or abstract.
            axis_filters: axis -> term; records must carry every one as a stated (non-inferred) annotation.
            kind: "work" or "dataset".
            min_donors: Only records with at least this many donors.
            limit: Maximum records to return.
        """
        return json.dumps(agent_tools.search_records(
            _conn(), text, axis_filters, kind, min_donors, limit))

    @beta_tool
    def get_record_flags(record_ids: list[int]) -> str:
        """Design weaknesses recorded for specific records.

        Args:
            record_ids: Internal record ids, as returned by search_records.
        """
        return json.dumps(agent_tools.get_record_flags(_conn(), record_ids))

    @beta_tool
    def get_claim_graph(entity: str, limit: int = 30) -> str:
        """Claims in which `entity` appears as subject or object.

        Args:
            entity: Gene, pathway or phenotype name exactly as stored.
            limit: Maximum claims to return.
        """
        return json.dumps(agent_tools.get_claim_graph(_conn(), entity, limit))

    @beta_tool
    def list_gaps(kind: str | None = None, status: str | None = None, limit: int = 15) -> str:
        """Gaps already computed and stored, highest priority first.

        Args:
            kind: coverage | design | method_transfer | abc | contradiction | agent.
            status: new | accepted | rejected | known | parked.
            limit: Maximum gaps to return.
        """
        return json.dumps(agent_tools.list_gaps(_conn(), kind, status, limit))

    @beta_tool
    def propose_workflow(gap_id: int) -> str:
        """Template hypothesis, workflow and falsifier for a stored gap.

        Args:
            gap_id: Id of the gap, from list_gaps.
        """
        return json.dumps(agent_tools.propose_workflow(_conn(), gap_id))

    @beta_tool
    def save_finding(title: str, detail: str, key: dict, priority: float = 0.5,
                     evidence_ids: list[int] | None = None) -> str:
        """Save a gap you have verified, for human triage.

        Args:
            title: One line naming the gap.
            detail: What is missing, what evidence supports the absence, and why it matters.
            key: Structured identity of the gap, so it deduplicates across runs.
            priority: 0-1, your judgement of how worth pursuing this is.
            evidence_ids: Record ids you relied on.
        """
        return json.dumps(agent_tools.save_finding(
            _conn(), title, detail, key, priority, evidence_ids))

    return [list_axes, query_coverage, verify_absence, search_records, get_record_flags,
            get_claim_graph, list_gaps, propose_workflow, save_finding]


def run(db_path: str, prompt: str, max_tokens: int = 16000, verbose: bool = True) -> str:
    global _CONN
    import anthropic

    _CONN = db.connect(db_path)
    client = anthropic.Anthropic()

    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        tools=_build_tools(),
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        # Route around a safety refusal instead of returning an empty answer.
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": prompt}],
    )

    final = ""
    for message in runner:
        for block in message.content:
            if block.type == "text":
                final = block.text
                if verbose:
                    print(block.text)
            elif block.type == "tool_use" and verbose:
                print(f"  → {block.name}({json.dumps(block.input)[:120]})", file=sys.stderr)
        if message.stop_reason == "refusal":
            detail = getattr(message, "stop_details", None)
            return f"[refused: {getattr(detail, 'category', 'unspecified')}]"
    return final


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("prompt")
    p.add_argument("--db", default="data/gapfinder.db")
    p.add_argument("--max-tokens", type=int, default=16000)
    args = p.parse_args(argv)

    if not Path(args.db).exists():
        sys.exit(f"{args.db} does not exist — run `python cli.py demo` first")
    run(args.db, args.prompt, args.max_tokens)


if __name__ == "__main__":
    main()
