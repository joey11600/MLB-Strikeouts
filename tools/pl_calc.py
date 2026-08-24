"""Canonical P&L calculator — the ONLY source of P&L numbers.

Before stating any P&L figure anywhere, run this script and copy the
number. Never mental-math the column.

Reads from the CSV ledger (Supabase fallback TBD in Phase 4).
Recomputes every row's profit_loss_units via tracker._calc_pnl and
flags DRIFT on disagreements.

Port of NRFI Terminal's tools/pl_calc.py.
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from tracker import _calc_pnl, PICKS_PATH, market_of

MARKET_LABELS = {"K": "STRIKEOUTS", "OUTS": "OUTS RECORDED"}


def _summarize(rows: list[dict], drifts: list[dict]) -> dict:
    total_pnl = 0.0
    wins = losses = voids = pushes = 0
    for row in rows:
        graded = (row.get("graded_result") or "").strip().upper()
        if graded == "WIN":
            wins += 1
        elif graded == "LOSS":
            losses += 1
        elif graded == "VOID":
            voids += 1
        elif graded == "PUSH":
            pushes += 1

        computed = _calc_pnl(row)
        stored = float(row.get("profit_loss_units") or 0)
        total_pnl += computed

        if abs(computed - stored) > 0.001 and graded in ("WIN", "LOSS"):
            drifts.append(
                {
                    "game_pk": row.get("game_pk"),
                    "pitcher": row.get("pitcher_name"),
                    "stored": stored,
                    "computed": computed,
                }
            )
    return {"pnl": total_pnl, "wins": wins, "losses": losses,
            "voids": voids, "pushes": pushes}


def main():
    if not PICKS_PATH.exists():
        print(f"No picks file at {PICKS_PATH}")
        return

    rows = []
    with open(PICKS_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("No picks found.")
        return

    # The two markets are SEPARATE PRODUCTS (operator directive
    # 2026-08-24): P&L is reported per market and NEVER as a combined
    # figure — a blended number would be quoted, and once quoted it
    # exists. While only strikeouts rows exist the output is unchanged.
    by_market: dict[str, list[dict]] = {}
    for row in rows:
        by_market.setdefault(market_of(row), []).append(row)

    drifts: list[dict] = []
    multi = len(by_market) > 1
    for mkt in sorted(by_market):
        s = _summarize(by_market[mkt], drifts)
        graded_total = s["wins"] + s["losses"]
        hit_rate = (s["wins"] / graded_total * 100) if graded_total > 0 else 0
        if multi:
            print(f"=== {MARKET_LABELS.get(mkt, mkt)} (separate market — "
                  f"never combine) ===")
        print(f"Record: {s['wins']}W-{s['losses']}L ({hit_rate:.1f}%)")
        print(f"Voids: {s['voids']} | Pushes: {s['pushes']}")
        print(f"P&L: {s['pnl']:+.2f} units")
        if multi:
            print()

    if drifts:
        print(f"\n*** DRIFT DETECTED in {len(drifts)} rows ***")
        for d in drifts:
            print(f"  {d['game_pk']} {d['pitcher']}: stored={d['stored']}, computed={d['computed']}")
        sys.exit(1)


if __name__ == "__main__":
    main()
