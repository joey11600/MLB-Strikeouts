"""Market-based factor screen (Phase 11).

Every factor the repo ever screened was tested on "does it predict
strikeouts better than naive" — never "does it find prices the market
got wrong" (roadmap: those are different questions; the market already
prices season K%, opponent K% and TTO). This screen asks the paying
question. For each market-scored start (score_vs_market's PRIMARY
sample) and each candidate factor X, two correlations:

  r_resid   X vs (over_hit - p_raw)      does X predict the MODEL's error?
  r_gap     X vs (fair - p_raw)          does the market lean where X points?

A factor significant on BOTH with the same sign is information the book
uses and the model lacks — the candidate list for closing the A-041
information gap. A factor significant on r_resid but NOT r_gap is the
rarer prize: something the market may not fully price either.

Factors come from the repo's own as-of layer (never recomputed ad hoc)
plus the H2 movement columns in model_log. The disagreement-bucket
table (the A-041 adverse-selection readout) prints alongside.

POWER: Phase 11 set ~1,000 market-scored starts as the decision
threshold. Below it this prints with a loud provisional banner — run
it, read it, promote nothing from it.

Usage: python tools/market_factor_screen.py
"""
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from data.backfill_statcast import load_cached
from features.asof import asof_pitcher_game_table, asof_team_zone_contact
from tools.model_log import LOG_PATH

DECISION_N = 1000

FACTORS = ["asof_swstr_pct", "asof_csw_pct", "p5_pitches", "velo_trend",
           "is_home", "opp_zcontact", "days_since_prior",
           "h2_line_move", "h2_fair_move"]


def build_table() -> pd.DataFrame:
    from tools.score_vs_market import build

    df = build("closing_2026-*.csv", ladder=False).dropna(subset=["fair"])
    if df.empty:
        return df
    lo = pd.to_datetime(df["date"]).min().date()
    hi = pd.to_datetime(df["date"]).max().date()

    cache = load_cached(date(lo.year, 3, 26), hi)
    pt = asof_pitcher_game_table(cache)
    pt["game_date"] = pd.to_datetime(pt["game_date"]).dt.normalize()
    tzc = asof_team_zone_contact(cache)
    pt = pt.merge(tzc.rename(columns={"team": "opp_team"}),
                  on=["opp_team", "game_date"], how="left")

    keyed = pt.set_index(["game_pk", "pitcher"])
    cols = ["asof_swstr_pct", "asof_csw_pct", "p5_pitches", "velo_trend",
            "is_home", "opp_zcontact", "days_since_prior"]
    feats = []
    for _, r in df.iterrows():
        key = (int(r["game_pk"]), int(r["pitcher_id"]))
        if key in keyed.index:
            row = keyed.loc[key]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[0]
            feats.append({c: row.get(c) for c in cols})
        else:
            feats.append({c: np.nan for c in cols})
    df = pd.concat([df.reset_index(drop=True), pd.DataFrame(feats)], axis=1)

    df["h2_line_move"] = np.nan
    df["h2_fair_move"] = np.nan
    if LOG_PATH.exists():
        ml = pd.read_csv(LOG_PATH)
        # Rows logged before 2026-08-24 predate the H2 columns.
        if {"h2_line_move", "h2_fair_move"} <= set(ml.columns):
            ml["date"] = ml["date"].astype(str)
            h2 = ml[["date", "pitcher_id", "h2_line_move", "h2_fair_move"]]
            df = df.drop(columns=["h2_line_move", "h2_fair_move"])
            df["date"] = df["date"].astype(str)
            df = df.merge(h2, on=["date", "pitcher_id"], how="left")
    return df


def screen(df: pd.DataFrame) -> None:
    n = len(df)
    banner = ("DECISION-GRADE" if n >= DECISION_N else
              f"PROVISIONAL — {n}/{DECISION_N} starts; promote NOTHING from this")
    print(f"\n{'=' * 74}\nMARKET FACTOR SCREEN [{banner}]\n{'=' * 74}")
    df = df.copy()
    df["resid"] = df["over_hit"] - df["raw"]
    df["gap"] = df["fair"] - df["raw"]

    print(f"{'factor':<18}{'n':>5} {'r_resid':>8} {'z':>6} {'r_gap':>8} {'z':>6}  reading")
    for f in FACTORS:
        d = df.dropna(subset=[f])
        if len(d) < 100:
            print(f"{f:<18}{len(d):>5}  (too few)")
            continue
        x = pd.to_numeric(d[f], errors="coerce")
        ok = x.notna()
        x = (x[ok] - x[ok].mean()) / max(x[ok].std(), 1e-12)
        r1 = float(np.corrcoef(x, d.loc[ok.index[ok], "resid"])[0, 1])
        r2 = float(np.corrcoef(x, d.loc[ok.index[ok], "gap"])[0, 1])
        m = int(ok.sum())
        z1, z2 = r1 * np.sqrt(m - 2), r2 * np.sqrt(m - 2)
        if abs(z1) > 1.96 and abs(z2) > 1.96 and np.sign(r1) == np.sign(r2):
            note = "market has it, model lacks it"
        elif abs(z1) > 1.96 and abs(z2) <= 1.96:
            note = "may beat the MARKET — verify hard"
        elif abs(z2) > 1.96:
            note = "book prices it; model error unclear"
        else:
            note = ""
        print(f"{f:<18}{m:>5} {r1:>+8.3f} {z1:>+6.2f} {r2:>+8.3f} {z2:>+6.2f}  {note}")

    print("\nDisagreement buckets (model raw minus closing fair):")
    bins = [-1, -0.10, -0.03, 0.03, 0.10, 1]
    labels = ["much UNDER", "under", "agrees", "over", "much OVER"]
    df["bucket"] = pd.cut(df["raw"] - df["fair"], bins, labels=labels)
    for lab in labels:
        d = df[df["bucket"] == lab]
        if len(d) == 0:
            continue
        print(f"  {lab:<11} n={len(d):>4}  model {d['raw'].mean():.3f}  "
              f"market {d['fair'].mean():.3f}  actual {d['over_hit'].mean():.3f}")


def main() -> int:
    df = build_table()
    if df.empty:
        print("no market-scored rows")
        return 1
    screen(df)
    return 0


if __name__ == "__main__":
    sys.exit(main())
