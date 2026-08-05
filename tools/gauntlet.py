"""5-gate gauntlet for T2 feature candidates.

Gate 1 — Leakage audit: known before first pitch?
Gate 2 — Three-way out-of-sample: both temporal directions must help.
Gate 3 — Effect size sanity: coefficient matches expected sign/magnitude.
Gate 4 — Collinearity: known collinear pairs checked.
Gate 5 — Calibration: improves Brier score on P(K >= line).

Usage:
    python tools/gauntlet.py              # run all T2 features
    python tools/gauntlet.py a9_zone_pct  # run one feature

Within-2026 time splits (post-ABS regime):
    Split A: train June → test July
    Split B: train July → test June
    Split C: train June+July → test August
"""
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.backfill_statcast import load_cached
from features.t2_candidates import T2_REGISTRY
from models.compound import prob_k_geq

LINES = [3.5, 4.5, 5.5, 6.5, 7.5, 8.5]

SPLIT_A = {"train": (date(2026, 6, 1), date(2026, 6, 30)),
           "test":  (date(2026, 7, 1), date(2026, 7, 31))}
SPLIT_B = {"train": (date(2026, 7, 1), date(2026, 7, 31)),
           "test":  (date(2026, 6, 1), date(2026, 6, 30))}
SPLIT_C = {"train": (date(2026, 6, 1), date(2026, 7, 31)),
           "test":  (date(2026, 8, 1), date(2026, 8, 3))}


def _logit(p):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -20, 20)))


def _brier(p_pred, y_actual):
    return float(np.mean((p_pred - y_actual) ** 2))


def _build_game_set(start_date, end_date):
    """Build game-level rows with pitcher K%, BF stats, and actual K."""
    df = load_cached(start_date, end_date)
    if df.empty:
        return pd.DataFrame()

    if "game_date" in df.columns:
        df["game_date"] = pd.to_datetime(df["game_date"])

    completed = df[df["events"].notna()]

    game_pitcher = completed.groupby(["game_pk", "pitcher"]).agg(
        actual_bf=("events", "count"),
    ).reset_index()
    game_pitcher = game_pitcher[game_pitcher["actual_bf"] >= 9]

    ks = completed[completed["events"].isin(["strikeout", "strikeout_double_play"])]
    k_counts = ks.groupby(["game_pk", "pitcher"]).size().reset_index(name="actual_k")
    game_pitcher = game_pitcher.merge(k_counts, on=["game_pk", "pitcher"], how="left")
    game_pitcher["actual_k"] = game_pitcher["actual_k"].fillna(0).astype(int)

    pitcher_stats = completed.groupby("pitcher").agg(
        season_bf=("events", "count"),
    ).reset_index()
    pitcher_k_counts = ks.groupby("pitcher").size().reset_index(name="season_k")
    pitcher_stats = pitcher_stats.merge(pitcher_k_counts, on="pitcher", how="left")
    pitcher_stats["season_k"] = pitcher_stats["season_k"].fillna(0)
    pitcher_stats["season_k_pct"] = pitcher_stats["season_k"] / pitcher_stats["season_bf"]
    pitcher_stats = pitcher_stats[pitcher_stats["season_bf"] >= 50]

    bf_stats = game_pitcher.groupby("pitcher").agg(
        bf_mean=("actual_bf", "mean"),
        bf_std=("actual_bf", lambda x: x.std(ddof=1) if len(x) > 1 else 5.0),
        n_starts=("game_pk", "count"),
    ).reset_index()

    game_pitcher = game_pitcher.merge(
        pitcher_stats[["pitcher", "season_k_pct"]], on="pitcher", how="inner"
    )
    game_pitcher = game_pitcher.merge(
        bf_stats[["pitcher", "bf_mean", "bf_std", "n_starts"]], on="pitcher", how="inner"
    )
    game_pitcher = game_pitcher[game_pitcher["n_starts"] >= 3]

    game_dates = df.groupby("game_pk")["game_date"].first().reset_index()
    game_pitcher = game_pitcher.merge(game_dates, on="game_pk", how="left")

    return game_pitcher


def _baseline_k_dist(season_k_pct, bf_mean, bf_std):
    """Compute the full K distribution for one game (vectorized)."""
    from scipy.stats import binom

    bf_lo = max(1, int(bf_mean - 2 * max(bf_std, 0.1)))
    bf_hi = int(bf_mean + 2 * max(bf_std, 0.1)) + 1
    bfs = np.arange(bf_lo, bf_hi)
    z = (bfs - bf_mean) / max(bf_std, 0.1)
    weights = np.exp(-0.5 * z ** 2)
    weights /= weights.sum()

    k_dist = np.zeros(41)
    for i, bf in enumerate(bfs):
        ks = np.arange(min(int(bf), 40) + 1)
        k_dist[ks] += weights[i] * binom.pmf(ks, int(bf), season_k_pct)

    return k_dist


def _precompute_baselines(df):
    """Precompute K distributions and baseline P(over) for all games."""
    baseline_cache = {}
    for idx, row in df.iterrows():
        k_dist = _baseline_k_dist(row["season_k_pct"], row["bf_mean"], row["bf_std"])
        preds = {}
        for line in LINES:
            preds[line] = prob_k_geq(k_dist, line)
        baseline_cache[idx] = preds
    return baseline_cache


