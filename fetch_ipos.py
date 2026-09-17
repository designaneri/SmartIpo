"""
Fetches official IPO data from the Upstox v2 API and writes it to ipos.json.

Official fields fetched from Upstox include:
- IPO name
- Mainboard / SME
- bidding dates
- allotment date
- listing date
- price band
- lot size
- subscription
- issue size
- minimum application amount
- actual listing price, once available

Upstox does NOT provide GMP, so the existing GMP value in ipos.json
is preserved.

The manually maintained research fields are also preserved:
- verdict
- holdLabel
- rationale
- dataNote

REQUIRES:
    UPSTOX_EXTENDED_TOKEN

The token is supplied through the GitHub Actions secret and is never
stored in this file.
"""

import json
import os
import sys
import time
from datetime import date, timedelta

import requests


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

EXTENDED_TOKEN = os.environ.get("UPSTOX_EXTENDED_TOKEN")

BASE_URL = "https://api.upstox.com/v2/ipos"
OUTPUT_PATH = os.environ.get("OUTPUT_PATH", "ipos.json")

# Keep recently listed IPOs so the ledger can still show listing results
# and compare the actual listing price with the IPO price.
LISTED_LOOKBACK_DAYS = 21

PAGE_SIZE = 30

ISSUE_TYPE_LABEL = {
    "regular": "Mainboard",
    "sme": "SME",
}

# These fields are controlled manually by your IPO research / HTML.
# The API must never overwrite them.
MANUAL_FIELDS = {
    "gmp",
    "verdict",
    "holdLabel",
    "rationale",
    "dataNote",
}


# ---------------------------------------------------------------------------
# HTTP HELPERS
# ---------------------------------------------------------------------------

def _headers():
    if not EXTENDED_TOKEN:
        sys.exit(
            "UPSTOX_EXTENDED_TOKEN is not set. "
            "Add the extended token as a GitHub Actions secret."
        )

    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {EXTENDED_TOKEN}",
    }


def api_get(url, params=None, retries=3):
    """
    GET helper with basic retry handling for temporary API/network errors.
    """

    last_error = None

    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                url,
                headers=_headers(),
                params=params,
                timeout=30,
            )

            # Rate limited
            if response.status_code == 429:
                wait_seconds = min(10 * attempt, 30)
                print(
                    f"WARN: Upstox rate limit hit. "
                    f"Retrying in {wait_seconds}s...",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()

            payload = response.json()

            if "data" not in payload:
                raise RuntimeError(
                    f"Unexpected Upstox response: {payload}"
                )

            return payload["data"]

        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc

            if attempt < retries:
                wait_seconds = 3 * attempt
                print(
                    f"WARN: API request failed "
                    f"(attempt {attempt}/{retries}): {exc}. "
                    f"Retrying in {wait_seconds}s...",
                    file=sys.stderr,
                )
                time.sleep(wait_seconds)
            else:
                raise last_error


# ---------------------------------------------------------------------------
# UPSTOX API
# ---------------------------------------------------------------------------

def get_ipos(status, issue_type=None, page_size=PAGE_SIZE):
    """
    Fetch every IPO for a lifecycle status.

    Upstox supports:
        upcoming
        open
        closed
        listed

    Pagination is handled so IPOs beyond the first page aren't lost.
    """

    all_records = []
    page = 1

    while True:
        params = {
            "status": status,
            "page_number": page,
            "records": page_size,
        }

        if issue_type:
            params["issue_type"] = issue_type

        page_data = api_get(BASE_URL, params=params)

        if not page_data:
            break

        all_records.extend(page_data)

        if len(page_data) < page_size:
            break

        page += 1

    return all_records


def get_ipo_details(ipo_id):
    """
    Fetch the complete IPO record.

    This contains timeline, lot size, price band,
    subscription and listing_price.
    """

    return api_get(f"{BASE_URL}/{ipo_id}")


# ---------------------------------------------------------------------------
# FORMATTING HELPERS
# ---------------------------------------------------------------------------

def fmt_inr(amount):
    """
    1234567 -> '12,34,567'
    """

    if amount is None:
        return None

    try:
        value = float(amount)
    except (TypeError, ValueError):
        return None

    s = str(int(round(value)))

    if len(s) <= 3:
        return s

    head = s[:-3]
    tail = s[-3:]

    parts = []

    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]

    if head:
        parts.insert(0, head)

    return ",".join(parts + [tail])


def format_issue_size(value):
    if value in (None, ""):
        return None

    try:
        return f"₹{fmt_inr(float(value))} Cr"
    except (TypeError, ValueError):
        return None


def format_subscription(value):
    if value in (None, ""):
        return None

    try:
        return f"{float(value):.2f}x"
    except (TypeError, ValueError):
        return None


