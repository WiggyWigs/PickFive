#!/usr/bin/env python3
"""
Pull Bank Builder entries from the published Google Sheet (CSV export)
and write a normalized data/bank_builder.json.

Requires BANK_BUILDER_SHEET_CSV_URL (a Google Sheets "Publish to web"
CSV link, set as a repo Actions variable, not a secret - it's a public
read-only export URL, not a credential).

Expected sheet headers (case/whitespace-insensitive):
  Week      - a plain sequential week number: 1, 2, 3...
  Wager     - shown as-is
  Pick      - shown as-is
  Amount    - amount risked
  Payout    - amount returned (blank if the wager hasn't resolved yet)
  Net       - profit/loss for that wager (blank if pending)
"""
import csv
import io
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

HEADER_ALIASES = {
    "week": "week",
    "wager": "wager",
    "pick": "pick",
    "amount": "amount",
    "payout": "payout",
    "net": "net",
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


def normalize_number(v: str):
    """Handles $, commas, and (123.45)-style negative notation."""
    v = (v or "").strip()
    if not v:
        return None
    negative = v.startswith("(") and v.endswith(")")
    v = v.strip("()")
    v = re.sub(r"[$,]", "", v).strip()
    if not v:
        return None
    try:
        n = float(v)
        return -n if negative else n
    except ValueError:
        print(f"[warn] couldn't parse number: {v!r}", file=sys.stderr)
        return None


def normalize_week(v: str):
    v = (v or "").strip()
    if not v:
        return None
    try:
        return int(v)
    except ValueError:
        print(f"[warn] couldn't parse week value: {v!r}", file=sys.stderr)
        return None


def main():
    csv_url = os.environ.get("BANK_BUILDER_SHEET_CSV_URL")
    if not csv_url:
        print("BANK_BUILDER_SHEET_CSV_URL is not set (add it under repo Settings > Secrets and variables > Actions > Variables)", file=sys.stderr)
        sys.exit(1)

    raw = fetch_csv(csv_url)
    reader = csv.DictReader(io.StringIO(raw))

    if reader.fieldnames is None:
        print("[error] sheet CSV appears empty or unreadable", file=sys.stderr)
        sys.exit(1)

    field_map = {original: normalize_header(original) for original in reader.fieldnames}

    entries = []
    skipped = 0
    for i, raw_row in enumerate(reader, start=2):  # row 1 is the header
        row = {field_map[k]: v for k, v in raw_row.items() if k in field_map}

        week = normalize_week(row.get("week"))
        wager = (row.get("wager") or "").strip()
        pick = (row.get("pick") or "").strip()

        if week is None or not wager or not pick:
            skipped += 1
            print(f"[warn] row {i}: missing Week/Wager/Pick, skipping", file=sys.stderr)
            continue

        entries.append(
            {
                "week": week,
                "wager": wager,
                "pick": pick,
                "amount": normalize_number(row.get("amount")),
                "payout": normalize_number(row.get("payout")),
                "net": normalize_number(row.get("net")),
            }
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "bank_builder.json"
    out_path.write_text(json.dumps(entries, indent=2))

    print(f"Wrote {len(entries)} entries ({skipped} skipped) to {out_path}")


if __name__ == "__main__":
    main()
