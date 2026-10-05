#!/usr/bin/env python3
"""
Pull NFL + NCAAF betting splits (bet % and money %) from Action Network,
save a daily snapshot, and build the data behind index.html (the Sharp page).

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
  that side has under SHARP_MAX_TICKETS % of the tickets, AND
  the DraftKings spread has moved SHARP_MIN_MOVE+ points toward that side
  from the opening line (DraftKings' Monday ~9 AM line; DraftKings at Splash time, then
  Tuesday's line, if there's no open) -
  its number got worse for new bettors, e.g. -3 -> -4 or +7 -> +6.

Tuesday's line comes from our own Tuesday splits snapshot. If there
isn't one (first week, or a failed Tuesday run) it falls back to the
Odds API DraftKings line in data/weeks/<week_id>/tuesday.json - the same
number the Weekly Lines page anchors to. The current line is also
DraftKings, so the two are the same book.
"""
import csv
import io
import json
import os
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
SHARP_MIN_MOVE = 1.0  # points the DraftKings line must move toward the money side
SHARP_MAX_TICKETS = 40  # money side must have under this % of the tickets (page default; page can change it)

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


def monday_line(game, cache):
    """DraftKings' Monday ~9 AM Eastern line - the Weekly Lines Monday pull
    (data/weeks/<week_id>/monday.json). This is the "opening line" Sharp and
    Trap measure from: Action Network's own "Open" turned out to be stale
    lookahead numbers (e.g. CHI -8.5 vs DraftKings -3 on 2026-09-28)."""
    wk = game["week_id"]
    if wk not in cache:
        cache[wk] = load_json(LINES_WEEKS_DIR / wk / "monday.json", [])
    row = odds_api_match(game, cache[wk])
    return row["spread"] if row and row.get("spread") is not None else None


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


# --- Splash (contest) lines -------------------------------------------------

def load_splash_lines():
    """Rows from the Splash Lines sheet tab, filled by the Copy Splash Lines
    bookmarklet (splash-bookmarklet.html). Optional: without the
    SPLASH_LINES_CSV_URL variable, or if the fetch fails, the page just falls
    back to DraftKings' Tuesday line."""
    url = os.environ.get("SPLASH_LINES_CSV_URL", "").strip()
    if not url:
        return [], None
    try:
        with urlopen(Request(url, headers={"User-Agent": HEADERS["User-Agent"]}), timeout=30) as res:
            text = res.read().decode("utf-8-sig")
    except (HTTPError, URLError) as e:
        print(f"WARNING: couldn't fetch Splash lines ({e}) - using Tuesday DraftKings lines only")
        return [], None

    rows, copied_at = [], None
    for raw in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        try:
            away_line = float(r["away line"]) if r.get("away line") else None
            home_line = float(r["home line"]) if r.get("home line") else None
        except ValueError:
            continue
        if not r.get("away") or not r.get("home") or (away_line is None and home_line is None):
            continue
        rows.append({"away": r["away"], "home": r["home"],
                     "home_line": home_line if home_line is not None else -away_line})
        copied_at = copied_at or r.get("copied at") or None
    return rows, copied_at


def contained(short, full):
    """Share of the short name's words found in the full name. Splash shows
    school/city names ("Western Kentucky", "NC State") while Action Network
    uses full names ("Western Kentucky Hilltoppers"), so plain overlap
    (similar) under-scores them."""
    ts, tf = norm_tokens(short), norm_tokens(full)
    return len(ts & tf) / len(ts) if ts else 0.0


def splash_line_for(game, rows):
    """Home-team Splash spread for this game. Both teams must match, which is
    what keeps "Miami" from matching the wrong Miami."""
    best, best_score = None, 0.0
    for r in rows:
        for flipped in (False, True):  # neutral-site games can list home/away differently
            ra, rh = (r["home"], r["away"]) if flipped else (r["away"], r["home"])
            a, h = contained(ra, game["away"]), contained(rh, game["home"])
            if min(a, h) < 0.5:
                continue
            # tie-break on plain overlap so "Texas" prefers Texas Longhorns over Texas Tech
            score = a + h + 0.01 * (similar(ra, game["away"]) + similar(rh, game["home"]))
            if score > best_score:
                best, best_score = (-r["home_line"] if flipped else r["home_line"]), score
    return best if best_score >= 1.5 else None


