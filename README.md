# leetcode-nagger

A Discord bot that reads your DSA study tracker from Notion and nags
you — daily, via GitHub Actions — until you actually do the work. Not a
scheduler. Not a dashboard. A small, opinionated accountability layer on
top of spaced repetition, built because good intentions alone don't clear
a 150-problem backlog.

Notion-backed fork of [sandera0606/leetcode-nagger](https://github.com/sandera0606/leetcode-nagger).

## Why this exists

Studying DSA on your own time with a loaded schedule has a predictable
failure mode: you solve a problem once, feel good about it, and never see
it again. Weeks later you can't reproduce it cold. Spaced repetition fixes
this in theory, but only if something actually makes you come back on
schedule, and only if the schedule bends when life doesn't cooperate.

This bot is that "something." It watches a Notion tracker, decides once a
day whether you're behind on new problems or overdue on a review, and
posts to Discord if so — silent otherwise. It's deliberately boring
infrastructure so the interesting part (spaced repetition + honest
tracking of what actually went wrong) can live in Notion where it's easy
to edit by hand.

Built and used by one person studying DSA around a full-time job and
part-time coursework — which is also why it has a built-in catch-up mode
instead of assuming a perfect, uninterrupted study cadence.

## How it works

Everything lives in one file, `nag.py`. It reads two required Notion
databases and one optional one, decides what (if anything) needs saying,
and posts a single Discord embed.

### The spaced-repetition ladder

Every problem you attempt gets tracked through three review checkpoints:
**Cold attempt → D+9 → D+30 → D+90.** Solve it cleanly (`Result = Yes`)
and it advances on that fixed schedule, retiring for good once D+90
passes clean. Struggle with it (`Result = Partial` or `No`) and instead of
waiting for the next milestone, it resets to a short retry — 4 days by
default — so the correction lands while it's still fresh, instead of a
month later.

### Catch-up mode

If your overdue-review count crosses a threshold (5 by default), the bot
stops nagging you about new problems and switches to a single "clear the
backlog first" message. The point is to prevent the exact spiral that
kills most self-directed study plans: falling a little behind, then
falling further behind trying to keep up with new material on top of an
already-overdue pile.

If you're using a Master Schedule, catch-up mode also **pauses the
schedule itself** — every day it's active, the plan's effective position
falls one more day behind real time, so "this week" doesn't march forward
on a calendar you can no longer keep up with. Once the backlog clears, the
schedule resumes advancing from wherever it paused, permanently shifted
back by however long the catch-up period lasted. This is tracked purely
as a day-count (`schedule_offset_days`) in the bot's own `state.json` —
the Master Schedule's dates in Notion are never rewritten, so the original
plan stays intact as a reference even as the bot's read of "now" drifts
from it.

### Weak-pattern priming

If you keep a `Weak Patterns` database (optional) tagging recurring
mistakes to the topic/pattern that causes them — "mixing recursion
paradigms" tagged to Backtracking, say — the bot checks each new problem's
`Pattern` against it and adds a one-line warning before you start. Catches
the mistake before it happens instead of diagnosing it after.

### Diagnostic recall

If a problem has a short `Last Snag` note (what went wrong last time), it
gets surfaced alongside the overdue-review nag — so a retry starts with
"here's what tripped you up," not a blank page and false confidence.

### Other behavior

- **Weekly quota** — if you're using a Master Schedule, the bot tracks
  cold attempts against that week's target and posts a (snarky) congrats
  embed the first time you hit it, with a streak counter across weeks.
- **Sundays** are special-cased: instead of the daily nag, it posts every
  problem you've ever attempted as a "go re-read your notes" prompt.
- **No Master Schedule?** Set `SCHEDULE_DATABASE_ID` blank and it falls
  back to "nag once a day if you haven't done a cold attempt, no weekly
  cap" — the review ladder and catch-up mode still work fully, since they
  read the tracker directly and don't depend on the schedule at all.

Notion API version is pinned to `2025-09-03` — every database ID gets
resolved to a `data_source_id` once at startup, since query endpoints live
under `/v1/data_sources/{id}/query`, not the older `/v1/databases/{id}/query`.
Filtering (overdue reviews, weekly counts) happens server-side via Notion's
`filter` API rather than pulling every row and walking it in Python.

## Repo layout

- `nag.py` — the whole script.
- `.github/workflows/nag.yml` — the daily cron (fires within a 5pm–11pm
  Eastern window to tolerate GitHub Actions scheduling delays, with a
  same-day dedup guard so a delayed run can't double-post) + a
  `state.json` commit-back step. `workflow_dispatch` lets you trigger a
  run manually, bypassing the time window.
- `state.json` — `last_congratulated_week_start` / `last_week_seen` /
  `streak` / `last_run_date` / `schedule_offset_days`. Committed back by
  the workflow after any run that changes it.
- `requirements.txt` — `requests`, `python-dotenv`, and `tzdata` (Windows
  only, via an environment marker — Windows has no system IANA timezone
  database for `zoneinfo` to read).
- `.env.example` — the required and optional env vars.
- `CLAUDE.md` — notes for AI coding agents working in this repo.

## Setup

### 1. Create your Notion databases

You need two databases, and can optionally add a third. Property names
must match exactly — `nag.py` reads them by name, not by position.

**Tracker** (one row per problem):

| Property | Type | Notes |
| --- | --- | --- |
| `Problem` | title | |
| `Difficulty` | select | e.g. Easy/Medium/Hard |
| `Pattern` | select | topic tag — used for weak-pattern priming |
| `Cold ✓` | date | set this the day you first attempt it |
| `Review D+9 ✓` / `Review D+30 ✓` / `Review D+90 ✓` | date | set each as you complete that review pass |
| `Result` | select | `Yes` / `Partial` / `No` — set after *each* attempt, including reviews. Drives the ladder branching |
| `Last Snag` | rich text | optional — a one-sentence note on what went wrong, shown on the next overdue nag |
| `Scheduled Date` | date | optional — only needed if you're using a Master Schedule with per-problem scheduling |

**Master Schedule** (optional — one row per study week):

| Property | Type | Notes |
| --- | --- | --- |
| `Week` | title | |
| `Dates` | date range | the week's Mon–Sun (or however you define a week) span |
| `New Problems` | relation → Tracker | this week's new problems |
| `Come Back To` | relation → Tracker | this week's scheduled reviews (the bot only ever nags about entries here that already have `Cold ✓` set — see note below) |
| `# New` | number or rollup | this week's new-problem target, for the quota/streak feature |

**Weak Patterns** (optional — one row per recurring mistake):

| Property | Type | Notes |
| --- | --- | --- |
| `Rule` | title | short name |
| `Applies To` | multi-select | which `Pattern` value(s) this warning fires for — values should match the Tracker's `Pattern` options |
| `Description` | rich text | the warning shown before a matching new problem |
| `Evidence` | rich text | optional — which past problems taught you this |

> **Why `Come Back To` is filtered:** a static study plan's "come back to
> this" list is written in advance and can reference problems you never
> actually got to (a skipped phase, a reordered week). The bot only ever
> surfaces a `Come Back To` entry if the linked problem has `Cold ✓` set —
> otherwise it's silently dropped from that week's nag. This makes the
> schedule self-correcting: it reflects what you actually did, not what
> the plan assumed you'd do.

### 2. Create a Notion internal integration

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations), create an internal integration, copy its token.
2. On the **parent page** containing your databases → **···** → **Connections** → add the integration. It inherits down to databases that already exist under that page — but a database you create *after* connecting won't automatically inherit it, so if you add one later, connect the integration to it directly too.

### 3. Get the database IDs

Open each database as a full page (not inline) and copy the ID out of the URL: `notion.so/<workspace>/<title>-<32-char-id>?v=...`.

### 4. Create a Discord webhook

Channel Settings → Integrations → Webhooks → copy the URL. Optionally grab your Discord user ID (Developer Mode on, right-click your name) to get @-pinged.

### 5. Fill in env vars

Copy `.env.example` to `.env`:

```
NOTION_TOKEN=                  # from step 2
BLIND75_DATABASE_ID=           # your Tracker database ID
SCHEDULE_DATABASE_ID=          # your Master Schedule ID — leave blank for "nag once a day, no weekly cap"
WEAK_PATTERNS_DATABASE_ID=     # optional — leave blank to skip pattern priming
DISCORD_WEBHOOK_URL=           # from step 4
DISCORD_USER_ID=               # optional, from step 4
```

(The `BLIND75_DATABASE_ID` name is a holdover from this project's origin — it just means "the Tracker.")

### 6. Run it locally

```
python -m venv .venv
.venv\Scripts\activate     # or `source .venv/bin/activate` on macOS/Linux
pip install -r requirements.txt
python nag.py
```

Exits `0` on success (including "nothing needed nagging" — a quiet run
with no Discord post), `1` if the Discord post itself failed. Check
`state.json` afterward — a successful run rewrites it.

### 7. Push and add the GitHub Actions secrets

Add the same env vars as repo secrets: Settings → Secrets and variables →
Actions. Then Actions tab → **leetcode-nag** → **Run workflow** to test —
`workflow_dispatch` bypasses the time-window gate. Confirm the Discord
post arrives and the workflow's last step commits an updated
`state.json` if it changed.

## A worked example: my own plan

To make the schema above concrete, here's what this repo's own Notion
workspace actually runs — the NeetCode 150, spread over 71 weeks and 5
phases (Sep 2026 → Dec 2027), designed around a full-time job and part-time
coursework rather than a full-time bootcamp pace:

- **Phase 0 — blocked practice** (skipped in my own run, since I started
  the tracker mid-plan; problems from this phase just sit in the Tracker
  as ordinary unattempted rows and surface later via interleaving).
  Backtracking and trees only, deliberately *not* mixed with other topics
  yet — the point of blocked practice is to build pattern recognition
  before interleaving forces you to also recall *which* pattern applies.
- **Phase 1 — interleaved fundamentals**: trees, arrays, two pointers,
  sliding window, stack — 2-3 new problems/week, mixed topics from here
  on, plus a 🎲 "mixed set" week roughly once a month where you redraw
  from everything seen so far with the pattern label hidden.
- **Phases 2-4** — binary search/linked lists/heaps/tries, then
  graphs/1-D DP/intervals, then 2-D DP/greedy/math/bit manipulation, at
  the same light pace, tapering into full mock-interview problems.

A single Master Schedule row looks like: `Week 6 (Phase 1)`,
`Sep 7–13 2026`, `New Problems` → *Binary Tree Level Order Traversal*,
*Binary Tree Right Side View*. A Tracker row for one of those:
`Problem: Binary Tree Right Side View`, `Difficulty: Medium`,
`Pattern: Trees`, `Scheduled Date: 2026-09-07`, everything else blank
until it's actually attempted.

`RETRY_INTERVAL_DAYS = 4` and `CATCHUP_THRESHOLD = 5` (top of `nag.py`)
are the two knobs tuned for this pace — a struggled problem gets retried
in 4 days rather than the more aggressive 2, and 5+ simultaneous overdue
reviews trips catch-up mode. Nothing else about `nag.py` assumes this
specific plan.

## Adapting this to your own study plan

- Swap in your own problem list and pacing — the bot only cares about the
  property names and types in the schema above, not how many rows there
  are or how they're grouped.
- Tune `RETRY_INTERVAL_DAYS` and `CATCHUP_THRESHOLD` to match your own
  tolerance for a busy week — shorter/lower for tighter accountability,
  longer/higher for more slack.
- Skip the Master Schedule entirely if you don't want weekly pacing — the
  review ladder and catch-up mode work standalone off the Tracker alone.

## What's different from the original Sheets version

- Reads Notion **databases** instead of Sheets tabs, with typed `date`/`number`/`select`/`relation` properties instead of parsed strings.
- Adds a full D+9/30/90 spaced-repetition ladder with result-based branching, instead of a single fixed review interval.
- Adds catch-up mode, weak-pattern priming, and diagnostic recall — none of which existed in the original.
- Overdue-review and weekly-count logic is pushed into the Notion API `filter` object (`is_empty`, `on_or_before`, etc.) instead of walked row-by-row in Python.
- Every query resolves a `data_source_id` from the `database_id` first — required as of Notion API version `2025-09-03`, which split "database" (the container) from "data source" (the actual rows).
- `google-api-python-client` / `google-auth` replaced with plain `requests`.
