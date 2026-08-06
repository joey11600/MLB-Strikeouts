"use client";

// The machine's brain: honest backtest analytics + gauntlet results.
// Hand-rolled SVG charts, NRFI geometry idiom.
import Link from "next/link";
import { useState } from "react";
import { useDashboard } from "@/lib/data-context";
import { StatTile } from "@/components/stat-tiles";
import { cn, pctStr } from "@/lib/utils";
import type {
  CalibrationBin,
  LiveCalibrationBin,
  LiveModel,
  LiveModelDate,
  PerLineBrier,
} from "@/lib/types";

function CalibrationChart({ bins }: { bins: CalibrationBin[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 380;
  const H = 300;
  const padL = 40;
  const padR = 14;
  const padT = 12;
  const padB = 36;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const xFor = (v: number) => padL + v * innerW;
  const yFor = (v: number) => padT + (1 - v) * innerH;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label="Calibration curve: model predicted probability versus actual over rate, with the diagonal marking perfect calibration"
      >
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line
              x1={padL}
              x2={W - padR}
              y1={yFor(t)}
              y2={yFor(t)}
              stroke="rgba(255,255,255,0.05)"
            />
            <text
              x={padL - 6}
              y={yFor(t) + 3}
              textAnchor="end"
              fontSize="9"
              className="figure"
              fill="var(--color-ink-muted)"
            >
              {(t * 100).toFixed(0)}%
            </text>
            <text
              x={xFor(t)}
              y={H - padB + 14}
              textAnchor="middle"
              fontSize="9"
              className="figure"
              fill="var(--color-ink-muted)"
            >
              {(t * 100).toFixed(0)}%
            </text>
          </g>
        ))}
        <line
          x1={xFor(0)}
          y1={yFor(0)}
          x2={xFor(1)}
          y2={yFor(1)}
          stroke="rgba(255,255,255,0.18)"
          strokeDasharray="4 4"
        />
        <polyline
          points={bins
            .map((b) => `${xFor(b.pred_mean).toFixed(1)},${yFor(b.actual_rate).toFixed(1)}`)
            .join(" ")}
          fill="none"
          stroke="var(--color-under)"
          strokeWidth="1.6"
          opacity="0.85"
        />
        {bins.some((b) => b.calibrated_mean != null) && (
          <polyline
            points={bins
              .filter((b) => b.calibrated_mean != null)
              .map((b) => `${xFor(b.calibrated_mean as number).toFixed(1)},${yFor(b.actual_rate).toFixed(1)}`)
              .join(" ")}
            fill="none"
            stroke="var(--color-over)"
            strokeWidth="1.6"
          />
        )}
        {bins.map((b, i) => (
          <circle
            key={i}
            cx={xFor(b.pred_mean)}
            cy={yFor(b.actual_rate)}
            r={hover === i ? 4.5 : 3}
            fill="var(--color-under)"
            onPointerEnter={() => setHover(i)}
            onPointerLeave={() => setHover(null)}
          />
        ))}
        <text x={W / 2} y={H - 4} textAnchor="middle" fontSize="9.5" fill="var(--color-ink-muted)">
          model predicted P(over)
        </text>
      </svg>
      {hover != null && bins[hover] && (
        <div
          className="figure pointer-events-none absolute z-10 -translate-x-1/2 rounded-md border border-line bg-surface-2 px-2 py-1 text-[10px] text-ink-secondary shadow-[0_6px_18px_rgba(0,0,0,0.4)]"
          style={{
            left: `${((xFor(bins[hover].pred_mean) / W) * 100).toFixed(1)}%`,
            top: 0,
          }}
          role="status"
        >
          pred {(bins[hover].pred_mean * 100).toFixed(0)}% → actual{" "}
          {(bins[hover].actual_rate * 100).toFixed(0)}% (n={bins[hover].n})
        </div>
      )}
      <div className="mt-1 flex items-center gap-4 text-[10px] text-ink-secondary">
        <span className="flex items-center gap-1.5">
          <span className="h-[2px] w-4 bg-under" /> raw model
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-[2px] w-4 bg-over" /> after calibration
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0 w-4 border-t border-dashed border-white/30" /> perfect
        </span>
      </div>
    </div>
  );
}

