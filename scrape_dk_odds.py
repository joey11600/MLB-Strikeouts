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
  Subcat   : 17413 (Outs Recorded O/U)     -- Over/Under, outs not strikeouts

  Those three are the whole of category 1031; enumerate the rest with
  the `subcategories` array that every response carries.  The outs board
  is 1:1 with the strikeout board -- measured 2026-08-08, both returned
  the same 14 pitchers -- so it costs one extra request, not a second
  discovery pass.

  Schema: events / markets / selections are sibling arrays joined by IDs.
  Each market has its eventId; each selection has its marketId.
  Selections carry a `participants` array with the pitcher's name and
  `venueRole` ("HomePlayer" / "AwayPlayer").
  The O/U selections have `outcomeType` ("Over"/"Under") and `points`
  (the line, e.g. 5.5).

  Bot gating -- measured 2026-08-06 from a residential IP:
    * curl_cffi impersonate=chrome120 ......... HTTP 200
    * curl_cffi chrome131/136/145/safari184/ff  HTTP 200
    * plain `requests` + browser headers ...... HTTP 200  <-- no TLS
                                                             impersonation
    * plain `requests` with the default
      python-requests User-Agent .............. HTTP 403
  So the gate this endpoint applies is (a) a browser-like User-Agent and
  (b) egress IP reputation -- NOT the TLS/JA3 fingerprint.  curl_cffi is
  kept because it costs nothing and hardens us against DK turning JA3
  checks back on, but it does NOT defeat a datacenter-IP block.  The
  same code returning 200 here returns 403 from the Railway container;
  that is an IP-reputation block and no client-side knob fixes it.

Snapshot fallback (see OddsSnapshotUnavailable below):
  When the live fetch RAISES, the public fetch_* helpers can fall back
  to the most recent pre-fetched CSV in the odds directory -- written
  either by this script's CLI or by tools/odds_relay.py running on the
  operator's residential machine.  It is OFF by default and must be
  enabled with DK_ODDS_SNAPSHOT_FALLBACK=1 (or allow_snapshot=True).
  It never fabricates odds: with no snapshot, or one older than
  DK_ODDS_SNAPSHOT_MAX_AGE_H (default 6h), it raises.  Every returned
  row carries `odds_source` ("live" / "snapshot") and, for snapshots,
  `snapshot_captured_at`, so stale prices can never masquerade as live.

This script handles:
  - DK's en-dash minus sign (U+2212) in odds (replaces with ASCII '-')
  - Team-abbr mismatches (KCR/TBR/SFG/etc.)
  - UTC -> ET date conversion (DK timestamps are UTC; our slate is ET)
  - Pitcher-to-team mapping via selection participant venueRole
  - Both O/U (primary) and milestone (alt) line formats
"""

import csv
import gzip
import json
import os
import re
import sys
import time
import urllib.request
import zlib
from datetime import datetime, timedelta, timezone
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
OUTS_OU_SUB = 17413            # subcategory "Outs Recorded O/U"

# Market-name suffixes.  DK names each market "<Pitcher> <suffix>", so
# the suffix is what we strip to recover the pitcher.  Note the outs
# board does NOT echo its own subcategory name: the subcategory is
# "Outs Recorded O/U" but every market inside it is named
# "Gerrit Cole Outs O/U".  Verified live 2026-08-08 across 14 markets.
STRIKEOUTS_OU_SUFFIX = " Strikeouts Thrown O/U"
STRIKEOUTS_ALT_SUFFIX = " Strikeouts Thrown"
OUTS_OU_SUFFIX = " Outs O/U"

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


def _decode_body(raw: bytes, content_encoding: str) -> bytes:
    """Decompress a response body urllib handed us verbatim.

    HEADERS advertises `Accept-Encoding: gzip, deflate` and DK honours
    it, but urllib -- unlike requests/curl_cffi -- does NOT decode the
    body.  Feeding those bytes to json.loads raised UnicodeDecodeError
    ('invalid start byte 0x8b' -- the gzip magic number), which made
    this whole third-tier fallback dead code.  Verified 2026-08-06.
    """
    enc = (content_encoding or "").lower().strip()
    if "gzip" in enc:
        return gzip.decompress(raw)
    if "deflate" in enc:
        try:
            return zlib.decompress(raw)
        except zlib.error:
            # Raw deflate stream with no zlib header.
            return zlib.decompress(raw, -zlib.MAX_WBITS)
    return raw


def _fetch_via_urllib(url: str, retries: int = 3, backoff: float = 2.0) -> dict:
    """Last-resort urllib fetch path.  No TLS impersonation, so this may
    get 403'd by DK's CDN depending on the egress IP."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=45) as resp:
                body = _decode_body(
                    resp.read(), resp.headers.get("Content-Encoding", "")
                )
                return json.loads(body)
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

