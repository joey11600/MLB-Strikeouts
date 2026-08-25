"""Outs paper tracks — three staking policies graded nightly, no money.

Born from the 2026-08-24 slate, where three plausible rules disagreed
by a factor of eight on the same board (+2.22u / +5.67u / +17.73u).
Instead of arguing, every settled slate is now scored under all three
and the running totals published on the /outs page:

  gates          entry: the production edge gates as written
                 (models.edge.compute_edge clears_threshold);
                 stake: quarter-Kelly on the HALF-TRUST blended
                 probability, quantized, 2u cap, portfolio daily cap.
  gold_capped    entry: every row the page highlights gold
                 (|raw model-vs-fair gap| >= GOLD_GAP, currently 8pp);
                 stake: sized exactly like `gates`.
  gold_uncapped  entry: same gold rows; stake: raw quarter-Kelly on
                 the UNCALIBRATED model probability — no quantize, no
                 per-bet cap, no daily cap. The "believe the model"
                 upper bound, tracked to see what that faith costs.

Fidelity rules (the shadow.py lesson): entries and stakes go through
models.edge / models.staking — the code money would use — never a
reimplementation. Bets derive from the SLATE sidecar (pre-game data
only); settlements come from the graded evidence log. A (date, policy)
pair is written once and FROZEN — the locked-picks rule applied to
paper — so a later code change cannot rewrite history it disagrees
with. All stakes are sized on a flat 100-unit bankroll per slate
(non-compounding); published sums are flat-basis sums of those rows.

Settlement mirrors the prop grading rules: a bet on a pitcher with no
graded actual once the date's grades have landed is VOID (stake
returned, no action — the no-pitch / called-early rule); landing
exactly on a whole-number line is a PUSH (stake returned).
"""
import csv
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from models.edge import american_to_decimal, compute_edge
from models.staking import kelly_stake, portfolio_daily_cap, quantize_stake
from tools.outs_serve import OUTS_LOG_PATH, available_dates, load_slate

PAPER_PATH = OUTS_LOG_PATH.parent / "outs_paper_tracks.csv"

# The /outs page's amber-highlight threshold (dashboard/app/outs/
# page.tsx uses 0.08 in the Gap cell). If one moves, move both.
GOLD_GAP = 0.08

KELLY_FRACTION = 0.25
POLICIES = ("gates", "gold_capped", "gold_uncapped")

PAPER_FIELDS = [
    "date", "policy", "game_pk", "pitcher_id", "pitcher_name", "side",
    "line", "odds", "stake_units", "result", "pl_units", "logged_at",
]


def _policy_bets(policy: str, board: list[dict]) -> list[dict]:
    """The policy's bets for one slate, from pre-game fields only."""
    picks = []
    for r in board:
        p_over = r.get("p_over_cal")
        fair = r.get("fair_over")
        if p_over is None or fair is None:
            continue
        if not r.get("over_odds") or not r.get("under_odds"):
            continue
        e = compute_edge(float(p_over), r["over_odds"], r["under_odds"],
                         lineup_confirmed=True)
        raw_gap = float(p_over) - e["fair_over"]

        if policy == "gates":
            if not e["clears_threshold"]:
                continue
        elif abs(raw_gap) < GOLD_GAP:
            continue

        side = e["best_side"]
        odds = int(r["over_odds"] if side == "OVER" else r["under_odds"])
        dec = american_to_decimal(odds)
        if policy == "gold_uncapped":
            p = float(p_over) if side == "OVER" else 1.0 - float(p_over)
            b = dec - 1.0
            f_star = (b * p - (1.0 - p)) / b if b > 0 else 0.0
            units = round(max(KELLY_FRACTION * f_star * 100.0, 0.0), 2)
        else:
            units = quantize_stake(kelly_stake(e["model_prob_best"], dec))
        if units <= 0:
            continue
        picks.append({
            "game_pk": r.get("game_pk", ""),
            "pitcher_id": r.get("pitcher_id", ""),
            "pitcher_name": r.get("pitcher_name", ""),
            "side": side, "line": float(r["line"]), "odds": odds,
            "units_risked": units, "best_edge": e["best_edge"],
        })
    if policy != "gold_uncapped":
        picks = portfolio_daily_cap(picks)
        picks = [p for p in picks if p["units_risked"] > 0]
    return picks


