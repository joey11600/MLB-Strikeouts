"""Sweep Kelly fraction, leverage, and model trust against real closing prices.

Answers three staking questions on the ONLY sample with both a model
probability and a price the book actually posted: the closing captures
from 2026-08-05 onward (same window as score_vs_market.py, same
reconstruction).

  KELLY FRACTION  quarter-Kelly is production (KELLY_FRACTION=0.25).
                  Does an eighth do better? Does a half?
  LEVERAGE        a multiplier ON TOP of the Kelly stake. Leverage is
                  only ever a growth argument when the edge is positive;
                  the sweep is here so that claim is checked, not assumed.
  MODEL TRUST     MODEL_TRUST_WEIGHT blends model with market. w=0 is
                  "bet the market's own number" (no bets clear, by
                  construction) and w=1 is model-only. This axis reveals
                  whether trusting the model more helps or hurts.

Two bankroll bases are reported and NAMED, per the money rules:

  FLAT   1 unit = 1% of a FIXED 100u bankroll. Stakes never rescale.
         This is the published basis and the one the ledger uses.
  COMPOUND  stakes rescale with the running bankroll. Kelly is a
         compounding argument, so growth-rate claims belong here. It is
         reported alongside FLAT, never summed with it.

Usage:
    python tools/kelly_sweep.py
    python tools/kelly_sweep.py --lineup-penalty   # add the 5pp charge
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from models.calibration import (  # noqa: E402
    CALIBRATOR_PATH, USE_CALIBRATOR, IsotonicCalibrator, clamp_prob)
from models.edge import (  # noqa: E402
    EDGE_MARGIN, MIN_EDGE_PCT, MIN_EV, PROJECTED_LINEUP_EDGE_PENALTY,
    american_to_decimal, expected_value, no_vig_fair_prob,
)
from models.staking import MAX_STAKE_UNITS  # noqa: E402
from tools.daily_pipeline import _normalize_name  # noqa: E402
from tools.score_vs_market import (  # noqa: E402
    actual_k_lookup, load_closing, p_over_from_kdist, slate_index,
)


def build_priced_rows() -> pd.DataFrame:
    """One row per start: model probability, posted prices, outcome.

    Mirrors score_vs_market.build() for the PRIMARY (two-sided) sample,
    but carries the American odds through so a payout can be computed.
    """
    closing = load_closing("closing_2*.csv")
    if closing.empty:
        return pd.DataFrame()

    cal = IsotonicCalibrator()
    if CALIBRATOR_PATH.exists():
        cal.load(CALIBRATOR_PATH)

    actual = actual_k_lookup(set(closing["date"].astype(str)))

    out = []
    for date, group in closing.groupby("date"):
        idx = slate_index(str(date))
        if not idx:
            continue
        for _, r in group.iterrows():
            p = idx.get(_normalize_name(r["pitcher_name"]))
            if not p:
                continue
            gp, pid = int(p.get("game_pk") or 0), int(p.get("pitcher_id") or 0)
            k = actual.get((gp, pid))
            if k is None:
                continue

            line = float(r["line"])
            raw = p_over_from_kdist(p.get("k_dist") or [], line)
            if raw is None:
                continue
            nv = no_vig_fair_prob(r["over_odds"], r["under_odds"])
            out.append({
                "date": str(date),
                "pitcher": r["pitcher_name"],
                "line": line,
                "raw": raw,
                "cal": cal.predict(raw) if cal.is_fitted else raw,
                "fair_over": nv["fair_over"],
                "fair_under": nv["fair_under"],
                "hold_pct": nv["hold_pct"],
                "over_odds": int(r["over_odds"]),
                "under_odds": int(r["under_odds"]),
                "over_hit": float(k > line),
            })
    df = pd.DataFrame(out)
    if not df.empty:
        # What production actually ships (A-044: isotonic map off).
        df["served"] = (df["cal"] if USE_CALIBRATOR
                        else df["raw"].map(clamp_prob))
    return df


def select_bets(df: pd.DataFrame, trust: float, lineup_penalty: bool) -> pd.DataFrame:
    """Apply the production gates at a given model-trust weight."""
    blend = trust * df["served"] + (1 - trust) * df["fair_over"]
    blend_under = 1.0 - blend

    edge_over = blend - df["fair_over"]
    edge_under = blend_under - df["fair_under"]

    take_over = edge_over >= edge_under
    best_edge = np.where(take_over, edge_over, edge_under)
    best_prob = np.where(take_over, blend, blend_under)
    best_odds = np.where(take_over, df["over_odds"], df["under_odds"])
    won = np.where(take_over, df["over_hit"], 1.0 - df["over_hit"])

    threshold = np.maximum(df["hold_pct"] + EDGE_MARGIN, MIN_EDGE_PCT)
    if lineup_penalty:
        threshold = threshold + PROJECTED_LINEUP_EDGE_PENALTY

    dec = np.array([american_to_decimal(int(o)) for o in best_odds])
    ev = np.array([expected_value(p, d) for p, d in zip(best_prob, dec)])

    clears = (best_edge >= threshold) & (ev >= MIN_EV)

    return pd.DataFrame({
        "date": df["date"], "pitcher": df["pitcher"],
        "side": np.where(take_over, "OVER", "UNDER"),
        "prob": best_prob, "edge": best_edge, "ev": ev,
        "dec": dec, "won": won, "clears": clears,
    })[clears].reset_index(drop=True)


def _quantize_to(units: float, cap: float) -> float:
    """quantize_stake's denominations, but against an explicit cap.

    models.staking.quantize_stake hard-caps at MAX_STAKE_UNITS, which
    makes a leverage multiplier a no-op — every levered stake saturates
    at 2u and the whole sweep reads flat. Leverage means "bet past the
    cap", so the cap has to move with it or the test measures nothing.
    """
    if units < 0.125:
        return 0.0
    if units < 0.375:
        return min(0.25, cap)
    if units < 0.75:
        return min(0.5, cap)
    return float(min(round(units), cap))


def simulate(bets: pd.DataFrame, fraction: float, leverage: float) -> dict:
    """Stake each bet and walk both bankroll bases in date order."""
    if bets.empty:
        return {"n": 0, "w": 0, "l": 0, "staked": 0.0, "flat_pl": 0.0,
                "roi": 0.0, "compound": 100.0, "max_dd": 0.0, "ruin": False}

    bets = bets.sort_values("date").reset_index(drop=True)

    flat_pl = staked = 0.0
    bank = 100.0
    peak = 100.0
    max_dd = 0.0
    ruin = False
    w = l = 0

    for _, b in bets.iterrows():
        b_odds = b["dec"] - 1.0
        # Raw Kelly in units of a 100u bank, then leverage, then the
        # per-bet cap. quantize_stake mirrors the published denominations.
        f_star = (b_odds * b["prob"] - (1 - b["prob"])) / b_odds
        raw_units = max(0.0, fraction * f_star * 100.0) * leverage
        stake = _quantize_to(raw_units, MAX_STAKE_UNITS * leverage)
        if stake <= 0:
            continue

        payout = stake * b_odds if b["won"] else -stake
        flat_pl += payout
        staked += stake
        w += int(b["won"])
        l += int(not b["won"])

        # COMPOUND basis: the same stake expressed as a share of the
        # running bank, so growth-rate claims are made where they belong.
        if bank > 0:
            c_stake = stake * (bank / 100.0)
            bank += c_stake * b_odds if b["won"] else -c_stake
        if bank <= 0:
            bank = 0.0
            ruin = True
        peak = max(peak, bank)
        max_dd = max(max_dd, (peak - bank) / peak if peak > 0 else 0.0)

    return {"n": w + l, "w": w, "l": l, "staked": staked, "flat_pl": flat_pl,
            "roi": 100 * flat_pl / staked if staked else 0.0,
            "compound": bank, "max_dd": 100 * max_dd, "ruin": ruin}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lineup-penalty", action="store_true",
                    help="add the 5pp projected-lineup edge charge")
    args = ap.parse_args()

    df = build_priced_rows()
    if df.empty:
        print("No priced rows — need closing odds + slate sidecars + results.")
        return 1

    print(f"Sample: {len(df)} starts, {df['date'].nunique()} dates "
          f"({df['date'].min()} .. {df['date'].max()})")
    print(f"Actual over-rate: {df['over_hit'].mean():.4f}   "
          f"mean model P(over): {df['cal'].mean():.4f}   "
          f"mean market fair: {df['fair_over'].mean():.4f}")
    print(f"Gates: edge >= max(hold+{EDGE_MARGIN}, {MIN_EDGE_PCT})"
          f"{' + ' + str(PROJECTED_LINEUP_EDGE_PENALTY) if args.lineup_penalty else ''}"
          f"  AND  ev >= {MIN_EV}")

    print("\n" + "=" * 78)
    print("MODEL TRUST SWEEP  (quarter-Kelly, no leverage — production settings)")
    print("=" * 78)
    print(f"{'trust':>6} {'bets':>5} {'W-L':>8} {'staked':>8} "
          f"{'FLAT P/L':>9} {'ROI':>8} {'COMPOUND':>9} {'maxDD':>7}")
    for trust in (0.0, 0.25, 0.5, 0.75, 1.0):
        bets = select_bets(df, trust, args.lineup_penalty)
        s = simulate(bets, fraction=0.25, leverage=1.0)
        print(f"{trust:>6.2f} {s['n']:>5} {s['w']:>3}W-{s['l']:<3}L "
              f"{s['staked']:>8.2f} {s['flat_pl']:>+9.2f} {s['roi']:>+7.1f}% "
              f"{s['compound']:>9.2f} {s['max_dd']:>6.1f}%")

    print("\n" + "=" * 78)
    print("KELLY x LEVERAGE SWEEP  (at production trust w=0.5)")
    print("=" * 78)
    bets = select_bets(df, 0.5, args.lineup_penalty)
    print(f"{len(bets)} bets clear the gates at w=0.50\n")
    print(f"{'fraction':>9} {'lev':>5} {'bets':>5} {'staked':>8} "
          f"{'FLAT P/L':>9} {'ROI':>8} {'COMPOUND':>9} {'maxDD':>7} {'ruin':>5}")
    for fraction in (0.125, 0.25, 0.5, 1.0):
        for lev in (1.0, 2.0, 3.0):
            s = simulate(bets, fraction, lev)
            tag = " <- production" if fraction == 0.25 and lev == 1.0 else ""
            print(f"{fraction:>9.3f} {lev:>4.0f}x {s['n']:>5} {s['staked']:>8.2f} "
                  f"{s['flat_pl']:>+9.2f} {s['roi']:>+7.1f}% {s['compound']:>9.2f} "
                  f"{s['max_dd']:>6.1f}% {'YES' if s['ruin'] else '':>5}{tag}")

    print("\nFLAT and COMPOUND are separate bases and are never summed.")
    print("FLAT: 1u = 1% of a fixed 100u bank. COMPOUND: stakes rescale "
          "with the running bank (start 100.00).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
