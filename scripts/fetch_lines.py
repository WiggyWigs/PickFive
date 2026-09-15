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
VALID_STAGES = ["tuesday", "wednesday", "thursday", "friday", "saturday"]


def fetch_sport(sport_key: str, api_key: str) -> list:
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
        f"?regions=us&markets=spreads&oddsFormat=american&apiKey={api_key}"
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


def build_rows(sport_key: str, league_label: str, api_key: str) -> list:
    rows = []
    for game in fetch_sport(sport_key, api_key):
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
    for sport_key, league_label in SPORTS.items():
        rows.extend(build_rows(sport_key, league_label, api_key))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / f"{args.stage}.json"
    out_path.write_text(json.dumps(rows, indent=2))

    print(f"Wrote {len(rows)} games ({args.stage}) to {out_path}")


if __name__ == "__main__":
    main()
