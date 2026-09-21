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
    """Return the date of the upcoming Monday.

    This NEVER returns today's own date, even when today is Monday -
    Monday's pull is meant to preview next week's slate, not close out
    this week's, so "upcoming Monday" always means at least 7 days out
    on a Monday specifically.
    """
    now = now or datetime.now(timezone.utc)
    days_until_monday = (7 - now.weekday()) % 7  # Monday == 0
    if days_until_monday == 0:
        days_until_monday = 7  # today IS Monday - target next Monday, not today
    return (now + timedelta(days=days_until_monday)).date()


def compute_cutoff() -> str:
    """
    Return an ISO8601 UTC timestamp for the upcoming Monday, used as
    commenceTimeTo so we only pull one week's slate at a time - not
    the week after that, which may already be up on the board.
    """
    target_monday = compute_week_id()
    cutoff = datetime.combine(target_monday + timedelta(days=1), time(9, 0), tzinfo=timezone.utc)
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_monday_from(now: datetime) -> str:
    """
    Monday-only lower bound: 9 AM UTC on Tuesday - not midnight - so
    Monday's own pull skips tonight's still-upcoming MNF game entirely
    and jumps straight to next week's slate. A plain midnight-UTC
    boundary isn't late enough: a typical 8:15 PM ET kickoff already
    lands at ~00:15 UTC Tuesday, past midnight but still MNF, and an
    occasional 10:15 PM ET kickoff lands even later. 9 AM UTC clears
    any realistic MNF kickoff with room to spare, same buffer already
    used for this exact reason in compute_cutoff(). That game's lines
    were already captured all week by Tuesday-Saturday's own pulls -
    nothing is lost by Monday morning no longer including it again.
    """
    tomorrow = (now + timedelta(days=1)).date()
    return datetime.combine(tomorrow, time(9, 0), tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fetch_sport(sport_key: str, api_key: str, cutoff: str, cutoff_from: str = None) -> list:
    url = (
        f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
        f"?regions=us&markets=spreads&oddsFormat=american"
        f"&commenceTimeTo={cutoff}&apiKey={api_key}"
    )
    if cutoff_from:
        url += f"&commenceTimeFrom={cutoff_from}"
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


def build_rows(sport_key: str, league_label: str, api_key: str, cutoff: str, now: datetime, cutoff_from: str = None) -> list:
    rows = []
    for game in fetch_sport(sport_key, api_key, cutoff, cutoff_from):
        commence_time = game.get("commence_time")

        # The Odds API excludes *completed* games from /odds, but not
        # *in-progress* ones - a Thursday night game still being played
        # can show up in Friday's pull with a stale or live-betting
        # line mixed in with everyone else's pre-game numbers. Drop
        # anything whose kickoff has already passed, regardless of
        # what the API itself considers "still live."
        if commence_time:
            try:
                kickoff = datetime.strptime(commence_time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if kickoff <= now:
                    continue
            except ValueError:
                pass  # unexpected timestamp format - don't silently drop a game over a parse quirk

        point, book = pick_spread(game)
        rows.append(
            {
                "id": game.get("id"),
                "league": league_label,
                "home_team": game.get("home_team"),
                "away_team": game.get("away_team"),
                "commence_time": commence_time,
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
    now = datetime.now(timezone.utc)
    cutoff = compute_cutoff()
    cutoff_from = compute_monday_from(now) if args.stage == "monday" else None
    for sport_key, league_label in SPORTS.items():
        rows.extend(build_rows(sport_key, league_label, api_key, cutoff, now, cutoff_from))

    week_id = compute_week_id().isoformat()  # the week this pull's data belongs to

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

    # Monday's pull now previews NEXT week's slate, so the other rolling
    # files (Tue-Sat) still hold the week that's ending - left alone,
    # they'd sit there mismatched against Monday's new games until each
    # day's own pull runs later this week. Clear them immediately instead
    # so the table doesn't show a stale, half-old/half-new mix all week.
    # The permanent archive under data/weeks/ is untouched - this only
    # clears the rolling "current view" files.
    if args.stage == "monday":
        for other_stage in VALID_STAGES:
            if other_stage == "monday":
                continue
            other_path = DATA_DIR / f"{other_stage}.json"
            other_path.write_text(json.dumps([], indent=2))
        print("Cleared Tue-Sat rolling files (Monday now previews next week's slate)")


if __name__ == "__main__":
    main()
