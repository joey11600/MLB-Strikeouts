"""Data source health checker.

Verifies every endpoint the Strikeouts Model depends on. Run this
before building on any data source.

Usage:
    python tools/check_sources.py
"""
import json
import sys
import time
import urllib.request
import urllib.error


_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

# DraftKings CDN requires the full Chrome header fingerprint, not just
# User-Agent.  Without these, the Nash API returns 403 even though the
# endpoint itself is public and unauthenticated.
DK_EXTRA_HEADERS = {
    "Accept":             "application/json, text/plain, */*",
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


def check_url(name: str, url: str, expect_json: bool = False, expect_csv: bool = False,
              timeout: int = 15, extra_headers: dict | None = None) -> dict:
    result = {"name": name, "url": url, "status": "UNKNOWN", "detail": ""}
    req = urllib.request.Request(url)
    # Use a realistic browser UA -- DraftKings (and some Savant endpoints)
    # reject bot-style User-Agent strings with 403.
    req.add_header("User-Agent", _BROWSER_UA)
    if extra_headers:
        for k, v in extra_headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            content_type = resp.headers.get("Content-Type", "")
            body = resp.read(8000).decode("utf-8", errors="replace")
            result["http_status"] = status

            if status == 200:
                if expect_csv:
                    if "text/csv" in content_type or body.strip().startswith(("\"", "player", "pitch", "last_name", "year", "venue", "n_")):
                        result["status"] = "OK"
                        result["detail"] = f"CSV response, {len(body)}+ bytes"
                    elif "<html" in body.lower() or "<script" in body.lower():
                        result["status"] = "WARN"
                        result["detail"] = "Expected CSV but got HTML (JS-rendered table?)"
                    else:
                        result["status"] = "OK"
                        result["detail"] = f"200 OK, content-type: {content_type}"
                elif expect_json:
                    if "json" in content_type:
                        result["status"] = "OK"
                        result["detail"] = f"JSON response, {len(body)}+ bytes"
                    elif "<html" in body.lower():
                        result["status"] = "WARN"
                        result["detail"] = "Expected JSON but got HTML"
                    else:
                        result["status"] = "WARN"
                        result["detail"] = f"200 but unexpected content-type: {content_type}"
                else:
                    result["status"] = "OK"
                    result["detail"] = f"200 OK, content-type: {content_type}"
            else:
                result["status"] = "FAIL"
                result["detail"] = f"HTTP {status}"
    except urllib.error.HTTPError as e:
        result["status"] = "FAIL"
        result["http_status"] = e.code
        result["detail"] = f"HTTP {e.code}: {e.reason}"
    except urllib.error.URLError as e:
        result["status"] = "FAIL"
        result["detail"] = f"URL error: {e.reason}"
    except Exception as e:
        result["status"] = "FAIL"
        result["detail"] = f"Error: {type(e).__name__}: {e}"
    return result


SOURCES = [
    {
        "name": "Statcast pitch-level (sample game_pk=745544)",
        "url": "https://baseballsavant.mlb.com/statcast_search/csv?all=true&type=details&game_pk=745544",
        "expect_csv": True,
    },
    {
        "name": "Savant CSV docs",
        "url": "https://baseballsavant.mlb.com/csv-docs",
    },
    {
        "name": "Pitch arsenal stats (pitchers, 2026)",
        "url": "https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?type=pitcher&year=2026&min=100&csv=true",
        "expect_csv": True,
    },
    {
        "name": "Pitch arsenal stats (batters, 2026)",
        "url": "https://baseballsavant.mlb.com/leaderboard/pitch-arsenal-stats?type=batter&year=2026&min=100&csv=true",
        "expect_csv": True,
    },
    {
        "name": "Catcher framing (2026)",
        "url": "https://baseballsavant.mlb.com/leaderboard/catcher-framing?gameType=Regular&seasonStart=2026&seasonEnd=2026&type=catcher&minPitches=q&minResults=1&csv=true",
        "expect_csv": True,
    },
    {
        "name": "Park factors (SO, 2026)",
        "url": "https://baseballsavant.mlb.com/leaderboard/statcast-park-factors?type=year&year=2026&stat=index_K&condition=All&parks=mlb",
        "expect_csv": False,
    },
    {
        "name": "ABS challenges (pitcher, 2026)",
        "url": "https://baseballsavant.mlb.com/leaderboard/abs-challenges?gameType=regular&year=2026&challengeType=pitcher&level=mlb&minChal=1&minOppChal=0&csv=true",
        "expect_csv": True,
    },
    {
        "name": "ABS challenges (catcher, 2026)",
        "url": "https://baseballsavant.mlb.com/leaderboard/abs-challenges?gameType=regular&year=2026&challengeType=catcher&level=mlb&minChal=1&minOppChal=0&csv=true",
        "expect_csv": True,
    },
    {
        "name": "MLB Stats API - today's schedule",
        "url": "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-08-04&hydrate=probablePitcher(note),team,venue",
        "expect_json": True,
    },
    {
        "name": "MLB Stats API - schedule with lineups",
        "url": "https://statsapi.mlb.com/api/v1/schedule?sportId=1&date=2026-08-04&hydrate=lineups,weather,venue(fieldInfo)",
        "expect_json": True,
    },
    {
        "name": "MLB Stats API - venues (Yankee Stadium=3313)",
        "url": "https://statsapi.mlb.com/api/v1/venues?venueIds=3313&hydrate=location,fieldInfo",
        "expect_json": True,
    },
    {
        "name": "MLB Stats API - umpire directory",
        "url": "https://statsapi.mlb.com/api/v1/jobs/umpires",
        "expect_json": True,
    },
    {
        "name": "Open-Meteo forecast (Yankee Stadium coords)",
        "url": (
            "https://api.open-meteo.com/v1/forecast?"
            "latitude=40.8296&longitude=-73.9262"
            "&hourly=temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,surface_pressure"
            "&temperature_unit=fahrenheit&wind_speed_unit=mph"
            "&timezone=America/New_York&forecast_days=1"
        ),
        "expect_json": True,
    },
    {
        "name": "NWS weather (Yankee Stadium)",
        "url": "https://api.weather.gov/points/40.8296,-73.9262",
        "expect_json": True,
    },
    {
        "name": "Chadwick Bureau register (shard 0)",
        "url": "https://raw.githubusercontent.com/chadwickbureau/register/master/data/people-0.csv",
        "expect_csv": True,
    },
    {
        "name": "DraftKings Nash - Pitcher Props (cat 1031)",
        "url": "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusin/v1/leagues/84240/categories/1031",
        "expect_json": True,
        "extra_headers": DK_EXTRA_HEADERS,
    },
    {
        "name": "DraftKings Nash - Strikeouts Thrown O/U (subcat 15221)",
        "url": "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusin/v1/leagues/84240/categories/1031/subcategories/15221",
        "expect_json": True,
        "extra_headers": DK_EXTRA_HEADERS,
    },
]


def main():
    print("=" * 70)
    print("STRIKEOUTS MODEL — DATA SOURCE HEALTH CHECK")
    print(f"Checking {len(SOURCES)} endpoints...")
    print("=" * 70)
    print()

    results = []
    for src in SOURCES:
        print(f"  Checking: {src['name']}...", end=" ", flush=True)
        r = check_url(
            src["name"],
            src["url"],
            expect_json=src.get("expect_json", False),
            expect_csv=src.get("expect_csv", False),
            extra_headers=src.get("extra_headers"),
        )
        results.append(r)

        if r["status"] == "OK":
            print(f"OK — {r['detail']}")
        elif r["status"] == "WARN":
            print(f"WARN — {r['detail']}")
        else:
            print(f"FAIL — {r['detail']}")

        time.sleep(0.5)

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)

    ok = [r for r in results if r["status"] == "OK"]
    warn = [r for r in results if r["status"] == "WARN"]
    fail = [r for r in results if r["status"] == "FAIL"]

    print(f"  OK:   {len(ok)}")
    print(f"  WARN: {len(warn)}")
    print(f"  FAIL: {len(fail)}")

    if warn:
        print()
        print("WARNINGS:")
        for r in warn:
            print(f"  [{r['name']}] {r['detail']}")

    if fail:
        print()
        print("FAILURES:")
        for r in fail:
            print(f"  [{r['name']}] {r['detail']}")

    print()
    if fail:
        print("Some sources are broken. Fix before depending on them.")
        return 1
    elif warn:
        print("All sources reachable, but some need attention.")
        return 0
    else:
        print("All sources healthy.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