def _settle(bet: dict, actual: int | None) -> tuple[str, float]:
    """(result, pl_units) under the prop grading rules."""
    if actual is None:
        return "VOID", 0.0
    if actual == bet["line"]:
        return "PUSH", 0.0
    dec = american_to_decimal(bet["odds"])
    won = (actual > bet["line"]) == (bet["side"] == "OVER")
    return ("WIN", round(bet["units_risked"] * (dec - 1.0), 2)) if won \
        else ("LOSS", round(-bet["units_risked"], 2))


def _actuals_for(date_iso: str) -> dict | None:
    """pitcher_id -> actual_outs from the evidence log; None until the
    date has ANY graded row (grades land in the next morning's job)."""
    if not OUTS_LOG_PATH.exists():
        return None
    got = {}
    with open(OUTS_LOG_PATH, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("date") == date_iso:
                try:
                    got[str(r["pitcher_id"])] = int(r["actual_outs"])
                except (TypeError, ValueError, KeyError):
                    continue
    return got or None


def _existing() -> tuple[list[dict], set]:
    rows = []
    if PAPER_PATH.exists():
        with open(PAPER_PATH, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    return rows, {(r["date"], r["policy"]) for r in rows}


def log_paper_tracks() -> int:
    """Score every settled, not-yet-papered (date, policy) pair.

    Append-only: pairs already on file are FROZEN and never recomputed.
    Atomic write, refuses to shrink — the model_log rules.
    """
    existing, done = _existing()
    today = datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc).isoformat()

    fresh = []
    for d in sorted(available_dates()):
        if d >= today:
            continue                      # only finished dates settle
        actuals = _actuals_for(d)
        if actuals is None:
            continue                      # grades have not landed yet
        slate = load_slate(d)
        board = (slate or {}).get("board") or []
        if not board:
            continue
        for policy in POLICIES:
            if (d, policy) in done:
                continue
            for bet in _policy_bets(policy, board):
                result, pl = _settle(bet, actuals.get(str(bet["pitcher_id"])))
                fresh.append({
                    "date": d, "policy": policy,
                    "game_pk": bet["game_pk"],
                    "pitcher_id": bet["pitcher_id"],
                    "pitcher_name": bet["pitcher_name"],
                    "side": bet["side"], "line": bet["line"],
                    "odds": bet["odds"],
                    "stake_units": bet["units_risked"],
                    "result": result, "pl_units": pl,
                    "logged_at": now,
                })
            done.add((d, policy))

    if not fresh:
        return 0
    rows = existing + fresh
    if len(rows) < len(existing):
        raise RuntimeError("paper track would shrink; refusing")
    PAPER_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PAPER_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=PAPER_FIELDS,
                               extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, PAPER_PATH)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return len(fresh)


def paper_summary() -> dict | None:
    """Cumulative flat-basis totals per policy for the payload."""
    if not PAPER_PATH.exists():
        return None
    with open(PAPER_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    out = {}
    for policy in POLICIES:
        mine = [r for r in rows if r["policy"] == policy]
        wins = sum(1 for r in mine if r["result"] == "WIN")
        losses = sum(1 for r in mine if r["result"] == "LOSS")
        pushes = sum(1 for r in mine if r["result"] == "PUSH")
        voids = sum(1 for r in mine if r["result"] == "VOID")
        staked = sum(float(r["stake_units"]) for r in mine
                     if r["result"] in ("WIN", "LOSS"))
        pl = sum(float(r["pl_units"]) for r in mine)
        out[policy] = {
            "bets": len(mine), "wins": wins, "losses": losses,
            "pushes": pushes, "voids": voids,
            "staked": round(staked, 2), "pl": round(pl, 2),
            "dates": len({r["date"] for r in mine}),
        }
    return {
        "policies": out,
        "since": min(r["date"] for r in rows),
        "basis": "flat units, 100u bankroll per slate, non-compounding",
    }


if __name__ == "__main__":
    n = log_paper_tracks()
    print(f"{n} paper row(s) written to {PAPER_PATH.name}")
    s = paper_summary()
    if s:
        for name, p in s["policies"].items():
            print(f"  {name:<14} {p['wins']}-{p['losses']}"
                  f"{('-' + str(p['pushes']) + 'P') if p['pushes'] else ''}"
                  f"  staked {p['staked']}u  P&L {p['pl']:+.2f}u"
                  f"  over {p['dates']} date(s)")
