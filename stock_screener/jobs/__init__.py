"""Job tracking + launching.

- `recorder.record_run` — context manager the batch scripts wrap their
  main() with. Writes a 'running' row on entry, updates to 'success' /
  'failed' on exit. Captures stdout/stderr to a rotating log file so
  the dashboard can tail recent output.

- `launcher.launch_job` — spawns one of the known scripts as a
  detached subprocess. Non-blocking so the Streamlit worker returns
  immediately.

- `JOBS` — canonical list of (name, script path, human label). One
  source of truth for both the runners and the dashboard panel.
"""

from stock_screener.jobs.registry import JOBS, JobDef
from stock_screener.jobs.recorder import record_run
from stock_screener.jobs.launcher import launch_job, is_job_running
from stock_screener.jobs.queries import (
    latest_run_per_job,
    recent_runs,
    read_log_tail,
)

__all__ = [
    "JOBS",
    "JobDef",
    "record_run",
    "launch_job",
    "is_job_running",
    "latest_run_per_job",
    "recent_runs",
    "read_log_tail",
]