def extract_ou_odds(
    data: dict,
    subcategory_id: int = STRIKEOUTS_OU_SUB,
    name_suffix: str = STRIKEOUTS_OU_SUFFIX,
) -> list[dict]:
    """Parse a two-sided pitcher O/U board.

    Defaults to Strikeouts Thrown O/U (subcategory 15221); pass
    OUTS_OU_SUB / OUTS_OU_SUFFIX for the Outs Recorded board.  The two
    boards are structurally identical -- same events/markets/selections
    join, same outcomeType/points/displayOdds shape, same venueRole team
    resolution -- so they share one parser rather than a near-duplicate.

    Each market is one pitcher's O/U line.  Two selections per market:
    Over and Under, each with `points` (the line, e.g. 5.5 strikeouts or
    17.5 outs) and `displayOdds.american`.

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
        if m.get("subcategoryId") != subcategory_id:
            continue

        event = events_by_id.get(m.get("eventId"))
        if not event:
            continue

        pitcher_name = _pitcher_name_from_market(m.get("name", ""), name_suffix)

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


def extract_outs_ou_odds(data: dict) -> list[dict]:
    """Parse the Outs Recorded O/U response (subcategory 17413).

    Thin wrapper so this can be passed as _fetch_board's one-argument
    `extractor`.  Same row shape as extract_ou_odds -- `line` carries
    outs, not strikeouts.
    """
    return extract_ou_odds(data, OUTS_OU_SUB, OUTS_OU_SUFFIX)


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
            m.get("name", ""), STRIKEOUTS_ALT_SUFFIX
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
# Snapshot fallback -- serve pre-fetched odds when the live fetch is blocked
# ---------------------------------------------------------------------------
#
# Why this exists: DraftKings 403s our Railway container because it is a
# datacenter IP.  The same request from the operator's residential
# connection returns 200.  So the cloud worker reads a CSV snapshot that
# a residential machine already wrote (see tools/odds_relay.py) instead
# of hitting DK itself.
#
# The danger this code exists to prevent is a stale snapshot being
# priced as if it were live.  Three guards:
#   1. OFF by default -- must be enabled explicitly.
#   2. Hard staleness ceiling -- too old means raise, never serve.
#   3. Provenance stamped on every row and logged loudly at fetch time.

SNAPSHOT_FALLBACK_ENV = "DK_ODDS_SNAPSHOT_FALLBACK"
SNAPSHOT_MAX_AGE_ENV = "DK_ODDS_SNAPSHOT_MAX_AGE_H"
SNAPSHOT_DIR_ENV = "DK_ODDS_DIR"

# A prop board moves all day.  Six hours is generous enough to cover a
# morning-slate run reading a snapshot taken at wake-up, and tight
# enough that yesterday's board can never be served as today's.
DEFAULT_SNAPSHOT_MAX_AGE_H = 6.0

_DATE_RE = r"(\d{4}-\d{2}-\d{2})"


class OddsSnapshotUnavailable(RuntimeError):
    """No usable pre-fetched odds snapshot.

    Deliberately an exception rather than an empty list.  A caller that
    silently received [] would report "no props today" on a full slate;
    a caller that received stale rows labelled as live would price a
    dead board.  Both are worse failures than a loud crash, and the
    repo's rule is that we never invent odds.
    """


def _log(msg: str) -> None:
    """Odds-provenance log line.  stderr so it survives stdout capture."""
    print(f"[odds] {msg}", file=sys.stderr, flush=True)


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_allow_snapshot(allow_snapshot: bool | None) -> bool:
    """Explicit kwarg wins; otherwise consult the environment."""
    if allow_snapshot is not None:
        return bool(allow_snapshot)
    return _env_flag(SNAPSHOT_FALLBACK_ENV)


def _resolve_max_age_hours(max_age_hours: float | None) -> float:
    if max_age_hours is not None:
        return float(max_age_hours)
    raw = os.environ.get(SNAPSHOT_MAX_AGE_ENV, "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            _log(f"WARNING: {SNAPSHOT_MAX_AGE_ENV}={raw!r} is not a number; "
                 f"using default {DEFAULT_SNAPSHOT_MAX_AGE_H}h")
    return DEFAULT_SNAPSHOT_MAX_AGE_H


def odds_dirs() -> list[Path]:
    """Candidate snapshot directories, most authoritative first.

    On Railway the live odds live on the persistent volume that
    tracker.DATA_STATE_DIR points at, NOT in the image's data/ dir, so
    the volume has to be searched first.

    DK_ODDS_DIR is EXCLUSIVE, not additive: if the operator names a
    directory, that is the only place we look.  Additive would mean a
    caller trying to pin the source could still silently pick up an old
    file from the repo checkout.
    """
    cands: list[Path] = []
    env_dir = os.environ.get(SNAPSHOT_DIR_ENV, "").strip()
    if env_dir:
        return [Path(p) for p in env_dir.split(os.pathsep) if p.strip()]
    try:
        from tracker import DATA_STATE_DIR
        cands.append(Path(DATA_STATE_DIR) / "odds")
    except Exception:
        pass
    cands.append(Path(__file__).resolve().parent / "data" / "odds")
    cands.append(Path("data") / "odds")

    seen: set[str] = set()
    out: list[Path] = []
    for c in cands:
        try:
            key = str(c.resolve())
        except OSError:
            key = str(c)
        if key not in seen:
            seen.add(key)
            out.append(c)
    return out


def _parse_captured_at(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _read_snapshot(path: Path) -> tuple[list[dict], datetime | None]:
    """Read a snapshot CSV, attaching each row's own capture time.

    Every row must carry an explicit captured_at. There is deliberately
    NO file-mtime fallback: the documented transport for snapshots is git
    (and Docker COPY on the container), and both rewrite mtime to
    checkout/build time. A mtime clock would report a week-old board as
    freshly captured, which is precisely the failure the ceiling exists
    to prevent. A file without stamps is refused, not guessed at.

    The returned datetime is the OLDEST row stamp, used only for
    reporting; per-row ages are what actually gate. A single refreshed
    pitcher must not re-validate a whole stale board.
    """
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    stamped: list[dict] = []
    for r in rows:
        dt = _parse_captured_at(r.get("captured_at", ""))
        if dt is not None:
            r["_captured_at"] = dt
            stamped.append(r)

    if not stamped:
        return [], None
    return stamped, min(r["_captured_at"] for r in stamped)


def _candidate_snapshots(prefixes: list[str], iso_date: str | None) -> list[Path]:
    """Every snapshot file matching the prefixes, newest-dated first.

    Prefix order is a preference order: dk_k_* (a clean full-board
    scrape) is preferred over closing_* (append-log of intraday
    snapshots) for the same date.
    """
    found: list[tuple[str, float, int, Path]] = []
    for rank, prefix in enumerate(prefixes):
        pattern = re.compile(rf"^{re.escape(prefix)}_{_DATE_RE}\.csv$")
        for d in odds_dirs():
            if not d.is_dir():
                continue
            for p in d.iterdir():
                if not p.is_file():
                    continue
                m = pattern.match(p.name)
                if not m:
                    continue
                if iso_date and m.group(1) != iso_date:
                    continue
                found.append((m.group(1), _newest_stamp(p), rank, p))

    # Newest slate date wins, then the freshest CAPTURE, then the
    # preferred prefix as a last tiebreak. Freshness must outrank prefix:
    # a closing_* file written two minutes ago carries better prices than
    # a dk_k_* board from this morning, and the old ordering always
    # picked the morning board. Capture time comes from inside the file,
    # never mtime -- see _read_snapshot.
    found.sort(key=lambda t: (t[0], t[1], -t[2]), reverse=True)
    return [p for _, _, _, p in found]


def _newest_stamp(path: Path) -> float:
    """Newest in-file captured_at as a POSIX timestamp; 0.0 if unstamped.

    Used only for ORDERING candidates. Unstamped files sort last and are
    refused outright by _read_snapshot, so they can never be served.
    """
    try:
        with open(path, encoding="utf-8", newline="") as f:
            stamps = [
                dt for dt in (
                    _parse_captured_at(r.get("captured_at", ""))
                    for r in csv.DictReader(f)
                ) if dt is not None
            ]
        return max(stamps).timestamp() if stamps else 0.0
    except (OSError, csv.Error, UnicodeDecodeError):
        return 0.0


def _dedupe_latest(rows: list[dict], key_fields: tuple[str, ...]) -> list[dict]:
    """Collapse an append-log to one row per key, keeping the newest.

    closing_*.csv holds several snapshots of the same pitcher stamped
    with different captured_at values.  Rows are appended in
    chronological order, so last-wins is the newest price.
    """
    out: dict[tuple, dict] = {}
    for r in rows:
        out[tuple((r.get(k) or "") for k in key_fields)] = r
    return list(out.values())


def _load_snapshot(
    prefixes: list[str],
    key_fields: tuple[str, ...],
    required: tuple[str, ...],
    fields: list[str],
    kind: str,
    iso_date: str | None,
    max_age_hours: float | None,
) -> list[dict]:
    """Shared snapshot loader for both the O/U and alt boards."""
    max_age = _resolve_max_age_hours(max_age_hours)
    searched = ", ".join(str(d) for d in odds_dirs())

    paths = _candidate_snapshots(prefixes, iso_date)
    if not paths:
        raise OddsSnapshotUnavailable(
            f"no {kind} odds snapshot found"
            + (f" for {iso_date}" if iso_date else "")
            + f" (looked for {'/'.join(p + '_<date>.csv' for p in prefixes)} "
            f"in: {searched})"
        )

    path = paths[0]
    rows, oldest = _read_snapshot(path)

    if not rows:
        raise OddsSnapshotUnavailable(
            f"{kind} snapshot {path} carries no rows with a captured_at "
            f"stamp -- refusing to date it from the file mtime, which git "
            f"checkout and Docker COPY both reset. Re-capture it with "
            f"'python tools/odds_relay.py snapshot'."
        )

    # The slate date is checked here, not left to a downstream filter.
    # daily_pipeline's date== filter used to be the only guard, and it
    # can be defeated by anything that re-dates rows on the way through.
    if iso_date:
        wrong = {(r.get("date") or "").strip() for r in rows} - {iso_date, ""}
        if wrong:
            raise OddsSnapshotUnavailable(
                f"{kind} snapshot {path} holds rows for {sorted(wrong)} but "
                f"{iso_date} was requested -- refusing to price another "
                f"slate's board."
            )

    now = datetime.now(timezone.utc)
    shaped: list[dict] = []
    dropped_stale = 0
    for r in _dedupe_latest(rows, key_fields):
        if any(not (r.get(k) or "").strip() for k in required):
            continue
        # Per-row ceiling. A file-level MAX age would let one late
        # refresh re-validate every stale row beside it in the same
        # append-log; a MIN would throw away good rows. Each row is
        # judged on its own capture time.
        row_age = (now - r["_captured_at"]).total_seconds() / 3600.0
        if row_age > max_age:
            dropped_stale += 1
            continue
        row = {f: (r.get(f) or "") for f in fields}
        row["odds_source"] = "snapshot"
        row["snapshot_captured_at"] = r["_captured_at"].isoformat()
        row["snapshot_age_hours"] = round(row_age, 3)
        row["snapshot_path"] = str(path)
        shaped.append(row)

    if not shaped:
        oldest_h = (now - oldest).total_seconds() / 3600.0 if oldest else float("nan")
        raise OddsSnapshotUnavailable(
            f"{kind} snapshot {path} had {len(rows)} row(s) but none were "
            f"usable: {dropped_stale} past the {max_age:.1f}h ceiling "
            f"(oldest {oldest_h:.1f}h), the rest missing required columns "
            f"({', '.join(required)}). Refresh it "
            f"(python tools/odds_relay.py snapshot) or raise "
            f"{SNAPSHOT_MAX_AGE_ENV} deliberately."
        )

    ages = [r["snapshot_age_hours"] for r in shaped]
    _log(
        f"SOURCE=SNAPSHOT  {kind}: {len(shaped)} rows from {path.name} "
        f"({min(ages):.2f}-{max(ages):.2f}h old, ceiling {max_age:.1f}h"
        + (f", {dropped_stale} row(s) dropped as stale" if dropped_stale else "")
        + ") -- these are NOT live prices"
    )
    return shaped


def load_snapshot_props(
    iso_date: str | None = None,
    max_age_hours: float | None = None,
) -> list[dict]:
    """Load the newest pre-fetched O/U board.

    Same dict shape as fetch_dk_strikeout_props(), plus odds_source /
    snapshot_captured_at / snapshot_path.  Raises
    OddsSnapshotUnavailable rather than returning [] when there is
    nothing fresh enough to serve.
    """
    return _load_snapshot(
        prefixes=["dk_k", "closing"],
        key_fields=("pitcher_name", "event_id"),
        required=("pitcher_name", "line"),
        fields=OU_FIELDS,
        kind="O/U",
        iso_date=iso_date,
        max_age_hours=max_age_hours,
    )


def load_snapshot_outs(
    iso_date: str | None = None,
    max_age_hours: float | None = None,
) -> list[dict]:
    """Load the newest pre-fetched Outs Recorded O/U board.

    Separate prefixes from the strikeout board on purpose.  The
    _candidate_snapshots regex anchors the date immediately after the
    prefix, so "dk_outs" cannot match dk_k_*, and "closing_outs" cannot
    match closing_* -- the two markets can never serve each other's
    prices even though both carry a `line` column.
    """
    return _load_snapshot(
        prefixes=["dk_outs", "closing_outs"],
        key_fields=("pitcher_name", "event_id"),
        required=("pitcher_name", "line"),
        fields=OU_FIELDS,
        kind="outs O/U",
        iso_date=iso_date,
        max_age_hours=max_age_hours,
    )


def load_snapshot_alts(
    iso_date: str | None = None,
    max_age_hours: float | None = None,
) -> list[dict]:
    """Load the newest pre-fetched milestone/alt board.

    Same dict shape as fetch_dk_strikeout_alts().  Raises
    OddsSnapshotUnavailable when nothing fresh enough exists.
    """
    return _load_snapshot(
        prefixes=["dk_k_alts", "closing_alts"],
        key_fields=("pitcher_name", "event_id", "milestone"),
        required=("pitcher_name", "milestone", "odds"),
        fields=ALT_FIELDS,
        kind="alt",
        iso_date=iso_date,
        max_age_hours=max_age_hours,
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def _fetch_board(
    subcategory_id: int,
    extractor,
    loader,
    kind: str,
    retries: int,
    backoff: float,
    allow_snapshot: bool | None,
    iso_date: str | None,
    max_age_hours: float | None,
) -> list[dict]:
    """Live fetch first; fall back to a snapshot only if enabled.

    Note that the fallback triggers on an EXCEPTION, never on an empty
    result.  An empty live board is a legitimate state (slate not
    posted, everything locked); falling back there would resurrect an
    older board and price a slate that DK is not currently offering.
    """
    try:
        rows = extractor(
            fetch_dk_strikeout_data(
                subcategory_id=subcategory_id, retries=retries, backoff=backoff
            )
        )
    except Exception as live_exc:
        if not _resolve_allow_snapshot(allow_snapshot):
            _log(
                f"SOURCE=LIVE FAILED  {kind}: {type(live_exc).__name__}: "
                f"{live_exc} -- snapshot fallback is OFF "
                f"(set {SNAPSHOT_FALLBACK_ENV}=1 to enable)"
            )
            raise
        _log(
            f"live {kind} fetch failed ({type(live_exc).__name__}: "
            f"{live_exc}) -- trying snapshot fallback"
        )
        try:
            return loader(iso_date=iso_date, max_age_hours=max_age_hours)
        except OddsSnapshotUnavailable as snap_exc:
            raise OddsSnapshotUnavailable(
                f"{kind} odds unavailable: live fetch failed "
                f"({type(live_exc).__name__}: {live_exc}) AND {snap_exc}"
            ) from live_exc

    # Live rows carry the same keys as snapshot rows so that every
    # downstream consumer sees one shape and provenance is never absent
    # by accident -- an empty odds_source would read as "unknown", and
    # unknown provenance on a price is what we are guarding against.
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        r["odds_source"] = "live"
        r["captured_at"] = now
        r["snapshot_captured_at"] = ""
        r["snapshot_age_hours"] = 0.0
        r["snapshot_path"] = ""
    _log(f"SOURCE=LIVE  {kind}: {len(rows)} rows from DraftKings")
    return rows


def fetch_dk_strikeout_props(
    retries: int = 3,
    backoff: float = 2.0,
    allow_snapshot: bool | None = None,
    iso_date: str | None = None,
    max_age_hours: float | None = None,
) -> list[dict]:
    """Fetch current DraftKings pitcher strikeout prop odds.

    Returns a list of dicts, each containing:
      pitcher_name, team, line, over_odds, under_odds, event_id,
      event_name, start_time_utc, date, odds_source

    allow_snapshot:
      None  -- consult DK_ODDS_SNAPSHOT_FALLBACK (default: disabled)
      True  -- on a live failure, serve the newest fresh snapshot
      False -- live only; a failure propagates
    """
    return _fetch_board(
        subcategory_id=STRIKEOUTS_OU_SUB,
        extractor=extract_ou_odds,
        loader=load_snapshot_props,
        kind="O/U",
        retries=retries,
        backoff=backoff,
        allow_snapshot=allow_snapshot,
        iso_date=iso_date,
        max_age_hours=max_age_hours,
    )


def fetch_dk_outs_props(
    retries: int = 3,
    backoff: float = 2.0,
    allow_snapshot: bool | None = None,
    iso_date: str | None = None,
    max_age_hours: float | None = None,
) -> list[dict]:
    """Fetch current DraftKings pitcher Outs Recorded O/U odds.

    Same row shape as fetch_dk_strikeout_props(); `line` is outs, not
    strikeouts.  Lines observed live are half-integers (14.5/15.5/16.5/
    17.5), so the market resolves win/loss with no push -- but that is an
    observation, not a guarantee, and nothing downstream may assume it.
    An integer line would carry real push mass (P(exactly 18 outs) is
    0.22 league-wide), so a caller that prices one must handle three
    outcomes before quoting an edge.

    See fetch_dk_strikeout_props for allow_snapshot semantics.
    """
    return _fetch_board(
        subcategory_id=OUTS_OU_SUB,
        extractor=extract_outs_ou_odds,
        loader=load_snapshot_outs,
        kind="outs O/U",
        retries=retries,
        backoff=backoff,
        allow_snapshot=allow_snapshot,
        iso_date=iso_date,
        max_age_hours=max_age_hours,
    )


def fetch_dk_strikeout_alts(
    retries: int = 3,
    backoff: float = 2.0,
    allow_snapshot: bool | None = None,
    iso_date: str | None = None,
    max_age_hours: float | None = None,
) -> list[dict]:
    """Fetch DraftKings milestone/alt strikeout lines (3+, 4+, ...).

    Returns a list of dicts, each containing:
      pitcher_name, team, milestone, odds, event_id, event_name,
      start_time_utc, date, odds_source

    See fetch_dk_strikeout_props for allow_snapshot semantics.
    """
    return _fetch_board(
        subcategory_id=STRIKEOUTS_ALT_SUB,
        extractor=extract_alt_lines,
        loader=load_snapshot_alts,
        kind="alt",
        retries=retries,
        backoff=backoff,
        allow_snapshot=allow_snapshot,
        iso_date=iso_date,
        max_age_hours=max_age_hours,
    )


# ---------------------------------------------------------------------------
# CSV output
# ---------------------------------------------------------------------------

# captured_at leads both field lists and is written on every row. It is
# NOT decoration: it is the only trustworthy record of when a board was
# real. File mtime cannot serve that purpose -- git checkout and Docker
# COPY both reset mtime to build time, so a week-old board arrives on the
# container looking freshly captured. The staleness ceiling is only
# meaningful if the clock travels inside the file.
OU_FIELDS = [
    "captured_at",
    "date", "pitcher_name", "team", "line", "over_odds", "under_odds",
    "event_id", "event_name", "start_time_utc",
]

ALT_FIELDS = [
    "captured_at",
    "date", "pitcher_name", "team", "milestone", "odds",
    "event_id", "event_name", "start_time_utc",
]


def stamp_captured_at(rows: list[dict]) -> list[dict]:
    """Stamp every row with the capture time, unless it already has one."""
    now = datetime.now(timezone.utc).isoformat()
    for r in rows:
        if not (r.get("captured_at") or "").strip():
            r["captured_at"] = now
    return rows


def write_csv(rows: list[dict], path: Path, fields: list[str]) -> None:
    """Write rows to CSV with atomic write (tempfile + replace)."""
    if "captured_at" in fields:
        stamp_captured_at(rows)
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


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _seed_test_odds_dir(
    tmp: Path, iso_date: str, age_hours: float = 0.0, mtime_hours: float | None = None
) -> None:
    """Populate a THROWAWAY directory with snapshot files for the tests.

    Prefers copying a real snapshot out of the repo so the test
    exercises the real column layout.  If the repo has none, it writes
    an obviously-synthetic fixture instead.  Neither is ever written
    into data/odds and the fixture's pitcher name could not be mistaken
    for a real market -- this is test scaffolding, not invented odds.

    age_hours backdates the in-file captured_at stamps; mtime_hours
    backdates the filesystem mtime independently. Keeping the two
    separate is the point: the production failure is fresh-mtime with
    old-content (git checkout / Docker COPY reset mtime), which an
    mtime-only test can never reach.
    """
    repo_odds = Path(__file__).resolve().parent / "data" / "odds"
    stamp = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()

    def newest(pattern: re.Pattern) -> Path | None:
        if not repo_odds.is_dir():
            return None
        hits = sorted(
            (p for p in repo_odds.iterdir() if p.is_file() and pattern.match(p.name)),
            key=lambda p: p.name,
        )
        return hits[-1] if hits else None

    def copy_restamped(src: Path, dst: Path, fields: list[str]) -> None:
        with open(src, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            r["captured_at"] = stamp
            r["date"] = iso_date
        write_csv(rows, dst, fields)

    ou_src = newest(re.compile(rf"^dk_k_{_DATE_RE}\.csv$"))
    alt_src = newest(re.compile(rf"^dk_k_alts_{_DATE_RE}\.csv$"))

    if ou_src:
        copy_restamped(ou_src, tmp / f"dk_k_{iso_date}.csv", OU_FIELDS)
    else:
        write_csv([{
            "captured_at": stamp,
            "date": iso_date, "pitcher_name": "Fixture Testpitcher",
            "team": "TST", "line": "5.5", "over_odds": "-110",
            "under_odds": "-110", "event_id": "0",
            "event_name": "TST @ TST", "start_time_utc": "",
        }], tmp / f"dk_k_{iso_date}.csv", OU_FIELDS)

    if alt_src:
        copy_restamped(alt_src, tmp / f"dk_k_alts_{iso_date}.csv", ALT_FIELDS)
    else:
        write_csv([{
            "captured_at": stamp,
            "date": iso_date, "pitcher_name": "Fixture Testpitcher",
            "team": "TST", "milestone": "5", "odds": "-150",
            "event_id": "0", "event_name": "TST @ TST", "start_time_utc": "",
        }], tmp / f"dk_k_alts_{iso_date}.csv", ALT_FIELDS)

    t = time.time() - (mtime_hours or 0.0) * 3600
    for p in tmp.iterdir():
        os.utime(p, (t, t))


def self_test() -> int:
    """Prove the live path, the snapshot fallback, and the refusals.

    Exit codes: 0 all good; 1 a logic test failed (a real bug);
    2 only the live fetch failed (expected inside a blocked container).
    """
    import tempfile

    results: list[tuple[str, bool, str]] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        results.append((label, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
              + (f"\n         {detail}" if detail else ""))

    def boom(*_a, **_k):
        raise RuntimeError("HTTP Error 403: Forbidden (simulated Railway block)")

    iso_date = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
    print("=" * 70)
    print("scrape_dk_odds self-test")
    print("=" * 70)

    # -- A. live path still works (environment-dependent) --
    print("\nA. live fetch (no fallback)")
    live_ok = False
    try:
        rows = fetch_dk_strikeout_props(allow_snapshot=False, retries=1)
        shape_ok = all(
            {"pitcher_name", "line", "over_odds", "under_odds"} <= set(r)
            and r.get("odds_source") == "live"
            for r in rows
        )
        live_ok = shape_ok
        check("live fetch returns live-stamped rows", shape_ok,
              f"{len(rows)} O/U rows"
              + (f", e.g. {rows[0]['pitcher_name']} {rows[0]['line']}"
                 if rows else " (empty board -- legitimate off-hours)"))
    except Exception as exc:
        check("live fetch returns live-stamped rows", False,
              f"{type(exc).__name__}: {exc}")

    # -- B. snapshot fallback returns correctly-shaped data --
    print("\nB. snapshot fallback when live fetch fails")
    orig = globals()["fetch_dk_strikeout_data"]
    globals()["fetch_dk_strikeout_data"] = boom
    prev_dir = os.environ.get(SNAPSHOT_DIR_ENV)
    try:
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed_test_odds_dir(tmp, iso_date)
            os.environ[SNAPSHOT_DIR_ENV] = str(tmp)

            props = fetch_dk_strikeout_props(allow_snapshot=True, retries=1)
            ok = (
                len(props) > 0
                and all(set(OU_FIELDS) <= set(r) for r in props)
                and all(r["odds_source"] == "snapshot" for r in props)
                and all(r["snapshot_captured_at"] for r in props)
            )
            check("O/U fallback returns snapshot-shaped rows", ok,
                  f"{len(props)} rows, keys match OU_FIELDS, "
                  f"captured_at={props[0]['snapshot_captured_at'] if props else 'n/a'}")

            alts = fetch_dk_strikeout_alts(allow_snapshot=True, retries=1)
            ok = (
                len(alts) > 0
                and all(set(ALT_FIELDS) <= set(r) for r in alts)
                and all(r["odds_source"] == "snapshot" for r in alts)
            )
            check("alt fallback returns snapshot-shaped rows", ok,
                  f"{len(alts)} rows, keys match ALT_FIELDS")

        # -- C. missing snapshot raises, never returns empty/fabricated --
        print("\nC. missing snapshot must raise, not return []")
        with tempfile.TemporaryDirectory() as td:
            os.environ[SNAPSHOT_DIR_ENV] = td  # empty dir
            try:
                got = fetch_dk_strikeout_props(allow_snapshot=True, retries=1)
                check("missing snapshot raises", False,
                      f"returned {len(got)} rows instead of raising -- "
                      f"THIS WOULD PRICE A SLATE WITH NO ODDS")
            except OddsSnapshotUnavailable as exc:
                check("missing snapshot raises OddsSnapshotUnavailable", True,
                      str(exc)[:150])
            except Exception as exc:
                check("missing snapshot raises OddsSnapshotUnavailable", False,
                      f"raised {type(exc).__name__} instead: {exc}")

        # -- D. stale snapshot refused --
        print("\nD. stale snapshot must be refused")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed_test_odds_dir(tmp, iso_date, age_hours=48)
            os.environ[SNAPSHOT_DIR_ENV] = str(tmp)
            try:
                got = fetch_dk_strikeout_props(allow_snapshot=True, retries=1)
                check("48h-old snapshot refused", False,
                      f"served {len(got)} stale rows as if usable")
            except OddsSnapshotUnavailable as exc:
                check("48h-old snapshot refused", True, str(exc)[:150])
            except Exception as exc:
                check("48h-old snapshot refused", False,
                      f"raised {type(exc).__name__}: {exc}")

        # -- D2. THE production case: old content, fresh mtime --
        # git clone and Docker COPY both stamp mtime = now. A loader that
        # dates a board by mtime reports a week-old board as brand new,
        # and the staleness ceiling never fires again.
        print("\nD2. old board with mtime reset to now must still be refused")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed_test_odds_dir(tmp, iso_date, age_hours=168, mtime_hours=0)
            os.environ[SNAPSHOT_DIR_ENV] = str(tmp)
            try:
                got = fetch_dk_strikeout_props(allow_snapshot=True, retries=1)
                check("7-day-old board with fresh mtime refused", False,
                      f"served {len(got)} rows -- MTIME CLOCK IS BACK, a "
                      f"git-pulled stale board would be priced as live")
            except OddsSnapshotUnavailable as exc:
                check("7-day-old board with fresh mtime refused", True,
                      str(exc)[:150])
            except Exception as exc:
                check("7-day-old board with fresh mtime refused", False,
                      f"raised {type(exc).__name__}: {exc}")

        # -- D3. an unstamped board is refused rather than mtime-dated --
        print("\nD3. board with no captured_at column must be refused")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            legacy = [f for f in OU_FIELDS if f != "captured_at"]
            write_csv([{
                "date": iso_date, "pitcher_name": "Fixture Testpitcher",
                "team": "TST", "line": "5.5", "over_odds": "-110",
                "under_odds": "-110", "event_id": "0",
                "event_name": "TST @ TST", "start_time_utc": "",
            }], tmp / f"dk_k_{iso_date}.csv", legacy)
            os.environ[SNAPSHOT_DIR_ENV] = str(tmp)
            try:
                got = fetch_dk_strikeout_props(allow_snapshot=True, retries=1)
                check("unstamped board refused", False,
                      f"served {len(got)} rows of unknown age")
            except OddsSnapshotUnavailable as exc:
                check("unstamped board refused", True, str(exc)[:130])
            except Exception as exc:
                check("unstamped board refused", False,
                      f"raised {type(exc).__name__}: {exc}")

        # -- D4. one fresh row must not re-validate stale neighbours --
        print("\nD4. per-row staleness, not file-level max")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            now_dt = datetime.now(timezone.utc)
            write_csv([
                {"captured_at": (now_dt - timedelta(hours=30)).isoformat(),
                 "date": iso_date, "pitcher_name": "Stale Fixture",
                 "team": "TST", "line": "5.5", "over_odds": "+999",
                 "under_odds": "-999", "event_id": "1",
                 "event_name": "TST @ TST", "start_time_utc": ""},
                {"captured_at": now_dt.isoformat(),
                 "date": iso_date, "pitcher_name": "Fresh Fixture",
                 "team": "TST", "line": "5.5", "over_odds": "-110",
                 "under_odds": "-110", "event_id": "2",
                 "event_name": "TST @ TST", "start_time_utc": ""},
            ], tmp / f"dk_k_{iso_date}.csv", OU_FIELDS)
            os.environ[SNAPSHOT_DIR_ENV] = str(tmp)
            try:
                got = fetch_dk_strikeout_props(allow_snapshot=True, retries=1)
                names = {r["pitcher_name"] for r in got}
                ok = names == {"Fresh Fixture"}
                check("stale row dropped, fresh row kept", ok,
                      f"served {sorted(names)} (want just Fresh Fixture)")
            except Exception as exc:
                check("stale row dropped, fresh row kept", False,
                      f"raised {type(exc).__name__}: {exc}")

        # -- D5. another slate's board must never be served --
        print("\nD5. wrong-date board must be refused")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            other = (datetime.now(ZoneInfo("America/New_York"))
                     - timedelta(days=1)).strftime("%Y-%m-%d")
            _seed_test_odds_dir(tmp, other, age_hours=0.1)
            os.environ[SNAPSHOT_DIR_ENV] = str(tmp)
            try:
                got = fetch_dk_strikeout_props(
                    allow_snapshot=True, retries=1, iso_date=iso_date)
                check("yesterday's board refused for today", False,
                      f"served {len(got)} rows dated {other}")
            except OddsSnapshotUnavailable as exc:
                check("yesterday's board refused for today", True,
                      str(exc)[:130])
            except Exception as exc:
                check("yesterday's board refused for today", False,
                      f"raised {type(exc).__name__}: {exc}")

        # -- D6. closing_odds.py must never accept a snapshot --
        print("\nD6. closing capture must refuse snapshots outright")
        import inspect as _inspect
        from tools import closing_odds as _co
        src = _inspect.getsource(_co.capture_closing)
        # Check the CALL SITES, not a substring count -- the prose around
        # this function says "allow_snapshot=False" several times, so a
        # count would pass on comments alone while a real call went
        # unpinned. Every fetcher named here must be pinned at the call.
        _pinned = ("fetch_dk_strikeout_props", "fetch_dk_strikeout_alts",
                   "fetch_dk_outs_props")
        unpinned = [
            n for n in _pinned
            if n + "(" in src and n + "(allow_snapshot=False)" not in src
        ]
        missing = [n for n in _pinned if n + "(" not in src]
        ok = not unpinned and not missing
        check("capture_closing pins allow_snapshot=False", ok,
              (f"unpinned: {unpinned}; not captured at all: {missing} -- "
               "a snapshot here would be re-dated to today and re-stamped "
               "to now, laundering a stale board into the closing line")
              if not ok else f"all {len(_pinned)} fetchers pinned at the call")

        # -- D7. the outs board cannot serve strikeout prices, or vice
        # versa. Both boards carry a `line` column and identical row
        # shapes, so a prefix that matched across markets would quietly
        # price 17.5 outs as 17.5 strikeouts. --
        print("\nD7. outs and strikeout snapshots stay separated")
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed_test_odds_dir(tmp, iso_date)   # writes dk_k_* only
            os.environ[SNAPSHOT_DIR_ENV] = str(tmp)
            try:
                got = fetch_dk_outs_props(
                    allow_snapshot=True, retries=1, iso_date=iso_date)
                check("outs fallback refuses the strikeout board", False,
                      f"served {len(got)} rows from a dk_k_* file")
            except OddsSnapshotUnavailable:
                check("outs fallback refuses the strikeout board", True,
                      "dk_k_*/closing_* are invisible to the outs loader")
            except Exception as exc:
                check("outs fallback refuses the strikeout board", False,
                      f"raised {type(exc).__name__}: {exc}")

        # -- E. fallback is OFF unless asked for --
        print("\nE. fallback stays off by default")
        prev_flag = os.environ.pop(SNAPSHOT_FALLBACK_ENV, None)
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _seed_test_odds_dir(tmp, iso_date)
            os.environ[SNAPSHOT_DIR_ENV] = str(tmp)
            try:
                got = fetch_dk_strikeout_props(retries=1)  # allow_snapshot=None
                check("default config does not silently use snapshot", False,
                      f"returned {len(got)} rows with the flag unset")
            except OddsSnapshotUnavailable as exc:
                check("default config does not silently use snapshot", False,
                      f"fell back anyway: {exc}")
            except Exception as exc:
                check("default config propagates the live error", True,
                      f"{type(exc).__name__}: {str(exc)[:90]}")
        if prev_flag is not None:
            os.environ[SNAPSHOT_FALLBACK_ENV] = prev_flag
    finally:
        globals()["fetch_dk_strikeout_data"] = orig
        if prev_dir is None:
            os.environ.pop(SNAPSHOT_DIR_ENV, None)
        else:
            os.environ[SNAPSHOT_DIR_ENV] = prev_dir

    logic = [r for r in results[1:]]
    n_fail_logic = sum(1 for _, ok, _ in logic if not ok)
    print("\n" + "=" * 70)
    print(f"  {sum(1 for _, ok, _ in results if ok)}/{len(results)} passed")
    if n_fail_logic:
        print("  FAILED -- snapshot-fallback logic is broken")
        return 1
    if not live_ok:
        print("  Logic OK; live DK fetch failed (expected from a blocked IP).")
        return 2
    print("  All good: live path works AND the fallback behaves.")
    return 0


def check_snapshot(iso_date: str | None) -> int:
    """Report what the fallback would serve right now, without fetching."""
    print("Snapshot directories searched (in order):")
    for d in odds_dirs():
        print(f"  {'OK ' if d.is_dir() else '-- '} {d}")
    print(f"\nStaleness ceiling: {_resolve_max_age_hours(None):.1f}h  "
          f"(fallback enabled: {_env_flag(SNAPSHOT_FALLBACK_ENV)})")
    rc = 0
    for label, loader in (("O/U", load_snapshot_props), ("alt", load_snapshot_alts)):
        print(f"\n{label} board:")
        try:
            rows = loader(iso_date=iso_date)
            print(f"  {len(rows)} usable rows from {rows[0]['snapshot_path']}")
            print(f"  captured {rows[0]['snapshot_captured_at']}")
        except OddsSnapshotUnavailable as exc:
            print(f"  UNAVAILABLE: {exc}")
            rc = 1
    return rc


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
        "--self-test", action="store_true",
        help="Verify the live path and the snapshot-fallback guarantees",
    )
    parser.add_argument(
        "--check-snapshot", action="store_true",
        help="Report which snapshot the fallback would serve (no fetch)",
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Slate date for --check-snapshot (default: newest available)",
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

    if args.self_test:
        sys.exit(self_test())

    if args.check_snapshot:
        sys.exit(check_snapshot(args.date))

    if args.probe_unders:
        print("Probing candidate under-side alt K subcategories...")
        probe_alt_under_markets()
        return

    et_today = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")

    # -- Over/Under lines (primary) --
    # allow_snapshot=False is load-bearing: this path WRITES the
    # snapshot files.  Letting it read a snapshot and rewrite it would
    # refresh the file's mtime, resetting the staleness clock on odds
    # that never got any newer -- laundering a stale board into a
    # "fresh" one.  The CLI is a producer; it must always go to DK.
    print("Fetching DraftKings pitcher strikeout O/U lines...", flush=True)
    try:
        ou_rows = fetch_dk_strikeout_props(allow_snapshot=False)
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
            alt_rows = fetch_dk_strikeout_alts(allow_snapshot=False)
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
