"""Shadow portfolio — what the model WOULD have bet, and how it did.

Why this exists
---------------
The live filter currently bets almost nothing. Edge is halved by
MODEL_TRUST_WEIGHT=0.5 and then tested against a ~8% bar, so a bet
needs a ~16% raw disagreement with DraftKings (26% on a projected
lineup). Real prop edges live at 3-8%, so the bar is effectively
unreachable.

That creates a deadlock with AUDIT A-006, which says trust may only be
raised "after 100+ graded bets with positive CLV": at ~0 bets a day we
never accumulate the evidence the gate demands.

This module breaks the deadlock without risking a dollar. Every
evaluated pitcher is already logged (data/model_log.csv, ~20/night with
its settled outcome), so the counterfactual can be scored directly:
for a grid of trust weights, which pitchers WOULD have cleared, and
what would that portfolio have returned?

Honesty rules
-------------
1. Shadow P&L is counterfactual and is tagged `shadow_flat_100u`, never
   the real `flat_100u`. tools/pnl_guard.py enforces that the two can
   never appear in each other's subtree, so a shadow figure can't be
   read as money that moved.
2. Sizing, edge and threshold all come from the PRODUCTION functions
   (models.edge.compute_edge with a trust_weight override,
   models.staking.kelly_stake). A reimplementation would drift and
   quietly invalidate the evidence.
3. Reconstructed rows are excluded by default: they were priced
   retroactively against known outcomes and are not prospective.
4. It reports CLV as well as win rate. CLV is the fast honest signal —
   it converges in weeks where W/L needs months — and it is available
   for EVERY logged pitcher, not just the ones we bet.

Usage
-----
    python tools/shadow.py                 # report to stdout
    python tools/shadow.py --include-reconstructed
    python tools/shadow.py --json          # machine-readable
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from models.edge import (  # noqa: E402
    MODEL_TRUST_WEIGHT,
    american_to_decimal,
    compute_edge,
)
from models.staking import (  # noqa: E402
    MAX_STAKE_UNITS,
    kelly_stake,
    quantize_stake,
)
from tracker import DATA_STATE_DIR  # noqa: E402

MODEL_LOG = DATA_STATE_DIR / "model_log.csv"
ODDS_DIR = DATA_STATE_DIR / "odds"

# Trust weights to sweep. 0.5 is production; 1.0 means "believe the
# model outright". The point is not to pick a winner today but to watch
# which column's CLV holds up as rows accumulate.
TRUST_GRID = [0.5, 0.65, 0.8, 1.0]

SHADOW_BASIS = "shadow_flat_100u"


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _tag(v: float) -> dict:
    return {"value": round(v, 4), "basis": SHADOW_BASIS}


def load_rows(include_reconstructed: bool = False) -> list[dict]:
    """Graded model-log rows with everything needed to re-price them."""
    if not MODEL_LOG.exists():
        return []
    out = []
    with open(MODEL_LOG, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            recon = str(r.get("reconstructed", "")).strip() not in ("0", "", "false")
            if recon and not include_reconstructed:
                continue
            if r.get("over_hit") in (None, ""):
                continue  # not settled yet
            p_cal = _f(r.get("p_over_calibrated"))
            over, under = _i(r.get("over_odds")), _i(r.get("under_odds"))
            if p_cal is None or not over or not under:
                continue
            out.append({
                "date": (r.get("date") or "").strip(),
                "pitcher_name": r.get("pitcher_name") or "",
                "pitcher_id": r.get("pitcher_id") or "",
                "line": _f(r.get("line")),
                "p_cal": p_cal,
                "over_odds": over,
                "under_odds": under,
                "over_hit": _i(r.get("over_hit")),
                "lineup_confirmed": (r.get("lineup_source") or "") == "confirmed",
                "reconstructed": recon,
            })
    return out


def _closing_odds_index() -> dict:
    """(date, pitcher_name) -> newest closing over/under odds.

    Used for CLV. The closing files cover the WHOLE board, so a shadow
    pick has the same closing-line evidence a real bet would.
    """
    idx: dict[tuple, dict] = {}
    if not ODDS_DIR.is_dir():
        return idx
    for path in sorted(ODDS_DIR.glob("closing_*.csv")):
        try:
            with open(path, encoding="utf-8", newline="") as f:
                for r in csv.DictReader(f):
                    key = ((r.get("date") or "").strip(),
                           (r.get("pitcher_name") or "").strip())
                    o, u = _i(r.get("over_odds")), _i(r.get("under_odds"))
                    if o and u:
                        idx[key] = {"over": o, "under": u}
        except (OSError, csv.Error):
            continue
    return idx


def _clv_pct(taken: int, closing: int) -> float | None:
    """Positive means we beat the close: our price paid more."""
    try:
        return (american_to_decimal(taken) / american_to_decimal(closing) - 1.0) * 100
    except (ValueError, ZeroDivisionError):
        return None


def evaluate(rows: list[dict], trust: float, closing: dict) -> dict:
    """Score the portfolio this trust weight would have produced."""
    picks = []
    for r in rows:
        e = compute_edge(
            r["p_cal"], r["over_odds"], r["under_odds"],
            lineup_confirmed=r["lineup_confirmed"],
            trust_weight=trust,
        )
        if not e["clears_threshold"]:
            continue

        units = quantize_stake(
            min(kelly_stake(e["model_prob_best"],
                            american_to_decimal(e["best_odds"])),
                MAX_STAKE_UNITS)
        )
        if units <= 0:
            continue

        won = (r["over_hit"] == 1) if e["best_side"] == "OVER" else (r["over_hit"] == 0)
        dec = american_to_decimal(e["best_odds"])
        pnl = units * (dec - 1.0) if won else -units

        clv = None
        c = closing.get((r["date"], r["pitcher_name"]))
        if c:
            clv = _clv_pct(e["best_odds"],
                           c["over"] if e["best_side"] == "OVER" else c["under"])

        picks.append({
            "date": r["date"],
            "pitcher_name": r["pitcher_name"],
            "line": r["line"],
            "side": e["best_side"],
            "odds": e["best_odds"],
            "edge": round(e["best_edge"], 4),
            "threshold": round(e["threshold"], 4),
            "units": units,
            "won": won,
            "pnl": _tag(pnl),
            "clv_pct": round(clv, 3) if clv is not None else None,
        })

    n = len(picks)
    wins = sum(1 for p in picks if p["won"])
    staked = sum(p["units"] for p in picks)
    pnl = sum(p["pnl"]["value"] for p in picks)
    clvs = [p["clv_pct"] for p in picks if p["clv_pct"] is not None]

    return {
        "trust_weight": trust,
        "is_production": abs(trust - MODEL_TRUST_WEIGHT) < 1e-9,
        "n_bets": n,
        "wins": wins,
        "losses": n - wins,
        "hit_rate": round(wins / n, 4) if n else None,
        "units_staked": _tag(staked),
        "pnl": _tag(pnl),
        "roi": round(pnl / staked, 4) if staked else None,
        "clv_n": len(clvs),
        "avg_clv_pct": round(sum(clvs) / len(clvs), 3) if clvs else None,
        "picks": picks,
    }


#: A-006: raise MODEL_TRUST_WEIGHT only after this many GRADED BETS with
#: positive average CLV. Bets, not evaluated pitchers -- see build().
BET_TARGET = 100


def _is_ready(g: dict | None) -> bool:
    """Both halves of A-006, or not ready.

    Positive CLV alone is not enough (it can be one lucky bet) and volume
    alone is not enough (a losing edge does not improve with repetition).
    """
    if not g:
        return False
    clv = g.get("avg_clv_pct")
    return bool(g.get("n_bets", 0) >= BET_TARGET and clv is not None and clv > 0)


def build(include_reconstructed: bool = False) -> dict:
    rows = load_rows(include_reconstructed)
    closing = _closing_odds_index()
    grid = [evaluate(rows, w, closing) for w in TRUST_GRID]
    dates = sorted({r["date"] for r in rows if r["date"]})

    # Deliberately states the sample size next to every conclusion. The
    # whole point is to decide a money rule on evidence, and 26 rows is
    # not evidence -- naming that is part of the output, not a caveat
    # someone has to remember.
    return {
        "basis": SHADOW_BASIS,
        "n_observations": len(rows),
        "n_dates": len(dates),
        "dates": dates,
        "includes_reconstructed": include_reconstructed,
        "production_trust_weight": MODEL_TRUST_WEIGHT,
        "grid": grid,
        # A-006's gate is "100+ graded BETS with positive average CLV" --
        # not 100 evaluated pitchers. Counting observations declared READY
        # at 100 rows while the production weight had TWO bets behind it,
        # roughly fiftyfold less evidence than the gate asks for, and it
        # erred toward RAISING the trust weight, which increases stake
        # exposure. A readiness signal that answers an easier question than
        # the one that matters is the same defect as a health check
        # reporting configuration instead of capability.
        #
        # Readiness is judged at the PRODUCTION weight, because that is the
        # only column whose bets are real. The other columns are
        # counterfactual: useful for direction, never sufficient on their
        # own to move a money rule.
        "target_graded_bets": BET_TARGET,
        "n_observations_note": (
            "observations are evaluated pitchers, NOT bets -- they size the "
            "evidence pool, they do not satisfy A-006"
        ),
        "ready_to_decide": _is_ready(next(
            (g for g in grid if g["is_production"]), None)),
        "note": (
            "Counterfactual. No money was placed on any of these. "
            "Sizing, edge and threshold come from the production "
            "functions with only the trust weight varied."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--include-reconstructed", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    data = build(args.include_reconstructed)

    if args.json:
        print(json.dumps(data, indent=1))
        return 0

    print("=" * 74)
    print("SHADOW PORTFOLIO — what the model would have bet (no money at risk)")
    print("=" * 74)
    prod = next((g for g in data["grid"] if g["is_production"]), None)
    target = data["target_graded_bets"]
    print(f"  observations: {data['n_observations']} over {data['n_dates']} slate(s)"
          f"  (evaluated pitchers, NOT bets)")
    print(f"  reconstructed rows included: {data['includes_reconstructed']}")
    if prod:
        clv = prod["avg_clv_pct"]
        print(f"  A-006 gate: {target}+ graded bets AND positive average CLV, "
              f"at the live weight ({prod['trust_weight']:.2f})")
        print(f"     graded bets: {prod['n_bets']}/{target}"
              f"   average CLV: "
              + (f"{clv:+.2f}%" if clv is not None else "—")
              + f"   -> {'READY' if data['ready_to_decide'] else 'NOT YET'}")
    print()
    print(f"  {'TRUST':>6} {'BETS':>5} {'W-L':>7} {'HIT':>6} {'UNITS':>7} "
          f"{'P&L':>8} {'ROI':>7} {'CLV':>7} {'vs TARGET':>10}")
    for g in data["grid"]:
        tag = " (live)" if g["is_production"] else ""
        hit = f"{g['hit_rate']:.0%}" if g["hit_rate"] is not None else "—"
        roi = f"{g['roi']:+.1%}" if g["roi"] is not None else "—"
        clv = f"{g['avg_clv_pct']:+.2f}%" if g["avg_clv_pct"] is not None else "—"
        # Naming the shortfall on every row, so no column can be read as
        # decisive on a handful of bets just because its CLV looks good.
        prog = f"{g['n_bets']}/{target}"
        print(f"  {g['trust_weight']:>6.2f} {g['n_bets']:>5} "
              f"{g['wins']}-{g['losses']:<5} {hit:>6} "
              f"{g['units_staked']['value']:>7.2f} "
              f"{g['pnl']['value']:>+8.2f} {roi:>7} {clv:>7} {prog:>10}{tag}")
    print()
    if not data["ready_to_decide"]:
        print("  NOT YET — do not move the trust weight on this. Every column")
        print("  above the live one is COUNTERFACTUAL: it shows direction, not")
        print("  evidence. A CLV that looks good on a handful of bets is noise.")
        print()
    print("  CLV is the signal to watch — it converges in weeks where W/L needs")
    print("  months. A trust weight is only worth raising if its CLV stays")
    print("  positive as the sample grows.")
    print()
    print(f"  {data['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
