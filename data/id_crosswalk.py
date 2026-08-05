"""Player ID crosswalk from Chadwick Bureau register.

Maps between MLB (key_mlbam), Statcast (same as mlbam), FanGraphs
(key_fangraphs), Retrosheet (key_retro), and Baseball Reference
(key_bbref) IDs.

Downloads the 16 register shards directly from GitHub. More reliable
than pybaseball.playerid_lookup()'s cache layer.
"""
import csv
import io
import os
import time
import urllib.request
from pathlib import Path

CACHE_DIR = Path(__file__).parent / "chadwick_cache"
REGISTER_BASE = "https://raw.githubusercontent.com/chadwickbureau/register/master/data"
SHARDS = [str(i) for i in range(10)] + list("abcdef")


def download_register(force: bool = False) -> Path:
    """Download all 16 Chadwick register shards and merge."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    merged_path = CACHE_DIR / "register_merged.csv"

    if merged_path.exists() and not force:
        age_days = (time.time() - merged_path.stat().st_mtime) / 86400
        if age_days < 7:
            return merged_path

    print("Downloading Chadwick register shards...")
    all_rows = []
    header = None

    for shard in SHARDS:
        url = f"{REGISTER_BASE}/people-{shard}.csv"
        print(f"  Fetching people-{shard}.csv...", end=" ", flush=True)
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "StrikeoutsModel/0.1")
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8")
                reader = csv.DictReader(io.StringIO(text))
                if header is None:
                    header = reader.fieldnames
                rows = list(reader)
                all_rows.extend(rows)
                print(f"{len(rows)} players")
        except Exception as e:
            print(f"ERROR: {e}")
        time.sleep(0.3)

    with open(merged_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Merged {len(all_rows)} players to {merged_path}")
    return merged_path


def load_crosswalk() -> dict:
    """Load the crosswalk as a dict keyed by key_mlbam (int).

    Returns {mlbam_id: {key_mlbam, key_fangraphs, key_retro, key_bbref,
    name_first, name_last, ...}}
    """
    merged_path = download_register()
    crosswalk = {}
    with open(merged_path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mlbam = row.get("key_mlbam", "").strip()
            if mlbam:
                try:
                    crosswalk[int(mlbam)] = row
                except ValueError:
                    pass
    return crosswalk


def mlbam_to_name(crosswalk: dict, mlbam_id: int) -> str:
    """Look up a player name by MLBAM ID."""
    entry = crosswalk.get(mlbam_id, {})
    first = entry.get("name_first", "")
    last = entry.get("name_last", "")
    return f"{first} {last}".strip() or f"Unknown ({mlbam_id})"


if __name__ == "__main__":
    xwalk = load_crosswalk()
    print(f"\nCrosswalk loaded: {len(xwalk)} players with MLBAM IDs")

    test_ids = {
        543037: "Gerrit Cole",
        669373: "Tarik Skubal",
        675911: "Spencer Strider",
    }
    print("\nTest lookups:")
    for mid, expected in test_ids.items():
        name = mlbam_to_name(xwalk, mid)
        match = "MATCH" if expected.lower() in name.lower() else "MISMATCH"
        print(f"  {mid} -> {name} ({match})")
