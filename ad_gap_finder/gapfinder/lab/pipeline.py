"""The stage machine.

Runs synthesis → hypothesis → analysis → in-silico → wet-lab, evaluating a gate
between every pair. A failed gate halts the run and records why; it does not
retry with softer criteria, because a gate that can be argued down is not a gate.

`run_many` fans out across several goals with a bounded thread pool — each run
gets its own connection, since SQLite objects are not shareable across threads,
and its own trace. That is the "at scale" part: the traces are what make
fifty concurrent runs inspectable rather than merely fast.
"""

from __future__ import annotations

import queue
import sqlite3
import threading
from pathlib import Path
from typing import Callable

from .. import db
from . import gates
from .artifacts import STAGES, create_run, finish_run, save
from .stages import STAGE_FN, Context
from .tracing import Tracer, mantis_status

NEXT_STAGE = dict(zip(STAGES, STAGES[1:] + ["done"]))


def run_pipeline(conn: sqlite3.Connection, goal: str, mode: str = "dry",
                 model: str = "claude-opus-5", on_event: Callable | None = None,
                 enable_mantis: bool = True) -> dict:
    """Execute one run. Returns a summary including where it stopped and why."""
    run_id = create_run(conn, goal, mode)
    tracer = Tracer(conn, run_id, enable_mantis=enable_mantis)
    conn.execute("UPDATE runs SET trace_id = ? WHERE id = ?", (tracer.trace_id, run_id))
    conn.commit()

    ctx = Context(conn=conn, run_id=run_id, tracer=tracer, goal=goal, mode=mode, model=model)
    emit = on_event or (lambda *_a, **_k: None)

    reached, halt_reason, awaiting = None, None, False
    try:
        with tracer.span(f"run:{run_id}", kind="stage", input={"goal": goal, "mode": mode}) as root:
            root.set_attribute("run.id", run_id)
            for stage in STAGES:
                with tracer.span(f"stage:{stage}", kind="stage") as span:
                    artifact = STAGE_FN[stage](ctx)
                    artifact.id = save(conn, run_id, artifact)
                    ctx.artifacts[stage] = artifact
                    reached = stage
                    span.set_output({"artifact_id": artifact.id, "title": artifact.title,
                                     "digest": artifact.digest[:12]})
                emit("stage", stage=stage, artifact=artifact)

                to_stage = NEXT_STAGE[stage]
                with tracer.span(f"gate:{stage}->{to_stage}", kind="gate") as span:
                    passed, checks = gates.evaluate(conn, run_id, stage, to_stage, artifact.body)
                    span.set_output({"passed": passed,
                                     "checks": [{"name": n, "passed": p} for n, p, _ in checks]})
                    if not passed:
                        span.fail(gates.failures(checks))
                emit("gate", stage=stage, to_stage=to_stage, passed=passed, checks=checks)

                if not passed:
                    failed = [n for n, ok, _ in checks if not ok]
                    if failed == [gates.SIGNOFF_CHECK]:
                        # Everything the pipeline can decide has been decided.
                        # Waiting for a person is the design, not a failure.
                        awaiting = True
                        break
                    halt_reason = f"gate {stage}->{to_stage} failed: {gates.failures(checks)}"
                    break

            root.set_output({"reached": reached, "halted": bool(halt_reason)})
    except Exception as exc:
        finish_run(conn, run_id, "failed", reached, f"{type(exc).__name__}: {exc}")
        tracer.flush()
        raise
    finally:
        tracer.flush()

    status = "awaiting_signoff" if awaiting else ("halted" if halt_reason else "completed")
    finish_run(conn, run_id, status, reached, halt_reason)

    return {
        "run_id": run_id, "trace_id": tracer.trace_id, "status": status,
        "reached_stage": reached, "halt_reason": halt_reason,
        "awaiting_human_signoff": awaiting,
        "artifacts": {s: a.id for s, a in ctx.artifacts.items()},
        "mantis": {**mantis_status(), "active": tracer.mantis_active},
    }


def run_many(db_path: str | Path, goals: list[str], mode: str = "dry",
             workers: int = 4, model: str = "claude-opus-5") -> list[dict]:
    """Fan out across goals. One connection per thread — sqlite3 requires it."""
    results: queue.Queue = queue.Queue()
    work: queue.Queue = queue.Queue()
    for i, goal in enumerate(goals):
        work.put((i, goal))

    def worker():
        conn = db.connect(db_path)
        try:
            while True:
                try:
                    index, goal = work.get_nowait()
                except queue.Empty:
                    return
                try:
                    results.put((index, run_pipeline(conn, goal, mode, model)))
                except Exception as exc:
                    results.put((index, {"goal": goal, "status": "failed",
                                         "halt_reason": f"{type(exc).__name__}: {exc}"}))
                finally:
                    work.task_done()
        finally:
            conn.close()

    threads = [threading.Thread(target=worker, daemon=True)
               for _ in range(max(1, min(workers, len(goals))))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    out = []
    while not results.empty():
        out.append(results.get())
    return [r for _i, r in sorted(out, key=lambda p: p[0])]
