#!/usr/bin/env python3
"""Diagnostic: test DraftKings endpoints for strikeout props.

Tries multiple endpoint / HTTP-client combinations and reports which
ones return 200 vs 403.  Helps pinpoint whether the block is TLS
fingerprinting (curl_cffi fixes it) or something else.
"""
import json
import sys
import urllib.request
import urllib.error

# -- Endpoints to test --
DK_NASH_BASE = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkusin/v1"
DK_IL_BASE = "https://sportsbook-us-il.draftkings.com//sites/US-IL-SB/api/v5/eventgroups"
MLB_LEAGUE = 84240

ENDPOINTS = {
    "Nash - league root":
        f"{DK_NASH_BASE}/leagues/{MLB_LEAGUE}",
    "Nash - category 1031 (pitcher props)":
        f"{DK_NASH_BASE}/leagues/{MLB_LEAGUE}/categories/1031",
    "Nash - category 1024 (1st inning, known-good from NRFI)":
        f"{DK_NASH_BASE}/leagues/{MLB_LEAGUE}/categories/1024",
    "IL - event group 84240":
        f"{DK_IL_BASE}/{MLB_LEAGUE}",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://sportsbook.draftkings.com/",
    "Origin": "https://sportsbook.draftkings.com",
    "sec-ch-ua": '"Chromium";v="123", "Not(A:Brand";v="24", "Google Chrome";v="123"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "Connection": "keep-alive",
}


def try_urllib(name: str, url: str) -> dict:
    """Plain urllib -- no TLS impersonation."""
    result = {"name": name, "method": "urllib", "url": url}
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(4000).decode("utf-8", errors="replace")
            result["status"] = resp.status
            result["ok"] = resp.status == 200
            result["snippet"] = body[:300]
    except urllib.error.HTTPError as e:
        result["status"] = e.code
        result["ok"] = False
        result["snippet"] = f"HTTPError: {e.reason}"
    except Exception as e:
        result["status"] = 0
        result["ok"] = False
        result["snippet"] = f"{type(e).__name__}: {e}"
    return result


def try_curl_cffi(name: str, url: str) -> dict:
    """curl_cffi with Chrome 120 TLS impersonation."""
    result = {"name": name, "method": "curl_cffi", "url": url}
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        result["status"] = 0
        result["ok"] = False
        result["snippet"] = "curl_cffi not installed"
        return result

    try:
        with curl_requests.Session(impersonate="chrome120") as sess:
            sess.headers.update(HEADERS)
            # Warmup GET
            try:
                sess.get("https://sportsbook.draftkings.com/leagues/baseball/mlb",
                         timeout=(10, 20))
            except Exception:
                pass  # warmup is best-effort
            resp = sess.get(url, timeout=(10, 30))
            body = resp.text[:4000] if resp.text else ""
            result["status"] = resp.status_code
            result["ok"] = resp.status_code == 200
            result["snippet"] = body[:300]
    except Exception as e:
        result["status"] = 0
        result["ok"] = False
        result["snippet"] = f"{type(e).__name__}: {e}"
    return result


