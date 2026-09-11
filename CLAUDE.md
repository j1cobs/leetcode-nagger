# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file Python script (`nag.py`) that reads a Notion problem Tracker
and (optionally) a Master Schedule and a Weak Patterns database, decides
whether there's anything to nag about today, and posts a message to a
Discord webhook. Runs daily via a GitHub Actions cron. Originally a port
of [sandera0606/leetcode-nagger](https://github.com/sandera0606/leetcode-nagger)
from Google Sheets to Notion, since extended with a D+9/30/90
spaced-repetition ladder, catch-up mode, weak-pattern priming, and
diagnostic recall. See README.md for the full user-facing picture.

## Commands

```
pip install -r requirements.txt   # requests, python-dotenv
python nag.py                     # run the nag check once
```

- Exits `0` if everything went fine (including "nothing to nag about"), `1` if the Discord post failed.
- No test suite, linter, or build step exists in this repo.
- Local runs need a `.env` (copy from `.env.example`): `NOTION_TOKEN`, `NEETCODE150_DATABASE_ID`, `DISCORD_WEBHOOK_URL` are required; `SCHEDULE_DATABASE_ID`, `WEAK_PATTERNS_DATABASE_ID`, `DISCORD_USER_ID` are optional.

## Architecture

Everything lives in `nag.py`. Key things to know before touching it:

- **Notion API version is pinned to `2025-09-03`**, which split "database" into a container + `data_source`(s). Query/write calls go to `/v1/data_sources/{id}/query`, not the old `/v1/databases/{id}/query`. `get_data_source_id()` resolves each `database_id` to its `data_source_id` once at startup and assumes exactly one data source per database, so don't split a database into multiple sources in the Notion UI, or that function will raise.
- **Spaced-repetition ladder**: `REVIEW_STAGES` defines the fixed D+9/D+30/D+90 offsets from a problem's `Cold ✓` date. `overdue_reviews()` walks each attempted problem's completed stages to find the last one, then branches on that stage's `Result`: `Yes` advances to the next fixed offset (retiring after D+90 passes with `Result = Yes`); `Partial`/`No` resets to a short `RETRY_INTERVAL_DAYS`-day retry instead of waiting for the next milestone. This is a **days-since-Cold✓** computation done in Python, not a Notion formula. The Tracker's own `Next Due` formula property (if present) is a display-only convenience and is never read by this script.
- **Catch-up mode** (`CATCHUP_THRESHOLD`): if `len(overdue) >= CATCHUP_THRESHOLD`, `main()` sets `catchup_mode = True`, which makes `build_nag_embed()` suppress the "new cold attempt pending" field (including weak-pattern warnings) and show a single "clear the backlog first" field instead. Overdue-reviews and "This week's reviews" fields are unaffected by catch-up mode.
- **Schedule offset (catch-up mode pauses the plan itself)**: `state["schedule_offset_days"]` increments by 1 on every run where `catchup_mode` is true. `main()` computes `effective_today = today - timedelta(days=offset)` and calls `current_week(schedule_ds_id, effective_today)` instead of real `today`. This is a purely internal reinterpretation, **never a write to the Master Schedule's own `Dates` in Notion**. Two correctness details depend on distinguishing the matched row's *stored* dates from the *real* calendar week you're actually in: (1) `cold_attempts_in_range()`'s window must use `stored_start/end + timedelta(days=offset)` (the real week you're living through), not the stored dates directly; (2) `week_key` (used for streak-reset detection) must stay keyed to `stored_start`, the plan week's own stable identity, not the real, offset-shifted dates, or a mid-week offset change would look like "a new week started" and incorrectly zero the streak. The offset only accumulates while `schedule_ds_id` is set (nothing to shift against otherwise); it never decreases. Once caught up, `effective_today` resumes advancing with real time, permanently lagging by however many days were spent in catch-up mode.
- **`Come Back To` is filtered to attempted problems only**: `main()` resolves the Master Schedule's `Come Back To` relation via `fetch_problem_info()`, then drops any linked problem that doesn't have `Cold ✓` set before it's shown as "This week's reviews." This is deliberate: a static schedule's `Come Back To` list can reference problems from an assumed history (e.g. a skipped study phase) that were never actually attempted, and nagging to "review" something never solved is a bug, not a feature.
- **Weak-pattern priming** (optional, gated on `WEAK_PATTERNS_DATABASE_ID`): `fetch_weak_patterns()` builds a `Pattern -> [Description, ...]` map from a database with `Rule`/`Applies To`/`Description`/`Evidence` properties. For each of this week's `New Problems`, if its `Pattern` (from the Tracker) matches a row's `Applies To`, that row's `Description` is appended as a "⚠ ..." line under the problem in the nag.
- **Diagnostic recall**: the Tracker's optional `Last Snag` rich-text property (a one-sentence note on what went wrong last time) is surfaced alongside a problem's entry in the overdue-reviews field, truncated to 100 chars.
- **Overdue-review and weekly-count filtering is server-side**: `overdue_reviews()`/`cold_attempts_in_range()` build Notion API `filter` objects (`is_empty`, `on_or_before`, etc.) rather than pulling every row and scanning in Python. `overdue_reviews()` doesn't depend on the Master Schedule at all; it queries the Tracker directly by date. Only the "new cold attempt pending" nag and the weekly quota/streak logic go through `current_week()` against the Master Schedule. `SCHEDULE_DATABASE_ID` is optional; if unset, the script falls back to "nag once a day, no weekly cap."
- **Streak state** lives in `state.json` (`last_congratulated_week_start`, `last_week_seen`, `streak`, `last_run_date`, `schedule_offset_days`), loaded/saved each run. The GitHub Actions workflow commits it back if it changed (`git diff --quiet -- state.json` guard), so don't add logic that expects `state.json` to be reset between runs. `last_run_date` exists purely for the workflow's own same-day dedup check (see below); `nag.py` itself never skips a run based on it.
- **Discord posting uses raw `urllib.request`**, not `requests`, with a custom `User-Agent`, since Discord 403s on the default urllib UA.
- **`main()` control flow**: resolve data sources → load state → compute `overdue`/`catchup_mode` from real `today` → update `schedule_offset_days` and compute `effective_today` → determine current week/quota + New Problems/Come Back To relations against `effective_today` → resolve problem info + weak-pattern warnings → reset streak if the prior plan week rolled over without being congratulated → compute `cold_done` (against the real, offset-shifted week window) → post congrats embed if quota was just hit → save state → build and post the daily nag embed. Sundays swap the quota/catch-up nag for a "re-read your notes" listing of every problem attempted so far.

## Workflow / deployment

- `.github/workflows/nag.yml` lives at the correct path for GitHub Actions to register it as a scheduled workflow.
- The cron has two lines (7pm EST and 7pm EDT in UTC), gated by a step that checks `TZ=America/New_York date +%H` against a **5pm-11pm Eastern tolerance window** (not an exact-hour match): GitHub Actions `schedule` triggers are best-effort and can fire an hour or more late, so an exact-hour gate was causing missed days. The same gate step also reads `last_run_date` from `state.json` and skips if it already equals today's Eastern date, preventing a double-post if both cron lines land inside the window on the same day. `workflow_dispatch` bypasses both checks for manual testing runs.
- Required GitHub Actions secrets mirror the `.env` vars: `NOTION_TOKEN`, `NEETCODE150_DATABASE_ID`, `DISCORD_WEBHOOK_URL` required; `SCHEDULE_DATABASE_ID`, `WEAK_PATTERNS_DATABASE_ID`, `DISCORD_USER_ID` optional.