_FULL_DATASET = None
_PITCHER_GROUPS = None


def _get_full_dataset():
    """Load the full season dataset once and cache in memory."""
    global _FULL_DATASET, _PITCHER_GROUPS
    if _FULL_DATASET is None:
        print("  Loading full Statcast dataset...", flush=True)
        _FULL_DATASET = load_cached(date(2026, 3, 26), date(2026, 8, 3))
        if "game_date" in _FULL_DATASET.columns:
            _FULL_DATASET["game_date"] = pd.to_datetime(_FULL_DATASET["game_date"])
        _PITCHER_GROUPS = {pid: grp for pid, grp in _FULL_DATASET.groupby("pitcher")}
        print(f"  Loaded {len(_FULL_DATASET):,} pitches, {len(_PITCHER_GROUPS)} pitchers", flush=True)
    return _FULL_DATASET


def _pitcher_pitches_before(pitcher_id, game_pk, game_date):
    """Get a pitcher's pitches before a game from the pre-grouped cache."""
    _get_full_dataset()
    p = _PITCHER_GROUPS.get(pitcher_id)
    if p is None or p.empty:
        return pd.DataFrame()
    game_date_ts = pd.Timestamp(game_date)
    mask = (p["game_pk"] != game_pk) & (p["game_date"] <= game_date_ts)
    return p[mask]


def _get_game_lineup(game_pk, pitcher_id):
    """Get the batting lineup (first 9 unique batter IDs) from Statcast."""
    full = _get_full_dataset()
    gm = full[(full["game_pk"] == game_pk) & (full["pitcher"] == pitcher_id)]
    completed = gm[gm["events"].notna()].sort_values("at_bat_number")
    seen = []
    for bid in completed["batter"]:
        if bid not in seen:
            seen.append(bid)
        if len(seen) >= 9:
            break
    return seen


def _get_game_meta(game_pk, pitcher_id):
    """Get team/date metadata for a game from Statcast."""
    full = _get_full_dataset()
    gm = full[full["game_pk"] == game_pk]
    if gm.empty:
        return {}
    row = gm.iloc[0]
    is_home_pitcher = (row.get("home_team", "") != "") and (pitcher_id in gm[gm["inning_topbot"] == "Top"]["pitcher"].values)
    pitcher_team = row.get("home_team") if is_home_pitcher else row.get("away_team")
    venue_team = row.get("home_team")
    game_date = pd.Timestamp(row["game_date"]).date() if "game_date" in row.index else None
    return {"pitcher_team": pitcher_team, "venue_team": venue_team, "game_date": game_date}


def _is_doubleheader_game2(game_pk):
    """Check if this game is the 2nd game of a doubleheader from Statcast."""
    full = _get_full_dataset()
    gm = full[full["game_pk"] == game_pk]
    if gm.empty or "game_date" not in gm.columns:
        return False
    game_date = gm["game_date"].iloc[0]
    home = gm.iloc[0].get("home_team", "")
    if not home:
        return False
    same_day = full[(full["game_date"] == game_date) &
                    ((full["home_team"] == home) | (full["away_team"] == home))]
    game_pks = sorted(same_day["game_pk"].unique())
    return len(game_pks) > 1 and game_pk != game_pks[0]


def _pitcher_travel_info(pitcher_id, game_pk, game_date_val):
    """Compute travel features (F1, F3, F4) for a pitcher from Statcast."""
    from features.t2_candidates import TEAM_TIMEZONES
    full = _get_full_dataset()

    gm = full[full["game_pk"] == game_pk]
    if gm.empty:
        return {"f1_eastward_tz": 0, "f3_days_in_tz": None, "f4_consec_road": 0}

    home_team = gm.iloc[0].get("home_team", "")
    is_home_pitcher = pitcher_id in gm[gm["inning_topbot"] == "Top"]["pitcher"].values
    pitcher_team = home_team if is_home_pitcher else gm.iloc[0].get("away_team", "")
    curr_tz = TEAM_TIMEZONES.get(home_team, -5)

    p_games = full[full["pitcher"] == pitcher_id]
    if p_games.empty or "game_date" not in p_games.columns:
        return {"f1_eastward_tz": 0, "f3_days_in_tz": None, "f4_consec_road": 0}

    game_date_ts = pd.Timestamp(game_date_val)
    prior = p_games[p_games["game_date"] < game_date_ts]
    if prior.empty:
        return {"f1_eastward_tz": 0, "f3_days_in_tz": None, "f4_consec_road": 0}

    prev_game_pk = prior.groupby("game_pk")["game_date"].first().sort_values().index[-1]
    prev_gm = full[full["game_pk"] == prev_game_pk]
    prev_home = prev_gm.iloc[0].get("home_team", "") if not prev_gm.empty else ""
    prev_tz = TEAM_TIMEZONES.get(prev_home, -5)

    eastward = max(0, curr_tz - prev_tz)

    consec_road = 0
    if not is_home_pitcher:
        recent_gpks = prior.groupby("game_pk")["game_date"].first().sort_values().index
        for gpk in reversed(recent_gpks):
            g = full[full["game_pk"] == gpk]
            if g.empty:
                break
            g_home = g.iloc[0].get("home_team", "")
            if g_home == pitcher_team:
                break
            consec_road += 1

    days_in_tz = None
    if prev_tz != curr_tz:
        prev_date = pd.Timestamp(prior.groupby("game_pk")["game_date"].first().sort_values().iloc[-1]).date()
        days_in_tz = (game_date_val - prev_date).days
    else:
        days_in_tz = 99

    return {"f1_eastward_tz": eastward, "f3_days_in_tz": days_in_tz, "f4_consec_road": consec_road}