def calculate_listing_gain(listing_price, issue_price):
    """
    Calculate listing gain against the IPO issue/cut-off price.

    Example:
        issue price = 100
        listing price = 120
        result = 20.0
    """

    if listing_price in (None, ""):
        return None

    if issue_price in (None, ""):
        return None

    try:
        listing_price = float(listing_price)
        issue_price = float(issue_price)

        if issue_price == 0:
            return None

        return round(
            ((listing_price - issue_price) / issue_price) * 100,
            2,
        )

    except (TypeError, ValueError):
        return None


def normalize_name(name):
    """
    Used to match an API IPO with an existing manually researched IPO.
    """

    if not name:
        return ""

    return (
        str(name)
        .lower()
        .replace("&", "and")
        .replace(".", "")
        .replace(",", "")
        .replace("-", " ")
        .replace("_", " ")
        .strip()
    )


# ---------------------------------------------------------------------------
# DATA MAPPING
# ---------------------------------------------------------------------------

def to_ledger_record(summary, details, existing=None):
    """
    Convert Upstox data into the structure expected by ipo-ledger.html.

    Existing manual fields are preserved.
    """

    existing = existing or {}

    timeline = details.get("timeline") or {}

    lot_size = details.get("lot_size")

    max_price = details.get("maximum_price")
    min_price = details.get("minimum_price")

    cut_off = details.get("cut_off_price") or max_price

    # ---------------------------------------------------------------
    # Minimum application amount
    # ---------------------------------------------------------------

    min_amount = None

    if lot_size and cut_off:
        try:
            min_amount = f"₹{fmt_inr(float(lot_size) * float(cut_off))}"
        except (TypeError, ValueError):
            pass

    # ---------------------------------------------------------------
    # Subscription
    # ---------------------------------------------------------------

    subs_raw = (
        details.get("total_subscription")
        if details.get("total_subscription") not in (None, "")
        else summary.get("total_subscription")
    )

    subs = format_subscription(subs_raw)

    # ---------------------------------------------------------------
    # Issue size
    # ---------------------------------------------------------------

    issue_size = details.get("issue_size")
    issue_size_formatted = format_issue_size(issue_size)

    # ---------------------------------------------------------------
    # Actual listing price
    # ---------------------------------------------------------------

    listing_price = details.get("listing_price")

    # Use cut-off price as the issue price for listing-gain calculation.
    # If cut-off isn't available, use the upper price band.
    issue_price = cut_off or max_price

    listing_gain_pct = calculate_listing_gain(
        listing_price,
        issue_price,
    )

    # ---------------------------------------------------------------
    # API-owned fields
    # ---------------------------------------------------------------

    api_record = {
        "name": details.get("name") or summary.get("name"),

        "type": ISSUE_TYPE_LABEL.get(
            details.get("issue_type"),
            details.get("issue_type"),
        ),

        "open": details.get("bidding_start_date"),

        "close": details.get("bidding_end_date"),

        "allotment": timeline.get("allotment_date"),

        "listing": timeline.get("listing_date"),

        "low": min_price,

        "high": max_price,

        "lot": lot_size,

        "subs": subs,

        "issueSize": issue_size_formatted,

        "minAmount": min_amount,

        # IMPORTANT:
        # Upstox does not provide GMP.
        # Preserve the existing manually entered value below.
        "listingPrice": listing_price,

        "listingGainPct": listing_gain_pct,

        # Additional useful official fields
        "registrar": details.get("registrar_info"),

        "listingExchange": details.get("listing_exchange"),

        "symbol": details.get("symbol"),

        "isin": details.get("isin"),

        "industry": details.get("industry"),

        "faceValue": details.get("face_value"),

        "minimumQuantity": details.get("minimum_quantity"),

        "cutOffPrice": details.get("cut_off_price"),

        "rhpUrl": details.get("rhp_url"),

        "drhpUrl": details.get("drhp_url"),

        "upstoxId": details.get("id") or summary.get("id"),
    }

    # ---------------------------------------------------------------
    # Preserve EVERYTHING from the old record that isn't explicitly
    # replaced by an API-owned field.
    # ---------------------------------------------------------------

    merged = dict(existing)

    merged.update(api_record)

    # ---------------------------------------------------------------
    # Explicitly preserve manual research fields.
    # ---------------------------------------------------------------

    for field in MANUAL_FIELDS:
        if field in existing:
            merged[field] = existing[field]

    # GMP is NOT supplied by Upstox.
    # If there was no previous GMP, leave it as null.
    if "gmp" not in merged:
        merged["gmp"] = None

    return merged


# ---------------------------------------------------------------------------
# EXISTING FILE
# ---------------------------------------------------------------------------

