#!/usr/bin/env python3
"""
scrape_dk_odds.py -- pull live pitcher-strikeout prop odds for today's
MLB slate from DraftKings' undocumented public JSON API.

Output: CSV in the format expected by strikeout_predictor.py --import-odds,
        ready to feed straight into the import flow.

Workflow:
  python scrape_dk_odds.py
    -> writes data/odds/dk_k_<date>.csv

  python scrape_dk_odds.py --print
    -> also dumps parsed lines to stdout

DraftKings API notes (undocumented, public, no auth):
  Base URL : https://sportsbook-nash.draftkings.com/api/sportscontent/dkusin/v1
  League   : MLB = 84240
  Category : 1031 (Pitcher Props)
  Subcat   : 15221 (Strikeouts Thrown O/U) -- Over/Under with two-sided odds
  Subcat   : 17323 (Strikeouts Thrown)     -- milestone lines (3+, 4+, 5+, ...)

  Schema: events / markets / selections are sibling arrays joined by IDs.
  Each market has its eventId; each selection has its marketId.
  Selections carry a `participants` array with the pitcher's name and
  `venueRole` ("HomePlayer" / "AwayPlayer").
  The O/U selections have `outcomeType` ("Over"/"Under") and `points`
  (the line, e.g. 5.5).

  TLS fingerprinting: DK's CDN rejects Python's default TLS handshake.
  Using curl_cffi with impersonate="chrome120" makes the handshake look
  like a real Chrome browser.  Falls back to standard requests if
  curl_cffi is not installed (may 403 from some IPs).

This script handles:
  - DK's en-dash minus sign (U+2212) in odds (replaces with ASCII '-')
  - Team-abbr mismatches (KCR/TBR/SFG/etc.)
  - UTC -> ET date conversion (DK timestamps are UTC; our slate is ET)
  - Pitcher-to-team mapping via selection participant venueRole
  - Both O/U (primary) and milestone (alt) line formats
"""

import csv
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# DraftKings API constants
# ---------------------------------------------------------------------------

DK_BASE = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusin/v1"
MLB_LEAGUE_ID = 84240
PITCHER_PROPS_CAT = 1031       # category "Pitcher Props"
STRIKEOUTS_OU_SUB = 15221      # subcategory "Strikeouts Thrown O/U"
STRIKEOUTS_ALT_SUB = 17323     # subcategory "Strikeouts Thrown" (milestones)

# Maximum units per bet -- referenced from CLAUDE.md money rules.
MAX_STAKE_UNITS = 2

# Full Chrome 123 browser fingerprint.  When using curl_cffi with
# impersonate="chrome120", most of these are overridden by the
# impersonation layer; we still set them so the plain-requests and
# urllib fallback paths send a realistic header set.
HEADERS = {
    "User-Agent":         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/123.0.0.0 Safari/537.36",
    "Accept":             "application/json, text/plain, */*",
    # gzip + deflate ONLY -- urllib doesn't decode brotli without an
    # extra dep, and `requests` decodes gzip/deflate transparently.
    "Accept-Encoding":    "gzip, deflate",
    "Accept-Language":    "en-US,en;q=0.9",
    "Referer":            "https://sportsbook.draftkings.com/",
    "Origin":             "https://sportsbook.draftkings.com",
    "sec-ch-ua":          '"Chromium";v="123", "Not(A:Brand";v="24", "Google Chrome";v="123"',
    "sec-ch-ua-mobile":   "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest":     "empty",
    "Sec-Fetch-Mode":     "cors",
    "Sec-Fetch-Site":     "same-site",
    "Connection":         "keep-alive",
}

# DK shortName -> our internal abbr.  Most match exactly; this maps
# the divergent ones.  Verified live from NRFI scraper + diagnostic
# runs 2026-08.
DK_TO_OUR_ABBR = {
    "A'S":  "OAK",   "OAK": "OAK",   "ATH": "OAK",
    "KCR":  "KC",    "KAN": "KC",
    "TBR":  "TB",    "TAM": "TB",
    "SFG":  "SF",    "SF":  "SF",
    "SDP":  "SD",    "SD":  "SD",
    "CHW":  "CWS",   "CWS": "CWS",
    "WAS":  "WSH",   "WSH": "WSH",
    # All others (NYY, BOS, LAA, ATL, ...) match exactly
}


def normalize_abbr(dk_abbr: str) -> str:
    """Map a DraftKings team shortName to our internal abbr."""
    a = (dk_abbr or "").strip().upper()
    return DK_TO_OUR_ABBR.get(a, a)


