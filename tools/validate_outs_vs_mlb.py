"""Cross-check reconstructed starter outs against the MLB boxscore.

data/outs_starts.parquet reconstructs outs by differencing Statcast's
`outs_when_up` state variable. This script compares that reconstruction to
`inningsPitched` from the public MLB stats API boxscore, which is an entirely
independent source. Read-only; no credentials.

    python tools/validate_outs_vs_mlb.py --date 2026-08-06
    python tools/validate_outs_vs_mlb.py --dates 2024-05-11,2025-07-19,2026-08-06
    python tools/validate_outs_vs_mlb.py --sample 5 --seed 7
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from tools.build_outs_dataset import load_outs_starts  # noqa: E402

API = "https://statsapi.mlb.com/api/v1/game/{}/boxscore"


def ip_to_outs(ip: str) -> int:
    """'6.2' innings pitched -> 20 outs."""
    whole, _, frac = str(ip).partition(".")
    return int(whole) * 3 + int(frac or 0)


def check_date(day: pd.DataFrame, verbose: bool = True) -> tuple[int, int, int]:
    ok = tot = unreachable = 0
    for gpk in sorted(day.game_pk.unique()):
        try:
            with urllib.request.urlopen(API.format(gpk), timeout=25) as r:
                box = json.load(r)
        except Exception as e:
            unreachable += 1
            if verbose:
                print(f"    game {gpk}: unreachable ({type(e).__name__})")
            continue
        for side in ("home", "away"):
            team = box["teams"][side]
            order = team.get("pitchers", [])
            if not order:
                continue
            pid = order[0]                      # first pitcher used = the starter
            try:
                ip = team["players"][f"ID{pid}"]["stats"]["pitching"]["inningsPitched"]
            except KeyError:
                continue
            mlb = ip_to_outs(ip)
            row = day[(day.game_pk == gpk) & (day.pitcher == pid)]
            tot += 1
            local = int(row.outs.iloc[0]) if len(row) == 1 else None
            if local == mlb:
                ok += 1
            elif verbose:
                print(f"    MISMATCH game={gpk} {side} pitcher={pid} "
                      f"mlb_ip={ip} ({mlb} outs) local={local}")
    return ok, tot, unreachable


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--dates", help="comma-separated")
    ap.add_argument("--sample", type=int, default=0, help="N random dates")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    d = load_outs_starts()
    all_dates = sorted(d.game_date.dt.strftime("%Y-%m-%d").unique())

    if a.dates:
        dates = [x.strip() for x in a.dates.split(",")]
    elif a.date:
        dates = [a.date]
    elif a.sample:
        dates = list(pd.Series(all_dates).sample(a.sample, random_state=a.seed))
    else:
        dates = [all_dates[-1]]

    OK = TOT = UN = 0
    for ds in sorted(dates):
        day = d[d.game_date.dt.strftime("%Y-%m-%d") == ds]
        if day.empty:
            print(f"{ds}: no starts in table")
            continue
        ok, tot, un = check_date(day)
        OK, TOT, UN = OK + ok, TOT + tot, UN + un
        print(f"{ds}: {ok}/{tot} starters match MLB boxscore"
              + (f"  ({un} games unreachable)" if un else ""))

    print(f"\nTOTAL {OK}/{TOT} exact matches"
          + (f"  ({UN} games unreachable)" if UN else ""))
    return 0 if (TOT and OK == TOT) else 1


if __name__ == "__main__":
    sys.exit(main())