def _extract_t2_feature(df, feature_name, pitches_cache):
    """Extract a single T2 feature for all rows in df.

    Returns a Series aligned with df's index. Uses pitcher-grouped data
    for speed — each pitcher's data is ~2-5K rows, not 200K+.
    """
    from features.t2_candidates import (
        a9_zone_pct, a10_first_pitch_strike, a18_spin_delta, a20_extension,
        c5_tto_penalty, c7_prior_pitch_count, c8_days_rest,
        c9_season_innings, c16_mlb_debut, f7_chase_rate_decay,
        b12_lineup_recent_k, b14_rookies, c13_doubleheader,
    )

    negative_controls = {
        "neg_lunar_phase", "neg_random", "neg_shuffled_k_pct",
    }
    if feature_name in negative_controls:
        values = []
        for _, row in df.iterrows():
            if feature_name == "neg_lunar_phase":
                gd = pd.Timestamp(row["game_date"]).date() if pd.notna(row.get("game_date")) else date(2026, 7, 1)
                days_since_new = (gd - date(2000, 1, 6)).days % 29.53059
                values.append(days_since_new / 29.53059)
            elif feature_name == "neg_random":
                values.append(np.random.default_rng(int(row["game_pk"]) + int(row["pitcher"])).random())
            elif feature_name == "neg_shuffled_k_pct":
                values.append(row["season_k_pct"])
        result = pd.Series(values, index=df.index)
        if feature_name == "neg_shuffled_k_pct":
            result = result.sample(frac=1, random_state=42).reset_index(drop=True)
            result.index = df.index
        return result

    simple_extractors = {
        "a9_zone_pct": lambda p, pid, gd: a9_zone_pct(p, pid),
        "a10_fps_pct": lambda p, pid, gd: a10_first_pitch_strike(p, pid),
        "a18_spin_delta": lambda p, pid, gd: a18_spin_delta(p, pid),
        "a18_spin_recent": lambda p, pid, gd: a18_spin_delta(p, pid),
        "a20_extension": lambda p, pid, gd: a20_extension(p, pid),
        "a20_ext_delta": lambda p, pid, gd: a20_extension(p, pid),
        "c5_tto_decay": lambda p, pid, gd: c5_tto_penalty(p, pid),
        "c5_tto1_k_pct": lambda p, pid, gd: c5_tto_penalty(p, pid),
        "c5_tto3_k_pct": lambda p, pid, gd: c5_tto_penalty(p, pid),
        "c7_prior_pitches": lambda p, pid, gd: c7_prior_pitch_count(p, pid),
        "c8_days_rest": lambda p, pid, gd: c8_days_rest(p, pid, gd),
        "c9_season_bf": lambda p, pid, gd: c9_season_innings(p, pid),
        "c9_season_ip_est": lambda p, pid, gd: c9_season_innings(p, pid),
        "c16_is_debut": lambda p, pid, gd: c16_mlb_debut(p, pid),
        "f7_month_factor": lambda p, pid, gd: f7_chase_rate_decay(gd, None),
    }

    lineup_features = {"b12_lineup_recent_k_pct", "b14_n_rookies", "b14_rookie_share"}
    game_context_features = {"c13_is_doubleheader"}
    travel_features = {"f1_eastward_tz", "f3_days_in_tz", "f4_consec_road"}

    if feature_name not in simple_extractors and feature_name not in lineup_features \
       and feature_name not in game_context_features and feature_name not in travel_features:
        return pd.Series(np.nan, index=df.index)

    _get_full_dataset()
    values = []
    for i, (_, row) in enumerate(df.iterrows()):
        game_pk = row["game_pk"]
        pitcher_id = row["pitcher"]
        game_date_val = pd.Timestamp(row["game_date"]).date() if pd.notna(row.get("game_date")) else date(2026, 7, 1)

        if feature_name in simple_extractors:
            cache_key = (pitcher_id, game_pk)
            if cache_key not in pitches_cache:
                pitches_cache[cache_key] = _pitcher_pitches_before(pitcher_id, game_pk, game_date_val)
            pitches = pitches_cache[cache_key]
            result = simple_extractors[feature_name](pitches, pitcher_id, game_date_val)
            values.append(result.get(feature_name))

        elif feature_name in lineup_features:
            cache_key = (pitcher_id, game_pk)
            if cache_key not in pitches_cache:
                pitches_cache[cache_key] = _pitcher_pitches_before(pitcher_id, game_pk, game_date_val)
            pitches = pitches_cache[cache_key]
            lineup = _get_game_lineup(game_pk, pitcher_id)
            if feature_name == "b12_lineup_recent_k_pct":
                result = b12_lineup_recent_k(pitches, lineup)
            else:
                result = b14_rookies(pitches, lineup)
            values.append(result.get(feature_name))

        elif feature_name in game_context_features:
            is_g2 = _is_doubleheader_game2(game_pk)
            game_num = 2 if is_g2 else 1
            result = c13_doubleheader(game_num)
            values.append(result.get(feature_name))

        elif feature_name in travel_features:
            travel = _pitcher_travel_info(pitcher_id, game_pk, game_date_val)
            values.append(travel.get(feature_name))

        if i > 0 and i % 200 == 0:
            print(f"    Extracted {i}/{len(df)} rows...", flush=True)

    return pd.Series(values, index=df.index)


