"use client";

// The outs market's OWN page (Phase 10; operator directive 2026-08-24:
// separate product, separate pages, separate numbers). Reads its own
// payload — /outs.json from the worker, bundled copy as fallback —
// and renders a DIAGNOSTIC board: model vs market, disagreements
// first, settled results grading in overnight. Nothing here is a
// pick, an edge, or a stake by construction: the payload carries none.
import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

const LIVE_OUTS_URL =
  process.env.NEXT_PUBLIC_OUTS_URL ??
  "https://worker-production-036c.up.railway.app/outs.json";

type OutsRow = {
  pitcher_id: number;
  pitcher_name: string;
  pitcher_team: string;
  opponent_team: string;
  line: number;
  over_odds: string;
  under_odds: string;
  expected_outs: number;
  p_over_cal: number;
  fair_over: number | null;
  actual_outs: number | null;
  odds_source: string;
};

type OutsPayload = {
  generated_at: string;
  latest_date: string | null;
  slates: Record<string, { generated_at: string; board: OutsRow[] }>;
  scorecard: Record<string, string> | null;
  calibration: string;
};

function useOutsData() {
  const [data, setData] = useState<OutsPayload | null>(null);
  const [source, setSource] = useState<"live" | "bundled" | "none">("none");
  useEffect(() => {
    let dead = false;
    async function load() {
      try {
        const live = await fetch(LIVE_OUTS_URL, { cache: "no-store" });
        if (live.ok) {
          const j = (await live.json()) as OutsPayload;
          if (!dead) {
            setData(j);
            setSource("live");
          }
          return;
        }
      } catch {
        /* fall through to the bundled copy */
      }
      try {
        const stat = await fetch("/outs.json", { cache: "no-store" });
        if (stat.ok) {
          const j = (await stat.json()) as OutsPayload;
          if (!dead) {
            setData(j);
            setSource("bundled");
          }
        }
      } catch {
        /* page renders its static explainer */
      }
    }
    load();
    // A-039's lesson: an open tab must keep moving on its own.
    const t = setInterval(load, 60_000);
    return () => {
      dead = true;
      clearInterval(t);
    };
  }, []);
  return { data, source };
}

function iso(n: number | null | undefined, digits = 1): string {
  return n === null || n === undefined ? "—" : n.toFixed(digits);
}

function pct(p: number | null | undefined): string {
  return p === null || p === undefined ? "—" : `${(p * 100).toFixed(1)}%`;
}