def parse_american_odds(s: str) -> str:
    """DK uses U+2212 (real minus sign) in displayOdds.american.
    Convert to ASCII '-' so downstream tools parse correctly."""
    if not s:
        return ""
    return s.replace("−", "-").strip()


# ---------------------------------------------------------------------------
# HTTP transport -- three tiers of fallback
# ---------------------------------------------------------------------------

def _build_session():
    """Return (session, use_curl_cffi) using the best available HTTP lib.

    Preference order:
      1. curl_cffi  -- TLS fingerprint impersonation, defeats DK CDN bot
                       detection.
      2. requests   -- connection reuse, gzip decoding, but Python TLS
                       fingerprint may trigger 403 from some IPs.
      3. None       -- caller falls back to urllib.
    """
    try:
        from curl_cffi import requests as _curl_requests
        sess = _curl_requests.Session(impersonate="chrome120")
        sess.headers.update(HEADERS)
        return sess, True
    except ImportError:
        pass
    try:
        import requests as _std_requests
        sess = _std_requests.Session()
        sess.headers.update(HEADERS)
        return sess, False
    except ImportError:
        pass
    return None, False


def _warmup(sess):
    """Send a warmup GET to the DK sportsbook MLB page.

    A real browser visiting the API has cookies from a prior page load;
    without the warmup our session has none, which DK's CDN may flag.
    Failure is non-fatal.
    """
    try:
        warmup = sess.get(
            "https://sportsbook.draftkings.com/leagues/baseball/mlb",
            timeout=(10, 20),
        )
        if warmup.status_code != 200:
            print(
                f"  DK warmup GET returned {warmup.status_code} "
                f"(non-fatal; proceeding)",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"  DK warmup GET failed ({type(exc).__name__}); proceeding",
            file=sys.stderr,
        )


def fetch_dk_strikeout_data(
    subcategory_id: int = STRIKEOUTS_OU_SUB,
    retries: int = 3,
    backoff: float = 2.0,
) -> dict:
    """Fetch the DK API response for a specific Pitcher Props subcategory.

    Returns the parsed JSON dict with keys: events, markets, selections,
    categories, subcategories, etc.

    Tries curl_cffi first (TLS impersonation), then standard requests,
    then urllib as a last resort.
    """
    url = (
        f"{DK_BASE}/leagues/{MLB_LEAGUE_ID}"
        f"/categories/{PITCHER_PROPS_CAT}"
        f"/subcategories/{subcategory_id}"
    )

    sess, use_curl = _build_session()
    if sess is None:
        return _fetch_via_urllib(url, retries=retries, backoff=backoff)

    lib_name = "curl_cffi" if use_curl else "requests"
    _warmup(sess)

    last_exc: Exception | None = None
    try:
        for attempt in range(retries):
            try:
                resp = sess.get(url, timeout=(10, 45))
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:
                last_exc = exc
                if attempt < retries - 1:
                    wait = backoff ** attempt
                    print(
                        f"  DK fetch attempt {attempt+1}/{retries} failed "
                        f"({type(exc).__name__}: {exc}); retrying in {wait:.1f}s...",
                        file=sys.stderr,
                    )
                    time.sleep(wait)
    finally:
        sess.close()

    raise last_exc if last_exc else RuntimeError("DK fetch failed")


