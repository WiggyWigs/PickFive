#!/usr/bin/env python3
"""
Pull NFL + NCAAF betting splits (bet % and money %) from Action Network,
save a daily snapshot, and build the data behind sharp.html.

Run daily via .github/workflows/pull-splits.yml. No API key needed -
this reads the same public JSON Action Network's own public-betting
page loads. It is unofficial, so if they change the shape the script
fails loudly rather than writing a half-empty file.

Files written (all under data/splits/):
  weeks/<week_id>/<YYYY-MM-DD>.json  raw daily snapshot, one per run day
  current.json                       this week's games + sharp flags (what the page reads)
  flags.json                         every flag ever raised, graded once the game is final

week_id is the Monday that closes a game's slate (same convention as
data/weeks/), computed from kickoff in Eastern time.

Sharp flag rule (kept deliberately simple so it can be checked by hand):
  money % - bet % >= EDGE_THRESHOLD on one side, AND
  that side's spread has moved toward it since Tuesday (its number got
  worse for new bettors, e.g. -3 -> -3.5 or +7 -> +6.5).

Tuesday's line comes from our own Tuesday splits snapshot. If there
isn't one (first week, or a failed Tuesday run) it falls back to the
Odds API DraftKings line in data/weeks/<week_id>/tuesday.json - the same
number the Weekly Lines page anchors to. The current line is also
DraftKings, so the two are the same book.
"""
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
SPLITS_DIR = ROOT / "data" / "splits"
LINES_WEEKS_DIR = ROOT / "data" / "weeks"

LEAGUES = {"nfl": "NFL", "ncaaf": "NCAAF"}
API = "https://api.actionnetwork.com/web/v2/scoreboard/{sport}"
PUBLIC_API = "https://api.actionnetwork.com/web/v2/scoreboard/publicbetting/{sport}"

SPLITS_BOOK = "15"  # Action Network "Consensus" - carries their bet/money %
LINE_BOOK = "68"    # DraftKings - same book as the Odds API lines pull
EDGE_THRESHOLD = 15  # money % minus bet %, in percentage points

EASTERN = ZoneInfo("America/New_York")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept": "application/json",
}


def get_json(url):
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=30) as res:
            return json.load(res)
    except (HTTPError, URLError) as e:
        sys.exit(f"Request failed for {url}: {e}")


def week_id_for(start_time: str) -> str:
    """Monday on or after the game's Eastern kickoff date."""
    kickoff = datetime.fromisoformat(start_time.replace("Z", "+00:00")).astimezone(EASTERN).date()
    return (kickoff + timedelta(days=(7 - kickoff.weekday()) % 7)).isoformat()


def spread_outcomes(game, book):
    return game.get("markets", {}).get(book, {}).get("event", {}).get("spread", [])


def pct(outcome, kind):
    info = outcome.get("bet_info") or {}
    return (info.get(kind) or {}).get("percent")


def parse_game(game, league):
    teams = {t["id"]: t for t in game.get("teams", [])}
    home = teams.get(game["home_team_id"], {})
    away = teams.get(game["away_team_id"], {})

    line = None
    for book in (LINE_BOOK, SPLITS_BOOK):
        for o in spread_outcomes(game, book):
            if o.get("side") == "home" and o.get("value") is not None:
                line = o["value"]
                break
        if line is not None:
            break

    splits = {}
    for book in (SPLITS_BOOK, LINE_BOOK):
        for o in spread_outcomes(game, book):
            b, m = pct(o, "tickets"), pct(o, "money")
            if o.get("side") in ("home", "away") and (b or m):
                splits[o["side"]] = {"bets": b, "money": m}
        if len(splits) == 2:
            break
    has_splits = len(splits) == 2

    box = game.get("boxscore") or {}
    return {
        "id": game["id"],
        "league": league,
        "season": game.get("season"),
        "an_week": game.get("week"),
        "week_id": week_id_for(game["start_time"]),
        "start_time": game["start_time"],
        "status": game.get("status"),
        "home": home.get("full_name"),
        "away": away.get("full_name"),
        "home_abbr": home.get("abbr"),
        "away_abbr": away.get("abbr"),
        "line": line,  # home team's spread, DraftKings
        "home_bets": splits["home"]["bets"] if has_splits else None,
        "home_money": splits["home"]["money"] if has_splits else None,
        "away_bets": splits["away"]["bets"] if has_splits else None,
        "away_money": splits["away"]["money"] if has_splits else None,
        "home_score": box.get("total_home_points"),
        "away_score": box.get("total_away_points"),
    }


