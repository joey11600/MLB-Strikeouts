"use client";

import Link from "next/link";
import { useDashboard } from "@/lib/data-context";
import { PnlChart } from "@/components/pnl-chart";
import { StatTile } from "@/components/stat-tiles";
import { cn, oddsStr, pctStr, pnlStr } from "@/lib/utils";
import type { SplitBucket } from "@/lib/types";

function SplitTable({
  title,
  buckets,
}: {
  title: string;
  buckets: Record<string, SplitBucket>;
}) {
  const keys = Object.keys(buckets);
  if (keys.length === 0) return null;
  return (
    <div className="rounded-card border border-line bg-surface p-4">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
        {title}
      </div>
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-line text-[10px] uppercase tracking-wider text-ink-muted">
            <th className="py-1.5 font-medium">Split</th>
            <th className="py-1.5 text-right font-medium">W–L</th>
            <th className="py-1.5 text-right font-medium">Hit</th>
            <th className="py-1.5 text-right font-medium">P&L</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const b = buckets[k];
            const v = b.pnl.value;
            return (
              <tr key={k} className="border-b border-line/50 last:border-0">
                <td className="py-1.5 font-medium">{k}</td>
                <td className="figure py-1.5 text-right">
                  {b.wins}–{b.losses}
                </td>
                <td className="figure py-1.5 text-right">{pctStr(b.hit_rate)}</td>
                <td
                  className={cn(
                    "figure py-1.5 text-right",
                    v > 0.005 && "text-over",
                    v < -0.005 && "text-under",
                  )}
                >
                  {pnlStr(v)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

export default function PerformancePage() {
  const { data, error } = useDashboard();

  if (error || !data) {
    return (
      <div className="py-24 text-center text-sm text-ink-muted">
        {error ? "Could not load data.json" : "Loading…"}
      </div>
    );
  }

  const perf = data.performance;
  const graded = data.record.wins + data.record.losses;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 text-xs text-ink-muted">
        <Link href="/" className="transition-colors hover:text-ink">
          ◂ Slate
        </Link>
        <span>/</span>
        <span className="text-ink-secondary">Performance</span>
      </div>

      <div className="stagger grid grid-cols-2 gap-2.5 sm:grid-cols-4">
        <StatTile
          label="Record"
          value={`${data.record.wins}–${data.record.losses}`}
          sub={graded ? `${pctStr(data.record.hit_rate)} hit rate` : "no graded bets"}
        />
        <StatTile
          label="Total P&L"
          value={pnlStr(data.pnl.total.value)}
          tone={data.pnl.total.value > 0 ? "positive" : data.pnl.total.value < 0 ? "negative" : "neutral"}
          sub={`${data.pnl.total_risked.value.toFixed(1)}u risked`}
        />
        <StatTile
          label="ROI"
          value={graded ? pctStr(data.pnl.roi) : "—"}
          tone={data.pnl.roi > 0 ? "positive" : data.pnl.roi < 0 ? "negative" : "neutral"}
        />
        <StatTile
          label="Avg CLV"
          value={perf.clv.avg_clv_pct != null ? pctStr(perf.clv.avg_clv_pct) : "—"}
          tone={
            perf.clv.avg_clv_pct == null
              ? "neutral"
              : perf.clv.avg_clv_pct > 0
                ? "positive"
                : "negative"
          }
          sub={
            perf.clv.n > 0
              ? `${perf.clv.n} picks measured`
              : "capture starts next slate"
          }
        />
      </div>

      <PnlChart daily={perf.daily} />

      <div className="grid gap-2.5 sm:grid-cols-3">
        <SplitTable title="By side" buckets={perf.splits.by_side} />
        <SplitTable title="By strength" buckets={perf.splits.by_strength} />
        <SplitTable title="Primary vs ladder" buckets={perf.splits.by_type} />
      </div>

      <div className="rounded-card border border-line bg-surface p-4">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
            Every bet, most recent first
          </span>
          <span className="figure text-[11px] text-ink-secondary">
            {perf.ledger.length} rows
          </span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[560px] text-left text-xs">
            <thead>
              <tr className="border-b border-line text-[10px] uppercase tracking-wider text-ink-muted">
                <th className="py-1.5 pr-3 font-medium">Date</th>
                <th className="py-1.5 pr-3 font-medium">Pitcher</th>
                <th className="py-1.5 pr-3 font-medium">Bet</th>
                <th className="py-1.5 pr-3 font-medium">Odds</th>
                <th className="py-1.5 pr-3 text-right font-medium">Stake</th>
                <th className="py-1.5 pr-3 font-medium">Result</th>
                <th className="py-1.5 pr-3 text-right font-medium">CLV</th>
                <th className="py-1.5 text-right font-medium">P&L</th>
              </tr>
            </thead>
            <tbody>
              {perf.ledger.map((row, i) => {
                const v = row.profit_loss_units.value;
                return (
                  <tr key={i} className="border-b border-line/50 last:border-0">
                    <td className="figure py-1.5 pr-3 text-ink-muted">
                      <Link
                        href={`/?date=${row.date}`}
                        className="transition-colors hover:text-ink"
                      >
                        {row.date}
                      </Link>
                    </td>
                    <td className="py-1.5 pr-3 font-medium">
                      {row.pitcher_name}
                      {row.is_ladder && (
                        <span className="figure ml-1.5 text-[9px] uppercase tracking-wider text-accent">
                          ladder
                        </span>
                      )}
                    </td>
                    <td className="figure py-1.5 pr-3">
                      {row.pick_side} {row.line}
                    </td>
                    <td className="figure py-1.5 pr-3">{oddsStr(row.odds)}</td>
                    <td className="figure py-1.5 pr-3 text-right">
                      {row.units_risked.toFixed(2)}u
                    </td>
                    <td className="py-1.5 pr-3">
                      {row.graded_result ? (
                        <span
                          className={cn(
                            "figure text-[11px] font-medium",
                            row.graded_result === "WIN" && "text-over",
                            row.graded_result === "LOSS" && "text-under",
                          )}
                        >
                          {row.graded_result}
                          {row.actual_strikeouts != null &&
                            ` · ${row.actual_strikeouts}K`}
                        </span>
                      ) : (
                        <span className="text-ink-muted">pending</span>
                      )}
                    </td>
                    <td className="figure py-1.5 pr-3 text-right text-ink-secondary">
                      {row.clv_pct != null ? pctStr(row.clv_pct) : "—"}
                    </td>
                    <td
                      className={cn(
                        "figure py-1.5 text-right font-medium",
                        v > 0.005 && "text-over",
                        v < -0.005 && "text-under",
                      )}
                    >
                      {pnlStr(v)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
