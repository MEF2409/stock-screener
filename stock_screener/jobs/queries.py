"""Read-side helpers for the dashboard."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from stock_screener.data.db import get_connection


# Per-job wall-clock ceilings. If a row has status='running' for
# longer than this, sweep_dead_runs will auto-mark it as failed with
# a 'timed out' message. Values are generous — the real observed
# runtimes are ~15 min for daily_refresh and ~3-5 min for the morning
# jobs. The extra headroom keeps a genuinely slow (but not dead) run
# from getting killed on the display.
_MAX_RUNTIME_MIN = {
    "daily_refresh": 45,
    "morning_fade": 20,
    "morning_gap_scan": 20,
}
_DEFAULT_MAX_MIN = 30


def _pid_alive(pid: int | None) -> bool:
    """True if the given pid is alive on this machine. False if the pid
    is gone or on a different machine (Fly restart wipes all pids)."""
    if not pid:
        return False
    try:
        os.kill(pid, 0)   # signal 0 = existence check, no-op
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def sweep_dead_runs() -> int:
    """Reconcile 'running' rows that are dead in reality:
      - pid gone (worker crashed or Fly restarted the machine)
      - wall-clock age exceeds per-job max runtime (job is hung on
        yfinance, an SSH tunnel, or an infinite loop)

    Marks such rows as 'failed' with an explanatory message. Called
    from the panel each render so the UI is always self-healing —
    the user shouldn't ever need to manually reset a stuck run.

    Returns the number of rows swept.
    """
    conn = get_connection()
    now = datetime.utcnow()
    swept = 0
    rows = conn.execute(
        "SELECT id, job_name, started_at, pid FROM job_runs WHERE status = 'running'"
    ).fetchall()
    for r in rows:
        run_id, job_name, started_at, pid = r["id"], r["job_name"], r["started_at"], r["pid"]
        try:
            started = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            continue
        age_min = (now - started).total_seconds() / 60
        max_min = _MAX_RUNTIME_MIN.get(job_name, _DEFAULT_MAX_MIN)

        reason: str | None = None
        if not _pid_alive(pid):
            reason = f"Process (pid {pid}) is gone — worker crashed or the machine restarted."
        elif age_min > max_min:
            reason = f"Timed out — running for {int(age_min)} min (cap {max_min})."

        if reason:
            conn.execute(
                "UPDATE job_runs SET status='failed', finished_at=?, message=? WHERE id=?",
                (now.strftime("%Y-%m-%d %H:%M:%S"), reason, run_id),
            )
            swept += 1
    if swept:
        conn.commit()
    conn.close()
    return swept


def reset_running_row(job_name: str) -> bool:
    """Force-mark the latest 'running' row for this job as failed.
    Used by the 'Reset' button when the user knows the run is stuck
    and doesn't want to wait for the auto-sweep threshold."""
    conn = get_connection()
    row = conn.execute(
        "SELECT id FROM job_runs WHERE job_name = ? AND status = 'running' "
        "ORDER BY started_at DESC LIMIT 1",
        (job_name,),
    ).fetchone()
    if row is None:
        conn.close()
        return False
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "UPDATE job_runs SET status='failed', finished_at=?, "
        "message='Manually reset from dashboard.' WHERE id=?",
        (now, row["id"]),
    )
    conn.commit()
    conn.close()
    return True


def latest_run_per_job() -> dict[str, dict]:
    """Return {job_name: latest_row_dict} — the newest row per job.

    Row keys: id, job_name, started_at, finished_at, status,
    triggered_by, message, log_path.
    """
    conn = get_connection()
    conn.row_factory = _dict_row
    rows = conn.execute("""
        SELECT jr.*
        FROM job_runs jr
        JOIN (
            SELECT job_name, MAX(started_at) AS max_started
            FROM job_runs
            GROUP BY job_name
        ) latest
          ON jr.job_name = latest.job_name
         AND jr.started_at = latest.max_started
    """).fetchall()
    conn.close()
    return {r["job_name"]: r for r in rows}


def recent_runs(job_name: str, limit: int = 10) -> list[dict]:
    conn = get_connection()
    conn.row_factory = _dict_row
    rows = conn.execute(
        "SELECT * FROM job_runs WHERE job_name = ? "
        "ORDER BY started_at DESC LIMIT ?",
        (job_name, limit),
    ).fetchall()
    conn.close()
    return rows


def read_log_tail(log_path: str | None, max_bytes: int = 8_000) -> str:
    """Read the last max_bytes of the log file. Empty string if missing."""
    if not log_path:
        return ""
    p = Path(log_path)
    if not p.exists():
        return ""
    size = p.stat().st_size
    with open(p, "r", errors="replace") as f:
        if size > max_bytes:
            f.seek(size - max_bytes)
            # Skip the first (likely partial) line
            f.readline()
        return f.read()


def _dict_row(cursor, row):
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}
