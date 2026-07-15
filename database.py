"""
database.py
-----------
SQLite persistence layer for CP Tracker.

Three tables:

  submissions     -- every solved problem ever recorded (the dedupe source of
                     truth). A UNIQUE constraint on (platform, problem_key)
                     makes duplicate insertion structurally impossible, on top
                     of the application-level checks.

  settings        -- simple key/value store (e.g. per-platform "last seen"
                     cursors so we don't refetch the whole history each run).

  execution_logs  -- one row per tracker run, with status, counts and any
                     error text, so every execution is auditable even after
                     the plain-text log files rotate away.

The database file itself lives in storage/tracker.db and is committed back to
the repository by the GitHub Actions workflow, which is what gives the system
memory between runs without any external server.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS submissions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,              -- LeetCode | Codeforces | AtCoder
    problem_key   TEXT NOT NULL,              -- stable per-problem identifier
    submission_id TEXT,                       -- platform submission id (info only)
    title         TEXT NOT NULL,
    link          TEXT NOT NULL,
    difficulty    TEXT NOT NULL,
    topics        TEXT DEFAULT '',
    solved_at_utc TEXT NOT NULL,              -- ISO-8601 UTC timestamp
    count_value   INTEGER,                    -- COUNT written to the sheet
    synced_to_sheet INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, problem_key)
);

CREATE INDEX IF NOT EXISTS idx_submissions_platform
    ON submissions (platform);
CREATE INDEX IF NOT EXISTS idx_submissions_unsynced
    ON submissions (synced_to_sheet);

CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS execution_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    status        TEXT NOT NULL DEFAULT 'running',  -- running|success|partial|failed
    new_problems  INTEGER NOT NULL DEFAULT 0,
    rows_appended INTEGER NOT NULL DEFAULT 0,
    details       TEXT DEFAULT ''
);
"""


def utc_now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@contextmanager
def get_conn():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# submissions
# ---------------------------------------------------------------------------
def is_problem_recorded(platform, problem_key):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM submissions WHERE platform = ? AND problem_key = ?",
            (platform, problem_key),
        ).fetchone()
        return row is not None


def record_submission(platform, problem_key, submission_id, title, link,
                      difficulty, topics, solved_at_utc):
    """Insert a solved problem. Returns True if inserted, False if it was
    already present (race-safe thanks to the UNIQUE constraint)."""
    with get_conn() as conn:
        try:
            conn.execute(
                """INSERT INTO submissions
                   (platform, problem_key, submission_id, title, link,
                    difficulty, topics, solved_at_utc)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (platform, problem_key, str(submission_id), title, link,
                 difficulty, topics, solved_at_utc),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_unsynced_submissions():
    """Problems recorded locally but not yet appended to the sheet, oldest
    first so COUNT values remain chronological."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM submissions
               WHERE synced_to_sheet = 0
               ORDER BY solved_at_utc ASC, id ASC"""
        ).fetchall()
        return [dict(r) for r in rows]


def mark_synced(submission_row_id, count_value):
    with get_conn() as conn:
        conn.execute(
            """UPDATE submissions
               SET synced_to_sheet = 1, count_value = ?
               WHERE id = ?""",
            (count_value, submission_row_id),
        )


# ---------------------------------------------------------------------------
# settings (key/value cursors)
# ---------------------------------------------------------------------------
def get_setting(key, default=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE
               SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, str(value), utc_now_iso()),
        )


# ---------------------------------------------------------------------------
# execution_logs
# ---------------------------------------------------------------------------
def start_execution_log():
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO execution_logs (started_at) VALUES (?)",
            (utc_now_iso(),),
        )
        return cur.lastrowid


def finish_execution_log(log_id, status, new_problems, rows_appended, details=""):
    with get_conn() as conn:
        conn.execute(
            """UPDATE execution_logs
               SET finished_at = ?, status = ?, new_problems = ?,
                   rows_appended = ?, details = ?
               WHERE id = ?""",
            (utc_now_iso(), status, new_problems, rows_appended,
             details[:4000], log_id),
        )
