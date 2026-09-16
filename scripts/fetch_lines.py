#!/usr/bin/env python3
"""
Pull current NFL + NCAAF point spreads from The Odds API and write a
dated snapshot to data/<stage>.json.

Run daily (Tue-Sat) via the GitHub Actions workflow in
.github/workflows/pull-lines.yml. Requires an ODDS_API_KEY environment
variable (stored as a repo secret, not committed).
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, time, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

SPORTS = {
    "americanfootball_nfl": "NFL",
    "americanfootball_ncaaf": "NCAAF",
}

# Bookmaker used for consistency across the week. If it's not offering a
# line on a given game (happens occasionally for smaller CFB matchups),
# we fall back to whichever book The Odds API lists first for that game.
PREFERRED_BOOKMAKER = "draftkings"

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
VALID_STAGES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]


def compute_week_id(now: datetime = None) -> "date":
    """Return the date of the upcoming Monday (today, if today is Monday).

    Used both as the commenceTimeTo cutoff basis and as the folder name
    for this week's permanent archive under data/weeks/<week_id>/.
    """
    now = now or datetime.now(timezone.utc)
    days_until_monday = (7 - now.weekday()) % 7  # Monday == 0
    return (now + timedelta(days=days_until_monday)).date()


def compute_cutoff() -> str:
    """
    Return an ISO8601 UTC timestamp for the upcoming Monday, used as
    commenceTimeTo so we only pull this week's slate - not next
    week's games that are already up on the board.

    If today is Monday, "upcoming Monday" is today (0 days out), so
    tonight's Monday Night Football game is still included. The
    cutoff is set to 9 AM UTC the day *after* that Monday (not
    midnight) to cover late-kickoff MNF games, which can commence
    just after midnight UTC.
    """
    target_monday = compute_week_id()
    cutoff = datetime.combine(target_monday + timedelta(days=1), time(9, 0), tzinfo=timezone.utc)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_sport(sport_key: str, api_key: str, cutoff: str) -> list:
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
        f"?regions=us&markets=spreads&oddsFormat=american"
        f"&commenceTimeTo={cutoff}&apiKey={api_key}"
    )
    req = Request(url, headers={"User-Agent": "PickFive/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except HTTPError as e:
        body = e.read().decode(errors="replace")
        print(f"[error] {sport_key}: HTTP {e.code} - {body}", file=sys.stderr)
        return []
    except URLError as e:
        print(f"[error] {sport_key}: {e.reason}", file=sys.stderr)
        return []


def pick_spread(game: dict):
    """Return (home_team_point, bookmaker_key_used) for one game."""
    books = game.get("bookmakers", [])
    if not books:
        return None, None

    chosen = next((b for b in books if b.get("key") == PREFERRED_BOOKMAKER), books[0])

    for market in chosen.get("markets", []):
        if market.get("key") == "spreads":
            for outcome in market.get("outcomes", []):
                if outcome.get("name") == game.get("home_team"):
                    return outcome.get("point"), chosen.get("key")

    return None, chosen.get("key")


def build_rows(sport_key: str, league_label: str, api_key: str, cutoff: str) -> list:
    rows = []
    for game in fetch_sport(sport_key, api_key, cutoff):
        point, book = pick_spread(game)
        rows.append(
            {
                "id": game.get("id"),
                "league": league_label,
                "home_team": game.get("home_team"),
                "away_team": game.get("away_team"),
                "commence_time": game.get("commence_time"),
                "spread": point,  # home team's spread; negative = home favored
                "bookmaker": book,
            }
        )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=VALID_STAGES)
    args = parser.parse_args()

    api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        print("ODDS_API_KEY is not set (add it under repo Settings > Secrets > Actions)", file=sys.stderr)
        sys.exit(1)

    rows = []
    cutoff = compute_cutoff()
    for sport_key, league_label in SPORTS.items():
        rows.extend(build_rows(sport_key, league_label, api_key, cutoff))

    week_id = compute_week_id().isoformat()  # this week's Monday, e.g. "2026-09-21"

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{args.stage}.json"
    out_path.write_text(json.dumps(rows, indent=2))

    # Permanent per-week archive - the rolling data/<stage>.json files above
    # get overwritten every week, but data/weeks/<week_id>/ never does.
    # This is what the Picks page matches historical picks against.
    archive_dir = DATA_DIR / "weeks" / week_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / f"{args.stage}.json"
    archive_path.write_text(json.dumps(rows, indent=2))

    print(f"Wrote {len(rows)} games ({args.stage}, week={week_id}, cutoff={cutoff}) to {out_path} and {archive_path}")


if __name__ == "__main__":
    main()
