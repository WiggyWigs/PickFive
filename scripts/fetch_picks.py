#!/usr/bin/env python3
"""
Pull picks from the published Google Sheet (CSV export) and write a
normalized data/picks.json.

Every field - Week, Person, Team Picked, Opponent, Home/Away, Fav/Dog,
Spread, Result - is entered by hand in the sheet and passed through
mostly as-is. This is intentional: the line at the moment a pick was
locked in on the actual contest site can differ from whatever our own
odds pull shows (different book, different timing), so we don't
attempt to re-derive it from data/weeks/ - we trust the sheet.

Requires PICKS_SHEET_CSV_URL (a Google Sheets "Publish to web" CSV
link, set as a repo Actions variable, not a secret - it's a public
read-only export URL, not a credential).

Expected sheet headers (case/whitespace-insensitive):
  Year          - season year, e.g. 2026
  Week          - week number, e.g. 1, 2, 3
  Person        - must match one of the known picker slots
  Team_Picked   - team name, shown as-is
  Home/Away     - "Home" or "Away" (optional)
  Spread        - the picked team's own spread, e.g. -3.5 or +6
  Result        - "W", "L", or blank if the game hasn't been played yet

Fav/Dog isn't a sheet column - it's derived from the sign of Spread
(negative = Favorite, positive = Underdog), so there's nothing to
keep in sync by hand.
"""
import csv
import io
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Canonical header names we look for, mapped from a normalized
# (lowercased, stripped) version of whatever the sheet actually has -
# keeps this resilient to minor renaming/whitespace in the sheet.
HEADER_ALIASES = {
    "year": "year",
    "week": "week",
    "person": "person",
    "team picked": "team_picked",
    "team_picked": "team_picked",
    "team": "team_picked",
    "home/away": "home_away",
    "home or away": "home_away",
    "spread": "spread",
    "result": "result",
    "w/l": "result",
}


def normalize_header(h: str) -> str:
    return HEADER_ALIASES.get(h.strip().lower(), h.strip().lower())


def fetch_csv(url: str) -> str:
    req = Request(url, headers={"User-Agent": "PickFive/1.0"})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode("utf-8-sig")
    except HTTPError as e:
        print(f"[error] fetching sheet: HTTP {e.code} - {e.read().decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"[error] fetching sheet: {e.reason}", file=sys.stderr)
        sys.exit(1)


def normalize_home_away(v: str):
    v = (v or "").strip().lower()
    if v.startswith("home"):
        return "Home"
    if v.startswith("away"):
        return "Away"
    return None


def derive_fav_dog(spread):
    if spread is None:
        return None
    if spread < 0:
        return "Favorite"
    if spread > 0:
        return "Underdog"
    return "Pick'em"


def normalize_result(v: str):
    v = (v or "").strip().upper()
    if v in ("W", "WIN"):
        return "W"
    if v in ("L", "LOSS"):
        return "L"
    return None


def normalize_year(v: str):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        print(f"[warn] couldn't parse year value: {v!r}", file=sys.stderr)
        return None


def normalize_spread(v: str):
    v = (v or "").strip().replace("+", "")
    if not v:
        return None
    try:
        return float(v)
    except ValueError:
        print(f"[warn] couldn't parse spread value: {v!r}", file=sys.stderr)
        return None


def main():
    csv_url = os.environ.get("PICKS_SHEET_CSV_URL")
    if not csv_url:
        print("PICKS_SHEET_CSV_URL is not set (add it under repo Settings > Secrets and variables > Actions > Variables)", file=sys.stderr)
        sys.exit(1)

    raw = fetch_csv(csv_url)
    reader = csv.DictReader(io.StringIO(raw))

    if reader.fieldnames is None:
        print("[error] sheet CSV appears empty or unreadable", file=sys.stderr)
        sys.exit(1)

    field_map = {original: normalize_header(original) for original in reader.fieldnames}

    picks = []
    skipped = 0
    for i, raw_row in enumerate(reader, start=2):  # row 1 is the header
        row = {field_map[k]: v for k, v in raw_row.items() if k in field_map}

        year = normalize_year(row.get("year"))
        week = (row.get("week") or "").strip()
        person = (row.get("person") or "").strip()
        team_picked = (row.get("team_picked") or "").strip()

        if year is None or not week or not person or not team_picked:
            skipped += 1
            print(f"[warn] row {i}: missing Year/Week/Person/Team Picked, skipping", file=sys.stderr)
            continue

        spread = normalize_spread(row.get("spread"))
        picks.append(
            {
                "year": year,
                "week": week,
                "person": person,
                "team_picked": team_picked,
                "home_away": normalize_home_away(row.get("home_away")),
                "fav_dog": derive_fav_dog(spread),
                "spread": spread,
                "result": normalize_result(row.get("result")),
            }
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "picks.json"
    out_path.write_text(json.dumps(picks, indent=2))

    print(f"Wrote {len(picks)} picks ({skipped} skipped) to {out_path}")


if __name__ == "__main__":
    main()
