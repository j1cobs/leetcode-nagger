# leetcode-nagger (Notion edition)

Notion-backed fork of [sandera0606/leetcode-nagger](https://github.com/sandera0606/leetcode-nagger).
A daily GitHub Actions cron reads a Blind 75 tracker and a study schedule
from two Notion databases, decides whether there's anything to nag about,
and posts to a Discord webhook at 7pm Eastern — same behavior as the
original, reading from Notion instead of a Google Sheet.

The Notion workspace side (the **LeetCode Nagger** page plus its two child
databases, pre-loaded with the Blind 75 list and a 17-week schedule) is
already set up. This repo is just the script and the workflow that runs it.

## How it works

Everything lives in a single file, `nag.py`:

- **Cold attempts** — a problem's `Cold ✓` date being set for today counts as today's new attempt. The script counts how many cold attempts landed in the current Notion Master Schedule week and compares against that week's `# New` target.
- **Spaced-repetition reviews** — a problem is flagged overdue if `Cold ✓` is set but `1wk Review` is empty and more than 7 days have passed, or if `1wk Review` is set but `3wk Review` is empty and more than 14 days have passed since. This check reads the Blind 75 Tracker directly and doesn't depend on the schedule at all, so it still works even past the schedule's last week.
- **Streak** — the first time a week's quota is hit, the streak increments and a congrats message posts to Discord; state is tracked in `state.json` and committed back by the workflow. A streak resets to 0 if a new week starts without the previous week's quota ever being met.
- **Sundays** are special-cased: instead of the quota nag, the bot posts every problem attempted so far as a "go re-read your notes" prompt.
- If `SCHEDULE_DATABASE_ID` is left unset, the script falls back to "nag once a day, no weekly cap" — the quota/streak logic is skipped entirely, but overdue-review nagging still works.

Notion API version is pinned to `2025-09-03`— every database ID gets
resolved to a `data_source_id` once at startup (`get_data_source_id()`),
since query endpoints live under `/v1/data_sources/{id}/query`, not the
older `/v1/databases/{id}/query`. Overdue-review and weekly-count filtering
happen server-side via the Notion API's `filter` object rather than by
pulling every row and walking it in Python.

## Repo layout

- `nag.py` — the whole script.
- `.github/workflows/nag.yml` — the cron (7pm Eastern, DST-safe via a same-day hour check) + `state.json` commit-back step. `workflow_dispatch` lets you trigger a run manually, bypassing the 7pm gate.
- `state.json` — `last_congratulated_week_start` / `last_week_seen` / `streak`. Committed back by the workflow after each run that changes it.
- `requirements.txt` — `requests`, `python-dotenv`, and `tzdata` (Windows only, via an environment marker — Windows has no system IANA timezone database for `zoneinfo` to read).
- `.env.example` — the 5 required env vars.
- `CLAUDE.md` — notes for AI coding agents working in this repo.

## Setup

### 1. Create a Notion internal integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations), create a new internal integration, copy its token.
2. Open the **LeetCode Nagger** page in Notion → **···** → **Connections** → add the integration. It inherits down to both child databases automatically, so you only need to do this once at the page level.

### 2. Get the database IDs

Open each database as a full page (not inline) and copy the ID out of the URL: `notion.so/<workspace>/<title>-<32-char-id>?v=...` — the 32-char id is what goes in `BLIND75_DATABASE_ID` / `SCHEDULE_DATABASE_ID`.

### 3. Create a Discord webhook

Create a webhook in the target channel (Channel Settings → Integrations → Webhooks) and copy its URL for `DISCORD_WEBHOOK_URL`. Optionally, with Developer Mode on, copy your Discord user ID for `DISCORD_USER_ID` to get @-pinged.

### 4. Fill in env vars

Copy `.env.example` to `.env`:

```
NOTION_TOKEN=            # from step 1
BLIND75_DATABASE_ID=     # from step 2
SCHEDULE_DATABASE_ID=    # from step 2 — leave blank for "nag once a day, no weekly cap"
DISCORD_WEBHOOK_URL=     # from step 3
DISCORD_USER_ID=         # optional, from step 3
```

### 5. Run it locally

```
python -m venv .venv
.venv\Scripts\activate     # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
python nag.py
```

Exits `0` if everything went fine (including "nothing needed nagging" — that's a quiet run with no Discord post), `1` if the Discord post itself failed. Check `state.json` afterward — a successful run rewrites it.

### 6. Push and add the GitHub Actions secrets

Add the same 5 values as repo secrets: Settings → Secrets and variables → Actions. Then Actions tab → **leetcode-nag** → **Run workflow** to test — the 7pm gate is skipped on manual `workflow_dispatch` triggers. Confirm the Discord post arrives and the workflow's last step commits an updated `state.json` if it changed.

## What's different from the Sheets version

- Reads two Notion **databases** instead of two tabs. Notion has no "tabs within one database" equivalent to sheet tabs.
- Uses typed `date` and `number` properties instead of parsed date/number strings — no more "scan past the dashboard to find the real header row."
- Overdue-review and weekly-count logic is pushed into the Notion API `filter` object (`is_empty`, `on_or_before`, etc.) instead of walked row-by-row in Python.
- Every query resolves a `data_source_id` from the `database_id` first — required as of Notion API version `2025-09-03`, which split "database" (the container) from "data source" (the actual rows). If you ever split a database into multiple data sources from the Notion UI, `get_data_source_id()` will raise — it assumes exactly one.
- `google-api-python-client` / `google-auth` replaced with plain `requests`.
- Master Schedule's `Dates` column is a native Notion date **range** property instead of a string like `"May 20-24"` that needs parsing.
