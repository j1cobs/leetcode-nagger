#!/usr/bin/env python3
"""
leetcode-nagger (Notion edition)

Reads a Blind 75 Tracker and a Master Schedule from Notion, decides whether
there's anything to nag about today, and posts to a Discord webhook.

Port of https://github.com/sandera0606/leetcode-nagger from Google Sheets to
the Notion API. Notion split "database" into a container + data source(s) as
of API version 2025-09-03 — query endpoints live under /v1/data_sources, not
/v1/databases, so every database ID gets resolved to a data_source_id once
at startup. See: https://developers.notion.com/docs/upgrade-faqs-2025-09-03
"""

import json
import os
import random
import sys
import urllib.request
import urllib.error
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

NOTION_TOKEN = os.environ["NOTION_TOKEN"]
BLIND75_DATABASE_ID = os.environ["BLIND75_DATABASE_ID"]
SCHEDULE_DATABASE_ID = os.environ.get("SCHEDULE_DATABASE_ID")  # optional, like original SCHEDULE_TAB
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]
DISCORD_USER_ID = os.environ.get("DISCORD_USER_ID")

NOTION_VERSION = "2025-09-03"
STATE_PATH = Path(__file__).resolve().parent / "state.json"
TZ = ZoneInfo("America/New_York")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# Notion access
# ---------------------------------------------------------------------------

def get_data_source_id(database_id: str) -> str:
    """Resolve a database's data_source_id. Required as of API 2025-09-03 —
    the old flat /v1/databases/{id}/query endpoint no longer accepts a
    database ID directly."""
    r = requests.get(f"https://api.notion.com/v1/databases/{database_id}", headers=HEADERS, timeout=15)
    r.raise_for_status()
    data_sources = r.json()["data_sources"]
    if len(data_sources) != 1:
        raise RuntimeError(
            f"Database {database_id} has {len(data_sources)} data sources; "
            "this script assumes exactly one (don't split it into multiple "
            "sources in the Notion UI)."
        )
    return data_sources[0]["id"]


