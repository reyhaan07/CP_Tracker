"""
main.py
-------
CP Tracker orchestrator. One invocation = one tracking cycle:

  1. Init DB + logging; open an execution_logs row.
  2. Fetch new accepted problems from LeetCode, Codeforces and AtCoder.
     Each platform is isolated: if one API is down, the other two still run
     (the run is marked 'partial' instead of 'success').
  3. Record new problems in SQLite (UNIQUE constraint = hard dedupe).
  4. Read the sheet's last COUNT value and existing links, then append only
     genuinely new rows with COUNT continuing from the sheet's last value.
  5. Mark synced rows in SQLite and close the execution log.

Sheet write model is strictly append-only — existing rows are never touched.

Exit codes: 0 on success/partial (so the scheduled workflow still commits the
updated DB), 1 only on total failure (all platforms failed or the DB/sheet
layer is broken).
"""

import logging
import sys
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

import config
import database


def setup_logging():
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root = logging.getLogger()
    root.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        config.LOG_FILE, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)


logger = logging.getLogger("cp-tracker.main")


def format_sheet_date(iso_utc):
    """ISO UTC timestamp -> DD/MM/YY as used in the sheet (e.g. 14/07/26)."""
    dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )
    return dt.strftime("%d/%m/%y")


def collect_new_problems():
    """Run all three platform trackers, isolating failures per platform.

    Returns (new_problem_dicts, failed_platform_names)."""
    from trackers import leetcode, codeforces, atcoder

    platforms = [
        ("LeetCode", leetcode.fetch_new_solves),
        ("Codeforces", codeforces.fetch_new_solves),
        ("AtCoder", atcoder.fetch_new_solves),
    ]

    new_problems = []
    failures = []
    for name, fetcher in platforms:
        try:
            new_problems.extend(fetcher())
        except Exception as exc:
            logger.error("%s tracker failed: %s", name, exc, exc_info=True)
            failures.append(name)
    return new_problems, failures


def persist_new_problems(problems):
    """Insert into SQLite; the UNIQUE constraint silently drops anything that
    slipped past the earlier checks. Returns count actually inserted."""
    inserted = 0
    for p in problems:
        if database.record_submission(
            platform=p["platform"],
            problem_key=p["problem_key"],
            submission_id=p["submission_id"],
            title=p["title"],
            link=p["link"],
            difficulty=p["difficulty"],
            topics=p["topics"],
            solved_at_utc=p["solved_at_utc"],
        ):
            inserted += 1
            logger.info("NEW  [%s] %s (%s)",
                        p["platform"], p["title"], p["difficulty"])
    return inserted


def sync_to_sheet():
    """Append all unsynced DB rows to the sheet, continuing COUNT from the
    sheet's own last value. Returns number of rows appended."""
    pending = database.get_unsynced_submissions()
    if not pending:
        logger.info("Nothing to sync to the sheet")
        return 0

    from sheets.google_sheets import SheetClient
    client = SheetClient()

    last_count = client.get_last_count()
    existing_links = client.get_existing_links()
    logger.info("Sheet state: last COUNT = %d, %d existing links",
                last_count, len(existing_links))

    rows, row_meta = [], []
    next_count = last_count
    for sub in pending:
        link_norm = sub["link"].strip().rstrip("/")
        if link_norm in existing_links:
            # Already in the sheet (e.g. pre-tracker historical entry).
            # Mark synced without appending — never duplicate a row.
            logger.info("Skipping already-present row: %s", sub["title"])
            database.mark_synced(sub["id"], None)
            continue

        next_count += 1
        rows.append([
            format_sheet_date(sub["solved_at_utc"]),  # DATE
            sub["title"],                              # PROGRAM TITLE
            sub["link"],                               # LINK
            sub["difficulty"],                         # DIFFICULTY
            sub["platform"],                           # PLATFORM
            sub["topics"],                             # TOPIC
            next_count,                                # COUNT (continues)
        ])
        row_meta.append((sub["id"], next_count))

    if not rows:
        return 0

    appended = client.append_rows(rows)

    # Only mark rows synced after the API confirms the append, so a failed
    # append is retried on the next run instead of being lost.
    for row_id, count_value in row_meta:
        database.mark_synced(row_id, count_value)

    return appended


def main():
    setup_logging()
    database.init_db()

    log_id = database.start_execution_log()
    logger.info("=" * 60)
    logger.info("CP Tracker run started (execution log #%d)", log_id)

    status = "success"
    details = []
    new_count = 0
    appended = 0

    try:
        problems, failures = collect_new_problems()
        new_count = persist_new_problems(problems)

        if failures:
            status = "partial"
            details.append(f"Platform failures: {', '.join(failures)}")
            if len(failures) == 3:
                status = "failed"

        try:
            appended = sync_to_sheet()
        except Exception as exc:
            # New solves are safely stored in SQLite and will sync next run.
            logger.error("Sheet sync failed: %s", exc, exc_info=True)
            status = "partial" if status == "success" else status
            details.append(f"Sheet sync error: {exc}")

    except Exception as exc:  # truly unexpected
        status = "failed"
        details.append(f"Fatal: {exc}")
        logger.critical("Fatal error: %s", exc, exc_info=True)

    database.finish_execution_log(
        log_id, status, new_count, appended, "; ".join(details)
    )
    logger.info(
        "Run finished: status=%s new_problems=%d rows_appended=%d",
        status, new_count, appended,
    )
    logger.info("=" * 60)

    return 0 if status in ("success", "partial") else 1


if __name__ == "__main__":
    sys.exit(main())
