# CP Tracker

Automatically tracks newly solved problems on **LeetCode**, **Codeforces** and **AtCoder** and appends them to an existing Google Sheet — running entirely on **GitHub Actions** every 15 minutes. No VPS, no cloud server, no always-on laptop.

Tracked accounts (configurable via env/secrets):

| Platform | Account |
|---|---|
| LeetCode | `Reyhaan-S` |
| Codeforces | `Reyhaan` |
| AtCoder | `Reyhaan` |

Target spreadsheet: `https://docs.google.com/spreadsheets/d/1VKV9kIzNWpXArqZXlg6xTK3OgvFiqumf9UCqlna2iJA`

The tracker is **strictly append-only**: it never creates a new sheet, never overwrites or edits existing rows, and continues the `COUNT` column from the last value already in the sheet (last row `COUNT = 74` → next new problem gets `75`).

---

## 1. Architecture

```
GitHub Actions (cron: every 15 min)
        │
        ▼
     main.py  ─── orchestrator: logging, execution log, exit codes
        │
        ├── trackers/leetcode.py    → LeetCode GraphQL (recentAcSubmissionList)
        ├── trackers/codeforces.py  → Official Codeforces REST API (user.status)
        ├── trackers/atcoder.py     → AtCoder Problems API (kenkoooo, v3)
        │
        ▼
   database.py (SQLite: storage/tracker.db)
        │   UNIQUE(platform, problem_key) = hard duplicate guard
        │   settings table = incremental fetch cursors
        │   execution_logs = one auditable row per run
        ▼
   sheets/google_sheets.py
        │   reads last COUNT + existing LINKs
        │   values.append(INSERT_ROWS) — append-only by API contract
        ▼
   Existing Google Sheet (historical data preserved)
        │
        ▼
   Workflow commits storage/tracker.db back to the repo
   (this commit IS the persistence layer between runs)
```

