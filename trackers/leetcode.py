"""
trackers/leetcode.py
--------------------
LeetCode tracker.

API approach (LeetCode has no official public REST API):
  LeetCode's own website is powered by a GraphQL endpoint at
  https://leetcode.com/graphql which is publicly readable for profile data.
  We use two well-known queries:

    1. recentAcSubmissionList(username, limit)
         -> the user's most recent ACCEPTED submissions:
            id, title, titleSlug, timestamp
       This only returns accepted submissions, which is exactly what we want.

    2. question(titleSlug)
         -> per-problem metadata: difficulty + topicTags.

  Caveat: this endpoint is unofficial, so the module is written defensively —
  any schema change or block results in a logged per-platform failure, never
  a crash of the whole run, and the next successful run picks everything up
  again because deduplication is keyed on the problem slug.

Deduplication key: the problem's titleSlug (stable, unique per problem).
"""

import logging
from datetime import datetime, timezone

import config
from trackers import post_json, get_json

logger = logging.getLogger("cp-tracker.leetcode")

GRAPHQL_URL = "https://leetcode.com/graphql"

RECENT_AC_QUERY = """
query recentAcSubmissions($username: String!, $limit: Int!) {
  recentAcSubmissionList(username: $username, limit: $limit) {
    id
    title
    titleSlug
    timestamp
  }
}
"""

QUESTION_DETAIL_QUERY = """
query questionDetail($titleSlug: String!) {
  question(titleSlug: $titleSlug) {
    difficulty
    topicTags { name }
  }
}
"""

_HEADERS = {
    "Content-Type": "application/json",
    "Referer": "https://leetcode.com",
    "Origin": "https://leetcode.com",
}


def _graphql(query, variables):
    data = post_json(
        GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers=_HEADERS,
    )
    if "errors" in data:
        raise RuntimeError(f"LeetCode GraphQL error: {data['errors']}")
    return data.get("data") or {}


def _fetch_question_meta(title_slug):
    """Difficulty + topics for one problem. Failure here should not lose the
    submission, so callers get 'Unknown'/'' fallbacks on error."""
    try:
        q = _graphql(QUESTION_DETAIL_QUERY, {"titleSlug": title_slug}).get("question") or {}
        difficulty = q.get("difficulty") or "Unknown"
        topics = ", ".join(t["name"] for t in (q.get("topicTags") or []))
        return difficulty, topics
    except Exception as exc:
        logger.warning("Could not fetch metadata for %s: %s", title_slug, exc)
        return "Unknown", ""


def fetch_new_solves():
    """Return a list of newly solved LeetCode problems (dicts ready for the
    database layer). Never raises for per-item issues; raises only if the
    recent-submissions call itself is irrecoverable so main.py can mark the
    platform as failed for this run.
    """
    username = config.LEETCODE_USERNAME
    logger.info("Fetching recent accepted submissions for LeetCode user %s", username)

    data = _graphql(
        RECENT_AC_QUERY,
        {"username": username, "limit": config.LEETCODE_FETCH_LIMIT},
    )
    submissions = data.get("recentAcSubmissionList") or []
    logger.info("LeetCode returned %d recent accepted submissions", len(submissions))

    import database

    results = []
    seen_slugs = set()  # multiple ACs of the same problem within one batch

    # Oldest first so chronological COUNT ordering is preserved.
    for sub in sorted(submissions, key=lambda s: int(s.get("timestamp", 0))):
        slug = sub.get("titleSlug")
        if not slug or slug in seen_slugs:
            continue
        seen_slugs.add(slug)

        if database.is_problem_recorded("LeetCode", slug):
            continue

        difficulty, topics = _fetch_question_meta(slug)
        solved_at = datetime.fromtimestamp(
            int(sub.get("timestamp", 0)), tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        results.append({
            "platform": "LeetCode",
            "problem_key": slug,
            "submission_id": sub.get("id", ""),
            "title": sub.get("title", slug),
            "link": f"https://leetcode.com/problems/{slug}/",
            "difficulty": difficulty,
            "topics": topics,
            "solved_at_utc": solved_at,
        })

    logger.info("LeetCode: %d new problems detected", len(results))
    return results
