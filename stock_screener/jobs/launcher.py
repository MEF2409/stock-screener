"""Launch a job as a detached subprocess.

The dashboard button calls launch_job() which spawns the script in a
new process and returns immediately. The recorder inside the script
writes the run row itself, so this file just fires and forgets.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from stock_screener.data.db import get_connection
from stock_screener.jobs.registry import JobDef, get_job

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def is_job_running(job_name: str) -> bool:
    """A job is 'running' if its latest row for that name has no
    finished_at set. Cheap check the panel uses to disable the button."""
    conn = get_connection()
    row = conn.execute(
        "SELECT status, finished_at FROM job_runs "
        "WHERE job_name = ? ORDER BY started_at DESC LIMIT 1",
        (job_name,),
    ).fetchone()
    conn.close()
    if not row:
        return False
    status, finished_at = row
    return status == "running" and finished_at is None


def launch_job(job_name: str, triggered_by: str = "manual") -> tuple[bool, str]:
    """Spawn the script for `job_name` in a detached subprocess.

    Returns (started, message). If a run is already in flight for the
    same job, refuses and returns (False, "already running").
    """
    if is_job_running(job_name):
        return False, "Already running — wait for the current run to finish."

    job = get_job(job_name)
    if job is None:
        return False, f"Unknown job: {job_name}"
    if not job.script.exists():
        return False, f"Script not found: {job.script}"

    env = os.environ.copy()
    env["JOB_TRIGGERED_BY"] = triggered_by
    # Ensure the child can `import stock_screener` regardless of cwd.
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")

    # Detach from the Streamlit process group so a Streamlit reload /
    # container restart doesn't kill the run. On POSIX we start a new
    # session; on Windows we set CREATE_NEW_PROCESS_GROUP.
    kwargs: dict = {
        "cwd": str(REPO_ROOT),
        "env": env,
        "stdout": subprocess.DEVNULL,   # the recorder tees to the log file
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:
        kwargs["creationflags"] = 0x00000200  # CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [sys.executable, str(job.script)],
        **kwargs,
    )
    return True, f"Launched {job.label} (pid {proc.pid})."