def query_data_source(data_source_id: str, filter_obj: dict | None = None) -> list[dict]:
    results, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if filter_obj:
            payload["filter"] = filter_obj
        if cursor:
            payload["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/data_sources/{data_source_id}/query",
            headers=HEADERS, json=payload, timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        results.extend(data["results"])
        if not data.get("has_more"):
            return results
        cursor = data["next_cursor"]


def prop_date(page: dict, name: str) -> date | None:
    d = page["properties"].get(name, {}).get("date")
    if not d or not d.get("start"):
        return None
    return date.fromisoformat(d["start"][:10])


def prop_number(page: dict, name: str) -> float | None:
    return page["properties"].get(name, {}).get("number")


def prop_title(page: dict, name: str) -> str:
    parts = page["properties"].get(name, {}).get("title", [])
    return "".join(p.get("plain_text", "") for p in parts) or "Untitled"


def prop_select(page: dict, name: str) -> str | None:
    sel = page["properties"].get(name, {}).get("select")
    return sel["name"] if sel else None


def prop_relation_ids(page: dict, name: str) -> list[str]:
    return [r["id"] for r in page["properties"].get(name, {}).get("relation", [])]


def prop_rollup_number(page: dict, name: str) -> float | None:
    return page["properties"].get(name, {}).get("rollup", {}).get("number")


def fetch_page_title_map(ds_id: str) -> dict[str, str]:
    """Maps Tracker page id -> Problem title, so a Master Schedule week's
    New Problems / Come Back To relations can be resolved to names."""
    return {p["id"]: prop_title(p, "Problem") for p in query_data_source(ds_id)}


# ---------------------------------------------------------------------------
# Schedule
# ---------------------------------------------------------------------------

def current_week(schedule_ds_id: str | None, today: date):
    """Returns (week_start, week_end, target, new_problem_ids, come_back_ids)
    for the week containing `today`, or (None, None, None, [], []) if there's
    no Master Schedule / no matching week. `target` reads the "# New" rollup
    if present, falling back to a plain number for older schema versions."""
    if not schedule_ds_id:
        return None, None, None, [], []
    for page in query_data_source(schedule_ds_id):
        d = page["properties"].get("Dates", {}).get("date")
        if not d or not d.get("start") or not d.get("end"):
            continue
        start = date.fromisoformat(d["start"][:10])
        end = date.fromisoformat(d["end"][:10])
        if start <= today <= end:
            target = prop_rollup_number(page, "# New")
            if target is None:
                target = prop_number(page, "# New")
            new_ids = prop_relation_ids(page, "New Problems")
            come_back_ids = prop_relation_ids(page, "Come Back To")
            return start, end, int(target) if target is not None else None, new_ids, come_back_ids
    return None, None, None, [], []


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

def cold_attempts_in_range(blind75_ds_id: str, start: date, end: date) -> int:
    filter_obj = {
        "and": [
            {"property": "Cold ✓", "date": {"on_or_after": start.isoformat()}},
            {"property": "Cold ✓", "date": {"on_or_before": end.isoformat()}},
        ]
    }
    return len(query_data_source(blind75_ds_id, filter_obj))


# Spaced-repetition ladder: each stage's Notion property, and its offset in
# days from the original Cold ✓ attempt (not from the previous stage).
REVIEW_STAGES = [
    ("Review D+2 ✓", 2),
    ("Review D+9 ✓", 9),
    ("Review D+30 ✓", 30),
    ("Review D+90 ✓", 90),
]


def overdue_reviews(blind75_ds_id: str, today: date) -> list[dict]:
    """Walks each attempted problem's ladder (Cold ✓ -> D+2 -> D+9 -> D+30 ->
    D+90) to find whether its next review stage is due.

    A stage's due date is normally Cold ✓ + that stage's fixed offset. But if
    Result on the most recently completed stage was "Partial" or "No" (a hint
    was needed, or it wasn't solved), the ladder doesn't advance to the next
    fixed offset — it resets to a short 2-day retry from that stage's
    completion date instead. A problem retires (stops being nagged) once
    Review D+90 is completed with Result "Yes".
    """
    overdue = []
    filter_obj = {"property": "Cold ✓", "date": {"is_not_empty": True}}
    for page in query_data_source(blind75_ds_id, filter_obj):
        cold = prop_date(page, "Cold ✓")
        result = prop_select(page, "Result")

        last_stage_date = cold
        last_stage_index = -1  # -1 == only Cold ✓ done so far
        for i, (prop_name, _offset) in enumerate(REVIEW_STAGES):
            d = prop_date(page, prop_name)
            if d is None:
                break
            last_stage_date = d
            last_stage_index = i

        if last_stage_index == len(REVIEW_STAGES) - 1 and result == "Yes":
            continue  # retired: D+90 passed cleanly

        if result in ("Partial", "No"):
            due = last_stage_date + timedelta(days=2)
        else:
            next_index = min(last_stage_index + 1, len(REVIEW_STAGES) - 1)
            due = cold + timedelta(days=REVIEW_STAGES[next_index][1])

        if due <= today:
            overdue.append({
                "name": prop_title(page, "Problem"),
                "difficulty": prop_select(page, "Difficulty"),
                "since": due,
            })
    return overdue


def all_cold_attempted(blind75_ds_id: str) -> list[str]:
    filter_obj = {"property": "Cold ✓", "date": {"is_not_empty": True}}
    return [prop_title(p, "Problem") for p in query_data_source(blind75_ds_id, filter_obj)]


# ---------------------------------------------------------------------------
# State / streak
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"last_congratulated_week_start": None, "last_week_seen": None, "streak": 0}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


# ---------------------------------------------------------------------------
# Discord
# ---------------------------------------------------------------------------

SNARKY_TITLES = [
    "Quota met. I'm shocked. Pleasantly shocked.",
    "Look who did their reps.",
    "Miracles happen, apparently.",
    "The bar was on the floor and you still tripped over it upward.",
]