def _fetch_via_urllib(url: str, retries: int = 3, backoff: float = 2.0) -> dict:
    """Last-resort urllib fetch path.  No TLS impersonation, so this may
    get 403'd by DK's CDN depending on the egress IP."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=45) as resp:
                return json.loads(resp.read())
        except Exception as exc:
            last_exc = exc
            if attempt < retries - 1:
                wait = backoff ** attempt
                print(
                    f"  DK fetch (urllib) attempt {attempt+1}/{retries} "
                    f"failed ({exc!r}); retrying in {wait:.1f}s...",
                    file=sys.stderr,
                )
                time.sleep(wait)
    raise last_exc if last_exc else RuntimeError("DK fetch failed (urllib)")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def utc_iso_to_et_date(utc_iso: str) -> str:
    """Convert DK's startEventDate (UTC) to the ET-local calendar date.
    Critical for late-night West Coast games that start past midnight UTC
    but are listed on the previous ET date in our slate."""
    if not utc_iso:
        return ""
    s = utc_iso.replace("Z", "+00:00")
    if "." in s:
        head, _, tail = s.partition(".")
        plus = tail.find("+")
        s = head + (tail[plus:] if plus >= 0 else "")
    try:
        utc_dt = datetime.fromisoformat(s)
    except ValueError:
        return ""
    et_dt = utc_dt.astimezone(ZoneInfo("America/New_York"))
    return et_dt.strftime("%Y-%m-%d")


def _pitcher_name_from_market(market_name: str, suffix: str) -> str:
    """Extract pitcher name from market name like
    'Grayson Rodriguez Strikeouts Thrown O/U'."""
    if suffix and market_name.endswith(suffix):
        return market_name[: -len(suffix)].strip()
    return market_name.strip()


def _pitcher_team(
    selection: dict,
    event: dict,
) -> str:
    """Determine which team the pitcher belongs to from the selection's
    participant venueRole and the event's participants.

    venueRole is "HomePlayer" or "AwayPlayer".  We match that to the
    event's participants list to find the team shortName.
    """
    # Get venueRole from the selection's participant
    sel_parts = selection.get("participants", [])
    venue_role = ""
    for sp in sel_parts:
        vr = (sp.get("venueRole") or "").lower()
        if vr:
            venue_role = vr
            break

    # Parse event name "Away @ Home" to determine away/home teams
    event_parts = event.get("participants", [])
    name = event.get("name", "") or ""
    away_abbr = home_abbr = ""

    if " @ " in name and len(event_parts) == 2:
        away_str, home_str = name.split(" @ ", 1)
        for p in event_parts:
            short = (p.get("metadata") or {}).get("shortName") or ""
            p_name = p.get("name", "")
            if p_name == away_str.strip():
                away_abbr = normalize_abbr(short)
            elif p_name == home_str.strip():
                home_abbr = normalize_abbr(short)

    # Fallback: first participant = home, second = away (DK convention)
    if (not away_abbr or not home_abbr) and len(event_parts) == 2:
        home_abbr = home_abbr or normalize_abbr(
            (event_parts[0].get("metadata") or {}).get("shortName", "")
        )
        away_abbr = away_abbr or normalize_abbr(
            (event_parts[1].get("metadata") or {}).get("shortName", "")
        )

    if "home" in venue_role:
        return home_abbr
    elif "away" in venue_role:
        return away_abbr
    else:
        # venueRole unknown -- return empty; caller can try market name
        return ""


# ---------------------------------------------------------------------------
# Extract structured odds from API response
# ---------------------------------------------------------------------------

def extract_ou_odds(data: dict) -> list[dict]:
    """Parse the Strikeouts Thrown O/U response (subcategory 15221).

    Each market is one pitcher's O/U line.  Two selections per market:
    Over and Under, each with `points` (the line, e.g. 5.5) and
    `displayOdds.american`.

    Returns one dict per pitcher with keys:
      pitcher_name, team, line, over_odds, under_odds, event_id,
      event_name, start_time_utc, date
    """
    events_by_id = {e["id"]: e for e in data.get("events", [])}
    selections = data.get("selections", [])
    markets = data.get("markets", [])

    # Group selections by marketId for fast lookup
    sels_by_market: dict[str, list[dict]] = {}
    for s in selections:
        mid = s.get("marketId")
        if mid:
            sels_by_market.setdefault(mid, []).append(s)

    out: list[dict] = []
    for m in markets:
        if m.get("subcategoryId") != STRIKEOUTS_OU_SUB:
            continue

        event = events_by_id.get(m.get("eventId"))
        if not event:
            continue

        pitcher_name = _pitcher_name_from_market(
            m.get("name", ""), " Strikeouts Thrown O/U"
        )

        market_sels = sels_by_market.get(m["id"], [])
        over_odds = ""
        under_odds = ""
        line = ""
        team = ""

        for s in market_sels:
            outcome = (s.get("outcomeType") or "").lower()
            american = parse_american_odds(
                (s.get("displayOdds") or {}).get("american", "")
            )
            pts = s.get("points")

            if outcome == "over":
                over_odds = american
                if pts is not None:
                    line = str(pts)
            elif outcome == "under":
                under_odds = american
                if pts is not None and not line:
                    line = str(pts)

            # Get team from first selection with participant info
            if not team:
                team = _pitcher_team(s, event)

        if not over_odds and not under_odds:
            continue

        date_iso = utc_iso_to_et_date(event.get("startEventDate", ""))

        out.append({
            "pitcher_name":   pitcher_name,
            "team":           team,
            "line":           line,
            "over_odds":      over_odds,
            "under_odds":     under_odds,
            "event_id":       str(m.get("eventId", "")),
            "event_name":     event.get("name", ""),
            "start_time_utc": event.get("startEventDate", "") or "",
            "date":           date_iso,
        })

    return out


def extract_alt_lines(data: dict) -> list[dict]:
    """Parse the Strikeouts Thrown response (subcategory 17323).

    Each market is one pitcher with milestone selections (3+, 4+, 5+, ...).
    Each selection has `milestoneValue` (the K threshold) and a single
    price (the "over" price for reaching that milestone).

    Returns one dict per (pitcher, milestone) with keys:
      pitcher_name, team, milestone, odds, event_id, event_name,
      start_time_utc, date
    """
    events_by_id = {e["id"]: e for e in data.get("events", [])}
    selections = data.get("selections", [])
    markets = data.get("markets", [])

    sels_by_market: dict[str, list[dict]] = {}
    for s in selections:
        mid = s.get("marketId")
        if mid:
            sels_by_market.setdefault(mid, []).append(s)

    out: list[dict] = []
    for m in markets:
        if m.get("subcategoryId") != STRIKEOUTS_ALT_SUB:
            continue

        event = events_by_id.get(m.get("eventId"))
        if not event:
            continue

        pitcher_name = _pitcher_name_from_market(
            m.get("name", ""), " Strikeouts Thrown"
        )
        date_iso = utc_iso_to_et_date(event.get("startEventDate", ""))

        market_sels = sels_by_market.get(m["id"], [])
        team = ""

        for s in market_sels:
            american = parse_american_odds(
                (s.get("displayOdds") or {}).get("american", "")
            )
            milestone = s.get("milestoneValue")
            if milestone is None or not american:
                continue

            if not team:
                team = _pitcher_team(s, event)

            out.append({
                "pitcher_name":   pitcher_name,
                "team":           team,
                "milestone":      str(milestone),
                "odds":           american,
                "event_id":       str(m.get("eventId", "")),
                "event_name":     event.get("name", ""),
                "start_time_utc": event.get("startEventDate", "") or "",
                "date":           date_iso,
            })

    return out


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def fetch_dk_strikeout_props(retries: int = 3, backoff: float = 2.0) -> list[dict]:
    """Fetch current DraftKings pitcher strikeout prop odds.

    Returns a list of dicts, each containing:
      pitcher_name, team, line, over_odds, under_odds, event_id
    """
    data = fetch_dk_strikeout_data(
        subcategory_id=STRIKEOUTS_OU_SUB,
        retries=retries,
        backoff=backoff,
    )
    return extract_ou_odds(data)


def fetch_dk_strikeout_alts(retries: int = 3, backoff: float = 2.0) -> list[dict]:
    """Fetch DraftKings milestone/alt strikeout lines (3+, 4+, ...).

    Returns a list of dicts, each containing:
      pitcher_name, team, milestone, odds, event_id
    """
    data = fetch_dk_strikeout_data(
        subcategory_id=STRIKEOUTS_ALT_SUB,
        retries=retries,
        backoff=backoff,
    )
    return extract_alt_lines(data)


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

OU_FIELDS = [
    "date", "pitcher_name", "team", "line", "over_odds", "under_odds",
    "event_id", "event_name", "start_time_utc",
]

ALT_FIELDS = [
    "date", "pitcher_name", "team", "milestone", "odds",
    "event_id", "event_name", "start_time_utc",
]


def write_csv(rows: list[dict], path: Path, fields: list[str]) -> None:
    """Write rows to CSV with atomic write (tempfile + replace)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile, os
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), suffix=".tmp", prefix=path.stem
    )
    try:
        with os.fdopen(tmp_fd, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# Subcategories in Pitcher Props that could someday carry an under-side
# alt strikeout market. Probed empty on 2026-08-05 (evening); the
# morning automation re-probes daily and logs if one ever populates.
ALT_UNDER_PROBE_SUBS = [16217, 16268, 12975]


def probe_alt_under_markets() -> None:
    """Check candidate subcategories for an under-side alt K market.

    DK's known alt board (17323) sells only "X+" overs. If DK ever
    lists alternate unders, one of these probes will light up in the
    morning log and we wire it into the ladder for real under pricing.
    """
    for sub in ALT_UNDER_PROBE_SUBS:
        try:
            d = fetch_dk_strikeout_data(sub, retries=1)
            markets = d.get("markets", [])
            sels = d.get("selections", [])
            labels = sorted({str(s.get("label")) for s in sels})[:6]
            print(f"  probe sub {sub}: {len(markets)} markets, "
                  f"{len(sels)} selections{', labels: ' + str(labels) if sels else ''}")
            if any("under" in str(s.get("label", "")).lower() for s in sels):
                print(f"  *** sub {sub} HAS UNDER SELECTIONS — wire it in! ***")
        except Exception as exc:
            print(f"  probe sub {sub}: failed ({exc})")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Scrape DraftKings pitcher strikeout prop odds",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--probe-unders", action="store_true",
        help="Probe candidate subcategories for an under-side alt K market",
    )
    parser.add_argument(
        "--output", metavar="FILE",
        help="Output CSV path (default: data/odds/dk_k_<today_ET>.csv)",
    )
    parser.add_argument(
        "--alts", action="store_true",
        help="Also fetch milestone/alt lines and write a second CSV",
    )
    parser.add_argument(
        "--print", dest="do_print", action="store_true",
        help="Print parsed odds to stdout",
    )
    args = parser.parse_args()

    if args.probe_unders:
        print("Probing candidate under-side alt K subcategories...")
        probe_alt_under_markets()
        return

    et_today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    # -- Over/Under lines (primary) --
    print("Fetching DraftKings pitcher strikeout O/U lines...", flush=True)
    try:
        ou_rows = fetch_dk_strikeout_props()
    except Exception as exc:
        sys.exit(f"  Fetch failed: {exc}")

    if not ou_rows:
        et_now = datetime.now(ZoneInfo("America/New_York"))
        is_prime = 9 <= et_now.hour < 17
        if is_prime:
            print(
                "  WARNING: 0 strikeout O/U markets returned during prime "
                f"hours ({et_now.strftime('%I:%M %p ET')}).  DraftKings may "
                f"have changed the API subcategory IDs (currently "
                f"{STRIKEOUTS_OU_SUB}).  Verify at "
                "https://sportsbook.draftkings.com/leagues/baseball/mlb "
                "(Network tab -> Pitcher Props -> Strikeouts Thrown).",
                file=sys.stderr,
            )
            sys.exit(2)
        print("  No strikeout O/U markets found (slate may be empty or all locked).")
        return

    ou_path = Path(args.output) if args.output else Path("data/odds") / f"dk_k_{et_today}.csv"
    write_csv(ou_rows, ou_path, OU_FIELDS)
    print(f"  Wrote {len(ou_rows)} pitcher O/U lines -> {ou_path}")

    if args.do_print:
        print()
        print(f"  {'Pitcher':<25} {'Team':>4}  {'Line':>5}  {'Over':>6}  {'Under':>6}  {'Matchup'}")
        print(f"  {'-'*25} {'----':>4}  {'-----':>5}  {'------':>6}  {'------':>6}  {'-'*20}")
        for r in sorted(ou_rows, key=lambda x: x.get("team", "")):
            print(
                f"  {r['pitcher_name']:<25} {r['team']:>4}  "
                f"{r['line']:>5}  {r['over_odds']:>6}  {r['under_odds']:>6}  "
                f"{r['event_name']}"
            )

    # -- Alt/milestone lines (optional) --
    if args.alts:
        print()
        print("Fetching DraftKings pitcher strikeout alt lines...", flush=True)
        try:
            alt_rows = fetch_dk_strikeout_alts()
        except Exception as exc:
            print(f"  Alt-line fetch failed: {exc}", file=sys.stderr)
            alt_rows = []

        if alt_rows:
            alt_path = Path("data/odds") / f"dk_k_alts_{et_today}.csv"
            write_csv(alt_rows, alt_path, ALT_FIELDS)
            print(f"  Wrote {len(alt_rows)} alt lines -> {alt_path}")

            if args.do_print:
                print()
                print(f"  {'Pitcher':<25} {'Team':>4}  {'K+':>3}  {'Odds':>6}")
                for r in sorted(alt_rows, key=lambda x: (x.get("pitcher_name", ""), int(x.get("milestone", 0)))):
                    print(
                        f"  {r['pitcher_name']:<25} {r['team']:>4}  "
                        f"{r['milestone']:>2}+  {r['odds']:>6}"
                    )
        else:
            print("  No alt lines returned.")

    print()
    print(f"  Next: python strikeout_predictor.py --import-odds {ou_path}")


if __name__ == "__main__":
    main()
