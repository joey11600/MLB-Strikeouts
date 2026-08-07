"""Invariant watchdog — assert what must be true, fail loudly when it isn't.

Why assertions and not error monitoring
--------------------------------------
Sixteen correctness bugs were found on 2026-08-06. Two raised an
exception. Fourteen did not: they were successful-looking runs that
produced wrong, empty, or stale output —

  - the isotonic calibrator was constructed and never fit or applied
  - a re-price published a 0-pitcher board over a good 20-pitcher one
  - the container and the PC kept two silently diverging ledgers
  - a reconcile gated on row COUNT discarded every incoming grade
  - odds captured hours earlier were written as the closing line

Nothing crashed. An exception watcher would have caught 2 of 16. What
these share is that some invariant quietly stopped holding, so this
file states the invariants and checks them.

Design rules
------------
1. Every check answers "what must be true?", never "did anything throw?"
2. A check that cannot run is a WARN, not a silent pass. Not knowing is
   different from being fine, and conflating them is the bug class this
   file exists to catch.
3. FAIL exits non-zero so CI shows red. WARN is reported and exits 0.
4. Read-only. The watchdog never repairs anything: a self-healing
   watchdog hides the very signal it exists to surface.

Usage
-----
    python tools/watchdog.py            # human-readable report
    python tools/watchdog.py --json
    python tools/watchdog.py --strict   # WARN also exits non-zero
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tracker import DATA_STATE_DIR  # noqa: E402

ET = ZoneInfo("America/New_York")

PICKS = DATA_STATE_DIR / "picks_2026.csv"
MODEL_LOG = DATA_STATE_DIR / "model_log.csv"
SLATES = DATA_STATE_DIR / "slates"
ODDS = DATA_STATE_DIR / "odds"
CACHE = Path(__file__).parent.parent / "data" / "statcast_cache"

OK, WARN, FAIL = "OK", "WARN", "FAIL"


class Report:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, status: str, name: str, detail: str, why: str = "") -> None:
        self.rows.append({"status": status, "check": name,
                          "detail": detail, "why_it_matters": why})

    def ok(self, n, d, w=""):
        self.add(OK, n, d, w)

    def warn(self, n, d, w=""):
        self.add(WARN, n, d, w)

    def fail(self, n, d, w=""):
        self.add(FAIL, n, d, w)

    @property
    def failures(self):
        return [r for r in self.rows if r["status"] == FAIL]

    @property
    def warnings(self):
        return [r for r in self.rows if r["status"] == WARN]


def _rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _today() -> date:
    return datetime.now(ET).date()


# ---------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------

def check_calibrator_actually_applied(r: Report) -> None:
    """The dead-code class: a stage that exists but never runs.

    The calibrator was constructed, never fit, never loaded and never
    applied for weeks, while docs described a calibration step. Proving
    the file exists is not enough -- it must CHANGE a probability.
    """
    try:
        from models.calibration import CALIBRATOR_PATH, IsotonicCalibrator
    except Exception as exc:
        r.warn("calibrator wired", f"cannot import: {exc}")
        return
    if not CALIBRATOR_PATH.exists():
        r.fail("calibrator wired", f"{CALIBRATOR_PATH} missing",
               "live picks would ship RAW model probabilities")
        return
    try:
        cal = IsotonicCalibrator()
        cal.load()
        probes = [0.1, 0.3, 0.5, 0.7, 0.9]
        out = [cal.predict(p) for p in probes]
        moved = sum(1 for p, q in zip(probes, out) if abs(p - q) > 1e-9)
        if moved == 0:
            r.fail("calibrator wired",
                   "loads but is the identity on every probe",
                   "a fitted calibrator that changes nothing is dead code")
        else:
            r.ok("calibrator wired",
                 f"moves {moved}/{len(probes)} probes "
                 f"(e.g. 0.70 -> {out[3]:.3f})")
    except Exception as exc:
        r.fail("calibrator wired", f"load/predict raised: {exc}",
               "the live path would fall back to raw probabilities")


def check_models_fitted(r: Report) -> None:
    """Production models must carry real coefficients, not defaults."""
    try:
        from strikeout_predictor import StrikeoutPredictor
        p = StrikeoutPredictor()
        p.load_models()
        # Coefficients may be numpy arrays, so truthiness is ambiguous —
        # test for None/emptiness explicitly rather than `not x`.
        def _empty(v) -> bool:
            if v is None:
                return True
            try:
                return len(v) == 0
            except TypeError:
                return False

        bad = []
        for stage in ("stage_a", "stage_b"):
            obj = getattr(p, stage, None)
            if obj is None:
                bad.append(f"{stage} missing entirely")
            elif _empty(getattr(obj, "coefficients", None)):
                bad.append(f"{stage}.coefficients")
        if bad:
            r.fail("models fitted", "missing: " + ", ".join(bad),
                   "an unfitted stage silently substitutes league averages")
        else:
            na = len(p.stage_a.coefficients)
            nb = len(p.stage_b.coefficients)
            r.ok("models fitted",
                 f"Stage A {na} coef + Stage B {nb} coef, alpha "
                 f"{getattr(p.stage_a, 'alpha', '?')}")
    except Exception as exc:
        r.warn("models fitted", f"could not load: {exc}")


def check_ledger_monotonic(r: Report) -> None:
    """The ledger is append-mostly. Row count must never fall."""
    rows = _rows(PICKS)
    if not rows:
        r.warn("ledger non-empty", "no picks recorded yet")
        return
    state = DATA_STATE_DIR / ".watchdog_ledger_high_water"
    n = len(rows)
    prev = None
    if state.exists():
        try:
            prev = int(state.read_text().strip())
        except ValueError:
            prev = None
    if prev is not None and n < prev:
        r.fail("ledger never shrinks",
               f"{n} rows now, high-water was {prev} (-{prev - n})",
               "rows were deleted; CLAUDE.md forbids it")
    else:
        r.ok("ledger never shrinks", f"{n} rows (high-water {prev or n})")
    try:
        state.write_text(str(max(n, prev or 0)), encoding="utf-8")
    except OSError:
        pass


def check_pnl_no_drift(r: Report) -> None:
    """Stored P&L must equal a fresh recomputation."""
    try:
        from tracker import _calc_pnl
    except Exception as exc:
        r.warn("P&L free of drift", f"cannot import tracker: {exc}")
        return
    drift = []
    for row in _rows(PICKS):
        if (row.get("bet_placed") or "").upper() != "Y":
            continue
        stored = (row.get("profit_loss_units") or "").strip()
        if not stored:
            continue
        try:
            if abs(float(stored) - _calc_pnl(row)) > 0.005:
                drift.append(f"{row.get('date')} {row.get('pitcher_name')}")
        except Exception:
            drift.append(f"{row.get('date')} {row.get('pitcher_name')} (unparseable)")
    if drift:
        r.fail("P&L free of drift", f"{len(drift)} row(s): {drift[:3]}",
               "the published record does not match the canonical formula")
    else:
        r.ok("P&L free of drift", "every graded row recomputes exactly")


def check_todays_board(r: Report) -> None:
    """A slate must be recorded, and it must not have collapsed."""
    d = _today().isoformat()
    path = SLATES / f"{d}.json"
    if not path.exists():
        r.warn("today's board recorded", f"no sidecar for {d} yet",
               "expected once the morning run has fired")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        r.fail("today's board recorded", f"unreadable: {exc}")
        return
    n = len(payload.get("pitchers", []))
    if n == 0:
        r.fail("today's board recorded", f"{d} has 0 pitchers",
               "a board we could not compute was published as empty")
    elif n < 4:
        r.warn("today's board recorded", f"{d} has only {n} pitcher(s)",
               "suspiciously small for an MLB slate")
    else:
        r.ok("today's board recorded", f"{d}: {n} pitchers")

    priced = sum(1 for p in payload.get("pitchers", [])
                 if p.get("expected_k") not in (None, 0))
    if n and priced < n:
        r.warn("board fully priced", f"{n - priced} of {n} lack expected_k")
    elif n:
        r.ok("board fully priced", f"all {n} carry expected_k")


def check_model_log_growing(r: Report) -> None:
    """Every graded slate should appear in the model log."""
    log = _rows(MODEL_LOG)
    if not log:
        r.warn("model log growing", "empty",
               "the fast feedback loop is not collecting")
        return
    logged = {r_["date"] for r_ in log if r_.get("date")}
    slate_dates = {p.stem for p in SLATES.glob("*.json")} if SLATES.is_dir() else set()
    yesterday = (_today() - timedelta(days=1)).isoformat()
    missing = sorted(d for d in slate_dates
                     if d < _today().isoformat() and d not in logged)
    if yesterday in slate_dates and yesterday not in logged:
        # Baseball Savant publishes hours after the games end — measured
        # 0 pitches for 8/6 at 03:21 ET, 3,530 by 08:59 ET. So a gap
        # before early afternoon means "not published yet", not "lost".
        #
        # 13:00 specifically: attempts run at 03:00, 09:00 and 12:15 ET,
        # so this sits after the third. A noon cutoff would have opened a
        # false-alarm window between 12:00 and the 12:15 attempt — and a
        # threshold that fires just before the thing that fixes it is
        # worse than no threshold, because it teaches you to ignore it.
        if datetime.now(ET).hour < 13:
            r.warn("model log growing",
                   f"{yesterday} not logged yet "
                   f"({datetime.now(ET):%H:%M} ET) — Statcast usually "
                   f"publishes mid-morning; later jobs retry",
                   "only a failure if it is still missing this afternoon")
        else:
            r.fail("model log growing",
                   f"{yesterday} has a board but no model-log rows, and "
                   f"it is past 13:00 ET (three attempts have run)",
                   "last night's ~20 observations were lost")
    elif missing:
        r.warn("model log growing", f"no rows for {missing[:3]}")
    else:
        r.ok("model log growing", f"{len(log)} rows over {len(logged)} date(s)")


def check_statcast_fresh(r: Report) -> None:
    """Stale pitch data silently degrades every feature."""
    if not CACHE.is_dir():
        r.fail("Statcast cache fresh", "cache directory missing",
               "every pitcher fails with insufficient data")
        return
    files = sorted(p.stem for p in CACHE.rglob("*.parquet"))
    if not files:
        r.fail("Statcast cache fresh", "no parquet files",
               "every pitcher fails with insufficient data")
        return
    newest = files[-1]
    yesterday = (_today() - timedelta(days=1)).isoformat()
    if newest < yesterday:
        r.fail("Statcast cache fresh",
               f"newest day is {newest}, expected >= {yesterday}",
               "features are computed from stale pitches")
    else:
        r.ok("Statcast cache fresh", f"through {newest} ({len(files)} days)")


def check_yesterdays_results_present(r: Report) -> None:
    """Yesterday's board must actually carry results, not just exist.

    "Statcast cache fresh" passes on a cache that has a FILE for
    yesterday — even an empty one. On 2026-08-07 that check was green
    while Railway served a board showing zero results for all twenty of
    8/6's pitchers, because its cache only refreshes at 03:00 and
    Savant had not published yet. Freshness of the file is not presence
    of the data.

    Same publish-lag window as the model log: before 13:00 ET a gap is
    normal, after it is a real hole in what the operator sees.
    """
    y = (_today() - timedelta(days=1)).isoformat()
    path = SLATES / f"{y}.json"
    if not path.exists():
        r.ok("yesterday's results present", f"no slate for {y}")
        return
    try:
        pitchers = json.loads(path.read_text(encoding="utf-8")).get("pitchers", [])
    except (OSError, ValueError) as exc:
        r.warn("yesterday's results present", f"unreadable slate: {exc}")
        return
    if not pitchers:
        r.ok("yesterday's results present", f"{y} board is empty")
        return

    try:
        from datetime import date as _date

        from data.backfill_statcast import load_cached
        df = load_cached(_date.fromisoformat(y), _date.fromisoformat(y))
        n_pitches = 0 if df.empty else len(df)
    except Exception as exc:
        r.warn("yesterday's results present", f"cache unreadable: {exc}")
        return

    if n_pitches == 0:
        msg = f"{y}: {len(pitchers)} pitchers on the board, 0 pitches cached"
        if datetime.now(ET).hour < 13:
            r.warn("yesterday's results present",
                   msg + " — Savant publishes mid-morning",
                   "the board will show blanks until the cache is topped up")
        else:
            r.fail("yesterday's results present", msg,
                   "the dashboard is showing a finished slate with no "
                   "results; the Statcast cache never got refreshed")
    else:
        r.ok("yesterday's results present",
             f"{y}: {n_pitches:,} pitches cached for {len(pitchers)} pitchers")


def check_odds_provenance(r: Report) -> None:
    """No bet may be priced from odds of unknown or stale origin."""
    rows = [x for x in _rows(PICKS) if (x.get("bet_placed") or "").upper() == "Y"]
    if not rows:
        r.ok("odds provenance", "no placed bets to check")
        return
    snap = [x for x in rows if (x.get("odds_source") or "") == "snapshot"]
    missing = [x for x in rows if not (x.get("market_over_odds") or "").strip()]
    if missing:
        r.fail("odds provenance", f"{len(missing)} bet(s) with no odds recorded",
               "a bet with no price cannot be graded honestly")
    elif snap:
        r.warn("odds provenance", f"{len(snap)} bet(s) priced from a SNAPSHOT",
               "not live prices; check the capture age")
    else:
        r.ok("odds provenance", f"all {len(rows)} placed bets priced live "
                                f"(or predate tracking)")


def check_scheduler_ran(r: Report) -> None:
    """Silence is not success: prove today's due jobs actually fired."""
    state_path = DATA_STATE_DIR / "worker_state.json"
    if not state_path.exists():
        r.warn("scheduler ran today", "no worker_state.json",
               "cannot prove any job fired")
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        r.fail("scheduler ran today", f"unreadable state: {exc}")
        return
    today = _today().isoformat()
    ran = [k for k, v in state.items() if v == today]
    if not ran:
        r.fail("scheduler ran today", "no job recorded for today",
               "the whole rhythm is stalled")
    else:
        r.ok("scheduler ran today", f"{len(ran)} job(s): {', '.join(sorted(ran))}")