# --- Tuesday baseline -------------------------------------------------------

def norm_tokens(name):
    # drop apostrophes (Hawai'i, Ragin' Cajuns), split on other punctuation (UL-Monroe)
    name = re.sub(r"['\u2019]", "", (name or "").lower())
    return set(re.sub(r"[^a-z0-9]+", " ", name).split())


def similar(a, b):
    ta, tb = norm_tokens(a), norm_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def load_json(path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def tuesday_lines(week_id):
    """(own snapshot lines keyed by 'league:id', odds-api rows) for this week's Tuesday."""
    tuesday = (datetime.fromisoformat(week_id) - timedelta(days=6)).date().isoformat()
    snap = load_json(SPLITS_DIR / "weeks" / week_id / f"{tuesday}.json", {})
    own = {f"{g['league']}:{g['id']}": g.get("line") for g in snap.get("games", [])}
    odds_api = load_json(LINES_WEEKS_DIR / week_id / "tuesday.json", [])
    return own, odds_api


def odds_api_match(game, rows):
    """Best Odds API row for this game: same league, kickoff within 36h, names alike."""
    kickoff = datetime.fromisoformat(game["start_time"].replace("Z", "+00:00"))
    best, best_score = None, 0.0
    for r in rows:
        if r.get("league") != game["league"] or not r.get("commence_time"):
            continue
        t = datetime.fromisoformat(r["commence_time"].replace("Z", "+00:00"))
        if abs((t - kickoff).total_seconds()) > 36 * 3600:
            continue
        # Average, with a floor on the weaker side: "NC State Wolfpack" vs
        # "North Carolina State Wolfpack" only scores 0.4, but its opponent
        # matches exactly, and a team only plays once in a 36h window.
        h, a = similar(r["home_team"], game["home"]), similar(r["away_team"], game["away"])
        score = (h + a) / 2 if min(h, a) >= 0.25 else 0.0
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 0.5 else None


def tuesday_line(game, cache):
    wk = game["week_id"]
    if wk not in cache:
        cache[wk] = tuesday_lines(wk)
    own, odds_api = cache[wk]
    key = f"{game['league']}:{game['id']}"
    if own.get(key) is not None:
        return own[key], "splits"
    row = odds_api_match(game, odds_api)
    if row and row.get("spread") is not None:
        return row["spread"], "odds_api"
    return None, None


# --- Flag rule ---------------------------------------------------------------

def sharp_flag(g):
    """Return the flagged side dict, or None. Needs splits, a line, and a Tuesday line."""
    if g["home_money"] is None or g["line"] is None or g["tue_line"] is None:
        return None
    home_edge = g["home_money"] - g["home_bets"]
    away_edge = g["away_money"] - g["away_bets"]
    # move is in the home spread; the away spread is its negative
    if home_edge >= EDGE_THRESHOLD and g["move"] < 0:
        side, edge, side_move, side_line = "home", home_edge, g["move"], g["line"]
    elif away_edge >= EDGE_THRESHOLD and g["move"] > 0:
        side, edge, side_move, side_line = "away", away_edge, -g["move"], -g["line"]
    else:
        return None
    return {
        "side": side,
        "team": g[side],
        "abbr": g[f"{side}_abbr"],
        "edge": edge,
        "side_move": round(side_move, 1),
        "side_line": side_line,
    }


def grade(side_line, side_score, opp_score):
    margin = side_score + side_line - opp_score
    return "W" if margin > 0 else "L" if margin < 0 else "P"


# --- Main --------------------------------------------------------------------

def main():
    now = datetime.now(timezone.utc)
    today = now.astimezone(EASTERN).date().isoformat()

    games = []
    for sport, league in LEAGUES.items():
        data = get_json(PUBLIC_API.format(sport=sport) + f"?bookIds={SPLITS_BOOK},{LINE_BOOK}&periods=event")
        if "games" not in data:
            sys.exit(f"Unexpected response shape for {sport}: keys={list(data)}")
        games += [parse_game(g, league) for g in data["games"]]

    if not games:
        sys.exit("No games returned - refusing to overwrite data with an empty pull.")
    if not any(g["home_money"] is not None for g in games):
        sys.exit("Games returned but none carry bet/money % - Action Network may have changed or paywalled it.")

    # 1. Raw daily snapshot, grouped by each game's own week
    by_week = {}
    for g in games:
        by_week.setdefault(g["week_id"], []).append(g)
    for wk, wk_games in by_week.items():
        path = SPLITS_DIR / "weeks" / wk / f"{today}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pulled_at": now.isoformat(timespec="seconds"), "games": wk_games}, indent=2) + "\n")

    # 2. Movement since Tuesday + flag for each game
    cache = {}
    for g in games:
        g["tue_line"], g["tue_source"] = tuesday_line(g, cache)
        g["move"] = (round(g["line"] - g["tue_line"], 1)
                     if g["line"] is not None and g["tue_line"] is not None else None)
        g["flag"] = sharp_flag(g)

    # 3. Flag log. A flag stays live until kickoff, then freezes with the
    # last pre-kickoff numbers; it is graded once the game is final.
    flags = load_json(SPLITS_DIR / "flags.json", {})
    for g in games:
        key = f"{g['league']}:{g['id']}"
        started = datetime.fromisoformat(g["start_time"].replace("Z", "+00:00")) <= now
        if started or g["status"] != "scheduled":
            continue  # frozen - whatever was recorded before kickoff stands
        if g["flag"]:
            prev = flags.get(key, {})
            flags[key] = {
                "league": g["league"], "season": g["season"], "an_week": g["an_week"],
                "week_id": g["week_id"], "start_time": g["start_time"],
                "home": g["home"], "away": g["away"],
                "first_flagged": prev.get("first_flagged", today),
                "last_updated": today,
                **g["flag"],
                "result": None, "home_score": None, "away_score": None,
            }
        else:
            flags.pop(key, None)  # flag dropped before kickoff - never counted

    # 4. Grade anything final
    finals = {}
    for g in games:
        if g["status"] == "complete" and g["home_score"] is not None:
            finals[f"{g['league']}:{g['id']}"] = g
    pending_weeks = {(f["league"], f["an_week"]) for k, f in flags.items()
                     if f["result"] is None and k not in finals}
    for league, an_week in pending_weeks:
        sport = next(s for s, l in LEAGUES.items() if l == league)
        data = get_json(API.format(sport=sport) + f"?bookIds={LINE_BOOK}&periods=event&week={an_week}")
        for raw in data.get("games", []):
            g = parse_game(raw, league)
            if g["status"] == "complete" and g["home_score"] is not None:
                finals[f"{league}:{g['id']}"] = g
    for key, f in flags.items():
        fin = finals.get(key)
        if f["result"] is None and fin:
            side_score = fin[f"{f['side']}_score"]
            opp_score = fin["away_score" if f["side"] == "home" else "home_score"]
            f.update(home_score=fin["home_score"], away_score=fin["away_score"],
                     result=grade(f["side_line"], side_score, opp_score))

    (SPLITS_DIR / "flags.json").write_text(json.dumps(dict(sorted(flags.items())), indent=2) + "\n")

    # 5. What the page reads
    for g in games:
        g.update({k: finals[f"{g['league']}:{g['id']}"][k] for k in ("home_score", "away_score")}
                 if f"{g['league']}:{g['id']}" in finals else {})
    current = {
        "pulled_at": now.isoformat(timespec="seconds"),
        "edge_threshold": EDGE_THRESHOLD,
        "games": sorted(games, key=lambda g: g["start_time"]),
    }
    (SPLITS_DIR / "current.json").write_text(json.dumps(current, indent=2) + "\n")

    flagged = sum(1 for g in games if g["flag"])
    with_splits = sum(1 for g in games if g["home_money"] is not None)
    print(f"{len(games)} games, {with_splits} with splits, {flagged} flagged, {len(flags)} flags logged")


if __name__ == "__main__":
    main()
