"""
Fetches official IPO data (dates, price band, lot size, subscription, listing
price) from the Upstox v2 API and writes it to ipos.json in the shape
ipo-ledger.html expects.

WHAT THIS DOES NOT DO: fetch GMP. Upstox is a regulated broker and doesn't
publish grey-market data — that field is deliberately left null so the
ledger's existing manual GMP input still works. Same for the 16-factor
verdict/rationale: that's a judgment call, not something this script invents.

REQUIRES: an env var UPSTOX_ACCESS_TOKEN. Upstox tokens expire daily
(~3:30 AM IST) — see the GitHub Actions workflow for how that's handled.
"""

import json
import os
import sys
from datetime import date, timedelta

import requests

ACCESS_TOKEN = os.environ.get("UPSTOX_ACCESS_TOKEN")
BASE_URL = "https://api.upstox.com/v2/ipos"
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "ipos.json")

# How far back to keep "listed" IPOs around so the pending-listing check
# (did the verdict hold?) still has fresh listing_price data, without the
# file growing forever.
LISTED_LOOKBACK_DAYS = 21

ISSUE_TYPE_LABEL = {"regular": "Mainboard", "sme": "SME"}


def _headers():
    if not ACCESS_TOKEN:
        sys.exit("UPSTOX_ACCESS_TOKEN is not set — see README for how to get one.")
    return {"Accept": "application/json", "Authorization": f"Bearer {ACCESS_TOKEN}"}


def get_ipos(status, issue_type=None, page_size=30):
    """Every IPO for a given lifecycle status — pages through the full
    result set rather than assuming everything fits on page 1. A single
    page (the original version of this function) silently drops anything
    past the first `page_size` records, which is exactly how upcoming or
    closed IPOs go missing when the list is longer than expected."""
    all_records = []
    page = 1
    while True:
        params = {"status": status, "page_number": page, "records": page_size}
        if issue_type:
            params["issue_type"] = issue_type
        resp = requests.get(BASE_URL, headers=_headers(), params=params, timeout=20)
        resp.raise_for_status()
        page_data = resp.json()["data"]
        if not page_data:
            break
        all_records.extend(page_data)
        if len(page_data) < page_size:
            break  # last page was partial — nothing more to fetch
        page += 1
    return all_records


def get_ipo_details(ipo_id):
    """Full record for one IPO — includes timeline, lot size, listing_price."""
    resp = requests.get(f"{BASE_URL}/{ipo_id}", headers=_headers(), timeout=20)
    resp.raise_for_status()
    return resp.json()["data"]


def fmt_inr(amount):
    """1234567 -> '12,34,567' (Indian digit grouping)."""
    if amount is None:
        return None
    s = str(int(round(amount)))
    if len(s) <= 3:
        return s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts + [tail])


def to_ledger_record(summary, details):
    """Map one Upstox IPO into the object shape ipo-ledger.html reads."""
    timeline = details.get("timeline") or {}
    lot_size = details.get("lot_size")
    max_price = details.get("maximum_price") or None
    min_price = details.get("minimum_price") or None
    cut_off = details.get("cut_off_price") or max_price

    min_amount = None
    if lot_size and cut_off:
        min_amount = f"₹{fmt_inr(lot_size * cut_off)}"

    issue_size = details.get("issue_size")
    subs_raw = details.get("total_subscription") or summary.get("total_subscription")
    subs = f"{float(subs_raw):.2f}x" if subs_raw not in (None, "") else None

    return {
        "name": details.get("name") or summary.get("name"),
        "type": ISSUE_TYPE_LABEL.get(details.get("issue_type"), details.get("issue_type")),
        "open": details.get("bidding_start_date"),
        "close": details.get("bidding_end_date"),
        "allotment": timeline.get("allotment_date"),
        "listing": timeline.get("listing_date"),
        "low": min_price if min_price else None,
        "high": max_price if max_price else None,
        "lot": lot_size,
        "subs": subs,
        "issueSize": f"₹{issue_size} Cr" if issue_size else None,
        "minAmount": min_amount,
        "gmp": None,  # not available from Upstox — filled in manually in the UI
        "listingPrice": details.get("listing_price"),  # actual price, once known
        # verdict / holdLabel / rationale / dataNote intentionally omitted —
        # ipo-ledger.html hides the auto-read block gracefully when absent.
    }


def fetch_all():
    records = []
    seen_ids = set()

    cutoff = date.today() - timedelta(days=LISTED_LOOKBACK_DAYS)

    for status in ["upcoming", "open", "closed", "listed"]:
        try:
            summaries = get_ipos(status)
        except requests.HTTPError as e:
            print(f"WARN: could not fetch status={status}: {e}", file=sys.stderr)
            continue

        for summary in summaries:
            ipo_id = summary["id"]
            if ipo_id in seen_ids:
                continue

            if status == "listed":
                close_str = summary.get("bidding_end_date")
                if close_str:
                    try:
                        if date.fromisoformat(close_str) < cutoff:
                            continue
                    except ValueError:
                        pass

            try:
                details = get_ipo_details(ipo_id)
            except requests.HTTPError as e:
                print(f"WARN: could not fetch details for {ipo_id}: {e}", file=sys.stderr)
                continue

            seen_ids.add(ipo_id)
            records.append(to_ledger_record(summary, details))

    return records


def main():
    records = fetch_all()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(records)} IPOs to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
