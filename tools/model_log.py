"""Prediction-vs-outcome log for EVERY evaluated pitcher.

Why this exists
---------------
Each slate produces ~28 model predictions but only ~3 bets. P&L and CLV
can only ever evaluate the bets, so at 3 bets/night it takes months to
say anything about the model. The other ~25 predictions are equally
testable — we just never wrote the answers down.

Recording all of them turns one night into ~28 observations instead of
3, a ~9x faster feedback loop, and it measures the MODEL rather than
the bet selection (which is filtered by thresholds and therefore a
biased sample of the model's opinions).

It also watches the failure mode that has actually cost money: the
leash. `expected_bf` vs `actual_bf` is logged per start, so a
systematically wrong workload assumption shows up in aggregate long
before it prices a bet (see AUDIT A-007 — a reliever priced as a
21-BF starter).

Append-only and idempotent: re-running a date replaces that date's
rows rather than duplicating them.

Usage:
    python tools/model_log.py                # log all completed slates
    python tools/model_log.py 2026-08-05     # one date
    python tools/model_log.py --report       # live calibration report
"""
import csv
import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from tracker import DATA_STATE_DIR

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

SLATES_DIR = DATA_STATE_DIR / "slates"
LOG_PATH = DATA_STATE_DIR / "model_log.csv"

# A-046: pitchers production REFUSED but the prior-season window would
# recover, priced counterfactually by the pipeline. They are scored in
# their own file so no consumer of model_log.csv (live calibration,
# recalibration fits, dashboards) silently ingests shadow rows.
SHADOW_PRIOR_LOG_PATH = DATA_STATE_DIR / "shadow_prior_log.csv"

FIELDS = [
    "date", "game_pk", "pitcher_id", "pitcher_name", "pitcher_team",
    # is_home is logged for ONE reason: it is the only one of 68 screened
    # factors with signal against the posted LINE rather than merely against
    # actual strikeouts. Measured on 126 logged rows, home starters beat
    # their line by +0.300 K and away starters by -0.500 -- a 0.800 gap,
    # SE 0.407, t = +1.97, positive in 4 of 5 slates -- while the market
    # appears not to price it (line ~ is_home gives t = -0.21).
    #
    # That is one factor out of 68 with one slate flipping sign, so it is a
    # LEAD, not a finding, and nothing sizes on it. Logging it here is what
    # makes it judgable forward instead of re-derived by joining slates
    # every time someone asks.
    "is_home",
    "opponent_team", "line", "over_odds", "under_odds", "lineup_source",
    "expected_bf", "expected_k",
    "p_over_raw", "p_over_calibrated", "blended_prob_over", "fair_over",
    # A-046 shadow columns: raw P(over) under the two flag-off models
    # (hook mixture / prior-season window), logged every night so their
    # 2-week shadows actually accumulate. Blank on rows logged before
    # 2026-08-24. Nothing prices or stakes off these.
    "p_over_hookmix", "p_over_prior",
    # A-049: raw P(over) from the candidate Stage B (core + p5_pitches
    # + is_home — the first cross-season re-gauntlet KEEPs), production
    # Stage A distribution. Shadow only; blank before 2026-08-24.
    "p_over_candidate",
    # A-051: production distribution re-compounded with the
    # mean-preserving per-start rate random effect (sigma*=0.15, the
    # cross-season NLL argmin in both directions). Shadow only.
    "p_over_re",
    # A-049 H1/H2: the day's own market movement for this arm — open
    # line, movement to the last capture before this row's slate entry.
    # Diagnostics + future market-screen inputs; nothing prices off
    # them. Blank before 2026-08-24.
    "h1_open_line", "h2_line_move", "h2_fair_move",
    "best_side", "edge_best", "threshold", "strength",
    "units_risked",
    "actual_bf", "actual_k", "over_hit",
    "bf_error", "k_error",
    "reconstructed", "logged_at",
]


