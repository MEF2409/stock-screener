"""Context manager the batch scripts wrap their main() with.

Writes a 'running' row to job_runs on entry. On exit updates the row
to 'success' or 'failed' (with the traceback in message). Callers
shouldn't need to think about it — usage is:

    from stock_screener.jobs import record_run

    def main():
        ...

    if __name__ == "__main__":
        with record_run("daily_refresh"):
            main()
"""

from __future__ import annotations

import os
import sqlite3
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from stock_screener.data.db import get_connection, init_db

LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _log_path_for(job_name: str, run_id: int) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    return LOG_DIR / f"{job_name}_{stamp}_{run_id}.log"


class _Tee:
    """Duplicate writes to both the original stream and a log file so the
    script keeps printing to stdout AND we capture a full transcript for
    the dashboard tail view."""

    def __init__(self, original, log_file):
        self.original = original
        self.log_file = log_file

    def write(self, data):
        self.original.write(data)
        try:
            self.log_file.write(data)
            self.log_file.flush()
        except Exception:
            pass

    def flush(self):
        try:
            self.original.flush()
        except Exception:
            pass
        try:
            self.log_file.flush()
        except Exception:
            pass

    # Streamlit / third-party libs occasionally probe stream attrs.
    def __getattr__(self, item):
        return getattr(self.original, item)


@contextmanager
def record_run(job_name: str, triggered_by: str = "cron"):
    """Wrap a script's main() to record start/finish + capture logs."""
    init_db()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO job_runs(job_name, started_at, status, triggered_by, pid) "
        "VALUES (?, ?, 'running', ?, ?)",
        (job_name, _now(), triggered_by, os.getpid()),
    )
    run_id = cursor.lastrowid
    log_path = _log_path_for(job_name, run_id)
    cursor.execute(
        "UPDATE job_runs SET log_path = ? WHERE id = ?",
        (str(log_path), run_id),
    )
    conn.commit()
    conn.close()

    log_file = open(log_path, "w", buffering=1)  # line-buffered
    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    sys.stdout = _Tee(orig_stdout, log_file)
    sys.stderr = _Tee(orig_stderr, log_file)

    try:
        yield run_id
        status, message = "success", None
    except BaseException:
        status, message = "failed", traceback.format_exc()
        print(f"\n[job_runs] {job_name} failed:\n{message}", file=orig_stderr)
        # Fall through so the outer harness still sees the exit code
        raise
    finally:
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr
        log_file.close()
        try:
            conn = get_connection()
            conn.execute(
                "UPDATE job_runs SET finished_at = ?, status = ?, message = ? WHERE id = ?",
                (_now(), status, message, run_id),
            )
            conn.commit()
            conn.close()
        except sqlite3.Error:
            pass