**Why commit the SQLite DB to git?** GitHub Actions runners are ephemeral — every run starts from a clean machine. Committing `storage/tracker.db` after each run gives the system durable memory (what's already processed, API cursors, execution history) with zero external infrastructure. The DB stays tiny (a few KB per hundred problems), well within reasonable repo size.

### Data flow per run

1. Fetch recent **accepted** submissions from each platform (failures isolated per platform).
2. Filter to problems not already in SQLite → insert them (`synced_to_sheet = 0`).
3. Read the sheet's `COUNT` column bottom-up → last numeric value = baseline.
4. Read the sheet's `LINK` column → skip anything already present (protects against pre-existing historical rows).
5. Append new rows with `COUNT = baseline + 1, +2, …` via `values.append`.
6. Mark rows synced **only after** the API confirms the append.
7. Close the execution log; workflow commits DB + logs.

Two independent duplicate barriers (SQLite UNIQUE constraint **and** sheet link check) mean a duplicate row is impossible even if one layer's state is lost.

---

## 2. Database schema

```sql
submissions (
  id INTEGER PK,
  platform TEXT,            -- LeetCode | Codeforces | AtCoder
  problem_key TEXT,         -- LC: titleSlug | CF: "contestId-index" | AC: problem_id
  submission_id TEXT,
  title TEXT, link TEXT, difficulty TEXT, topics TEXT,
  solved_at_utc TEXT,
  count_value INTEGER,      -- COUNT written to sheet
  synced_to_sheet INTEGER,  -- 0 until the append is confirmed
  created_at TEXT,
  UNIQUE (platform, problem_key)
)

settings (key PK, value, updated_at)          -- e.g. atcoder_from_second cursor
execution_logs (id PK, started_at, finished_at,
                status,                       -- running|success|partial|failed
                new_problems, rows_appended, details)
```

---

## 3. Platform API notes (and uncertainty disclosure)

| Platform | API | Status |
|---|---|---|
| **Codeforces** | `https://codeforces.com/api/user.status?handle=…` | **Official & documented.** Returns verdicts, ratings and tags. Rating → Easy (≤1200) / Medium (1201–1800) / Hard (1801+). Unrated problems → `Unknown`. |
| **LeetCode** | `https://leetcode.com/graphql` — `recentAcSubmissionList` + `question(titleSlug)` for difficulty/topicTags | **Unofficial** but stable for years and used by every LeetCode stats tool. If LeetCode changes the schema, that platform is marked failed for the run and everything recovers automatically once fixed — nothing is lost, because dedupe is keyed on the problem slug, not on cursors. `recentAcSubmissionList` returns ~the last 20 accepted submissions; at a 15-minute cadence this is far more than enough. |
| **AtCoder** | `https://kenkoooo.com/atcoder/atcoder-api/v3/user/submissions?user=…&from_second=…` + `problem-models.json` for difficulty | **Community-run (AtCoder Problems)** — the de-facto standard, since AtCoder has no official submissions API. Incremental `from_second` cursor stored in `settings`. Difficulty estimate → Easy (≤800) / Medium (801–1600) / Hard (1601+); problems without an estimate → `Unknown`. AtCoder publishes **no topic tags**, so TOPIC is blank for AtCoder rows (spec: "whenever available"). |

---

## 4. Google Cloud + Sheets setup (one-time, ~5 minutes)

### 4.1 Create the service account

1. Go to **https://console.cloud.google.com/** → create (or select) a project, e.g. `cp-tracker`.
2. **APIs & Services → Library** → search **Google Sheets API** → **Enable**.
3. **APIs & Services → Credentials → Create Credentials → Service account**.
   - Name: `cp-tracker-bot` → Create → skip the optional role screens → Done.
4. Open the new service account → **Keys** tab → **Add key → Create new key → JSON** → download. This file is your credential — treat it like a password and never commit it.

### 4.2 Share your existing sheet with the service account

1. Open the downloaded JSON and copy the `client_email` value
   (looks like `cp-tracker-bot@cp-tracker.iam.gserviceaccount.com`).
2. Open your spreadsheet →
   `https://docs.google.com/spreadsheets/d/1VKV9kIzNWpXArqZXlg6xTK3OgvFiqumf9UCqlna2iJA`
3. Click **Share** → paste the `client_email` → role **Editor** → uncheck "Notify" → **Share**.

Without this share step the API returns `403 PERMISSION_DENIED` — it's the most common setup mistake.

### 4.3 Check the tab name

`GOOGLE_SHEET_NAME` must match the worksheet tab containing your table (default `Sheet1`). If your tab is named e.g. `Tracker`, set the env var/secret accordingly.

### 4.4 Encode the key for storage as a secret

```bash
# Linux
base64 -w 0 service-account.json
# macOS
base64 -i service-account.json | tr -d '\n'
# Windows PowerShell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("service-account.json"))
```

Copy the single-line output — that's the value for `GOOGLE_SERVICE_ACCOUNT_JSON`. (Raw JSON also works; base64 is just safer against quoting/newline issues.)

---

## 5. Deployment (GitHub Actions)

1. Create a GitHub repository and push this project:

   ```bash
   git init
   git add .
   git commit -m "feat: CP tracker"
   git branch -M main
   git remote add origin https://github.com/<you>/cp-tracker.git
   git push -u origin main
   ```

2. **Repo → Settings → Secrets and variables → Actions → New repository secret**, add:

   | Secret | Value |
   |---|---|
   | `GOOGLE_SERVICE_ACCOUNT_JSON` | base64 string from step 4.4 |
   | `GOOGLE_SHEET_ID` | `1VKV9kIzNWpXArqZXlg6xTK3OgvFiqumf9UCqlna2iJA` |
   | `GOOGLE_SHEET_NAME` | your tab name (e.g. `Sheet1`) |
   | `LEETCODE_USERNAME` | `Reyhaan-S` |
   | `CODEFORCES_HANDLE` | `Reyhaan` |
   | `ATCODER_USERNAME` | `Reyhaan` |

3. **Repo → Settings → Actions → General → Workflow permissions** → select **Read and write permissions** (needed so the workflow can commit the DB).

4. Test immediately: **Actions tab → CP Tracker → Run workflow**. Check the run logs, then check the sheet.

5. Done — the cron trigger runs it every 15 minutes from now on.

> **First-run note:** the very first run will treat every recent accepted submission (last ~20 on LeetCode, last 50 on Codeforces, full history on AtCoder via the cursor starting at 0) as "new", *except* anything whose link already exists in your sheet — those are detected and skipped, so your historical rows are never duplicated. If you'd rather start completely fresh from "now", run it once locally, verify, and commit the resulting `tracker.db` before pushing.

> **Scheduling caveat:** GitHub cron is best-effort; runs can be delayed a few minutes, and repos with no pushes for 60 days have scheduled workflows auto-disabled (the bot's own DB commits keep the repo active, so this won't normally trigger).

---

## 6. Local run (optional)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in credentials
python main.py
```

---

## 7. Error handling strategy

| Failure | Behaviour |
|---|---|
| One platform API down | Logged, other platforms continue; run status `partial`; picked up next run. |
| All platforms down | Run status `failed`, exit code 1 (visible red X in Actions). |
| HTTP 429/5xx / timeout / connection error | Automatic retry with exponential backoff (4 attempts: 2s → 4s → 8s). Hard 4xx errors are not retried. |
| Sheets append fails | New solves stay in SQLite as `synced_to_sheet = 0` and are appended on the next successful run. Nothing is lost. |
| Append succeeds but process dies before `mark_synced` | Next run re-attempts, and the sheet **link check** detects the row is already present → marks it synced without re-appending. No duplicates. |
| Two workflow runs overlap | Prevented by the workflow-level `concurrency` group. |
| Per-problem metadata fetch fails (e.g. LC question detail) | Row is still recorded with `Unknown` difficulty / empty topics rather than dropped. |

## 8. Logging strategy

- **Console (stdout)** → visible live in the GitHub Actions run page.
- **Rotating file log** → `logs/tracker.log` (1 MB × 3 backups), committed with the DB and also uploaded as a per-run Actions artifact (14-day retention).
- **`execution_logs` table** → one structured row per run (start/end time, status, new-problem count, rows appended, error details) — a permanent, queryable audit trail:
  ```bash
  sqlite3 storage/tracker.db "SELECT * FROM execution_logs ORDER BY id DESC LIMIT 10;"
  ```

## 9. Project structure

```
cp-tracker/
├── main.py                      # orchestrator
├── config.py                    # env config + difficulty mappings
├── database.py                  # SQLite schema + data access
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
├── trackers/
│   ├── __init__.py              # shared HTTP session + retry/backoff
│   ├── leetcode.py
│   ├── codeforces.py
│   └── atcoder.py
├── sheets/
│   ├── __init__.py
│   └── google_sheets.py         # append-only Sheets client + COUNT logic
├── storage/
│   └── tracker.db               # SQLite state (committed by the workflow)
├── logs/                        # rotating logs (committed + artifacts)
└── .github/workflows/tracker.yml
```
