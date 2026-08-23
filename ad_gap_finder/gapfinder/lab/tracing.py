"""Tracing for the autonomous pipeline: Mantis when available, local always.

Every stage, tool call and gate decision becomes a span. Spans go to two places
at once:

* **Mantis** (`mantisdk.tracing` → Metis Insight), when the SDK is installed and
  `INSIGHT_HOST` / `INSIGHT_PUBLIC_KEY` / `INSIGHT_SECRET_KEY` are set. This is
  what makes reasoning inspectable across many runs rather than one terminal.
* **The run's own database**, always. A run whose reasoning is only legible when
  a hosted backend happens to be reachable is not a transparent system, so the
  local mirror is not a fallback — it is the record, and Mantis is the lens.

The import is guarded against `BaseException` on purpose. mantisdk pulls in
litellm, which pulls in native crypto bindings; a broken system `cryptography`
raises a pyo3 `PanicException`, which is not an `ImportError` and in one
environment tested here took the whole process down at import time. An
observability layer must never be the thing that kills the run it is observing.
"""

from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Any

_MANTIS = None
_MANTIS_ERROR: str | None = None

try:  # noqa: SIM105 - see module docstring for why this is BaseException
    import mantisdk.tracing as _mantis_tracing

    _MANTIS = _mantis_tracing
except BaseException as exc:  # pragma: no cover - depends on the host environment
    _MANTIS_ERROR = f"{type(exc).__name__}: {exc}"

_CURRENT_SPAN: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "gapfinder_span", default=None)

INSIGHT_ENV = ("INSIGHT_HOST", "INSIGHT_PUBLIC_KEY", "INSIGHT_SECRET_KEY")


def mantis_status() -> dict:
    """Why Mantis is or is not in use — surfaced by `cli.py lab` on every run."""
    if _MANTIS is None:
        return {"available": False, "reason": _MANTIS_ERROR or "mantisdk not installed",
                "configured": False}
    missing = [v for v in INSIGHT_ENV if not os.environ.get(v)]
    return {"available": True, "configured": not missing,
            "reason": None if not missing else f"unset: {', '.join(missing)}"}


class Span:
    """One unit of work. Writes to the local mirror and to Mantis together."""

    def __init__(self, tracer: "Tracer", span_id: str, parent: str | None,
                 name: str, kind: str, mantis_span: Any = None):
        self.tracer = tracer
        self.span_id = span_id
        self.parent = parent
        self.name = name
        self.kind = kind
        self._mantis = mantis_span
        self.attributes: dict[str, Any] = {}
        self.output: Any = None
        self.status = "ok"
        self.started_at = time.time()

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value
        if self._mantis is not None:
            try:
                self._mantis.set_attribute(key, _stringify(value))
            except Exception:  # pragma: no cover - never break the run for a trace
                pass

    def set_output(self, value: Any) -> None:
        self.output = value
        if self._mantis is not None and _MANTIS is not None:
            try:
                self._mantis.set_attribute(_MANTIS.semconv.TRACE_OUTPUT, _stringify(value))
            except Exception:  # pragma: no cover
                pass

    def fail(self, message: str) -> None:
        self.status = "error"
        self.set_attribute("error.message", message)


class Tracer:
    """Span factory bound to one pipeline run."""

    def __init__(self, conn: sqlite3.Connection, run_id: int, service: str = "ad_gap_finder",
                 environment: str = "dev", enable_mantis: bool = True):
        self.conn = conn
        self.run_id = run_id
        self.trace_id = uuid.uuid4().hex
        self.mantis_active = False

        status = mantis_status()
        if enable_mantis and status["available"] and status["configured"]:
            try:
                _MANTIS.init(trace_name=f"gapfinder-run-{run_id}", service_name=service,
                             environment=environment)
                self.mantis_active = True
            except Exception as exc:  # pragma: no cover - depends on credentials
                self.mantis_error = str(exc)

    @contextmanager
    def span(self, name: str, kind: str = "stage", input: Any = None,
             attributes: dict | None = None):
        span_id = uuid.uuid4().hex[:16]
        parent = _CURRENT_SPAN.get()

        mantis_cm = mantis_span = None
        if self.mantis_active and _MANTIS is not None:
            try:
                # `tool` spans carry the tool-call semantics Insight renders
                # differently from ordinary work, so pick the matching helper.
                factory = _MANTIS.tool if kind == "tool" else _MANTIS.span
                mantis_cm = factory(name, input=_stringify(input),
                                    attributes=_flatten(attributes or {}))
                mantis_span = mantis_cm.__enter__()
            except Exception:  # pragma: no cover
                mantis_cm = mantis_span = None

        span = Span(self, span_id, parent, name, kind, mantis_span)
        for k, v in (attributes or {}).items():
            span.attributes[k] = v
        token = _CURRENT_SPAN.set(span_id)
        try:
            yield span
        except Exception as exc:
            span.fail(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            _CURRENT_SPAN.reset(token)
            self._persist(span, input)
            if mantis_cm is not None:
                try:
                    mantis_cm.__exit__(None, None, None)
                except Exception:  # pragma: no cover
                    pass

    def _persist(self, span: Span, input_value: Any) -> None:
        self.conn.execute(
            "INSERT INTO trace_events (run_id, span_id, parent_span, name, kind, status, "
            "input, output, attributes, started_at, ended_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, span.span_id, span.parent, span.name, span.kind, span.status,
             _stringify(input_value), _stringify(span.output),
             json.dumps(_flatten(span.attributes)), span.started_at, time.time()))
        self.conn.commit()

    def flush(self) -> None:
        if self.mantis_active and _MANTIS is not None:
            try:
                _MANTIS.flush()
            except Exception:  # pragma: no cover
                pass


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)


def _flatten(attrs: dict) -> dict:
    """OTLP attribute values must be scalars or scalar sequences."""
    out = {}
    for k, v in attrs.items():
        out[k] = v if isinstance(v, (str, int, float, bool)) else _stringify(v)
    return out
