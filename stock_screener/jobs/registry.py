"""Canonical registry of batch jobs.

Both the script wrappers and the dashboard panel read from here so a
new job only needs one edit.
"""

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@dataclass(frozen=True)
class JobDef:
    name: str          # stable key stored in job_runs.job_name
    label: str         # human-readable name for the dashboard
    script: Path       # absolute path to the script
    description: str   # one-line explanation shown in the panel


JOBS: list[JobDef] = [
    JobDef(
        name="daily_refresh",
        label="Daily Refresh",
        script=REPO_ROOT / "scripts" / "daily_refresh.py",
        description="Rebuilds the universe, refreshes 1yr OHLCV, runs all four EOD scanners.",
    ),
    JobDef(
        name="morning_fade",
        label="Morning Fade",
        script=REPO_ROOT / "scripts" / "morning_fade.py",
        description="9:45am ET fade scan — yesterday's runners fading at the open.",
    ),
    JobDef(
        name="morning_gap_scan",
        label="Morning Gap Scan",
        script=REPO_ROOT / "scripts" / "morning_gap_scan.py",
        description="Pre-open gap scan — stocks gapping >3% on volume.",
    ),
]


def get_job(name: str) -> JobDef | None:
    for j in JOBS:
        if j.name == name:
            return j
    return None
