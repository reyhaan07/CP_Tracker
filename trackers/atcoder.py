"""
trackers/atcoder.py
-------------------
AtCoder tracker.

API approach:
  AtCoder itself has no official public submissions API. The de-facto
  standard (used by virtually every AtCoder tooling project) is the
  community-run **AtCoder Problems** API by kenkoooo:

    Submissions (incremental, official recommended usage):
      https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions
          ?user=<user>&from_second=<unix_ts>
      -> up to 500 submissions with epoch_second >= from_second, ascending.
         We persist a `from_second` cursor in the settings table so each run
         only fetches what's new (the cursor is re-fetched from the last
         processed timestamp + 1).

    Problem difficulty estimates:
      https://kenkoooo.com/atcoder/resources/problem-models.json
      -> { problem_id: { difficulty: int, ... } }
         These are the community difficulty estimates shown on AtCoder
         Problems. Mapping: <=800 Easy, 801-1600 Medium, 1601+ Hard.
         Problems without an estimate map to "Unknown".

  Topic tags: AtCoder does not publish problem tags/topics, so TOPIC is left
  blank for AtCoder rows (documented limitation; the spec says "whenever
  available").

Deduplication key: AtCoder problem_id (e.g. "abc300_c"), stable per problem.
"""

import logging
from datetime import datetime, timezone

import config
import database
from trackers import get_json

logger = logging.getLogger("cp-tracker.atcoder")

SUBMISSIONS_URL = "https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions"
PROBLEM_MODELS_URL = "https://kenkoooo.com/atcoder/resources/problem-models.json"

CURSOR_KEY = "atcoder_from_second"

# The v3 endpoint returns at most this many submissions per call.
PAGE_LIMIT = 500

_problem_models_cache = None


def _get_problem_models():
    """Lazy-load and cache the (large) difficulty-model JSON once per run."""
    global _problem_models_cache
    if _problem_models_cache is None:
        try:
            _problem_models_cache = get_json(PROBLEM_MODELS_URL)
            logger.info("Loaded %d AtCoder problem difficulty models",
                        len(_problem_models_cache))
        except Exception as exc:
            logger.warning("Could not load AtCoder problem models: %s", exc)
            _problem_models_cache = {}
    return _problem_models_cache


def _difficulty_for(problem_id):
    model = _get_problem_models().get(problem_id) or {}
    return config.atcoder_difficulty(model.get("difficulty"))


def _title_for(problem_id):
    """Derive a readable title from the problem id, e.g. 'abc300_c' -> 'ABC300 C'.
    (Fetching resources/problems.json for exact titles would add a ~40MB
    download per run; ids are unambiguous and human-readable enough. If exact
    titles are desired, swap this for a lookup against that resource.)"""
    if "_" in problem_id:
        contest_part, _, task_part = problem_id.rpartition("_")
        return f"{contest_part.upper()} {task_part.upper()}"
    return problem_id


def fetch_new_solves():
    """Return newly accepted AtCoder problems as dicts for the DB layer."""
    user = config.ATCODER_USERNAME
    from_second = int(database.get_setting(CURSOR_KEY, "0"))
    logger.info("Fetching AtCoder submissions for %s from_second=%d",
                user, from_second)

    results = []
    seen_ids = set()
    max_epoch = from_second

    while True:
        batch = get_json(
            SUBMISSIONS_URL,
            params={"user": user, "from_second": from_second},
        )
        if not isinstance(batch, list):
            raise RuntimeError(f"Unexpected AtCoder API response: {batch!r:.200}")

        logger.info("AtCoder returned %d submissions in this page", len(batch))

        for sub in batch:  # already ascending by epoch_second
            epoch = int(sub.get("epoch_second", 0))
            max_epoch = max(max_epoch, epoch)

            if sub.get("result") != "AC":
                continue

            problem_id = sub.get("problem_id")
            if not problem_id or problem_id in seen_ids:
                continue
            seen_ids.add(problem_id)

            if database.is_problem_recorded("AtCoder", problem_id):
                continue

            contest_id = sub.get("contest_id", "")
            solved_at = datetime.fromtimestamp(
                epoch, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

            results.append({
                "platform": "AtCoder",
                "problem_key": problem_id,
                "submission_id": sub.get("id", ""),
                "title": _title_for(problem_id),
                "link": f"https://atcoder.jp/contests/{contest_id}/tasks/{problem_id}",
                "difficulty": _difficulty_for(problem_id),
                "topics": "",  # AtCoder does not expose topic tags
                "solved_at_utc": solved_at,
            })

        # Fewer than a full page -> we've reached the end of history.
        if len(batch) < PAGE_LIMIT:
            break
        from_second = max_epoch + 1

    # Advance the cursor only after a fully successful fetch so a failed run
    # can never skip submissions.
    database.set_setting(CURSOR_KEY, max_epoch + 1 if max_epoch else 0)

    logger.info("AtCoder: %d new problems detected", len(results))
    return results
