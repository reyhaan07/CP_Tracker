"""
sheets/google_sheets.py
-----------------------
Google Sheets integration (Service Account, official Sheets API v4 via
google-api-python-client).

DESIGN GUARANTEES (matching the requirement to protect existing data):

  * APPEND-ONLY. The only write ever issued is
    spreadsheets.values.append(..., insertDataOption="INSERT_ROWS"), which
    the Sheets API defines as "insert new rows after the table"; it is
    physically incapable of overwriting or editing existing cells.
    No update / clear / batchUpdate calls exist anywhere in this module.

  * COUNT CONTINUATION. Before appending, the existing COUNT column (G) is
    read and the last non-empty numeric value found becomes the baseline;
    the first new row gets baseline + 1, the next baseline + 2, and so on.
    If the sheet's history ends at COUNT = 74, the next problem is 75 —
    automatically, with no manual configuration.

  * The tracker never creates a spreadsheet and never touches any sheet/tab
    other than the configured one.

Credential loading supports (in priority order):
  1. GOOGLE_SERVICE_ACCOUNT_JSON  -- raw JSON string OR base64-encoded JSON
     (base64 is the most robust way to store a multi-line key in a GitHub
     secret).
  2. GOOGLE_APPLICATION_CREDENTIALS -- path to a key file on disk.
"""

import base64
import json
import logging

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config

logger = logging.getLogger("cp-tracker.sheets")

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# COUNT is column G (7th column) per the required layout:
# DATE | PROGRAM TITLE | LINK | DIFFICULTY | PLATFORM | TOPIC | COUNT
COUNT_COLUMN = "G"


def _load_credentials():
    raw = config.GOOGLE_SERVICE_ACCOUNT_JSON
    if raw:
        raw = raw.strip()
        if not raw.startswith("{"):
            # Assume base64-encoded JSON.
            try:
                raw = base64.b64decode(raw).decode("utf-8")
            except Exception as exc:
                raise RuntimeError(
                    "GOOGLE_SERVICE_ACCOUNT_JSON is neither valid JSON nor "
                    "valid base64-encoded JSON"
                ) from exc
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    if config.GOOGLE_APPLICATION_CREDENTIALS:
        return Credentials.from_service_account_file(
            config.GOOGLE_APPLICATION_CREDENTIALS, scopes=SCOPES
        )

    raise RuntimeError(
        "No Google credentials configured. Set GOOGLE_SERVICE_ACCOUNT_JSON "
        "or GOOGLE_APPLICATION_CREDENTIALS."
    )


class SheetClient:
    def __init__(self):
        if not config.GOOGLE_SHEET_ID:
            raise RuntimeError("GOOGLE_SHEET_ID is not configured")
        creds = _load_credentials()
        # cache_discovery=False avoids noisy warnings in ephemeral CI runners.
        self._service = build("sheets", "v4", credentials=creds,
                              cache_discovery=False)
        self._sheet_id = config.GOOGLE_SHEET_ID
        self._tab = config.GOOGLE_SHEET_NAME

    # -- reads ------------------------------------------------------------
    def get_last_count(self):
        """Read column G and return the last numeric COUNT value found.

        Robust to a header row, blank cells and stray non-numeric text: it
        scans bottom-up for the last cell that parses as an integer. Returns
        0 if the column has no numeric values yet (fresh table)."""
        rng = f"{self._tab}!{COUNT_COLUMN}:{COUNT_COLUMN}"
        result = self._service.spreadsheets().values().get(
            spreadsheetId=self._sheet_id, range=rng
        ).execute()
        values = result.get("values", [])

        for row in reversed(values):
            if not row:
                continue
            cell = str(row[0]).strip().replace(",", "")
            try:
                return int(float(cell))
            except ValueError:
                continue
        return 0

    def get_existing_links(self):
        """Read column C (LINK) so rows already present in the sheet — e.g.
        historical entries added before this tracker existed — are never
        duplicated even if the local database has no record of them."""
        rng = f"{self._tab}!C:C"
        result = self._service.spreadsheets().values().get(
            spreadsheetId=self._sheet_id, range=rng
        ).execute()
        links = set()
        for row in result.get("values", []):
            if row and row[0]:
                links.add(str(row[0]).strip().rstrip("/"))
        return links

    # -- writes (append-only) ---------------------------------------------
    def append_rows(self, rows):
        """Append rows below the existing table. Never overwrites anything.

        `rows` is a list of 7-element lists matching the sheet's columns.
        Returns the number of rows the API reports as appended."""
        if not rows:
            return 0

        body = {"values": rows}
        try:
            result = self._service.spreadsheets().values().append(
                spreadsheetId=self._sheet_id,
                range=f"{self._tab}!A:G",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",   # append-only semantics
                body=body,
            ).execute()
        except HttpError as exc:
            logger.error("Sheets append failed: %s", exc)
            raise

        updated = result.get("updates", {}).get("updatedRows", 0)
        logger.info("Appended %d row(s) to Google Sheet", updated)
        return updated