def check_statcast_confirms_grades(r: Report) -> None:
    """Independently re-derive every graded K count from Statcast.

    Grading now settles as soon as the starter is pulled, off the MLB
    boxscore. That is fast, but it is a live feed and live feeds revise
    themselves — a scoring change can turn a strikeout into something
    else after the fact.

    Statcast is a separate pipeline from a separate source, so agreement
    between them is real evidence and disagreement is a real problem.
    This is what makes early grading safe rather than merely quick: a
    wrong grade becomes a loud finding instead of a number sitting in
    the money ledger forever.

    Only checks rows Statcast can actually speak to. Silence about a
    date it has not published is not agreement.
    """
    rows = [x for x in _rows(PICKS)
            if (x.get("actual_strikeouts") or "").strip()]
    if not rows:
        r.ok("Statcast confirms grades", "no graded rows to confirm")
        return

    dates = {x.get("date") for x in rows if x.get("date")}
    try:
        from datetime import date as _date

        from data.backfill_statcast import load_cached
        lo = min(_date.fromisoformat(d) for d in dates)
        hi = max(_date.fromisoformat(d) for d in dates)
        df = load_cached(lo, hi)
    except Exception as exc:
        r.warn("Statcast confirms grades", f"cache unreadable: {exc}",
               "early grades are unconfirmed until this can run")
        return

    if df.empty:
        r.warn("Statcast confirms grades", "no Statcast rows for those dates",
               "early grades are unconfirmed, not agreed with")
        return

    done = df[df["events"].notna()]
    ks = done[done["events"].isin(["strikeout", "strikeout_double_play"])]
    counts = ks.groupby(["game_pk", "pitcher"]).size().to_dict()
    covered = set(done.groupby(["game_pk", "pitcher"]).size().to_dict())

    disagree, confirmed, unknown = [], 0, 0
    for x in rows:
        try:
            key = (int(x.get("game_pk")), int(x.get("pitcher_id")))
            ledger_k = int(x["actual_strikeouts"])
        except (TypeError, ValueError):
            continue
        if key not in covered:
            unknown += 1
            continue
        sc_k = int(counts.get(key, 0))
        if sc_k != ledger_k:
            disagree.append(
                f"{x.get('date')} {x.get('pitcher_name')} "
                f"ledger={ledger_k} statcast={sc_k} "
                f"[{x.get('graded_source') or 'pre-provenance'}]")
        else:
            confirmed += 1

    if disagree:
        r.fail("Statcast confirms grades",
               f"{len(disagree)} disagreement(s): {disagree[:3]}",
               "a graded row does not match the independent source — "
               "re-grade from Statcast and check the early-grade path")
    else:
        r.ok("Statcast confirms grades",
             f"{confirmed} row(s) agree exactly"
             + (f", {unknown} not yet published" if unknown else ""))