def _write_atomic(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _actuals_for(dates: set[str]) -> dict:
    """{(game_pk, pitcher_id): (actual_bf, actual_k)} from Statcast."""
    from data.backfill_statcast import load_cached

    lo = min(date.fromisoformat(d) for d in dates)
    hi = max(date.fromisoformat(d) for d in dates)
    df = load_cached(lo, hi)
    if df.empty:
        return {}
    completed = df[df["events"].notna()]
    is_k = completed["events"].isin(["strikeout", "strikeout_double_play"])
    grp = completed.assign(k=is_k.astype(int)).groupby(["game_pk", "pitcher"])
    out = {}
    for (gpk, pid), g in grp:
        out[(int(gpk), int(pid))] = (int(len(g)), int(g["k"].sum()))
    return out


def _row_from_pitcher(d: str, p: dict, abf: int, ak: int,
                      recon: bool, now: str) -> dict | None:
    """One log row from one sidecar pitcher record. None if the record
    can't be scored (unparseable line). Shared by the production log and
    the A-046 shadow-prior log so the two can never drift in schema."""
    try:
        line = float(p.get("line"))
    except (TypeError, ValueError):
        return None
    ebf = p.get("expected_bf")
    ek = p.get("expected_k")
    return {
        "date": d,
        "game_pk": p.get("game_pk"),
        "pitcher_id": p.get("pitcher_id"),
        "pitcher_name": p.get("pitcher_name"),
        "pitcher_team": p.get("pitcher_team"),
        # 1/0 rather than True/False so the column reads straight
        # into arithmetic without a per-consumer string coercion --
        # the `line` column is a string and that cost a silent NaN
        # in the chart renderer once already (A-026).
        "is_home": 1 if p.get("is_home") else 0,
        "opponent_team": p.get("opponent_team"),
        "line": line,
        "over_odds": p.get("over_odds"),
        "under_odds": p.get("under_odds"),
        "lineup_source": p.get("lineup_source"),
        "expected_bf": ebf,
        "expected_k": ek,
        "p_over_raw": p.get("p_over_raw"),
        "p_over_calibrated": p.get("p_over_calibrated"),
        "blended_prob_over": p.get("blended_prob_over"),
        "fair_over": p.get("fair_over"),
        "p_over_hookmix": p.get("p_over_hookmix"),
        "p_over_prior": p.get("p_over_prior"),
        "p_over_candidate": p.get("p_over_candidate"),
        "p_over_re": p.get("p_over_re"),
        "h1_open_line": p.get("h1_open_line"),
        "h2_line_move": p.get("h2_line_move"),
        "h2_fair_move": p.get("h2_fair_move"),
        "best_side": p.get("best_side"),
        "edge_best": p.get("edge_best"),
        "threshold": p.get("threshold"),
        "strength": p.get("strength", "SHADOW" if p.get("recovered_reason") else None),
        "units_risked": (p.get("pick") or {}).get("units_risked",
                                                  p.get("primary_units_risked", 0)),
        "actual_bf": abf,
        "actual_k": ak,
        "over_hit": 1 if ak > line else 0,
        "bf_error": round(abf - ebf, 2) if ebf is not None else "",
        "k_error": round(ak - ek, 2) if ek is not None else "",
        "reconstructed": int(recon),
        "logged_at": now,
    }


def _merge_union(path: Path, new_rows: list[dict]) -> list[dict]:
    """Union stored rows with fresh ones by (date, game_pk, pitcher_id).

    A freshly derived row supersedes the stored one; a stored row is
    never dropped merely because this run could not re-derive it (the
    A-030 rule). Raises rather than writing a smaller file.
    """
    existing = []
    if path.exists():
        with open(path, encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    def _key(r: dict) -> tuple:
        return (str(r.get("date")), str(r.get("game_pk")),
                str(r.get("pitcher_id")))

    merged = {_key(r): r for r in existing}
    kept = len(merged)
    for r in new_rows:
        merged[_key(r)] = r
    all_rows = list(merged.values())
    # Checked BEFORE the write, or the guard documents the loss instead of
    # preventing it.
    if len(all_rows) < kept:
        raise RuntimeError(
            f"{path.name} would shrink {kept} -> {len(all_rows)}; refusing "
            f"to write. This function is append/update-only."
        )
    all_rows.sort(key=lambda r: (r["date"], str(r["pitcher_name"])))
    return all_rows


def log_dates(targets: list[str] | None = None) -> int:
    if not SLATES_DIR.exists():
        print("no slates directory")
        return 0

    available = sorted(p.stem for p in SLATES_DIR.glob("*.json"))
    dates = [d for d in available if not targets or d in targets]
    if not dates:
        print("no matching slates")
        return 0

    actuals = _actuals_for(set(dates))
    if not actuals:
        print("no Statcast data for those dates yet — nothing to log")
        return 0

    now = datetime.now(UTC).isoformat()
    rows = []
    shadow_rows = []
    for d in dates:
        with open(SLATES_DIR / f"{d}.json", encoding="utf-8") as f:
            slate = json.load(f)
        recon = bool(slate.get("reconstructed"))
        logged = 0
        for p in slate.get("pitchers", []):
            gpk, pid = p.get("game_pk"), p.get("pitcher_id")
            if gpk is None or pid is None:
                continue
            got = actuals.get((int(gpk), int(pid)))
            if got is None:
                continue  # didn't pitch, or data not in yet
            row = _row_from_pitcher(d, p, *got, recon, now)
            if row is not None:
                rows.append(row)
                logged += 1
        # A-046: recovered-only pitchers, scored into their own file.
        n_shadow = 0
        for p in slate.get("shadow_prior_pitchers", []):
            gpk, pid = p.get("game_pk"), p.get("pitcher_id")
            if gpk is None or pid is None:
                continue
            got = actuals.get((int(gpk), int(pid)))
            if got is None:
                continue
            row = _row_from_pitcher(d, p, *got, recon, now)
            if row is not None:
                shadow_rows.append(row)
                n_shadow += 1
        print(f"  {d}: logged {logged} pitchers"
              + (f", {n_shadow} shadow-prior" if n_shadow else "")
              + ("  (reconstructed slate)" if recon else ""))

    # Union by key -- NEVER a wholesale replace of the dates being rebuilt.
    #
    # This used to drop every stored row whose date had a slate file, then
    # regenerate only what Statcast could derive right now. Those are not
    # the same set. A date whose pitches are not in the cache yet
    # regenerates ZERO rows, so the delete stood and the evidence was gone
    # for good. Measured 2026-08-08 against the real log: one run on a
    # machine whose cache stopped at 08-06 destroyed all 25 graded rows for
    # 08-07 -- real actual_k/actual_bf outcomes, unrecoverable. An
    # incomplete cache is an ordinary transient state (refresh lag, a
    # partial restore, a fresh checkout), and this runs on every close
    # task, so the loss was one unlucky ordering away at all times.
    #
    # A freshly derived row supersedes the stored one -- that is what makes
    # the backfill able to correct a row. But a stored row is never dropped
    # merely because this run could not re-derive it. Same union-only,
    # never-downgrade rule the ledger reconcile follows (KB invariant 9).
    all_rows = _merge_union(LOG_PATH, rows)
    _write_atomic(LOG_PATH, all_rows)
    print(f"\nmodel log now holds {len(all_rows)} prediction/outcome pairs -> {LOG_PATH}")

    if shadow_rows or SHADOW_PRIOR_LOG_PATH.exists():
        all_shadow = _merge_union(SHADOW_PRIOR_LOG_PATH, shadow_rows)
        if all_shadow:
            _write_atomic(SHADOW_PRIOR_LOG_PATH, all_shadow)
            print(f"shadow-prior log holds {len(all_shadow)} recovered-start "
                  f"pairs -> {SHADOW_PRIOR_LOG_PATH}")
    return len(rows)


def report() -> None:
    if not LOG_PATH.exists():
        print("no model log yet — run: python tools/model_log.py")
        return
    df = pd.read_csv(LOG_PATH)
    live = df[df["reconstructed"] == 0]
    print(f"MODEL LOG: {len(df)} pairs ({len(live)} from live slates, "
          f"{len(df)-len(live)} reconstructed)")
    if df.empty:
        return

    use = live if len(live) >= 20 else df
    if use is live:
        tag = "live slates — genuinely prospective predictions"
    else:
        tag = ("INCLUDES RECONSTRUCTED ROWS — those were priced retroactively "
               "and their dates may sit inside the training window, so treat "
               "these as diagnostic only, NOT as model validation")
    print(f"\nUsing {len(use)} rows\n  {tag}\n")

    p = use["p_over_calibrated"].astype(float)
    a = use["over_hit"].astype(int)
    brier = float(((p - a) ** 2).mean())
    print(f"  calibrated P(over) mean {p.mean():.3f} vs actual over-rate {a.mean():.3f}"
          f"   (bias {p.mean()-a.mean():+.3f})")
    print(f"  live Brier: {brier:.4f}   [backtest baseline 0.1491]")

    # The leash is where the model actually breaks — watch it explicitly.
    bf = use.dropna(subset=["expected_bf", "actual_bf"])
    if len(bf):
        err = bf["actual_bf"].astype(float) - bf["expected_bf"].astype(float)
        print(f"\n  workload (batters faced):")
        print(f"    mean error {err.mean():+.2f} BF   mean |error| {err.abs().mean():.2f}")
        print(f"    within +/-3 BF: {(err.abs()<=3).mean():.0%}   "
              f"badly short (<= -6): {(err<=-6).mean():.0%}")
        worst = bf.assign(e=err).nsmallest(3, "e")
        for r in worst.itertuples():
            print(f"      {r.pitcher_name:<20} expected {r.expected_bf:.1f} BF, "
                  f"faced {r.actual_bf}  ({r.e:+.1f})")

    ke = use.dropna(subset=["expected_k", "actual_k"])
    if len(ke):
        kerr = ke["actual_k"].astype(float) - ke["expected_k"].astype(float)
        print(f"\n  strikeouts: mean error {kerr.mean():+.2f} K   "
              f"mean |error| {kerr.abs().mean():.2f}")


def main():
    args = [a for a in sys.argv[1:]]
    if "--report" in args:
        report()
        return
    log_dates(args or None)


if __name__ == "__main__":
    main()
