"""Autonomous research pipeline: synthesis → hypothesis → analysis → in-silico → wet-lab.

Reasoning is traced to Mantis (`mantisdk.tracing` → Metis Insight) when it is
configured, and always to the run's own database, so a run is inspectable
whether or not a hosted backend is reachable.
"""

from .artifacts import STAGES  # noqa: F401
from .pipeline import run_many, run_pipeline  # noqa: F401
from .tracing import mantis_status  # noqa: F401