def check_dashboard_matches_ledger(r: Report) -> None:
    """The published record must equal the ledger it claims to show."""
    path = ROOT / "dashboard" / "public" / "data.json"
    if not path.exists():
        r.warn("dashboard matches ledger", "data.json not built")
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        r.fail("dashboard matches ledger", f"unreadable: {exc}")
        return
    try:
        from tracker import _calc_pnl
        truth = sum(_calc_pnl(x) for x in _rows(PICKS)
                    if (x.get("bet_placed") or "").upper() == "Y")
    except Exception as exc:
        r.warn("dashboard matches ledger", f"cannot recompute: {exc}")
        return
    shown = (data.get("pnl") or {}).get("total", {}).get("value")
    if shown is None:
        r.fail("dashboard matches ledger", "no tagged total in payload")
    elif abs(shown - truth) > 0.005:
        r.fail("dashboard matches ledger",
               f"published {shown:+.2f}u vs ledger {truth:+.2f}u",
               "the site is showing a number the ledger does not support")
    else:
        r.ok("dashboard matches ledger", f"both {truth:+.2f}u")


CHECKS = [
    check_calibrator_actually_applied,
    check_models_fitted,
    check_ledger_monotonic,
    check_pnl_no_drift,
    check_todays_board,
    check_model_log_growing,
    check_statcast_fresh,
    check_odds_provenance,
    check_yesterdays_results_present,
    check_statcast_confirms_grades,
    check_scheduler_ran,
    check_dashboard_matches_ledger,
]


