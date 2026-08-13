export interface TaggedPnl {
  value: number;
  basis: string;
}

export interface PickInfo {
  pick_side: string;
  pick_strength: string;
  pick_label: string;
  units_risked: number;
  bet_placed: boolean;
  graded_result: string | null;
  actual_strikeouts: number | null;
  profit_loss_units: TaggedPnl;
  clv_pct: number | null;
  edge_pct: number | null;
  model_prob_over: number | null;
  market_over_odds: string;
  market_under_odds: string;
  lineup_source: string;
}

export interface LadderRung {
  milestone: number;
  odds: string;
  raw_model_prob?: number;
  model_prob?: number;
  fair_prob?: number;
  blended_prob?: number;
  edge: number | null;
  strength?: string;
  units_risked: number;
  status: string;
  current_rules_units?: number;
  pick?: PickInfo | null;
}

export interface SlatePitcher {
  pitcher_id: number | null;
  pitcher_name: string;
  pitcher_team: string;
  opponent_team: string;
  is_home: boolean;
  venue: string;
  game_pk?: number;
  start_time_utc?: string;
  line: number | null;
  over_odds: string;
  under_odds: string;
  lineup_source?: string;
  expected_k?: number;
  expected_bf?: number;
  p_over_raw?: number;
  p_over_calibrated?: number;
  blended_prob_over?: number;
  fair_over?: number;
  hold_pct?: number;
  best_side?: string;
  edge_best: number | null;
  threshold?: number;
  strength?: string;
  primary_units_risked?: number;
  current_rules_primary_units?: number;
  k_dist: number[];
  /** Live line from the MLB-API watcher (workers/live_strikeouts.py). */
  live?: {
    strikeouts: number | null;
    batters_faced: number | null;
    pitches: number | null;
    innings: string | null;
    final: boolean;
    game_state?: string;
    status?: string;
    /**
     * The poll stopped mid-game but a settled total exists, so `final`
     * was forced true. The counts above are the last in-game
     * observation and may trail `actual_strikeouts`.
     */
    stale_poll?: boolean;
  };
  /** "live" when actual_strikeouts came from the watcher, not Statcast. */
  result_source?: string;
  ladder: LadderRung[];
  pick: PickInfo | null;
  actual_strikeouts: number | null;
}

export interface Slate {
  date: string;
  reconstructed: boolean;
  note: string | null;
  generated_at: string | null;
  pitcher_count: number;
  bet_count: number;
  pitchers: SlatePitcher[];
}

export interface DailyPoint {
  date: string;
  daily_pnl: TaggedPnl;
  cumulative_pnl: TaggedPnl;
  units: number;
  w: number;
  l: number;
}

export interface LedgerRow {
  date: string;
  pitcher_name: string;
  pick_label: string;
  line: string;
  pick_side: string;
  odds: string;
  units_risked: number;
  bet_placed: boolean;
  graded_result: string | null;
  actual_strikeouts: number | null;
  profit_loss_units: TaggedPnl;
  is_ladder: boolean;
  clv_pct: number | null;
}

export interface SplitBucket {
  wins: number;
  losses: number;
  hit_rate: number | null;
  pnl: TaggedPnl;
  roi: number | null;
}

export interface PerLineBrier {
  line: number;
  naive_brier: number;
  model_brier: number;
  improvement_pct: number;
  n: number;
}

export interface CalibrationBin {
  pred_mean: number;
  actual_rate: number;
  calibrated_mean: number | null;
  n: number;
}

/* ---- live model log (data/model_log.csv) ---------------------------- */

export interface LiveCalibrationBin {
  pred_mean: number;
  actual_rate: number;
  n: number;
}

export interface WorkloadMiss {
  date: string;
  pitcher_name: string;
  expected_bf: number;
  actual_bf: number;
  bf_error: number;
}

export interface LiveWorkload {
  n: number;
  mean_bf_error: number;
  mean_abs_bf_error: number;
  pct_within_3: number;
  pct_badly_short: number;
  worst_misses: WorkloadMiss[];
}

export interface LiveStrikeoutAccuracy {
  n: number;
  mean_k_error: number;
  mean_abs_k_error: number;
}

export interface LiveModelDate {
  date: string;
  n: number;
  n_scored: number;
  brier: number | null;
  /** Brier − floor for this date's own line mix. */
  excess: number | null;
  baseline_excess: number | null;
  mean_abs_bf_error: number | null;
  /** True if ANY row on this date was reconstructed. */
  reconstructed: boolean;
  n_reconstructed: number;
}

