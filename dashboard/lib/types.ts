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
}
