# leetcode-nagger (Notion edition)

Notion-backed fork of [sandera0606/leetcode-nagger](https://github.com/sandera0606/leetcode-nagger).
Same behavior — a daily GitHub Actions cron reads a tracker, decides whether
there's anything to nag about, and posts to a Discord webhook at 7pm Eastern
— reading from two Notion databases instead of two tabs in a Google Sheet.

The Notion workspace side (page + both databases, pre-loaded with the Blind
75 list) has already been set up — see the **LeetCode Nagger** page in
Notion. This folder is just the script.

## Setup

### 1. Create a Notion internal integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations), create a new internal integration, copy its token.
2. Open the **LeetCode Nagger** page in Notion → **···** → **Connections** → add the integration. It inherits down to both child databases automatically, so you only need to do this once at the page level.

### 2. Get the database IDs

Open each database as a full page (not inline) and copy the ID out of the URL: `notion.so/<workspace>/<title>-<32-char-id>?v=...` — the 32-char id is what goes in `BLIND75_DATABASE_ID` / `SCHEDULE_DATABASE_ID`.

### 3. Fill in env vars

Copy `.env.example` to `.env`:

```
NOTION_TOKEN=            # from step 1
BLIND75_DATABASE_ID=     # from step 2
SCHEDULE_DATABASE_ID=    # from step 2 — leave blank for "nag once a day, no weekly cap"
DISCORD_WEBHOOK_URL=
DISCORD_USER_ID=         # optional, makes the nag actually @-ping you
```

### 4. Run it locally

```
pip install -r requirements.txt
python nag.py
```

Exits 0 if everything went fine (or nothing needed nagging), 1 if the Discord post failed.

### 5. Push to GitHub and add the secrets

Same names as the env vars above, under Settings → Secrets and variables → Actions. Then Actions tab → **leetcode-nag** → Run workflow to test (the 7pm gate is skipped on manual triggers).

## What's different from the Sheets version

- Reads two Notion **databases** instead of two tabs. Notion has no "tabs within one database" equivalent to sheet tabs.
- Uses typed `date` and `number` properties instead of parsed date/number strings — no more "scan past the dashboard to find the real header row."
- Overdue-review and weekly-count logic is pushed into the Notion API `filter` object (`is_empty`, `on_or_before`, etc.) instead of walked row-by-row in Python.
- Every query resolves a `data_source_id` from the `database_id` first — required as of Notion API version `2025-09-03`, which split "database" (the container) from "data source" (the actual rows). Query endpoints live under `/v1/data_sources/{id}/query`, not `/v1/databases/{id}/query`. If you ever split a database into multiple data sources from the Notion UI, `get_data_source_id()` will raise — it assumes exactly one.
- `google-api-python-client` / `google-auth` replaced with plain `requests`.
- Master Schedule's `Dates` column is a native Notion date **range** property instead of a string like `"May 20-24"` that needs parsing.

## Files

- `nag.py` — everything, one file, same structure as the original.
- `.github/workflows/nag.yml` — the cron + 7pm-Eastern gate + `state.json` commit-back step.
- `state.json` — `last_congratulated_week_start` / `last_week_seen` / `streak`. Committed by the workflow.
- `requirements.txt`, `.env.example` — as above.