function BrierBars({ perLine }: { perLine: PerLineBrier[] }) {
  const W = 380;
  const H = 220;
  const padL = 40;
  const padR = 8;
  const padT = 10;
  const padB = 30;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const maxB = Math.max(...perLine.map((r) => Math.max(r.naive_brier, r.model_brier)));
  const slot = innerW / perLine.length;
  const barW = Math.min(20, slot * 0.28);

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      role="img"
      aria-label="Brier score by line, naive baseline versus model — lower is better"
    >
      {perLine.map((r, i) => {
        const cx = padL + i * slot + slot / 2;
        const nH = (r.naive_brier / maxB) * innerH;
        const mH = (r.model_brier / maxB) * innerH;
        const better = r.model_brier <= r.naive_brier;
        return (
          <g key={r.line}>
            <rect
              x={cx - barW - 1.5}
              y={padT + innerH - nH}
              width={barW}
              height={nH}
              rx={2}
              fill="rgba(237,237,239,0.22)"
            />
            <rect
              x={cx + 1.5}
              y={padT + innerH - mH}
              width={barW}
              height={mH}
              rx={2}
              fill={better ? "var(--color-over)" : "var(--color-under)"}
              opacity={0.85}
            />
            <text
              x={cx}
              y={H - padB + 14}
              textAnchor="middle"
              fontSize="10"
              className="figure"
              fill="var(--color-ink-secondary)"
            >
              {r.line}
            </text>
            <text
              x={cx}
              y={padT + innerH - Math.max(nH, mH) - 5}
              textAnchor="middle"
              fontSize="8.5"
              className="figure"
              fill={better ? "var(--color-over)" : "var(--color-under)"}
            >
              {r.improvement_pct > 0 ? "+" : ""}
              {r.improvement_pct.toFixed(0)}%
            </text>
          </g>
        );
      })}
      <text x={padL} y={H - 4} fontSize="9.5" fill="var(--color-ink-muted)">
        line
      </text>
      <text x={W - padR} y={padT + 2} textAnchor="end" fontSize="9.5" fill="var(--color-ink-muted)">
        gray = naive · green = model (lower wins)
      </text>
    </svg>
  );
}

// ---- LIVE model log (data/model_log.csv) -------------------------------
// Same hand-rolled SVG idiom as the backtest charts above: no chart
// library, explicit W/H/pad box, xFor/yFor mappers, role="img".

function LiveCalibrationChart({ bins }: { bins: LiveCalibrationBin[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 380;
  const H = 300;
  const padL = 40;
  const padR = 14;
  const padT = 12;
  const padB = 36;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const xFor = (v: number) => padL + v * innerW;
  const yFor = (v: number) => padT + (1 - v) * innerH;
  const maxN = Math.max(...bins.map((b) => b.n), 1);

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={
          "Live calibration: for each bucket of logged predictions, the model's " +
          "calibrated probability of the over versus the share that actually went " +
          "over. The dashed diagonal is perfect calibration — points above it mean " +
          "the model under-predicted overs, points below mean it over-predicted."
        }
      >
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line
              x1={padL}
              x2={W - padR}
              y1={yFor(t)}
              y2={yFor(t)}
              stroke="rgba(255,255,255,0.05)"
            />
            <text
              x={padL - 6}
              y={yFor(t) + 3}
              textAnchor="end"
              fontSize="9"
              className="figure"
              fill="var(--color-ink-muted)"
            >
              {(t * 100).toFixed(0)}%
            </text>
            <text
              x={xFor(t)}
              y={H - padB + 14}
              textAnchor="middle"
              fontSize="9"
              className="figure"
              fill="var(--color-ink-muted)"
            >
              {(t * 100).toFixed(0)}%
            </text>
          </g>
        ))}
        <line
          x1={xFor(0)}
          y1={yFor(0)}
          x2={xFor(1)}
          y2={yFor(1)}
          stroke="rgba(255,255,255,0.18)"
          strokeDasharray="4 4"
        />
        {bins.map((b, i) => (
          <line
            key={`d${i}`}
            x1={xFor(b.pred_mean)}
            y1={yFor(b.actual_rate)}
            x2={xFor(b.pred_mean)}
            y2={yFor(b.pred_mean)}
            stroke="rgba(245,158,11,0.35)"
            strokeWidth="1"
          />
        ))}
        <polyline
          points={bins
            .map((b) => `${xFor(b.pred_mean).toFixed(1)},${yFor(b.actual_rate).toFixed(1)}`)
            .join(" ")}
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="1.6"
          opacity="0.85"
        />
        {bins.map((b, i) => (
          <circle
            key={i}
            cx={xFor(b.pred_mean)}
            cy={yFor(b.actual_rate)}
            r={(hover === i ? 2 : 0) + 2.5 + 2.5 * Math.sqrt(b.n / maxN)}
            fill="var(--color-accent)"
            onPointerEnter={() => setHover(i)}
            onPointerLeave={() => setHover(null)}
          />
        ))}
        <text x={W / 2} y={H - 4} textAnchor="middle" fontSize="9.5" fill="var(--color-ink-muted)">
          calibrated P(over) — logged live
        </text>
      </svg>
      {hover != null && bins[hover] && (
        <div
          className="figure pointer-events-none absolute z-10 -translate-x-1/2 rounded-md border border-line bg-surface-2 px-2 py-1 text-[10px] text-ink-secondary shadow-[0_6px_18px_rgba(0,0,0,0.4)]"
          style={{
            left: `${((xFor(bins[hover].pred_mean) / W) * 100).toFixed(1)}%`,
            top: 0,
          }}
          role="status"
        >
          pred {(bins[hover].pred_mean * 100).toFixed(0)}% → actual{" "}
          {(bins[hover].actual_rate * 100).toFixed(0)}% (n={bins[hover].n})
        </div>
      )}
      <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-[10px] text-ink-secondary">
        <span className="flex items-center gap-1.5">
          <span className="h-[2px] w-4 bg-accent" /> live log
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-0 w-4 border-t border-dashed border-white/30" /> perfect
        </span>
        <span>dot size = observations in bucket</span>
      </div>
    </div>
  );
}

