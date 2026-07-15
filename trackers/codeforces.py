"""
trackers/codeforces.py
----------------------
Codeforces tracker.

API approach:
  Codeforces has an OFFICIAL, documented, keyless (for public data) REST API:
    https://codeforces.com/apiHelp

  We use:
    user.status?handle=<handle>&from=1&count=N
      -> the user's most recent submissions with full problem metadata
         (contestId, index, name, rating, tags) and verdict.

  Only verdict == "OK" submissions are considered. The problem `rating`
  drives the difficulty mapping (<=1200 Easy, 1201-1800 Medium, 1801+ Hard);
  unrated problems (very fresh contest problems, some gym problems) map to
  "Unknown" and can be fixed up manually in the sheet if desired.

Deduplication key: "<contestId>-<index>" (e.g. "1850-A"), which uniquely
identifies a Codeforces problem. Re-submitting an already-solved problem
therefore never produces a second row.
"""

import logging
from datetime import datetime, timezone

import config
from trackers import get_json

logger = logging.getLogger("cp-tracker.codeforces")

API_URL = "https://codeforces.com/api/user.status"


def _problem_link(problem):
    contest_id = problem.get("contestId")
    index = problem.get("index", "")
    if contest_id is None:
        # Problemset entries without contest ids are rare; fall back to the
        # problemset search page for the name.
        return "https://codeforces.com/problemset"
    # Gym contests have ids >= 100000 and live under /gym/.
    if contest_id >= 100000:
        return f"https://codeforces.com/gym/{contest_id}/problem/{index}"
    return f"https://codeforces.com/problemset/problem/{contest_id}/{index}"


def fetch_new_solves():
    """Return newly accepted Codeforces problems as dicts for the DB layer."""
    handle = config.CODEFORCES_HANDLE
    logger.info("Fetching recent submissions for Codeforces handle %s", handle)

    data = get_json(
        API_URL,
        params={"handle": handle, "from": 1,
                "count": config.CODEFORCES_FETCH_COUNT},
    )
    if data.get("status") != "OK":
        raise RuntimeError(
            f"Codeforces API returned status={data.get('status')} "
            f"comment={data.get('comment')}"
        )

    submissions = data.get("result") or []
    logger.info("Codeforces returned %d recent submissions", len(submissions))

    import database

    results = []
    seen_keys = set()

    # Oldest first for chronological COUNT ordering.
    for sub in sorted(submissions, key=lambda s: s.get("creationTimeSeconds", 0)):
        if sub.get("verdict") != "OK":
            continue

        problem = sub.get("problem") or {}
        contest_id = problem.get("contestId", "NA")
        index = problem.get("index", "")
        problem_key = f"{contest_id}-{index}"

        if problem_key in seen_keys:
            continue
        seen_keys.add(problem_key)

        if database.is_problem_recorded("Codeforces", problem_key):
            continue

        rating = problem.get("rating")  # may be absent for unrated problems
        difficulty = config.codeforces_difficulty(rating)
        topics = ", ".join(problem.get("tags") or [])
        solved_at = datetime.fromtimestamp(
            sub.get("creationTimeSeconds", 0), tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        results.append({
            "platform": "Codeforces",
            "problem_key": problem_key,
            "submission_id": sub.get("id", ""),
            "title": problem.get("name", problem_key),
            "link": _problem_link(problem),
            "difficulty": difficulty,
            "topics": topics,
            "solved_at_utc": solved_at,
        })

    logger.info("Codeforces: %d new problems detected", len(results))
    return results