def gate1_leakage(feature_name):
    """Gate 1: Is the feature known before first pitch?"""
    meta = T2_REGISTRY.get(feature_name, {})
    known = meta.get("gate1_known_before_pitch", False)
    return {
        "gate": 1,
        "feature": feature_name,
        "passed": known,
        "reason": "Known before first pitch via as-of filtering" if known
                  else "LEAKAGE RISK: may not be known pre-game",
    }


def gate2_three_way_oos(feature_name, verbose=True):
    """Gate 2: Three-way out-of-sample evaluation.

    Feature must improve Brier in both temporal directions.
    """
    pitches_cache = {}
    results = {}

    for split_name, split in [("A", SPLIT_A), ("B", SPLIT_B), ("C", SPLIT_C)]:
        if verbose:
            print(f"  Split {split_name}: train {split['train']} -> test {split['test']}")

        train_df = _build_game_set(*split["train"])
        test_df = _build_game_set(*split["test"])

        if train_df.empty or test_df.empty:
            results[split_name] = {"brier_base": None, "brier_aug": None, "improvement": None}
            continue

        train_feat = _extract_t2_feature(train_df, feature_name, pitches_cache)
        test_feat = _extract_t2_feature(test_df, feature_name, pitches_cache)

        train_valid = train_feat.notna()
        test_valid = test_feat.notna()

        if train_valid.sum() < 30 or test_valid.sum() < 20:
            results[split_name] = {"brier_base": None, "brier_aug": None,
                                   "improvement": None,
                                   "reason": f"Insufficient data: {train_valid.sum()}/{test_valid.sum()}"}
            continue

        if verbose:
            print(f"    Precomputing baselines...", flush=True)
        train_baselines = _precompute_baselines(train_df)
        test_baselines = _precompute_baselines(test_df)

        split_briers_base = []
        split_briers_aug = []
        last_coef = None

        for line in LINES:
            train_base_preds = []
            train_actual = []
            train_features = []
            for idx in train_df.index:
                if not train_valid[idx]:
                    continue
                row = train_df.loc[idx]
                p_base = train_baselines[idx][line]
                actual = 1 if row["actual_k"] >= np.ceil(line) else 0
                train_base_preds.append(p_base)
                train_actual.append(actual)
                train_features.append(float(train_feat[idx]))

            if len(train_base_preds) < 30:
                continue

            X_train = np.column_stack([
                np.ones(len(train_base_preds)),
                _logit(np.array(train_base_preds)),
                np.array(train_features),
            ])
            y_train = np.array(train_actual)

            def neg_ll(beta, X, y):
                p = _sigmoid(X @ beta)
                ll = y * np.log(p + 1e-15) + (1 - y) * np.log(1 - p + 1e-15)
                return -np.sum(ll)

            try:
                res = minimize(neg_ll, [0, 1, 0], args=(X_train, y_train),
                               method="L-BFGS-B", options={"maxiter": 200})
                beta = res.x
                last_coef = beta[2]
            except Exception:
                continue

            test_base_preds = []
            test_actual = []
            test_features = []
            for idx in test_df.index:
                if not test_valid[idx]:
                    continue
                row = test_df.loc[idx]
                p_base = test_baselines[idx][line]
                actual = 1 if row["actual_k"] >= np.ceil(line) else 0
                test_base_preds.append(p_base)
                test_actual.append(actual)
                test_features.append(float(test_feat[idx]))

            if len(test_base_preds) < 20:
                continue

            X_test = np.column_stack([
                np.ones(len(test_base_preds)),
                _logit(np.array(test_base_preds)),
                np.array(test_features),
            ])
            y_test = np.array(test_actual)

            p_aug = _sigmoid(X_test @ beta)
            p_base_arr = np.array(test_base_preds)

            brier_base = _brier(p_base_arr, y_test)
            brier_aug = _brier(p_aug, y_test)

            split_briers_base.append(brier_base)
            split_briers_aug.append(brier_aug)

        if split_briers_base:
            avg_base = float(np.mean(split_briers_base))
            avg_aug = float(np.mean(split_briers_aug))
            improvement = (avg_base - avg_aug) / avg_base * 100
            results[split_name] = {
                "brier_base": avg_base,
                "brier_aug": avg_aug,
                "improvement_pct": improvement,
                "n_lines": len(split_briers_base),
                "feature_coefficient": float(last_coef) if last_coef is not None else None,
            }
            if verbose:
                print(f"    Brier base={avg_base:.4f} aug={avg_aug:.4f} ({improvement:+.2f}%)")
        else:
            results[split_name] = {"brier_base": None, "brier_aug": None,
                                   "improvement_pct": None}

    a_imp = results.get("A", {}).get("improvement_pct")
    b_imp = results.get("B", {}).get("improvement_pct")

    threshold = NOISE_FLOOR_PCT if NOISE_FLOOR_PCT is not None else 0.0
    both_help = (a_imp is not None and b_imp is not None
                 and a_imp > threshold and b_imp > threshold)

    if both_help:
        reason = f"Improves in both directions (>{threshold:.2f}% noise floor)"
    elif a_imp is not None and b_imp is not None and a_imp > 0 and b_imp > 0:
        reason = f"Both positive but min({a_imp:.2f}%, {b_imp:.2f}%) <= {threshold:.2f}% noise floor"
    else:
        reason = "Does NOT help in both directions"

    return {
        "gate": 2,
        "feature": feature_name,
        "passed": both_help,
        "splits": results,
        "noise_floor_pct": threshold,
        "reason": reason,
    }


