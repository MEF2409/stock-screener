"""Read-side helpers for the dashboard."""

from __future__ import annotations

from pathlib import Path

from stock_screener.data.db import get_connection


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
