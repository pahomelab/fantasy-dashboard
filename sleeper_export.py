#!/usr/bin/env python3
"""
Sleeper export for the Superflex Desk dashboard.

Usage:
    python3 sleeper_export.py PaulMeilleur            (Mac / Linux)
    py sleeper_export.py PaulMeilleur                 (Windows)

Optional second argument: season (default 2026).
    python3 sleeper_export.py PaulMeilleur 2026

It reads Sleeper's public, read-only API (no login, no password, no API key) and
writes one file, sleeper_export.json, next to the script. Upload that file to Claude.

It needs only Python 3.7 or newer. Nothing to install.

Calls per run: about 20 per league, plus 3 shared ones. Sleeper asks for fewer than
1000 calls a minute, and this script pauses briefly between calls to stay far below that.
The player list (about 5 MB) is fetched once per run. Sleeper asks that it be fetched
at most once a day, so avoid running this in a loop.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "https://api.sleeper.app/v1"
PAUSE = 0.12

# Player fields worth keeping. gsis_id is the NFL's own ID, which lets the dashboard
# join Sleeper players to NFL stats exactly instead of matching on names.
PLAYER_FIELDS = [
    "full_name", "first_name", "last_name", "position", "fantasy_positions", "team",
    "status", "injury_status", "injury_body_part", "injury_start_date", "age",
    "years_exp", "depth_chart_position", "depth_chart_order", "number",
    "search_rank", "gsis_id", "sportradar_id", "espn_id", "yahoo_id",
]


def get(path, retries=3):
    """GET one Sleeper endpoint and return parsed JSON, or None if it is missing or keeps failing."""
    url = BASE + path
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "superflex-desk-export"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.load(resp)
            time.sleep(PAUSE)
            return data
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                time.sleep(5 * (attempt + 1))
            else:
                time.sleep(1.5 * (attempt + 1))
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    print(f"  ! gave up on {path}")
    return None


def slim_players(all_players, keep_ids):
    """Keep active players and anyone on a roster, with only the fields the dashboard uses."""
    out = {}
    for pid, p in (all_players or {}).items():
        on_roster = pid in keep_ids
        active = p.get("active") or p.get("status") == "Active"
        if not (on_roster or (active and p.get("team"))):
            continue
        row = {k: p.get(k) for k in PLAYER_FIELDS if p.get(k) is not None}
        if p.get("position") == "DEF":
            row["full_name"] = row.get("full_name") or f"{pid} D/ST"
        out[pid] = row
    return out


def export_league(lg, user_id):
    lid = lg["league_id"]
    name = lg.get("name")
    print(f"- {name} ({lid})")
    users = get(f"/league/{lid}/users") or []
    rosters = get(f"/league/{lid}/rosters") or []
    traded = get(f"/league/{lid}/traded_picks") or []

    # Fantasy schedule: every week's matchups. Future weeks have no points yet,
    # but matchup_id pairs still say who plays whom.
    matchups = {}
    for wk in range(1, 19):
        m = get(f"/league/{lid}/matchups/{wk}")
        if m:
            matchups[str(wk)] = m

    draft, picks = None, []
    if lg.get("draft_id"):
        draft = get(f"/draft/{lg['draft_id']}")
        picks = get(f"/draft/{lg['draft_id']}/picks") or []

    mine = next((r for r in rosters if r.get("owner_id") == user_id or user_id in (r.get("co_owners") or [])), None)
    return {
        "league": {k: lg.get(k) for k in (
            "league_id", "name", "season", "status", "sport", "total_rosters", "roster_positions",
            "scoring_settings", "settings", "draft_id", "previous_league_id")},
        "my_roster_id": mine["roster_id"] if mine else None,
        "users": users,
        "rosters": rosters,       # players, starters, reserve (IR), taxi, keepers, settings (record, points)
        "matchups": matchups,
        "traded_picks": traded,
        "draft": draft,
        "draft_picks": picks,     # round, pick_no, roster_id, player_id, is_keeper
    }


def main():
    username = sys.argv[1] if len(sys.argv) > 1 else "PaulMeilleur"
    season = sys.argv[2] if len(sys.argv) > 2 else "2026"

    print(f"Looking up {username} ...")
    user = get(f"/user/{username}")
    if not user:
        sys.exit("Could not find that Sleeper username (or Sleeper is not reachable). Check the spelling and your connection.")
    user_id = user["user_id"]

    state = get("/state/nfl") or {}
    leagues = get(f"/user/{user_id}/leagues/nfl/{season}") or []
    print(f"Found {len(leagues)} league(s) for {season}. NFL week: {state.get('week')}")

    exported = [export_league(lg, user_id) for lg in leagues]

    # Players on any roster in any league, so injured or inactive players are not dropped.
    keep = set()
    for e in exported:
        for r in e["rosters"]:
            for key in ("players", "reserve", "taxi", "starters"):
                keep.update(x for x in (r.get(key) or []) if x)
        for p in e["draft_picks"]:
            if p.get("player_id"):
                keep.add(p["player_id"])

    print("Downloading the NFL player list (about 5 MB) ...")
    players = slim_players(get("/players/nfl"), keep)
    trending = get("/players/nfl/trending/add?lookback_hours=48&limit=75") or []

    out = {
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "username": username,
        "user": {k: user.get(k) for k in ("user_id", "username", "display_name")},
        "season": season,
        "nfl_state": state,
        "leagues": exported,
        "players": players,
        "trending_add": trending,
    }
    with open("sleeper_export.json", "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    size = len(json.dumps(out)) / 1e6
    print(f"Done. Wrote sleeper_export.json ({size:.1f} MB) with {len(exported)} league(s) and {len(players)} players.")
    print("Upload that file to Claude.")


if __name__ == "__main__":
    main()