def inspect_response(name: str, url: str):
    """If curl_cffi works, fetch the full response and print top-level keys
    and structure to help understand the schema."""
    try:
        from curl_cffi import requests as curl_requests
    except ImportError:
        return

    try:
        with curl_requests.Session(impersonate="chrome120") as sess:
            sess.headers.update(HEADERS)
            try:
                sess.get("https://sportsbook.draftkings.com/leagues/baseball/mlb",
                         timeout=(10, 20))
            except Exception:
                pass
            resp = sess.get(url, timeout=(10, 30))
            if resp.status_code != 200:
                return
            data = resp.json()
            print(f"\n  === Schema inspection for: {name} ===")
            if isinstance(data, dict):
                print(f"  Top-level keys: {list(data.keys())}")
                for k, v in data.items():
                    if isinstance(v, list):
                        print(f"    {k}: list of {len(v)} items")
                        if v:
                            item = v[0]
                            if isinstance(item, dict):
                                print(f"      First item keys: {list(item.keys())[:15]}")
                    elif isinstance(v, dict):
                        print(f"    {k}: dict with keys {list(v.keys())[:10]}")
                    else:
                        print(f"    {k}: {type(v).__name__} = {str(v)[:80]}")

                # For pitcher props, look for categories / subcategories
                if "categories" in data:
                    cats = data["categories"]
                    print(f"\n  Categories found ({len(cats)}):")
                    for c in cats[:20]:
                        cid = c.get("id") or c.get("categoryId") or "?"
                        cname = c.get("name") or c.get("displayName") or "?"
                        print(f"    id={cid}  name={cname}")
                        subs = c.get("subcategories", [])
                        for s in subs[:10]:
                            sid = s.get("id") or s.get("subcategoryId") or "?"
                            sname = s.get("name") or s.get("displayName") or "?"
                            print(f"      sub id={sid}  name={sname}")

                # For the category-specific endpoint, look at markets/selections
                if "markets" in data:
                    markets = data["markets"]
                    print(f"\n  Markets: {len(markets)} total")
                    if markets:
                        m0 = markets[0]
                        print(f"    First market keys: {list(m0.keys())[:15]}")
                        print(f"    First market name: {m0.get('name', '?')}")
                        print(f"    subcategoryId: {m0.get('subcategoryId', '?')}")
                if "selections" in data:
                    sels = data["selections"]
                    print(f"\n  Selections: {len(sels)} total")
                    if sels:
                        s0 = sels[0]
                        print(f"    First selection keys: {list(s0.keys())[:15]}")
                        print(f"    label: {s0.get('label', '?')}")
                        print(f"    outcomeType: {s0.get('outcomeType', '?')}")
                        odds = s0.get("displayOdds", {})
                        print(f"    displayOdds: {odds}")
                if "events" in data:
                    evts = data["events"]
                    print(f"\n  Events: {len(evts)} total")
                    if evts:
                        e0 = evts[0]
                        print(f"    First event keys: {list(e0.keys())[:15]}")
                        print(f"    name: {e0.get('name', '?')}")
    except Exception as e:
        print(f"\n  Schema inspection failed: {e}")


def main():
    print("=" * 70)
    print("DraftKings Endpoint Diagnostic")
    print("=" * 70)

    all_results = []

    for name, url in ENDPOINTS.items():
        print(f"\n--- {name} ---")
        print(f"    URL: {url}")

        r_urllib = try_urllib(name, url)
        tag = "OK" if r_urllib["ok"] else "FAIL"
        print(f"    urllib:    HTTP {r_urllib['status']}  [{tag}]")
        if not r_urllib["ok"]:
            print(f"              {r_urllib['snippet'][:120]}")

        r_curl = try_curl_cffi(name, url)
        tag = "OK" if r_curl["ok"] else "FAIL"
        print(f"    curl_cffi: HTTP {r_curl['status']}  [{tag}]")
        if not r_curl["ok"]:
            print(f"              {r_curl['snippet'][:120]}")

        all_results.extend([r_urllib, r_curl])

        # If curl_cffi worked, inspect the response schema
        if r_curl["ok"]:
            inspect_response(name, url)

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    for r in all_results:
        tag = "OK" if r["ok"] else "FAIL"
        print(f"  [{tag:4}] {r['method']:10}  HTTP {r['status']:3}  {r['name']}")

    # Verdict
    urllib_ok = any(r["ok"] for r in all_results if r["method"] == "urllib")
    curl_ok = any(r["ok"] for r in all_results if r["method"] == "curl_cffi")
    print()
    if curl_ok and not urllib_ok:
        print("VERDICT: DK blocks plain urllib (TLS fingerprint check).")
        print("         curl_cffi with impersonate='chrome120' bypasses it.")
    elif curl_ok and urllib_ok:
        print("VERDICT: Both methods work. DK may have relaxed filtering.")
    elif not curl_ok and not urllib_ok:
        print("VERDICT: Neither method works. DK may be blocking this IP entirely,")
        print("         or the endpoint URLs have changed.")
    else:
        print("VERDICT: urllib works but curl_cffi doesn't (unexpected).")


if __name__ == "__main__":
    main()