def run() -> Report:
    r = Report()
    for fn in CHECKS:
        try:
            fn(r)
        except Exception as exc:  # a broken check is itself a finding
            r.warn(fn.__name__, f"check raised {type(exc).__name__}: {exc}",
                   "the watchdog could not evaluate this invariant")
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--strict", action="store_true",
                    help="treat WARN as failure too")
    args = ap.parse_args()

    r = run()

    if args.json:
        print(json.dumps({"rows": r.rows,
                          "n_fail": len(r.failures),
                          "n_warn": len(r.warnings)}, indent=1))
    else:
        print("=" * 72)
        print(f"WATCHDOG — {datetime.now(ET):%Y-%m-%d %H:%M %Z}")
        print("=" * 72)
        for row in r.rows:
            mark = {OK: "  ok  ", WARN: " WARN ", FAIL: " FAIL "}[row["status"]]
            print(f"[{mark}] {row['check']}")
            print(f"          {row['detail']}")
            if row["why_it_matters"] and row["status"] != OK:
                print(f"          -> {row['why_it_matters']}")
        print("-" * 72)
        print(f"{len(r.failures)} failure(s), {len(r.warnings)} warning(s), "
              f"{len(r.rows) - len(r.failures) - len(r.warnings)} ok")

    if r.failures:
        return 1
    if args.strict and r.warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