def load_existing_records():
    """
    Load the current ipos.json so manual research data is preserved.
    """

    if not os.path.exists(OUTPUT_PATH):
        return []

    try:
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            print(
                "WARN: Existing ipos.json is not a list. "
                "Starting with an empty dataset.",
                file=sys.stderr,
            )
            return []

        return data

    except (OSError, json.JSONDecodeError) as exc:
        print(
            f"WARN: Could not read existing {OUTPUT_PATH}: {exc}",
            file=sys.stderr,
        )
        return []


def build_existing_lookup(existing_records):
    """
    Build a normalized-name lookup for existing IPOs.
    """

    lookup = {}

    for record in existing_records:
        name = record.get("name")

        if name:
            lookup[normalize_name(name)] = record

    return lookup


# ---------------------------------------------------------------------------
# FETCH ALL
# ---------------------------------------------------------------------------

def fetch_all(existing_records):
    """
    Fetch all relevant IPOs from Upstox.

    Existing records are retained so a temporary API failure does not
    destroy your manually maintained IPO research.
    """

    existing_lookup = build_existing_lookup(existing_records)

    fetched_records = []
    seen_ids = set()
    fetched_names = set()

    cutoff = date.today() - timedelta(days=LISTED_LOOKBACK_DAYS)

    for status in ["upcoming", "open", "closed", "listed"]:

        try:
            summaries = get_ipos(status)

        except Exception as exc:
            print(
                f"WARN: Could not fetch status={status}: {exc}",
                file=sys.stderr,
            )
            continue

        print(
            f"Fetched {len(summaries)} {status} IPO summaries."
        )

        for summary in summaries:

            ipo_id = summary.get("id")

            if not ipo_id:
                continue

            if ipo_id in seen_ids:
                continue

            # -------------------------------------------------------
            # Keep only recently listed IPOs.
            # -------------------------------------------------------

            if status == "listed":

                close_str = summary.get("bidding_end_date")

                if close_str:
                    try:
                        if date.fromisoformat(close_str) < cutoff:
                            continue
                    except ValueError:
                        pass

            # -------------------------------------------------------
            # Fetch detailed record.
            # -------------------------------------------------------

            try:
                details = get_ipo_details(ipo_id)

            except Exception as exc:
                print(
                    f"WARN: Could not fetch details for "
                    f"{ipo_id}: {exc}",
                    file=sys.stderr,
                )

                # If we already have this IPO locally, preserve it.
                summary_name = normalize_name(summary.get("name"))

                if summary_name and summary_name in existing_lookup:
                    fetched_records.append(
                        existing_lookup[summary_name]
                    )
                    fetched_names.add(summary_name)

                continue

            seen_ids.add(ipo_id)

            name = (
                details.get("name")
                or summary.get("name")
                or ""
            )

            normalized_name = normalize_name(name)

            existing = existing_lookup.get(
                normalized_name,
                {},
            )

            record = to_ledger_record(
                summary,
                details,
                existing,
            )

            fetched_records.append(record)

            if normalized_name:
                fetched_names.add(normalized_name)

    # -------------------------------------------------------------------
    # Preserve old records that Upstox didn't return this time.
    #
    # This prevents temporary API omissions from deleting your research.
    # -------------------------------------------------------------------

    for old_record in existing_records:

        old_name = normalize_name(
            old_record.get("name")
        )

        if not old_name:
            continue

        if old_name not in fetched_names:
            fetched_records.append(old_record)

    return fetched_records


# ---------------------------------------------------------------------------
# SORTING
# ---------------------------------------------------------------------------

def sort_records(records):
    """
    Sort primarily by listing date, then opening date, then name.
    """

    def sort_key(record):
        return (
            record.get("listing") or "9999-99-99",
            record.get("open") or "9999-99-99",
            (record.get("name") or "").lower(),
        )

    return sorted(records, key=sort_key)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():

    print("Starting Upstox IPO fetch...")

    existing_records = load_existing_records()

    print(
        f"Existing {OUTPUT_PATH}: "
        f"{len(existing_records)} records."
    )

    records = fetch_all(existing_records)

    # ---------------------------------------------------------------
    # Safety check:
    # Never overwrite a good ipos.json with an empty API response.
    # ---------------------------------------------------------------

    if not records:

        if existing_records:

            print(
                "ERROR: Upstox returned no usable IPO records. "
                "Keeping existing ipos.json unchanged.",
                file=sys.stderr,
            )

            sys.exit(1)

        print(
            "ERROR: No IPO records were returned.",
            file=sys.stderr,
        )

        sys.exit(1)

    records = sort_records(records)

    # ---------------------------------------------------------------
    # Write JSON
    # ---------------------------------------------------------------

    with open(
        OUTPUT_PATH,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            records,
            f,
            indent=2,
            ensure_ascii=False,
        )

        f.write("\n")

    print(
        f"Wrote {len(records)} IPOs to {OUTPUT_PATH}"
    )


if __name__ == "__main__":
    main()
