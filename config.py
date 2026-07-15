"""
config.py
---------
Central configuration module for CP Tracker.

All configuration is sourced from environment variables so the exact same
code runs locally (via a .env file) and inside GitHub Actions (via repo
secrets injected as env vars). Nothing here is hard-coded except sane
defaults and the difficulty-mapping tables, which are part of the product
specification rather than "secrets".
"""

import os
from pathlib import Path

# Load a local .env file when running outside GitHub Actions (no-op if
# python-dotenv is not installed or the file doesn't exist).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# --------------------------------------------------------------------------
# Base paths
# --------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
LOGS_DIR = BASE_DIR / "logs"
STORAGE_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

DB_PATH = os.getenv("DB_PATH", str(STORAGE_DIR / "tracker.db"))
LOG_FILE = LOGS_DIR / "tracker.log"

# --------------------------------------------------------------------------
# Platform usernames/handles
# --------------------------------------------------------------------------
LEETCODE_USERNAME = os.getenv("LEETCODE_USERNAME", "Reyhaan-S")
CODEFORCES_HANDLE = os.getenv("CODEFORCES_HANDLE", "Reyhaan")
ATCODER_USERNAME = os.getenv("ATCODER_USERNAME", "Reyhaan")

# --------------------------------------------------------------------------
# Google Sheets
# --------------------------------------------------------------------------
# The service account JSON can be supplied in two ways:
#   1. GOOGLE_SERVICE_ACCOUNT_JSON  -> raw JSON string (or base64-encoded)
#   2. GOOGLE_APPLICATION_CREDENTIALS -> path to a JSON key file on disk
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_APPLICATION_CREDENTIALS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

# The user's existing tracking spreadsheet. The tracker NEVER creates a new
# sheet, never clears anything, and never rewrites existing rows -- it only
# ever appends below the current data.
GOOGLE_SHEET_ID = os.getenv(
    "GOOGLE_SHEET_ID", "1VKV9kIzNWpXArqZXlg6xTK3OgvFiqumf9UCqlna2iJA"
)
GOOGLE_SHEET_NAME = os.getenv("GOOGLE_SHEET_NAME", "Sheet1")

SHEET_HEADERS = [
    "DATE",
    "PROGRAM TITLE",
    "LINK",
    "DIFFICULTY",
    "PLATFORM",
    "TOPIC",
    "COUNT",
]

# --------------------------------------------------------------------------
# Networking / retry behaviour
# --------------------------------------------------------------------------
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "20"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "4"))
RETRY_BACKOFF_BASE = float(os.getenv("RETRY_BACKOFF_BASE", "2"))  # seconds

# How many recent submissions to look at per platform per run. Since the
# workflow runs every 15 minutes this only needs to comfortably exceed the
# number of problems solved in the busiest 15-minute window, but a larger
# safety margin is used in case a run is missed/delayed.
LEETCODE_FETCH_LIMIT = int(os.getenv("LEETCODE_FETCH_LIMIT", "40"))
CODEFORCES_FETCH_COUNT = int(os.getenv("CODEFORCES_FETCH_COUNT", "50"))

# --------------------------------------------------------------------------
# Difficulty mapping
# --------------------------------------------------------------------------
def codeforces_difficulty(rating):
    """Map a Codeforces problem rating to Easy/Medium/Hard."""
    if rating is None:
        return "Unknown"
    if rating <= 1200:
        return "Easy"
    if rating <= 1800:
        return "Medium"
    return "Hard"


def atcoder_difficulty(rating):
    """Map an AtCoder Problems 'difficulty' value to Easy/Medium/Hard."""
    if rating is None:
        return "Unknown"
    if rating <= 800:
        return "Easy"
    if rating <= 1600:
        return "Medium"
    return "Hard"


# LeetCode already labels difficulty as Easy/Medium/Hard natively, so no
# mapping function is required for it.

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