export default function OutsPage() {
  const { data, source } = useOutsData();
  const [date, setDate] = useState<string | null>(null);

  const dates = data ? Object.keys(data.slates).sort().reverse() : [];
  const active = date && dates.includes(date) ? date : dates[0] ?? null;
  const board = active ? data!.slates[active].board : [];
  const sc = data?.scorecard ?? null;

  return (
    <div className="space-y-5">
      <div>
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-2xl font-bold tracking-tight">Outs Recorded</h1>
          <span className="rounded-md bg-accent/15 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-accent">
            separate market
          </span>
          <span className="rounded-md bg-surface-2 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
            diagnostic only — no picks
          </span>
        </div>
        <p className="mt-1 max-w-2xl text-sm text-ink-secondary">
          How many outs the starter records before the hook — a separate
          product from strikeouts with its own model, its own ledger tag,
          and its own numbers. The board below shows where the model and
          the book disagree, biggest gaps first, and how those
          disagreements actually settled. It bets nothing until its
          calibration clears the same gates the strikeouts model is held
          to.
        </p>
      </div>

      {sc && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div className="rounded-xl border border-line bg-surface p-3.5">
            <div className="text-[11px] uppercase tracking-wider text-ink-muted">
              vs closing line
            </div>
            <div className="figure mt-1 text-lg font-semibold">
              z = {Number(sc.z_raw_vs_market) > 0 ? "+" : ""}
              {Number(sc.z_raw_vs_market).toFixed(2)}
            </div>
            <div className="text-[11px] text-ink-muted">
              {Math.abs(Number(sc.z_raw_vs_market)) < 1.96
                ? "indistinguishable from the book"
                : Number(sc.z_raw_vs_market) > 0
                  ? "behind the book"
                  : "ahead of the book"}
            </div>
          </div>
          <div className="rounded-xl border border-line bg-surface p-3.5">
            <div className="text-[11px] uppercase tracking-wider text-ink-muted">
              scored starts
            </div>
            <div className="figure mt-1 text-lg font-semibold">
              {sc.n_starts}
            </div>
            <div className="text-[11px] text-ink-muted">
              over {sc.n_dates} dates, all out-of-sample
            </div>
          </div>
          <div className="rounded-xl border border-line bg-surface p-3.5">
            <div className="text-[11px] uppercase tracking-wider text-ink-muted">
              model Brier
            </div>
            <div className="figure mt-1 text-lg font-semibold">
              {sc.brier_raw}
            </div>
            <div className="text-[11px] text-ink-muted">
              market {sc.brier_market} (lower is better)
            </div>
          </div>
          <div className="rounded-xl border border-line bg-surface p-3.5">
            <div className="text-[11px] uppercase tracking-wider text-ink-muted">
              status
            </div>
            <div className="mt-1 text-sm font-semibold text-under">
              betting blocked
            </div>
            <div className="text-[11px] text-ink-muted">
              calibration gate not passed
            </div>
          </div>
        </div>
      )}

      {dates.length > 1 && (
        <div className="flex flex-wrap gap-1.5">
          {dates.map((d) => (
            <button
              key={d}
              onClick={() => setDate(d)}
              className={cn(
                "rounded-md border px-2.5 py-1 text-xs transition-colors",
                d === active
                  ? "border-accent/50 bg-accent/10 text-accent"
                  : "border-line bg-surface text-ink-secondary hover:text-ink",
              )}
            >
              {d.slice(5)}
            </button>
          ))}
        </div>
      )}

      {board.length > 0 ? (
        <div className="overflow-x-auto rounded-xl border border-line bg-surface">
          <table className="w-full min-w-[780px] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-ink-muted">
                <th className="px-3 py-2.5">Pitcher</th>
                <th className="px-3 py-2.5">Line</th>
                <th className="figure px-3 py-2.5 text-right">O / U</th>
                <th className="figure px-3 py-2.5 text-right">E[outs]</th>
                <th className="figure px-3 py-2.5 text-right">Model P(over)</th>
                <th className="figure px-3 py-2.5 text-right">Market fair</th>
                <th className="figure px-3 py-2.5 text-right">Gap</th>
                <th className="figure px-3 py-2.5 text-right">Actual</th>
                <th className="px-3 py-2.5 text-right">Model lean</th>
              </tr>
            </thead>
            <tbody>
              {board.map((r) => {
                const gap =
                  r.fair_over === null ? null : r.p_over_cal - r.fair_over;
                const settled = r.actual_outs !== null;
                const wentOver = settled && r.actual_outs! > r.line;
                // Whole-number alternate lines can land exactly on the
                // line — that settles neither side (the push rule).
                const pushed = settled && r.actual_outs! === r.line;
                const leanOver = gap === null || gap === 0 ? null : gap > 0;
                const leanRight =
                  settled && !pushed && leanOver !== null
                    ? wentOver === leanOver
                    : null;
                return (
                  <tr
                    key={r.pitcher_id}
                    className="border-b border-line/60 last:border-0"
                  >
                    <td className="px-3 py-2.5">
                      <div className="font-medium">{r.pitcher_name}</div>
                      <div className="text-[11px] text-ink-muted">
                        {r.pitcher_team} vs {r.opponent_team}
                      </div>
                    </td>
                    <td className="figure px-3 py-2.5">{r.line}</td>
                    <td className="figure px-3 py-2.5 text-right text-ink-secondary">
                      {r.over_odds} / {r.under_odds}
                    </td>
                    <td className="figure px-3 py-2.5 text-right">
                      {iso(r.expected_outs)}
                    </td>
                    <td className="figure px-3 py-2.5 text-right">
                      {pct(r.p_over_cal)}
                    </td>
                    <td className="figure px-3 py-2.5 text-right">
                      {pct(r.fair_over)}
                    </td>
                    <td
                      className={cn(
                        "figure px-3 py-2.5 text-right",
                        gap !== null && Math.abs(gap) >= 0.08
                          ? "text-accent"
                          : "text-ink-secondary",
                      )}
                    >
                      {gap === null
                        ? "—"
                        : `${gap > 0 ? "+" : ""}${(gap * 100).toFixed(1)}pp`}
                    </td>
                    <td className="figure px-3 py-2.5 text-right">
                      {settled ? (
                        <span
                          className={
                            pushed
                              ? "text-ink-muted"
                              : wentOver
                                ? "text-over"
                                : "text-under"
                          }
                        >
                          {r.actual_outs}{" "}
                          {pushed ? "PUSH" : wentOver ? "OVER" : "UNDER"}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {pushed ? (
                        <span className="rounded-md bg-surface-2 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
                          push
                        </span>
                      ) : leanRight !== null ? (
                        <span
                          className={cn(
                            "rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider",
                            leanRight
                              ? "bg-over/15 text-over"
                              : "bg-under/15 text-under",
                          )}
                        >
                          {leanRight ? "✓ right" : "✗ wrong"}
                        </span>
                      ) : (
                        <span className="text-ink-muted">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="rounded-xl border border-line bg-surface p-5 text-sm text-ink-secondary">
          {data
            ? "No outs board published for this date yet — the book posts it in the morning."
            : "Loading the outs board… if nothing appears, the worker hasn't published an outs payload yet (first publish lands with the next scheduled run)."}
        </div>
      )}

      <p className="text-xs text-ink-muted">
        Model probabilities are served raw — both candidate calibration
        maps were refused on an untouched holdout (a map that doesn&rsquo;t
        calibrate is worse than none). Gaps are a research readout, not
        picks; the one-bet-per-pitcher rule spans both markets when
        betting ever opens. On settled rows, ✓/✗ marks only whether the
        side the model leaned toward matched how the line settled — no
        bet existed behind it, so it is not a win or a loss.{" "}
        {data && (
          <span className="figure">
            payload {source} · {data.generated_at?.slice(0, 16)}Z
          </span>
        )}
      </p>
    </div>
  );
}