# --- Flag rule ---------------------------------------------------------------

def sharp_flag(g):
    """Return the flagged side dict, or None. Needs splits, a line, and a base line
    (DraftKings at Splash-lock time, else Tuesday) to measure the move from."""
    if g["home_money"] is None or g["line"] is None or g["move"] is None:
        return None
    home_edge = g["home_money"] - g["home_bets"]
    away_edge = g["away_money"] - g["away_bets"]
    # move is in the home spread; the away spread is its negative
    if home_edge >= EDGE_THRESHOLD and g["home_bets"] < SHARP_MAX_TICKETS and g["move"] <= -SHARP_MIN_MOVE:
        side, edge, side_move, side_line = "home", home_edge, g["move"], g["line"]
    elif away_edge >= EDGE_THRESHOLD and g["away_bets"] < SHARP_MAX_TICKETS and g["move"] >= SHARP_MIN_MOVE:
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


# --- Freeze games at kickoff ---------------------------------------------------

FROZEN_FIELDS = ("line", "home_bets", "home_money", "away_bets", "away_money")

# Contest picks lock Saturday morning; no line or split may be taken after this
# for that week's games, even if GitHub starts a scheduled run hours late
# (the 9:05 AM slot on 2026-10-03 actually ran at 1:01 PM) or someone runs
# the pull by hand.
LOCK_WEEKDAY_OFFSET = 2          # Saturday = week_id (Monday) - 2 days
LOCK_TIME = (10, 30)             # 10:30 AM Eastern


def lock_time(week_id):
    """Saturday 10:30 AM Eastern of the week that ends on week_id (a Monday), in UTC."""
    sat = datetime.fromisoformat(week_id).date() - timedelta(days=LOCK_WEEKDAY_OFFSET)
    return datetime(sat.year, sat.month, sat.day, *LOCK_TIME, tzinfo=EASTERN).astimezone(timezone.utc)