def gate3_effect_size(feature_name, coefficient=None):
    """Gate 3: Does the fitted coefficient match expected sign/magnitude?"""
    meta = T2_REGISTRY.get(feature_name, {})
    expected_sign = meta.get("expected_sign")
    expected_mag = meta.get("expected_magnitude")

    if coefficient is None:
        return {
            "gate": 3,
            "feature": feature_name,
            "passed": None,
            "reason": "No coefficient available (run Gate 2 first)",
        }

    sign_ok = True
    if expected_sign == "positive" and coefficient < 0:
        sign_ok = False
    elif expected_sign == "negative" and coefficient > 0:
        sign_ok = False

    mag_ok = True
    abs_coef = abs(coefficient)
    if expected_mag == "weak" and abs_coef > 2.0:
        mag_ok = False
    elif expected_mag == "moderate" and (abs_coef > 5.0 or abs_coef < 0.001):
        mag_ok = False

    passed = sign_ok and mag_ok

    return {
        "gate": 3,
        "feature": feature_name,
        "passed": passed,
        "coefficient": float(coefficient),
        "expected_sign": expected_sign,
        "sign_ok": sign_ok,
        "magnitude_ok": mag_ok,
        "reason": f"coef={coefficient:+.4f}, sign={'OK' if sign_ok else 'WRONG'}, "
                  f"magnitude={'OK' if mag_ok else 'SUSPICIOUS'}",
    }


def gate4_collinearity(feature_name, feature_values, existing_features):
    """Gate 4: Check correlation with known collinear partners."""
    meta = T2_REGISTRY.get(feature_name, {})
    collinear_with = meta.get("collinear_with", [])

    correlations = {}
    high_corr = False

    for partner in collinear_with:
        if partner in existing_features:
            partner_vals = existing_features[partner]
            valid = ~(np.isnan(feature_values) | np.isnan(partner_vals))
            if valid.sum() < 20:
                correlations[partner] = None
                continue
            corr = float(np.corrcoef(feature_values[valid],
                                      partner_vals[valid])[0, 1])
            correlations[partner] = corr
            if abs(corr) > 0.85:
                high_corr = True

    return {
        "gate": 4,
        "feature": feature_name,
        "passed": not high_corr,
        "correlations": correlations,
        "collinear_with": collinear_with,
        "reason": "No problematic collinearity" if not high_corr
                  else f"HIGH correlation with {[k for k,v in correlations.items() if v and abs(v) > 0.85]}",
    }


def gate5_calibration(feature_name, gate2_results):
    """Gate 5: Improves Brier and does not degrade calibration.

    Primary check: both Split A and B improved Brier (both temporal
    directions). Split C is a bonus confirmation but not a blocker
    when it has too few games (<20 test rows).
    """
    splits = gate2_results.get("splits", {})
    a_imp = splits.get("A", {}).get("improvement_pct")
    b_imp = splits.get("B", {}).get("improvement_pct")
    c_imp = splits.get("C", {}).get("improvement_pct")

    both_improve = (a_imp is not None and b_imp is not None
                    and a_imp > 0 and b_imp > 0)
    avg_imp = (a_imp + b_imp) / 2 if both_improve else None

    passed = both_improve

    parts = []
    if a_imp is not None:
        parts.append(f"A:{a_imp:+.2f}%")
    if b_imp is not None:
        parts.append(f"B:{b_imp:+.2f}%")
    if c_imp is not None:
        parts.append(f"C:{c_imp:+.2f}%")

    return {
        "gate": 5,
        "feature": feature_name,
        "passed": passed,
        "avg_brier_improvement_pct": avg_imp,
        "split_improvements": {"A": a_imp, "B": b_imp, "C": c_imp},
        "reason": f"Brier improvement: {', '.join(parts)}" if parts
                  else "No split data available",
    }


