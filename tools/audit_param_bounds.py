"""Flag fitted parameters sitting on their bounds (A-043).

A parameter at its bound is a fit that FAILED, not one that finished:
the optimizer was asking for a value the search space did not contain
and stopped at the wall. The estimate you keep is then an artifact of
where the wall was put, and it looks exactly like a number.

This is not hypothetical here. Stage A's dispersion was
`alpha = 0.006737946999085467` — exactly `exp(-5)`, the lower bound of
its own `log_alpha` — in BOTH shipped pickles, for months, while the
board priced a 1-in-32 disaster start at 1-in-900 (A-042). Nothing
flagged it because a float is a float.

Three checks, because pinning wears three different faces:

  OPTIMIZER BOUND   a value at the edge of an explicit `bounds=` box
  GRID EDGE         a hyper-parameter selected at the end of its search
                    grid, where the curve may still have been improving
  SATURATION        a probability driven to exactly 0 or 1, which is
                    never a justified belief and breaks anything that
                    takes a log or divides by (1 - p)

Bounds are IMPORTED from the modules that declare them rather than
copied here. A duplicated constant would drift and the audit would go
quietly green.

Usage:
    python tools/audit_param_bounds.py        # exit 1 if anything is pinned
"""
from __future__ import annotations

import pickle
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

MODELS = ROOT / "models"
# How close to a bound counts as "on" it, relative to the bound's own
# magnitude. Deliberately loose: exp(-5) matched to 15 decimal places,
# but a fit that lands within 0.1% of a wall is leaning on it too.
REL_TOL = 1e-3


def _rel_close(value: float, bound: float) -> bool:
    if not np.isfinite(value) or not np.isfinite(bound):
        return False
    return abs(value - bound) <= REL_TOL * max(abs(bound), 1e-12)


def _load(name: str):
    path = MODELS / name
    if not path.exists():
        return None
    with open(path, "rb") as f:
        return pickle.load(f)


def check_stage_a() -> list[str]:
    """Stage A's dispersion against the log_alpha box it was fit in."""
    from models import stage_a_bf as sa
    import inspect

    # Read the bound out of the source that declares it, so a change to
    # the fit is picked up here instead of silently diverging.
    src = inspect.getsource(sa.StageA.fit)
    lo = hi = None
    for line in src.splitlines():
        if "bounds" in line and "(" in line and "n_features" in line:
            tail = line.split("+")[-1]
            nums = [t for t in tail.replace("[", " ").replace("]", " ")
                    .replace("(", " ").replace(")", " ").replace(",", " ")
                    .split() if t.replace(".", "").replace("-", "").isdigit()]
            if len(nums) >= 2:
                lo, hi = float(nums[0]), float(nums[1])
    findings = []
    for art in ("stage_a_fitted.pkl", "stage_a_eval.pkl"):
        d = _load(art)
        if not d or "alpha" not in d:
            continue
        alpha = float(d["alpha"])
        if lo is not None and _rel_close(np.log(alpha), lo):
            findings.append(
                f"OPTIMIZER BOUND  {art}: alpha={alpha!r} == exp({lo:g}), the "
                f"LOWER bound of log_alpha. The optimizer wanted less "
                f"dispersion than a negative binomial can give — the family "
                f"is wrong, not the bound (A-042)."
            )
        elif hi is not None and _rel_close(np.log(alpha), hi):
            findings.append(
                f"OPTIMIZER BOUND  {art}: alpha={alpha!r} == exp({hi:g}), the "
                f"UPPER bound of log_alpha."
            )
    return findings


def check_outs_hazard() -> list[str]:
    """The hazard model's penalty against the grid it was chosen from."""
    from models.outs_hazard import LAMBDA_GRID

    d = _load("outs_hazard.pkl")
    if not d:
        return []
    lam = d.get("lambda", (d.get("meta") or {}).get("lambda"))
    if lam is None:
        return []
    lam = float(lam)
    lo, hi = float(min(LAMBDA_GRID)), float(max(LAMBDA_GRID))
    if _rel_close(lam, hi):
        return [
            f"GRID EDGE        outs_hazard.pkl: lambda={lam:g} is the LARGEST "
            f"value in LAMBDA_GRID {tuple(LAMBDA_GRID)}. Selection stopped at "
            f"the end of the grid, so the true optimum may lie beyond it and "
            f"the model may be under-regularised. The per-lambda scores are "
            f"not persisted, so the curve cannot be inspected after the fact."
        ]
    if _rel_close(lam, lo):
        return [
            f"GRID EDGE        outs_hazard.pkl: lambda={lam:g} is the SMALLEST "
            f"value in LAMBDA_GRID {tuple(LAMBDA_GRID)} — the fit wanted less "
            f"penalty than the grid offered."
        ]
    return []


def check_calibrator() -> list[str]:
    """Isotonic knots driven to certainty.

    PAV assigns a bin its outcome mean, so a top bin whose starts all
    went OVER becomes exactly 1.0 — and every raw probability at or
    above that knot then calibrates to certainty. A staked bet at p=1.0
    has unbounded Kelly size and infinite log-loss when it loses, and
    the model HAS produced probabilities in that region: the ledger
    carries a 0.9408 that lost.
    """
    from models.calibration import IsotonicCalibrator, CALIBRATOR_PATH

    d = _load("calibrator.pkl")
    if not d:
        return []

    # What is SERVED, not what is stored. A saturated knot that can no
    # longer reach the board is a note; one that can is a defect.
    cal = IsotonicCalibrator()
    cal.load(CALIBRATOR_PATH)
    served = np.array([cal.predict(v) for v in np.linspace(0.0, 1.0, 2001)])
    findings = []
    if (served >= 1.0).any() or (served <= 0.0).any():
        findings.append(
            f"SATURATION       calibrator.pkl SERVES {served.max():.6f} / "
            f"{served.min():.6f} — a probability of 0 or 1 makes log-loss "
            f"infinite and Kelly size unbounded. Clamp the isotonic output."
        )

    y = np.asarray(d["y_knots"], float)
    x = np.asarray(d["x_knots"], float)
    if (y >= 1.0).any() and not findings:
        i = int(np.argmax(y >= 1.0))
        print(f"     note: {int((y >= 1.0).sum())} stored knot(s) at 1.0 "
              f"(raw >= {x[i]:.4f}) — neutralised by the PROB_EPS clamp, but "
              f"the top bin is still genuinely miscalibrated (A-041). A refit "
              f"should smooth it rather than rely on the guard.")
    return findings


def check_stage_b() -> list[str]:
    """Stage B is fit UNBOUNDED, so there is no bound to sit on.

    Recorded rather than skipped: "no bounds declared" is the finding,
    and it is the reason this model is clean, not an omission in the
    audit.
    """
    return []


CHECKS = (("Stage A (batters faced)", check_stage_a),
          ("Stage B (per-batter K rate)", check_stage_b),
          ("Outs hazard lattice", check_outs_hazard),
          ("Isotonic calibrator", check_calibrator))


def audit() -> list[str]:
    findings = []
    for label, fn in CHECKS:
        try:
            found = fn()
        except Exception as exc:  # a check that cannot run is not a pass
            found = [f"CHECK FAILED     {label}: {type(exc).__name__}: {exc}"]
        findings.extend(found)
    return findings


def main() -> int:
    findings = audit()
    print("=== fitted-parameter bound audit ===")
    for label, _ in CHECKS:
        print(f"  checked: {label}")
    print()
    if not findings:
        print("  no parameter is sitting on a bound.")
        return 0
    for f in findings:
        print(f"  {f}")
    print(f"\n  {len(findings)} finding(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
