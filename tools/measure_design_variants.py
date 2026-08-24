"""Measure two Stage B DESIGN variants cross-season (A-051):

  TTO4   split the folded 4th-time-through out of tto_3 (the current
         design prices trip 4 at trip 3's penalty)
  DAMP   a high-end damping term: the code base's own matchup.py argues
         log5 overshoots exactly where strikeout props live, and the
         audit's adverse-selection table shows the model's worst cell
         is its most confident one. Feature: relu(lp_c + lb_c)^2 where
         *_c are the logits centered at the league rate — zero for
         ordinary matchups, growing only where pitcher AND batter both
         point high. Expected sign: negative.

PA-level fits and NLL/Brier, both cross-season directions, identical
test rows per variant. Measurement only — nothing here touches a
production class or pickle; a variant that clears earns the full
candidate treatment (asof plumbing -> regauntlet -> shadow).

Usage: python tools/measure_design_variants.py
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest import SEASONS
from models.stage_b_rate import prepare_training_data, LEAGUE_K_RATE

L0 = float(np.log(LEAGUE_K_RATE / (1 - LEAGUE_K_RATE)))


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _design(df: pd.DataFrame, variant: str) -> np.ndarray:
    lp = _logit(df["pitcher_k_pct"].values)
    lb = _logit(df["batter_k_pct"].values)
    tto = df["tto"].values
    cols = [np.ones(len(df)), lp, lb,
            (tto == 2).astype(float)]
    if variant == "core":
        cols.append((tto >= 3).astype(float))
    elif variant == "tto4":
        cols.append((tto == 3).astype(float))
        cols.append((tto >= 4).astype(float))
    elif variant == "damp":
        cols.append((tto >= 3).astype(float))
        hi = np.maximum(0.0, (lp - L0) + (lb - L0))
        cols.append(hi ** 2)
    else:
        raise ValueError(variant)
    return np.column_stack(cols)


def _fit(X, y):
    def nll(b):
        z = np.clip(X @ b, -25, 25)
        return float(np.logaddexp(0, z).sum() - y @ z)

    def grad(b):
        p = 1 / (1 + np.exp(-np.clip(X @ b, -25, 25)))
        return X.T @ (p - y)
    r = minimize(nll, np.zeros(X.shape[1]), jac=grad, method="L-BFGS-B",
                 options={"maxiter": 500})
    return r.x


def main():
    frames = {y: prepare_training_data(*SEASONS[y]) for y in (2024, 2025)}
    splits = [("24to25", 2024, 2025), ("25to24", 2025, 2024)]
    names = {"core": ["int", "lp", "lb", "tto2", "tto3+"],
             "tto4": ["int", "lp", "lb", "tto2", "tto3", "tto4"],
             "damp": ["int", "lp", "lb", "tto2", "tto3+", "hi2"]}

    for split, tr_y, te_y in splits:
        tr, te = frames[tr_y], frames[te_y]
        y_tr = tr["is_k"].values.astype(float)
        y_te = te["is_k"].values.astype(float)
        print(f"\nSPLIT {split}  (train {len(tr)} PA, test {len(te)} PA; "
              f"test TTO4 share {float((te['tto'] >= 4).mean()):.4%})")
        base_nll = None
        for variant in ("core", "tto4", "damp"):
            Xtr = _design(tr, variant)
            Xte = _design(te, variant)
            b = _fit(Xtr, y_tr)
            z = np.clip(Xte @ b, -25, 25)
            p = 1 / (1 + np.exp(-z))
            nll = float(-(y_te * np.log(p + 1e-15)
                          + (1 - y_te) * np.log(1 - p + 1e-15)).mean())
            brier = float(np.mean((p - y_te) ** 2))
            if variant == "core":
                base_nll = nll
            extra = " ".join(f"{n}={v:+.4f}" for n, v in zip(names[variant], b))
            print(f"  {variant:>5}: NLL {nll:.6f} ({(base_nll - nll) * 1e4:+.2f}e-4)"
                  f"  Brier {brier:.6f}   [{extra}]")


if __name__ == "__main__":
    main()