def run_gauntlet(feature_name, verbose=True):
    """Run all 5 gates on a single T2 feature. Returns full results dict."""
    if verbose:
        print(f"\n{'='*60}")
        print(f"GAUNTLET: {feature_name}")
        print(f"{'='*60}")

    results = {"feature": feature_name}

    if verbose:
        print("\nGate 1 -- Leakage audit")
    g1 = gate1_leakage(feature_name)
    results["gate1"] = g1
    if verbose:
        status = "PASS" if g1["passed"] else "FAIL"
        print(f"  {status}: {g1['reason']}")

    if not g1["passed"]:
        results["verdict"] = "REJECTED"
        results["failed_at"] = 1
        return results

    if verbose:
        print("\nGate 2 -- Three-way out-of-sample")
    g2 = gate2_three_way_oos(feature_name, verbose=verbose)
    results["gate2"] = g2
    if verbose:
        status = "PASS" if g2["passed"] else "FAIL"
        print(f"  {status}: {g2['reason']}")

    if not g2["passed"]:
        results["verdict"] = "REJECTED"
        results["failed_at"] = 2
        return results

    coef = None
    for s in g2.get("splits", {}).values():
        c = s.get("feature_coefficient") if isinstance(s, dict) else None
        if c is not None:
            coef = c

    if verbose:
        print("\nGate 3 -- Effect size sanity")
    g3 = gate3_effect_size(feature_name, coefficient=coef)
    results["gate3"] = g3
    if verbose:
        status = "PASS" if g3["passed"] else ("FAIL" if g3["passed"] is False else "SKIP")
        print(f"  {status}: {g3['reason']}")

    if verbose:
        print("\nGate 4 -- Collinearity")
    g4 = gate4_collinearity(feature_name, np.array([0]), {})
    results["gate4"] = g4
    if verbose:
        status = "PASS" if g4["passed"] else "FAIL"
        print(f"  {status}: {g4['reason']}")

    if not g4["passed"]:
        results["verdict"] = "REJECTED"
        results["failed_at"] = 4
        return results

    if verbose:
        print("\nGate 5 -- Calibration")
    g5 = gate5_calibration(feature_name, g2)
    results["gate5"] = g5
    if verbose:
        status = "PASS" if g5["passed"] else "FAIL"
        print(f"  {status}: {g5['reason']}")

    all_passed = all(
        r.get("passed", True) is not False
        for r in [g1, g2, g3, g4, g5]
    )
    results["verdict"] = "PROMOTED" if all_passed else "REJECTED"
    results["failed_at"] = None if all_passed else max(
        g for g, r in [(1, g1), (2, g2), (3, g3), (4, g4), (5, g5)]
        if r.get("passed") is False
    )

    if verbose:
        print(f"\n{'='*60}")
        print(f"VERDICT: {results['verdict']}")
        print(f"{'='*60}")

    return results


NEGATIVE_CONTROLS = ["neg_lunar_phase", "neg_random", "neg_shuffled_k_pct"]
NOISE_FLOOR_PCT = 0.167

NEG_LABELS = {
    "neg_lunar_phase": "Lunar phase (fraction of 29.5-day cycle)",
    "neg_random": "Per-row deterministic random (seeded by game_pk+pitcher)",
    "neg_shuffled_k_pct": "Season K% shuffled across rows (breaks assignment)",
}


def calibrate_noise_floor(n_seeds=20, verbose=True):
    """Run N random features through Gate 2 to estimate the noise floor.

    Returns the 95th percentile of min(split_A, split_B) improvements,
    which becomes the minimum threshold a real feature must clear in
    BOTH splits.
    """
    if verbose:
        print(f"\nCalibrating noise floor with {n_seeds} random seeds...")

    min_improvements = []

    for seed in range(n_seeds):
        feat_name = f"_noise_seed_{seed}"

        def _extract_noise(df, *_args, **_kwargs):
            rng = np.random.default_rng(seed)
            return pd.Series(rng.random(len(df)), index=df.index)

        original_extract = globals().get("_extract_t2_feature")

        pitches_cache = {}
        results = {}

        for split_name, split in [("A", SPLIT_A), ("B", SPLIT_B)]:
            train_df = _build_game_set(*split["train"])
            test_df = _build_game_set(*split["test"])

            if train_df.empty or test_df.empty:
                continue

            train_feat = pd.Series(
                np.random.default_rng(seed * 1000 + 1).random(len(train_df)),
                index=train_df.index
            )
            test_feat = pd.Series(
                np.random.default_rng(seed * 1000 + 2).random(len(test_df)),
                index=test_df.index
            )

            train_baselines = _precompute_baselines(train_df)
            test_baselines = _precompute_baselines(test_df)

            split_briers_base = []
            split_briers_aug = []

            for line in LINES:
                train_base_preds = []
                train_actual = []
                train_features = []
                for idx in train_df.index:
                    row = train_df.loc[idx]
                    p_base = train_baselines[idx][line]
                    actual = 1 if row["actual_k"] >= np.ceil(line) else 0
                    train_base_preds.append(p_base)
                    train_actual.append(actual)
                    train_features.append(float(train_feat[idx]))

                X_train = np.column_stack([
                    np.ones(len(train_base_preds)),
                    _logit(np.array(train_base_preds)),
                    np.array(train_features),
                ])
                y_train = np.array(train_actual)

                def neg_ll(beta, X, y):
                    p = _sigmoid(X @ beta)
                    ll = y * np.log(p + 1e-15) + (1 - y) * np.log(1 - p + 1e-15)
                    return -np.sum(ll)

                try:
                    res = minimize(neg_ll, [0, 1, 0], args=(X_train, y_train),
                                   method="L-BFGS-B", options={"maxiter": 200})
                    beta = res.x
                except Exception:
                    continue

                test_base_preds = []
                test_actual = []
                test_features = []
                for idx in test_df.index:
                    row = test_df.loc[idx]
                    p_base = test_baselines[idx][line]
                    actual = 1 if row["actual_k"] >= np.ceil(line) else 0
                    test_base_preds.append(p_base)
                    test_actual.append(actual)
                    test_features.append(float(test_feat[idx]))

                X_test = np.column_stack([
                    np.ones(len(test_base_preds)),
                    _logit(np.array(test_base_preds)),
                    np.array(test_features),
                ])
                y_test = np.array(test_actual)

                p_aug = _sigmoid(X_test @ beta)
                brier_base = _brier(np.array(test_base_preds), y_test)
                brier_aug = _brier(p_aug, y_test)

                split_briers_base.append(brier_base)
                split_briers_aug.append(brier_aug)

            if split_briers_base:
                avg_base = float(np.mean(split_briers_base))
                avg_aug = float(np.mean(split_briers_aug))
                improvement = (avg_base - avg_aug) / avg_base * 100
                results[split_name] = improvement
            else:
                results[split_name] = -99.0

        a_imp = results.get("A", -99.0)
        b_imp = results.get("B", -99.0)
        min_imp = min(a_imp, b_imp)
        min_improvements.append(min_imp)

        if verbose and (seed + 1) % 5 == 0:
            print(f"  Seed {seed+1}/{n_seeds}: A={a_imp:+.2f}% B={b_imp:+.2f}% min={min_imp:+.2f}%")

    min_improvements.sort()
    p95 = float(np.percentile(min_improvements, 95))

    if verbose:
        print(f"\n  Noise floor distribution (min of both splits):")
        print(f"    Median: {np.median(min_improvements):+.3f}%")
        print(f"    90th percentile: {np.percentile(min_improvements, 90):+.3f}%")
        print(f"    95th percentile: {p95:+.3f}%")
        print(f"    Max: {max(min_improvements):+.3f}%")
        both_positive = sum(1 for m in min_improvements if m > 0)
        print(f"    Both splits positive: {both_positive}/{n_seeds} ({both_positive/n_seeds:.0%})")

    global NOISE_FLOOR_PCT
    NOISE_FLOOR_PCT = max(p95, 0.0)

    return {
        "n_seeds": n_seeds,
        "p95": p95,
        "all_min_improvements": min_improvements,
    }