def parse_time(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def freeze_started_games(games, now):
    """Freeze each game at kickoff or at the Saturday 10:30 AM Eastern lock,
    whichever comes first.

    Once a game kicks off, Action Network's feed switches to live in-game
    lines and splits (e.g. CLE +2.5 pre-game became -7.5 at 21-10 on
    2026-10-01). Keep each started game's last pre-kickoff numbers instead:
    from the previous pull if it ran before kickoff (or was already frozen),
    else from the latest of this week's snapshots taken before kickoff.
    Scores and status stay live - grading needs them.

    The first frozen numbers for each game are also kept in kickoff_lines.json,
    which is never rewritten for that game, so a later pull or an overwritten
    daily snapshot can't lose them."""
    store_path = SPLITS_DIR / "kickoff_lines.json"
    store = load_json(store_path, {})
    prev = load_json(SPLITS_DIR / "current.json", {})
    prev_at = parse_time(prev["pulled_at"]) if prev.get("pulled_at") else None
    prev_games = {f"{g['league']}:{g['id']}": g for g in prev.get("games", [])}
    snapshots = {}

    for g in games:
        kickoff = parse_time(g["start_time"])
        lock = lock_time(g["week_id"])
        cutoff = min(kickoff, lock)
        frozen_for = "lock" if lock < kickoff else "kickoff"
        if cutoff > now and g["status"] == "scheduled":
            continue
        key = f"{g['league']}:{g['id']}"
        src, src_at = None, None
        p = prev_games.get(key)
        if key in store:
            src, src_at = store[key], store[key]["frozen_at"]
        elif p and p.get("frozen_at"):
            src, src_at = p, p["frozen_at"]
        elif p and prev_at and prev_at < cutoff:
            src, src_at = p, prev["pulled_at"]
        else:
            wk = g["week_id"]
            if wk not in snapshots:
                snapshots[wk] = [load_json(f, {}) for f in sorted((SPLITS_DIR / "weeks" / wk).glob("*.json"))]
            for snap in snapshots[wk]:
                at = snap.get("pulled_at")
                hit = next((x for x in snap.get("games", []) if x["id"] == g["id"] and x["league"] == g["league"]), None)
                if hit and at and parse_time(at) < cutoff and (src_at is None or parse_time(at) > parse_time(src_at)):
                    src, src_at = hit, at
        for f in FROZEN_FIELDS:
            g[f] = src.get(f) if src else None  # no pre-kickoff numbers: blank, never live ones
        g["frozen_at"] = src_at
        g["frozen_for"] = (store.get(key) or {}).get("frozen_for", frozen_for)
        if src and key not in store:
            store[key] = {**{f: g[f] for f in FROZEN_FIELDS}, "frozen_at": src_at, "frozen_for": frozen_for,
                          "away": g["away"], "home": g["home"], "start_time": g["start_time"]}

    store_path.write_text(json.dumps(dict(sorted(store.items())), indent=2) + "\n")


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

    freeze_started_games(games, now)

    # 1. Raw daily snapshot, grouped by each game's own week
    by_week = {}
    for g in games:
        by_week.setdefault(g["week_id"], []).append(g)
    for wk, wk_games in by_week.items():
        path = SPLITS_DIR / "weeks" / wk / f"{today}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"pulled_at": now.isoformat(timespec="seconds"), "games": wk_games}, indent=2) + "\n")

    # 2. Splash lines, and the DraftKings line captured when they came in
    splash_rows, splash_copied_at = load_splash_lines()
    for g in games:
        g["splash_line"] = splash_line_for(g, splash_rows) if splash_rows else None
    if splash_rows:
        matched = sum(1 for g in games if g["splash_line"] is not None)
        print(f"Splash lines: {len(splash_rows)} rows, {matched} matched to games")

    # "Home Line" on the page: DraftKings' line in the first run that sees a
    # new Splash copy (the manual Pull Splits after pasting). Kept until the
    # next copy, so later runs don't overwrite it.
    baseline_path = SPLITS_DIR / "splash_baseline.json"
    baseline = load_json(baseline_path, {})
    if splash_copied_at and baseline.get("copied_at") != splash_copied_at:
        baseline = {"copied_at": splash_copied_at, "captured_at": now.isoformat(timespec="seconds"), "lines": {}}
    for g in games:
        key = f"{g['league']}:{g['id']}"
        if g["splash_line"] is not None and g["line"] is not None and key not in baseline.get("lines", {}):
            baseline.setdefault("lines", {})[key] = g["line"]
        g["dk_at_splash"] = baseline.get("lines", {}).get(key) if g["splash_line"] is not None else None
    if splash_copied_at:
        baseline_path.write_text(json.dumps(baseline, indent=2) + "\n")

    # 3. Market move + sharp flag. The move is DraftKings now vs the opening
    # line, which is DraftKings' Monday ~9 AM line from the Weekly Lines pull.
    # Falls back to DraftKings at Splash time, then Tuesday, if there's no Monday line.
    cache, monday_cache = {}, {}
    for g in games:
        g["tue_line"], g["tue_source"] = tuesday_line(g, cache)
        g["open_line"] = monday_line(g, monday_cache)
        base = next((b for b in (g["open_line"], g["dk_at_splash"], g["tue_line"]) if b is not None), None)
        g["move"] = round(g["line"] - base, 1) if g["line"] is not None and base is not None else None
        # With a Splash slate loaded, only its games can be flagged - the page shows nothing else
        on_slate = g["splash_line"] is not None or not splash_rows
        g["flag"] = sharp_flag(g) if on_slate else None

    # 4. Flag log. A flag stays live until kickoff, then freezes with the
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

    # 5. Grade anything final
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

    # 6. What the page reads
    for g in games:
        g.update({k: finals[f"{g['league']}:{g['id']}"][k] for k in ("home_score", "away_score")}
                 if f"{g['league']}:{g['id']}" in finals else {})
    current = {
        "pulled_at": now.isoformat(timespec="seconds"),
        "edge_threshold": EDGE_THRESHOLD,
        "splash_copied_at": splash_copied_at,
        "splash_baseline_at": baseline.get("captured_at") if splash_copied_at else None,
        "games": sorted(games, key=lambda g: g["start_time"]),
    }
    (SPLITS_DIR / "current.json").write_text(json.dumps(current, indent=2) + "\n")

    flagged = sum(1 for g in games if g["flag"])
    with_splits = sum(1 for g in games if g["home_money"] is not None)
    print(f"{len(games)} games, {with_splits} with splits, {flagged} flagged, {len(flags)} flags logged")


if __name__ == "__main__":
    main()
