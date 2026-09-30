#!/usr/bin/env python3
"""
Backtest: if you lock a side at an early line and the market later moves
toward that side, how often does it cover at your locked line?

Pulls completed NFL + NCAAF (FBS) weeks from Action Network for the seasons
below, and for every game with an opening and closing line and a final
score, grades the side the line moved TOWARD at the opening line, grouped
by how far it moved. Writes data/splits/move_edge.json, which sharp.html
uses to put a historical win rate next to each game's Tuesday -> now move.

The opening line stands in for the contest's Tuesday lock and the closing
line for Saturday morning - Action Network doesn't publish a Tuesday line
for past weeks. Run by hand when you want to refresh it (e.g. once a
season); it makes ~100 requests with a pause between each.

    python scripts/backtest_moves.py [--cache DIR]
"""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "splits" / "move_edge.json"

SEASONS = (2024, 2025, 2026)
WEEKS = {"nfl": range(1, 19), "ncaaf": range(1, 17)}
LEAGUES = {"nfl": "NFL", "ncaaf": "NCAAF"}
API = "https://api.actionnetwork.com/web/v2/scoreboard/{sport}?bookIds=15,30,68&periods=event&week={week}&season={season}"
OPEN_BOOK, CLOSE_BOOK, FALLBACK_BOOK = "30", "68", "15"  # Open, DraftKings, Consensus
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}

# Half-point buckets of |move|, in points. Upper bound inclusive.
BUCKETS = [("0.5", 0.5, 0.5), ("1-1.5", 1.0, 1.5), ("2-2.5", 2.0, 2.5), ("3-4.5", 3.0, 4.5), ("5+", 5.0, 999)]


def fetch(sport, season, week, cache):
    path = cache / f"{sport}_{season}_{week:02d}.json" if cache else None
    if path and path.exists():
        return json.loads(path.read_text())
    url = API.format(sport=sport, season=season, week=week) + ("&division=FBS" if sport == "ncaaf" else "")
    with urlopen(Request(url, headers=HEADERS), timeout=40) as res:
        data = json.load(res)
    if path:
        path.write_text(json.dumps(data))
    time.sleep(1.0)
    return data


def home_spread(game, book):
    for o in game.get("markets", {}).get(book, {}).get("event", {}).get("spread", []):
        if o.get("side") == "home" and o.get("value") is not None:
            return o["value"]
    return None


def bucket_for(move):
    a = abs(move)
    for name, lo, hi in BUCKETS:
        if lo <= a <= hi:
            return name
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, help="directory to cache API responses in")
    args = ap.parse_args()
    if args.cache:
        args.cache.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    tally = {lg: {b[0]: {"w": 0, "l": 0, "p": 0} for b in BUCKETS} for lg in list(LEAGUES.values()) + ["ALL"]}
    games_used = 0
    for sport, league in LEAGUES.items():
        for season in SEASONS:
            for week in WEEKS[sport]:
                for g in fetch(sport, season, week, args.cache).get("games", []):
                    box = g.get("boxscore") or {}
                    if g.get("season") != season or g.get("status") != "complete" or box.get("total_home_points") is None:
                        continue
                    if datetime.fromisoformat(g["start_time"].replace("Z", "+00:00")) > now:
                        continue
                    opn = home_spread(g, OPEN_BOOK)
                    cls = home_spread(g, CLOSE_BOOK)
                    cls = cls if cls is not None else home_spread(g, FALLBACK_BOOK)
                    if opn is None or cls is None or cls == opn:
                        continue
                    games_used += 1
                    # Line moved toward home if the home spread went down (-3 -> -4, +7 -> +6)
                    side_home = cls < opn
                    locked = opn if side_home else -opn
                    ss, os_ = ((box["total_home_points"], box["total_away_points"]) if side_home
                               else (box["total_away_points"], box["total_home_points"]))
                    margin = ss + locked - os_
                    r = "w" if margin > 0 else "l" if margin < 0 else "p"
                    b = bucket_for(cls - opn)
                    tally[league][b][r] += 1
                    tally["ALL"][b][r] += 1

    out = {
        "generated_at": now.isoformat(timespec="seconds"),
        "seasons": list(SEASONS),
        "games_with_move": games_used,
        "note": "Side the line moved toward, graded at the opening line. Opening line stands in for the Tuesday lock.",
        "buckets": [{"name": n, "min": lo, "max": hi} for n, lo, hi in BUCKETS],
        "leagues": tally,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2) + "\n")
    for lg, rows in tally.items():
        print(lg, {b: f"{v['w']}-{v['l']} ({v['w'] / max(1, v['w'] + v['l']):.1%})" for b, v in rows.items()})


if __name__ == "__main__":
    main()
