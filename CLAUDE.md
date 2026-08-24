# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python script (`nag.py`) that reads a Blind 75 problem tracker
and a weekly schedule from two Notion databases, decides whether there's
anything to nag about today, and posts a message to a Discord webhook. Runs
daily via a GitHub Actions cron. Port of
[sandera0606/leetcode-nagger](https://github.com/sandera0606/leetcode-nagger)
from Google Sheets to Notion.

## Commands

```
pip install -r requirements.txt   # requests, python-dotenv
python nag.py                     # run the nag check once
```

- Exits `0` if everything went fine (including "nothing to nag about"), `1` if the Discord post failed.
- No test suite, linter, or build step exists in this repo.
- Local runs need a `.env` (copy from `.env.example`) with all 5 vars filled in: `NOTION_TOKEN`, `BLIND75_DATABASE_ID`, `SCHEDULE_DATABASE_ID`, `DISCORD_WEBHOOK_URL`, `DISCORD_USER_ID`.

## Architecture

Everything lives in `nag.py`. Key things to know before touching it:

- **Notion API version is pinned to `2025-09-03`**, which split "database" into a container + `data_source`(s). Query/write calls go to `/v1/data_sources/{id}/query`, not the old `/v1/databases/{id}/query`. `get_data_source_id()` resolves each `database_id` to its `data_source_id` once at startup and assumes exactly one data source per database — don't split a database into multiple sources in the Notion UI, or that function will raise.
- **Overdue-review filtering is server-side**: `overdue_reviews()` builds Notion API `filter` objects (`is_empty`, `on_or_before`, etc.) rather than pulling every row and scanning in Python.
- **`overdue_reviews()` doesn't depend on the Master Schedule** — it queries the Blind 75 Tracker directly by date. Only the "new cold attempt pending" nag and the weekly quota/streak logic go through `current_week()` against the Master Schedule. `SCHEDULE_DATABASE_ID` is optional; if unset, the script falls back to "nag once a day, no weekly cap."
- **Streak state** lives in `state.json` (`last_congratulated_week_start`, `last_week_seen`, `streak`), loaded/saved each run. The GitHub Actions workflow commits it back if it changed (`git diff --quiet -- state.json` guard) — don't add logic that expects `state.json` to be reset between runs.
- **Discord posting uses raw `urllib.request`**, not `requests`, with a custom `User-Agent` — Discord 403s on the default urllib UA.
- **`main()` control flow**: resolve data sources → determine current week/quota → load state → reset streak if the prior week rolled over without being congratulated → compute `cold_done` / `did_cold_today` / `overdue` → post congrats embed if quota was just hit → save state → build and post the daily nag embed. Sundays swap the quota nag for a "re-read your notes" listing of every problem attempted so far.

## Workflow / deployment

- **`nag.yml` currently sits at the repo root, but needs to be at `.github/workflows/nag.yml`** for GitHub Actions to register it as a scheduled workflow — the README already documents the intended path, so this is a known pending move, not intentional.
- The cron has two lines (7pm EST and 7pm EDT in UTC) gated by a step that checks `TZ=America/New_York date +%H` and skips the run unless it's actually 19:00 Eastern — this handles DST without a smarter cron expression. `workflow_dispatch` bypasses that gate for manual testing runs.
- Required GitHub Actions secrets mirror the 5 `.env` vars exactly (`NOTION_TOKEN`, `BLIND75_DATABASE_ID`, `SCHEDULE_DATABASE_ID`, `DISCORD_WEBHOOK_URL`, `DISCORD_USER_ID`).