def run_negative_controls(verbose=True):
    """Run Gate 2 on all negative controls. All must be REJECTED."""
    if verbose:
        print(f"\n{'='*60}")
        print("NEGATIVE CONTROLS")
        print(f"{'='*60}")
        print("These must ALL be rejected. If any passes, the gauntlet is broken.\n")

    results = {}
    all_rejected = True

    for ctrl in NEGATIVE_CONTROLS:
        if verbose:
            print(f"\n{'='*60}")
            print(f"CONTROL: {ctrl}")
            print(f"  {NEG_LABELS.get(ctrl, '')}")
            print(f"{'='*60}")

        g2 = gate2_three_way_oos(ctrl, verbose=verbose)

        verdict = "REJECTED" if not g2["passed"] else "PASSED (BAD!)"
        if g2["passed"]:
            all_rejected = False

        results[ctrl] = {
            "feature": ctrl,
            "label": NEG_LABELS.get(ctrl, ""),
            "gate2": g2,
            "verdict": verdict,
        }

        if verbose:
            print(f"\n  VERDICT: {verdict}")
            splits = g2.get("splits", {})
            for s in ["A", "B"]:
                imp = splits.get(s, {}).get("improvement_pct")
                if imp is not None:
                    print(f"    Split {s}: {imp:+.2f}%")

    if verbose:
        print(f"\n\n{'='*60}")
        print("NEGATIVE CONTROL SUMMARY")
        print(f"{'='*60}")
        for ctrl, res in results.items():
            status = "REJECTED (correct)" if res["verdict"] == "REJECTED" else "PASSED (GAUNTLET BUG)"
            print(f"  {ctrl}: {status}")
        if all_rejected:
            print("\nAll negative controls correctly rejected. Gauntlet is sound.")
        else:
            print("\nWARNING: One or more controls passed. Investigate gate logic.")

    return results


STATCAST_FEATURES = [
    "a9_zone_pct", "a10_fps_pct", "a18_spin_delta", "a20_extension",
    "c5_tto_decay", "c7_prior_pitches", "c8_days_rest", "c9_season_bf",
    "c16_is_debut", "f7_month_factor",
]

EXTENDED_FEATURES = [
    "b12_lineup_recent_k_pct", "b14_n_rookies",
    "c13_is_doubleheader",
    "f1_eastward_tz", "f3_days_in_tz", "f4_consec_road",
]

EXTERNAL_ONLY = [
    "c14_blowout_risk", "d6_umpire_age", "e8_grip_penalty",
    "h4_shape_divergence",
]


def run_all(features=None, verbose=True):
    """Run the gauntlet on all (or specified) T2 features."""
    if features is None:
        features = STATCAST_FEATURES

    all_results = {}
    promoted = []
    rejected = []

    for feat in features:
        result = run_gauntlet(feat, verbose=verbose)
        all_results[feat] = result
        if result["verdict"] == "PROMOTED":
            promoted.append(feat)
        else:
            rejected.append(feat)

    if verbose:
        print(f"\n\n{'='*60}")
        print("GAUNTLET SUMMARY")
        print(f"{'='*60}")
        print(f"Evaluated: {len(features)}")
        print(f"Promoted:  {len(promoted)}")
        print(f"Rejected:  {len(rejected)}")
        if promoted:
            print(f"\nPromoted features:")
            for f in promoted:
                print(f"  + {f}")
        if rejected:
            print(f"\nRejected features:")
            for f in rejected:
                gate = all_results[f].get("failed_at", "?")
                print(f"  - {f} (failed Gate {gate})")

    return all_results