/** Calibration error by slate date. Plots excess-over-floor, NOT raw
 *  Brier: raw Brier moves with the night's line mix, so a slate of
 *  coin-flips would look like drift when nothing changed. Zero is the
 *  reference — the model's confidence was exactly earned. */
function LiveBrierTrend({ byDate, band }: { byDate: LiveModelDate[]; band: number | null }) {
  const pts = byDate.filter((d) => d.excess != null);
  const W = 380;
  const H = 170;
  const padL = 48;
  const padR = 12;
  const padT = 14;
  const padB = 30;
  const innerW = W - padL - padR;
  const innerH = H - padT - padB;
  const span =
    Math.max(0.05, band ?? 0, ...pts.map((d) => Math.abs(d.excess as number))) * 1.25;
  const xFor = (i: number) =>
    padL + (pts.length === 1 ? innerW / 2 : (i / (pts.length - 1)) * innerW);
  const yFor = (v: number) => padT + (1 - (v + span) / (2 * span)) * innerH;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="w-full"
      role="img"
      aria-label="Calibration error by slate date. Zero means the model's confidence was exactly earned; above zero means overconfident."
    >
      {band != null && (
        <rect
          x={padL}
          y={yFor(band)}
          width={innerW}
          height={Math.max(0, yFor(-band) - yFor(band))}
          fill="rgba(255,255,255,0.035)"
        />
      )}
      {[span, 0, -span].map((t) => (
        <g key={t}>
          <line
            x1={padL}
            x2={W - padR}
            y1={yFor(t)}
            y2={yFor(t)}
            stroke={t === 0 ? "var(--color-over)" : "rgba(255,255,255,0.05)"}
            strokeDasharray={t === 0 ? "4 4" : undefined}
            strokeWidth={t === 0 ? 1.2 : 1}
          />
          <text
            x={padL - 6}
            y={yFor(t) + 3}
            textAnchor="end"
            fontSize="9"
            className="figure"
            fill={t === 0 ? "var(--color-over)" : "var(--color-ink-muted)"}
          >
            {t > 0 ? "+" : ""}
            {t.toFixed(3)}
          </text>
        </g>
      ))}
      {pts.length > 1 && (
        <polyline
          points={pts
            .map((d, i) => `${xFor(i).toFixed(1)},${yFor(d.excess as number).toFixed(1)}`)
            .join(" ")}
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="1.6"
        />
      )}
      {pts.map((d, i) => (
        <g key={d.date}>
          <circle
            cx={xFor(i)}
            cy={yFor(d.excess as number)}
            r={3.2}
            fill={
              band != null && Math.abs(d.excess as number) <= band
                ? "var(--color-ink-muted)"
                : (d.excess as number) < 0
                  ? "var(--color-over)"
                  : "var(--color-under)"
            }
          >
            <title>
              {d.date}: calibration error {(d.excess as number) > 0 ? "+" : ""}
              {(d.excess as number).toFixed(4)} over {d.n_scored} scored
            </title>
          </circle>
          <text
            x={xFor(i)}
            y={H - padB + 14}
            textAnchor="middle"
            fontSize="8.5"
            className="figure"
            fill="var(--color-ink-muted)"
          >
            {d.date.slice(5)}
          </text>
        </g>
      ))}
    </svg>
  );
}