export interface LiveModel {
  n_total: number;
  n_live: number;
  /** Live rows that can actually be scored (have both a prob and an outcome). */
  n_live_scored: number;
  n_reconstructed: number;
  n_used: number;
  n_scored: number;
  /** "live" | "live_plus_reconstructed" */
  row_basis: string;
  /** Non-null whenever the sample is too thin (or contaminated) to validate. */
  sample_warning: string | null;
  /** Backtest Brier re-weighted onto the live line mix. */
  backtest_brier_baseline: number;
  /** "line_matched" | "flat" */
  baseline_basis: string;
  baseline_naive: number | null;
  baseline_lines_matched: number;
  baseline_lines_unmatched: number;
  backtest_brier_flat: number | null;
  brier: number | null;
  brier_se: number | null;
  /** 2 SE. A gap smaller than this is a tie, not drift. */
  verdict_band: number | null;
  /** live − baseline on RAW Brier. Kept for reference; not the verdict. */
  brier_delta: number | null;
  /** Mean p(1-p) — the Brier a perfectly calibrated model would post here. */
  brier_floor: number | null;
  /** brier − floor. THIS is what the verdict rides on. */
  excess: number | null;
  baseline_excess: number | null;
  excess_delta: number | null;
  verdict: string;
  mean_predicted_over: number | null;
  actual_over_rate: number | null;
  bias: number | null;
  calibration_bins: LiveCalibrationBin[];
  workload: LiveWorkload | null;
  strikeouts: LiveStrikeoutAccuracy | null;
  by_date: LiveModelDate[];
  dates: string[];
}

/* ---- shadow portfolio (tools/shadow.py) ------------------------------
 * Counterfactual: what the model WOULD have bet at other trust weights.
 * Every P&L here is tagged "shadow_flat_100u", never the real basis —
 * tools/pnl_guard.py enforces that separation in both directions. */

export interface ShadowPick {
  date: string;
  pitcher_name: string;
  line: number | null;
  side: string;
  odds: number;
  edge: number;
  threshold: number;
  units: number;
  won: boolean;
  pnl: TaggedPnl;
  clv_pct: number | null;
}

export interface ShadowGridRow {
  trust_weight: number;
  /** True for the weight production actually uses. */
  is_production: boolean;
  n_bets: number;
  wins: number;
  losses: number;
  hit_rate: number | null;
  units_staked: TaggedPnl;
  pnl: TaggedPnl;
  roi: number | null;
  clv_n: number;
  avg_clv_pct: number | null;
  picks: ShadowPick[];
}

export interface Shadow {
  basis: string;
  n_observations: number;
  n_dates: number;
  dates: string[];
  includes_reconstructed: boolean;
  production_trust_weight: number;
  grid: ShadowGridRow[];
  target_observations: number;
  ready_to_decide: boolean;
  note: string;
}

export interface GauntletFeature {
  feature: string;
  gates: Record<string, boolean | null>;
  promoted: boolean;
  failed_at: number | null;
  min_improvement_pct: number | null;
}

export interface DashboardData {
  generated_at: string;
  basis: string;
  today_et: string;
  record: {
    wins: number;
    losses: number;
    voids: number;
    pushes: number;
    postponed: number;
    pending: number;
    total_graded: number;
    hit_rate: number;
  };
  pnl: {
    total: TaggedPnl;
    total_risked: TaggedPnl;
    roi: number;
  };
  available_dates: string[];
  slates: Record<string, Slate>;
  performance: {
    daily: DailyPoint[];
    ledger: LedgerRow[];
    splits: {
      by_side: Record<string, SplitBucket>;
      by_strength: Record<string, SplitBucket>;
      by_type: Record<string, SplitBucket>;
    };
    clv: { n: number; avg_clv_pct: number | null };
  };
  model: {
    backtest: {
      train_desc: string;
      test_window: string;
      n_predictions: number;
      n_starts: number;
      naive_brier: number;
      model_brier: number;
      improvement_pct: number;
      per_line: PerLineBrier[];
      calibration_bins: CalibrationBin[];
    } | null;
    gauntlet: {
      features: GauntletFeature[];
      noise_floor_pct: number;
    } | null;
  };
  /** null until tools/model_log.py has written data/model_log.csv */
  live_model: LiveModel | null;
  /** Counterfactual portfolio. NOT money that moved. */
  shadow: Shadow | null;
}