def post_discord(payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "leetcode-nagger/notion"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=15)
    except urllib.error.HTTPError as e:
        print(f"Discord post failed: {e.code} {e.read().decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)


def build_nag_embed(today: date, did_cold_today: bool, cold_done: int, cold_target: int | None,
                     overdue: list[dict], is_sunday: bool, all_problems: list[str],
                     new_problem_names: list[str] | None = None,
                     come_back_names: list[str] | None = None) -> dict:
    fields = []
    color = 0xF1C40F  # amber default: "you didn't do a problem today"

    if overdue:
        color = 0xE74C3C  # red wins over everything else
        lines = "\n".join(
            f"• {o['name']} ({o['difficulty']}) — {(today - o['since']).days}d overdue"
            for o in overdue[:10]
        )
        fields.append({"name": f"{len(overdue)} review(s) overdue", "value": lines, "inline": False})

    if is_sunday:
        if not overdue:
            color = 0x3498DB  # blue, but only if nothing worse is going on
        listed = ", ".join(all_problems) if all_problems else "nothing logged yet"
        fields.append({"name": "Sunday: go re-read your notes", "value": listed[:1000], "inline": False})
    elif not did_cold_today and (cold_target is None or cold_done < cold_target):
        target_str = (f"{cold_done}/{cold_target} done this week."
                      if cold_target is not None else f"{cold_done} done this week.")
        if new_problem_names:
            problems_str = "\n".join(f"• {n}" for n in new_problem_names)
            value = f"{target_str}\n{problems_str}"
        else:
            value = f"Do a new cold attempt today.\n{target_str}"
        fields.append({"name": "New cold attempt pending", "value": value[:1000], "inline": False})

    if not is_sunday and come_back_names:
        fields.append({
            "name": "This week's reviews",
            "value": "\n".join(f"• {n}" for n in come_back_names)[:1000],
            "inline": False,
        })

    if not fields:
        return {}  # quiet day, nothing to say

    content = f"<@{DISCORD_USER_ID}>" if DISCORD_USER_ID else ""
    return {
        "content": content,
        "embeds": [{
            "title": f"LeetCode Nag · {today.strftime('%A, %B %d')}",
            "color": color,
            "fields": fields,
            "footer": {"text": "Stop procrastinating."},
        }],
    }


def build_congrats_embed(streak: int, cold_done: int, cold_target: int) -> dict:
    return {
        "embeds": [{
            "title": random.choice(SNARKY_TITLES),
            "color": 0x2ECC71,  # green, deliberately no @-mention
            "fields": [
                {"name": "Streak", "value": f"{streak}-week streak.", "inline": True},
                {"name": "This week", "value": f"{cold_done}/{cold_target} done.", "inline": True},
            ],
            "footer": {"text": "Don't get cocky."},
        }],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    today = datetime.now(TZ).date()
    is_sunday = today.weekday() == 6  # Monday=0 ... Sunday=6

    blind75_ds_id = get_data_source_id(BLIND75_DATABASE_ID)
    schedule_ds_id = get_data_source_id(SCHEDULE_DATABASE_ID) if SCHEDULE_DATABASE_ID else None

    week_start, week_end, cold_target, new_ids, come_back_ids = current_week(schedule_ds_id, today)
    if week_start is None:
        # No Master Schedule / no matching week row: fall back to "nag once a
        # day, no weekly cap" mode, same as leaving SCHEDULE_TAB blank in the
        # original.
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)
    week_key = week_start.isoformat()

    new_problem_names = come_back_names = []
    if new_ids or come_back_ids:
        title_map = fetch_page_title_map(blind75_ds_id)
        new_problem_names = [title_map[i] for i in new_ids if i in title_map]
        come_back_names = [title_map[i] for i in come_back_ids if i in title_map]

    state = load_state()

    # A new week started without ever hitting quota in the previous one -> streak resets.
    last_seen = state.get("last_week_seen")
    if last_seen and last_seen != week_key and state.get("last_congratulated_week_start") != last_seen:
        state["streak"] = 0
    state["last_week_seen"] = week_key

    cold_done = cold_attempts_in_range(blind75_ds_id, week_start, week_end)
    did_cold_today = cold_attempts_in_range(blind75_ds_id, today, today) > 0
    overdue = overdue_reviews(blind75_ds_id, today)
    hit_quota = cold_target is not None and cold_done >= cold_target

    if hit_quota and state.get("last_congratulated_week_start") != week_key:
        state["streak"] = state.get("streak", 0) + 1
        state["last_congratulated_week_start"] = week_key
        post_discord(build_congrats_embed(state["streak"], cold_done, cold_target))

    state["last_run_date"] = datetime.now(TZ).date().isoformat()
    save_state(state)

    all_problems = all_cold_attempted(blind75_ds_id) if is_sunday else []
    embed = build_nag_embed(today, did_cold_today, cold_done, cold_target, overdue, is_sunday,
                             all_problems, new_problem_names, come_back_names)
    if embed:
        post_discord(embed)
    # else: quiet day, no Discord ping.


if __name__ == "__main__":
    main()
