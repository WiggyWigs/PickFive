#!/usr/bin/env python3
"""
Pull ledger entries from the published Google Sheet (CSV export) and
write a normalized data/ledger.json.

Every row is one of:
  - a wager: Bet does NOT contain "transfer" - Net is that wager's
    profit/loss, trusted as-is from the sheet.
  - a deposit: Bet contains "transfer" and Payout is positive.
  - a withdraw: Bet contains "transfer" and Payout is negative.

Requires LEDGER_SHEET_CSV_URL (a Google Sheets "Publish to web" CSV
link, set as a repo Actions variable, not a secret - it's a public
read-only export URL, not a credential).

Expected sheet headers (case/whitespace-insensitive):
  Date          - shown as-is
  Name          - person's name
  Bet           - description; contains "Transfer" for deposits/withdraws
  Bet_Amount    - amount risked, for wagers (unused for transfers)
  Payout        - amount returned for a wager, OR the signed transfer
                  amount for a deposit (positive) / withdraw (negative)
  Net           - that wager's profit/loss (unused for transfers)
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
    "date": "date",
    "name": "name",
    "bet": "bet",
    "bet_amount": "bet_amount",
    "bet amount": "bet_amount",
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


def main():
    csv_url = os.environ.get("LEDGER_SHEET_CSV_URL")
    if not csv_url:
        print("LEDGER_SHEET_CSV_URL is not set (add it under repo Settings > Secrets and variables > Actions > Variables)", file=sys.stderr)
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

        date = (row.get("date") or "").strip()
        name = (row.get("name") or "").strip()
        bet = (row.get("bet") or "").strip()

        if not date or not name or not bet:
            skipped += 1
            print(f"[warn] row {i}: missing Date/Name/Bet, skipping", file=sys.stderr)
            continue

        # Doesn't block the row - just flags it, since a year-less date
        # (e.g. "9/1" instead of "9/1/2025") is a real ambiguity once
        # entries span more than one season, but the row is still usable.
        if re.fullmatch(r"\d{1,2}[/-]\d{1,2}", date):
            print(f"[warn] row {i}: Date {date!r} has no year - reformat the Date column in the sheet to include one", file=sys.stderr)

        bet_amount = normalize_number(row.get("bet_amount"))
        payout = normalize_number(row.get("payout"))
        net = normalize_number(row.get("net"))

        is_transfer = "transfer" in bet.lower()

        if is_transfer:
            if payout is None:
                skipped += 1
                print(f"[warn] row {i}: Transfer row missing Payout amount, skipping", file=sys.stderr)
                continue
            if payout == 0:
                skipped += 1
                print(f"[warn] row {i}: Transfer row has $0 Payout, skipping", file=sys.stderr)
                continue
            entry_type = "deposit" if payout > 0 else "withdraw"
        else:
            entry_type = "wager"
            if net is None:
                print(f"[warn] row {i}: wager missing Net value", file=sys.stderr)

        entries.append(
            {
                "date": date,
                "name": name,
                "bet": bet,
                "bet_amount": bet_amount,
                "payout": payout,
                "net": net,
                "type": entry_type,
            }
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "ledger.json"
    out_path.write_text(json.dumps(entries, indent=2))

    print(f"Wrote {len(entries)} entries ({skipped} skipped) to {out_path}")


if __name__ == "__main__":
    main()