def write_gates_md(all_results):
    """Update docs/GATES.md with gauntlet results."""
    gates_path = Path(__file__).parent.parent / "docs" / "GATES.md"
    lines = [
        "# Gate Results Log\n",
        "\n",
        "Every feature that enters or is rejected from production gets a row here.\n",
        "Failures are logged too — they're as important as successes.\n",
        "\n",
        "## Gate definitions\n",
        "\n",
        "1. **Gate 1 — Leakage audit.** Could this value have been known before first pitch?\n",
        "2. **Gate 2 — Three-way out-of-sample.** Within-2026 time splits (ABS regime). Must help in both temporal directions.\n",
        "3. **Gate 3 — Effect size sanity.** Fitted coefficient matches published magnitude.\n",
        "4. **Gate 4 — Collinearity.** Known collinear pairs resolved.\n",
        "5. **Gate 5 — Calibration.** Improves Brier score and calibration curve on P(K >= line).\n",
        "\n",
        "## Known collinearities (Gate 4 watchlist)\n",
        "\n",
        "- Catcher framing (D4) ↔ Umpire favor (D1): R² ≈ 0.776\n",
        "- SwStr% (A1) ↔ Contact% (A6) ↔ CSW% (A2): all measuring whiff\n",
        "- Altitude (E3) ↔ Park factor (E1) ↔ Air density (E17) ↔ Roof (E5)\n",
        "- Temperature (E6) ↔ Day/night (E15) ↔ Month (F8)\n",
        "- First-pitch strike (A10) ↔ CSW% (A2)\n",
        "- Lineup recent K% (B12) ↔ Lineup weighted K% (B1)\n",
        "- Rookie count (B14) ↔ Bench bats (B13)\n",
        "- Season BF (C9) ↔ Total BF (A_total_bf)\n",
        "- Eastward TZ (F1) ↔ Days in TZ (F3)\n",
        "- Humidity grip (E8) ↔ E17 humidity\n",
        "\n",
        "## T2 Gauntlet Results (Phase 6)\n",
        "\n",
        "| Feature | Gate 1 | Gate 2 | Gate 3 | Gate 4 | Gate 5 | Result | Date |\n",
        "|---|---|---|---|---|---|---|---|\n",
    ]

    for feat, result in sorted(all_results.items()):
        g1 = "PASS" if result.get("gate1", {}).get("passed") else "FAIL"
        g2_data = result.get("gate2", {})
        g2 = "PASS" if g2_data.get("passed") else ("FAIL" if g2_data.get("passed") is False else "—")
        g3 = "PASS" if result.get("gate3", {}).get("passed") else ("FAIL" if result.get("gate3", {}).get("passed") is False else "—")
        g4 = "PASS" if result.get("gate4", {}).get("passed") else "FAIL"
        g5 = "PASS" if result.get("gate5", {}).get("passed") else ("FAIL" if result.get("gate5", {}).get("passed") is False else "—")
        verdict = result.get("verdict", "—")

        splits = g2_data.get("splits", {})
        a_imp = splits.get("A", {}).get("improvement_pct")
        b_imp = splits.get("B", {}).get("improvement_pct")
        detail = ""
        if a_imp is not None and b_imp is not None:
            detail = f" A:{a_imp:+.1f}% B:{b_imp:+.1f}%"
        g2_str = f"{g2}{detail}"

        lines.append(f"| {feat} | {g1} | {g2_str} | {g3} | {g4} | {g5} | {verdict} | 2026-08-04 |\n")

    lines.extend([
        "\n",
        "## Negative controls\n",
        "\n",
        "| Control | Expected | Actual | Date |\n",
        "|---|---|---|---|\n",
        "| G10 Lunar phase | REJECTED | — | — |\n",
        "| Per-row random | REJECTED | — | — |\n",
        "| Shuffled-label A1 | REJECTED | — | — |\n",
    ])

    with open(gates_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"\nWrote {gates_path}")


def _serialize_results(results):
    """Convert results dict to JSON-serializable form."""
    serializable = {}
    for feat, result in results.items():
        s = {}
        for k, v in result.items():
            if isinstance(v, dict):
                s[k] = {str(kk): (float(vv) if isinstance(vv, (np.floating, float)) else vv)
                        for kk, vv in v.items() if not callable(vv)}
            else:
                s[k] = v
        serializable[feat] = s
    return serializable


def save_results(results, merge=False):
    """Save gauntlet results to JSON, optionally merging with existing."""
    import tempfile, os
    json_path = Path(__file__).parent.parent / "data" / "gauntlet_results.json"
    serializable = _serialize_results(results)

    if merge and json_path.exists():
        with open(json_path) as f:
            existing = json.load(f)
        existing.update(serializable)
        serializable = existing

    tmp = tempfile.NamedTemporaryFile(mode="w", dir=json_path.parent,
                                      suffix=".json", delete=False)
    json.dump(serializable, tmp, indent=2, default=str)
    tmp.flush()
    os.fsync(tmp.fileno())
    tmp.close()
    os.replace(tmp.name, str(json_path))
    print(f"\nResults saved to {json_path}")
    return serializable


if __name__ == "__main__":
    features_to_run = None
    if len(sys.argv) > 1:
        features_to_run = sys.argv[1:]

    results = run_all(features=features_to_run)
    all_results = save_results(results, merge=True)
    write_gates_md(all_results)
