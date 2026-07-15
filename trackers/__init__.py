"""
trackers package
----------------
Shared HTTP plumbing used by all three platform trackers: a requests session
with a browser-like User-Agent, plus GET/POST helpers implementing bounded
exponential-backoff retries so transient API hiccups never crash a run.
"""

import logging
import time

import requests

import config

logger = logging.getLogger("cp-tracker.http")

_session = requests.Session()
_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 cp-tracker/1.0"
    ),
    "Accept": "application/json",
})

# HTTP statuses worth retrying (rate limits / transient server errors).
_RETRYABLE = {429, 500, 502, 503, 504}


def request_with_retry(method, url, **kwargs):
    """Perform an HTTP request with exponential backoff.

    Retries on connection errors, timeouts and retryable status codes.
    Raises the final exception (or HTTPError) if all attempts fail, so the
    caller can degrade gracefully per-platform.
    """
    kwargs.setdefault("timeout", config.REQUEST_TIMEOUT)
    last_exc = None

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = _session.request(method, url, **kwargs)
            if resp.status_code in _RETRYABLE:
                raise requests.HTTPError(
                    f"HTTP {resp.status_code} from {url}", response=resp
                )
            resp.raise_for_status()
            return resp
        except (requests.ConnectionError, requests.Timeout,
                requests.HTTPError) as exc:
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            # Don't retry hard client errors like 404 -- they won't heal.
            if status is not None and status not in _RETRYABLE:
                raise
            if attempt < config.MAX_RETRIES:
                delay = config.RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "Request to %s failed (attempt %d/%d): %s -- retrying in %.1fs",
                    url, attempt, config.MAX_RETRIES, exc, delay,
                )
                time.sleep(delay)

    logger.error("Request to %s failed after %d attempts: %s",
                 url, config.MAX_RETRIES, last_exc)
    raise last_exc


def get_json(url, **kwargs):
    return request_with_retry("GET", url, **kwargs).json()


def post_json(url, **kwargs):
    return request_with_retry("POST", url, **kwargs).json()
