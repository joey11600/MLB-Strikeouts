"use client";

// The machine's brain: honest backtest analytics + gauntlet results.
// Hand-rolled SVG charts, NRFI geometry idiom.
import Link from "next/link";
import { useState } from "react";
import { useDashboard } from "@/lib/data-context";
import { StatTile } from "@/components/stat-tiles";
import { cn } from "@/lib/utils";
import type { CalibrationBin, PerLineBrier } from "@/lib/types";

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
              sub={`train ≤ ${bt.train_cutoff}`}
            />
          </div>

          <p className="rounded-card border border-line bg-surface px-4 py-3 text-xs leading-relaxed text-ink-secondary">
            Every number on this page is <span className="font-semibold text-ink">out-of-sample</span>:
            the model was fit only on games before {bt.train_cutoff}, then scored on{" "}
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