function LiveModelSection({ live }: { live: LiveModel | null }) {
  if (!live) {
    // Deliberately does NOT assert "no log yet". A payload generated
    // before this block shipped also arrives with live_model absent, and
    // telling the operator to run a command they already ran is worse
    // than saying nothing.
    return (
      <div className="rounded-card border border-line bg-surface p-8 text-center text-sm text-ink-muted">
        No live model figures in this payload. Either no slate has been logged yet, or the
        data feed predates this section — it fills in on the next{" "}
        <code className="figure rounded bg-surface-2 px-1.5 py-0.5 text-xs">
          tools/dashboard_data.py
        </code>{" "}
        run after a graded slate.
      </div>
    );
  }

  const baseline = live.backtest_brier_baseline;
  // The verdict rides on excess-over-floor, never on raw Brier — see
  // _line_matched_baseline in tools/dashboard_data.py for why comparing
  // raw Brier across samples is a rigged test.
  const delta = live.excess_delta;
  const tone =
    live.verdict === "better than backtest"
      ? "positive"
      : live.verdict === "worse than backtest"
        ? "negative"
        : "neutral";
  const wl = live.workload;
  const signed = (v: number | null | undefined, dp = 4) =>
    v == null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(dp)}`;
  const tie = (v: number) => live.verdict_band != null && Math.abs(v) <= live.verdict_band;

  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="text-sm font-semibold uppercase tracking-[0.14em] text-ink">
          Live model
        </h2>
        <span className="text-[11px] text-ink-muted">
          every evaluated pitcher, not just the bets · {live.n_used} of {live.n_total} logged
          rows in use
        </span>
      </div>

      {live.sample_warning && (
        <div className="rounded-card border border-accent/40 bg-accent-dim px-4 py-3">
          <div className="figure text-[10px] font-semibold uppercase tracking-[0.14em] text-accent">
            Warning — diagnostic only, not validation
          </div>
          <p className="mt-1 text-xs leading-relaxed text-ink-secondary">
            {live.sample_warning}
          </p>
        </div>
      )}

      <div className="stagger grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <StatTile
          label="Calibration error"
          value={signed(live.excess)}
          tone={tone as "positive" | "negative" | "neutral"}
          sub={
            live.excess == null
              ? "no scored rows yet"
              : `${live.verdict} · backtest ${signed(live.baseline_excess)}`
          }
        />
        <StatTile
          label="Noise band"
          value={live.verdict_band != null ? `±${live.verdict_band.toFixed(4)}` : "—"}
          sub={`2 SE at n=${live.n_scored} — inside this is a tie`}
        />
        <StatTile
          label="Scored observations"
          value={String(live.n_scored)}
          tone={live.n_live_scored >= 20 ? "neutral" : "accent"}
          sub={`${live.n_live} live · ${live.n_reconstructed} reconstructed · ${live.n_used - live.n_scored} unscored`}
        />
        <StatTile
          label="Mean |workload err|"
          value={wl ? `${wl.mean_abs_bf_error.toFixed(2)} BF` : "—"}
          sub={wl ? `${pctStr(wl.pct_within_3, 0)} within ±3 BF` : "no workload rows"}
        />
      </div>

      <div className="rounded-card border border-line bg-surface px-4 py-3 text-xs leading-relaxed text-ink-secondary">
        <p>
          The backtest above says the model{" "}
          <span className="font-semibold text-ink">was</span> good out-of-sample. This block
          asks the harder question: is it still behaving that way? Every pitcher the model
          priced gets logged against what actually happened, unfiltered by the bet
          thresholds — so it measures the model, not the bet selection.
        </p>
        <p className="mt-2">
          <span className="font-semibold text-ink">Why not just compare Brier scores?</span>{" "}
          Because the book hangs its line where the game is closest to a coin flip, so a
          live board is intrinsically harder to score well on than the backtest&rsquo;s fixed
          line grid. Raw Brier would read{" "}
          <span className="font-semibold text-ink">worse than backtest</span> forever, even
          for a flawless model. So the comparison here is{" "}
          <span className="font-semibold text-ink">calibration error</span> — how far the
          Brier sits above the best score achievable at the confidence the model actually
          claimed ({live.brier?.toFixed(4) ?? "—"} scored vs{" "}
          {live.brier_floor?.toFixed(4) ?? "—"} floor). Zero means the confidence was
          exactly earned. The backtest posted {signed(live.baseline_excess)} on this same
          line mix.
        </p>
        <p className="mt-2">
          <span className="font-semibold text-ink">
            Live is currently {live.excess == null ? "unscored" : live.verdict}
          </span>{" "}
          ({signed(delta)} vs backtest, band ±
          {live.verdict_band?.toFixed(4) ?? "—"}). At this sample size the band is wide
          enough that only gross breakage would trip it — the leash numbers and the
          calibration curve below move first and are the faster read.
        </p>
      </div>

      <div className="grid gap-2.5 lg:grid-cols-2">
        <div className="rounded-card border border-line bg-surface p-4">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
            Live calibration — predicted vs actual
          </div>
          {live.calibration_bins.length ? (
            <>
              <LiveCalibrationChart bins={live.calibration_bins} />
              {live.mean_predicted_over != null && live.actual_over_rate != null && (
                <div className="figure mt-2 text-[10.5px] text-ink-secondary">
                  predicted {pctStr(live.mean_predicted_over)} over · actual{" "}
                  {pctStr(live.actual_over_rate)} over · bias{" "}
                  <span
                    className={cn(
                      live.bias == null || live.bias === 0
                        ? "text-ink"
                        : live.bias > 0
                          ? "text-under"
                          : "text-over",
                    )}
                  >
                    {live.bias != null && live.bias > 0 ? "+" : ""}
                    {pctStr(live.bias)}
                  </span>{" "}
                  (
                  {live.bias == null || live.bias === 0
                    ? "no directional bias"
                    : live.bias > 0
                      ? "too many overs predicted"
                      : "too few overs predicted"}
                  )
                </div>
              )}
            </>
          ) : (
            <div className="py-10 text-center text-xs text-ink-muted">
              No scored rows yet — calibration appears once outcomes are logged.
            </div>
          )}
        </div>

        <div className="rounded-card border border-line bg-surface p-4">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
            The leash — expected vs actual batters faced
          </div>
          {wl ? (
            <>
              <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
                <div className="flex justify-between border-b border-line/50 pb-1.5">
                  <span className="text-ink-muted">mean error</span>
                  <span className="figure text-ink">
                    {wl.mean_bf_error > 0 ? "+" : ""}
                    {wl.mean_bf_error.toFixed(2)} BF
                  </span>
                </div>
                <div className="flex justify-between border-b border-line/50 pb-1.5">
                  <span className="text-ink-muted">mean |error|</span>
                  <span className="figure text-ink">{wl.mean_abs_bf_error.toFixed(2)} BF</span>
                </div>
                <div className="flex justify-between border-b border-line/50 pb-1.5">
                  <span className="text-ink-muted">within ±3 BF</span>
                  <span className="figure text-ink">{pctStr(wl.pct_within_3, 0)}</span>
                </div>
                <div className="flex justify-between border-b border-line/50 pb-1.5">
                  <span className="text-ink-muted">badly short (≤ −6)</span>
                  <span
                    className={cn(
                      "figure",
                      wl.pct_badly_short > 0.05 ? "text-under" : "text-ink",
                    )}
                  >
                    {pctStr(wl.pct_badly_short, 0)}
                  </span>
                </div>
              </div>
              {live.strikeouts && (
                <div className="figure mt-2 text-[10.5px] text-ink-secondary">
                  strikeouts: mean error {live.strikeouts.mean_k_error > 0 ? "+" : ""}
                  {live.strikeouts.mean_k_error.toFixed(2)} K · mean |error|{" "}
                  {live.strikeouts.mean_abs_k_error.toFixed(2)} K (n={live.strikeouts.n})
                </div>
              )}
              {wl.worst_misses.length > 0 && (
                <div className="mt-3">
                  <div className="mb-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
                    Shortest leashes
                  </div>
                  <ul className="space-y-1 text-xs">
                    {wl.worst_misses.map((m) => (
                      <li
                        key={`${m.date}-${m.pitcher_name}`}
                        className="flex items-baseline justify-between gap-2"
                      >
                        <span className="truncate text-ink-secondary">{m.pitcher_name}</span>
                        <span className="figure whitespace-nowrap text-[11px] text-ink-muted">
                          exp {m.expected_bf.toFixed(1)} → faced {m.actual_bf.toFixed(0)}{" "}
                          <span className={cn(m.bf_error <= -6 ? "text-under" : "text-ink-secondary")}>
                            ({m.bf_error > 0 ? "+" : ""}
                            {m.bf_error.toFixed(1)})
                          </span>
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          ) : (
            <div className="py-10 text-center text-xs text-ink-muted">
              No workload rows logged yet.
            </div>
          )}
        </div>
      </div>

      {live.by_date.length > 0 && (
        <div className="rounded-card border border-line bg-surface p-4">
          <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
            By slate date — drift watch
          </div>
          {live.by_date.filter((d) => d.excess != null).length > 1 && (
            <LiveBrierTrend byDate={live.by_date} band={live.verdict_band} />
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[360px] text-left text-xs">
              <thead>
                <tr className="border-b border-line text-[10px] uppercase tracking-wider text-ink-muted">
                  <th className="py-1.5 pr-3 font-medium">Date</th>
                  <th className="py-1.5 pr-3 text-right font-medium">n</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Brier</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Calib. err vs backtest</th>
                  <th className="py-1.5 pr-3 text-right font-medium">Mean |BF err|</th>
                  <th className="py-1.5 text-right font-medium">Source</th>
                </tr>
              </thead>
              <tbody>
                {live.by_date.map((d) => {
                  const dd =
                    d.excess != null && d.baseline_excess != null
                      ? d.excess - d.baseline_excess
                      : null;
                  return (
                    <tr key={d.date} className="border-b border-line/50 last:border-0">
                      <td className="figure py-1.5 pr-3">{d.date}</td>
                      <td className="figure py-1.5 pr-3 text-right text-ink-secondary">{d.n}</td>
                      <td className="figure py-1.5 pr-3 text-right">
                        {d.brier != null ? d.brier.toFixed(4) : "—"}
                      </td>
                      {/* Same noise band as the headline verdict, or a
                          single date could read "worse" while the block
                          above it reads "in line" off the same number. */}
                      <td
                        className={cn(
                          "figure py-1.5 pr-3 text-right",
                          dd == null || tie(dd)
                            ? "text-ink-muted"
                            : dd < 0
                              ? "text-over"
                              : "text-under",
                        )}
                      >
                        {dd == null
                          ? "—"
                          : `${dd > 0 ? "+" : ""}${dd.toFixed(4)} ${
                              tie(dd) ? "in line" : dd < 0 ? "better" : "worse"
                            }`}
                      </td>
                      <td className="figure py-1.5 pr-3 text-right text-ink-secondary">
                        {d.mean_abs_bf_error != null ? `${d.mean_abs_bf_error.toFixed(2)} BF` : "—"}
                      </td>
                      <td className="py-1.5 text-right">
                        <span
                          className={cn(
                            "figure text-[10px] uppercase tracking-wider",
                            d.reconstructed ? "text-accent" : "text-ink-muted",
                          )}
                        >
                          {!d.reconstructed
                            ? "live"
                            : d.n_reconstructed >= d.n
                              ? "reconstructed"
                              : `${d.n_reconstructed} of ${d.n} reconstructed`}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

export default function ModelPage() {
  const { data, error } = useDashboard();

  if (error || !data) {
    return (
      <div className="py-24 text-center text-sm text-ink-muted">
        {error ? "Could not load data.json" : "Loading…"}
      </div>
    );
  }

  const bt = data.model.backtest;
  const gauntlet = data.model.gauntlet;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-xs text-ink-muted">
        <Link href="/" className="transition-colors hover:text-ink">
          ◂ Slate
        </Link>
        <span>/</span>
        <span className="text-ink-secondary">Model</span>
      </div>

      {bt ? (
        <>
          <div className="stagger grid grid-cols-2 gap-2.5 sm:grid-cols-4">
            <StatTile
              label="Model Brier"
              value={bt.model_brier.toFixed(4)}
              tone="positive"
              sub="lower is better"
            />
            <StatTile
              label="Naive Brier"
              value={bt.naive_brier.toFixed(4)}
              sub="season K% baseline"
            />
            <StatTile
              label="Edge vs naive"
              value={`+${bt.improvement_pct.toFixed(1)}%`}
              tone="positive"
              sub={`${bt.n_starts} out-of-sample starts`}
            />
            <StatTile
              label="Honest split"
              value="as-of"
              tone="accent"
              sub={`train ${bt.train_desc}`}
            />
          </div>

          <p className="rounded-card border border-line bg-surface px-4 py-3 text-xs leading-relaxed text-ink-secondary">
            Every number on this page is <span className="font-semibold text-ink">out-of-sample</span>:
            the model was fit only on {bt.train_desc}, then scored on{" "}
            {bt.test_window} with features computed strictly as-of (no game ever
            sees its own data). The earlier leaky backtest numbers were retired —
            see CHANGELOG Phase 7.
          </p>

          <div className="grid gap-2.5 lg:grid-cols-2">
            <div className="rounded-card border border-line bg-surface p-4">
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
                Calibration — predicted vs actual
              </div>
              <CalibrationChart bins={bt.calibration_bins} />
            </div>
            <div className="rounded-card border border-line bg-surface p-4">
              <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
                Brier score by line
              </div>
              <BrierBars perLine={bt.per_line} />
            </div>
          </div>
        </>
      ) : (
        <div className="rounded-card border border-line bg-surface p-8 text-center text-sm text-ink-muted">
          No backtest predictions found — run{" "}
          <code className="figure rounded bg-surface-2 px-1.5 py-0.5 text-xs">
            python backtest.py
          </code>
        </div>
      )}

      <LiveModelSection live={data.live_model ?? null} />

      {gauntlet && (
        <div className="rounded-card border border-line bg-surface p-4">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
              Feature gauntlet — 5 gates, noise floor {gauntlet.noise_floor_pct}%
            </span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[480px] text-left text-xs">
              <thead>
                <tr className="border-b border-line text-[10px] uppercase tracking-wider text-ink-muted">
                  <th className="py-1.5 pr-3 font-medium">Feature</th>
                  {[1, 2, 3, 4, 5].map((g) => (
                    <th key={g} className="py-1.5 pr-2 text-center font-medium">
                      G{g}
                    </th>
                  ))}
                  <th className="py-1.5 pr-3 text-right font-medium">Min Δ</th>
                  <th className="py-1.5 text-right font-medium">Verdict</th>
                </tr>
              </thead>
              <tbody>
                {gauntlet.features.map((f) => (
                  <tr key={f.feature} className="border-b border-line/50 last:border-0">
                    <td className="figure py-1.5 pr-3">{f.feature}</td>
                    {[1, 2, 3, 4, 5].map((g) => {
                      const state = f.gates[`gate${g}`];
                      return (
                        <td key={g} className="py-1.5 pr-2 text-center">
                          <span
                            className={cn(
                              "inline-block h-2 w-2 rounded-full",
                              state === true && "bg-over",
                              state === false && "bg-under/60",
                              state == null && "bg-white/15",
                            )}
                          />
                        </td>
                      );
                    })}
                    <td className="figure py-1.5 pr-3 text-right text-ink-secondary">
                      {f.min_improvement_pct != null
                        ? `${f.min_improvement_pct > 0 ? "+" : ""}${f.min_improvement_pct.toFixed(2)}%`
                        : "—"}
                    </td>
                    <td className="py-1.5 text-right">
                      <span
                        className={cn(
                          "figure text-[10px] font-semibold uppercase tracking-wider",
                          f.promoted ? "text-over" : "text-ink-muted",
                        )}
                      >
                        {f.promoted ? "promoted" : "rejected"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
