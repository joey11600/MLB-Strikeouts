"use client";

import { cn, pctStr, pnlStr } from "@/lib/utils";

export function StatTile({
  label,
  value,
  tone = "neutral",
  sub,
}: {
  label: string;
  value: string;
  tone?: "positive" | "negative" | "neutral" | "accent";
  sub?: string;
}) {
  return (
    <div className="rounded-card border border-line bg-surface px-4 py-3.5 transition-colors hover:border-line-strong">
      <div
        className={cn(
          "figure text-xl font-semibold leading-tight",
          tone === "positive" && "text-over",
          tone === "negative" && "text-under",
          tone === "accent" && "text-accent",
        )}
      >
        {value}
      </div>
      <div className="mt-1 text-[10px] font-medium uppercase tracking-[0.12em] text-ink-muted">
        {label}
      </div>
      {sub && <div className="figure mt-0.5 text-[10.5px] text-ink-secondary">{sub}</div>}
    </div>
  );
}

export function HeroStats({
  record,
  pnl,
  roi,
  clv,
}: {
  record: { wins: number; losses: number; hit_rate: number };
  pnl: number;
  roi: number;
  clv: { n: number; avg_clv_pct: number | null };
}) {
  const graded = record.wins + record.losses;
  return (
    <div className="stagger grid grid-cols-2 gap-2.5 sm:grid-cols-4">
      <StatTile
        label="Record"
        value={`${record.wins}–${record.losses}`}
        sub={graded ? pctStr(record.hit_rate) + " hit rate" : undefined}
      />
      <StatTile
        label="P&L"
        value={pnlStr(pnl)}
        tone={pnl > 0.005 ? "positive" : pnl < -0.005 ? "negative" : "neutral"}
      />
      <StatTile
        label="ROI"
        value={graded ? pctStr(roi) : "—"}
        tone={roi > 0 ? "positive" : roi < 0 ? "negative" : "neutral"}
      />
      <StatTile
        label="Avg CLV"
        value={clv.avg_clv_pct != null ? pctStr(clv.avg_clv_pct) : "—"}
        tone={
          clv.avg_clv_pct == null
            ? "neutral"
            : clv.avg_clv_pct > 0
              ? "positive"
              : "negative"
        }
        sub={clv.n > 0 ? `${clv.n} picks vs close` : "starts next slate"}
      />
    </div>
  );
}
