"use client";

// The outs market's OWN page (Phase 10; operator directive 2026-08-24:
// separate product, separate pages, separate numbers). Reads its own
// payload — /outs.json from the worker, bundled copy as fallback —
// and renders the board from the MODEL'S SIDE: its probability vs the
// market's no-vig number for whichever side it leans, the edge between
// them, and the units the capped paper rule would stake (operator
// direction 2026-08-26). All stakes are hypothetical — betting is
// blocked and nothing is placed; the numbers come from the payload,
// which computes them through the real models.edge / models.staking
// path, never a re-derivation in the browser.
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
  // The projection column (2026-09-02). Half-integer lines make
  // `median > line` exactly `P(over) > 50%`, so this can never sit on
  // the other side of the line from the Side column — the mean did, on
  // 31% of rows. Absent on payloads built before it shipped.
  median_outs?: number | null;
  p_over_cal: number;
  fair_over: number | null;
  actual_outs: number | null;
  odds_source: string;
  // Paper columns (2026-08-26): absent on payloads built before the
  // worker shipped them, so every consumer tolerates undefined.
  paper_side?: "OVER" | "UNDER" | null;
  paper_stake_units?: number | null;
  clears_gates?: boolean | null;
  // The blended edge and the bar it had to clear (A-052). Absent on
  // payloads built before the worker shipped them, so the rejection
  // marker still renders without the numbers rather than showing NaN.
  gate_edge?: number | null;
  gate_threshold?: number | null;
  // Role facts (A-054, 2026-09-04): what the starter did in his PREVIOUS
  // appearance — the one thing the model has no feature for. Absent on
  // sidecars priced before the block existed, so every consumer
  // tolerates undefined.
  role?: {
    prev_app_date: string | null;
    prev_app_pitches: number | null;
    prev_app_was_start: boolean | null;
    relief_apps_since_last_start: number | null;
    days_since_prev_start: number | null;
  } | null;
  relief_role?: boolean | null;
  role_skip?: boolean | null;
  gates_role_units?: number | null;
};

type PaperPolicy = {
  bets: number;
  wins: number;
  losses: number;
  pushes: number;
  voids: number;
  staked: number;
  pl: number;
  dates: number;
};

type OutsPayload = {
  generated_at: string;
  latest_date: string | null;
  slates: Record<string, { generated_at: string; board: OutsRow[] }>;
  scorecard: Record<string, string> | null;
  paper_tracks: {
    policies: Record<string, PaperPolicy>;
    since: string;
    basis: string;
  } | null;
  calibration: string;
};

// Display order + wording for the four paper policies. Keys must
// match tools/outs_paper.py POLICIES.
const PAPER_POLICIES: [string, string, string][] = [
  ["gates", "Gates as written", "production entry bar · ¼-Kelly · 2u cap"],
  [
    "gates_role",
    "Gates, relief-role skip",
    "gates as written, minus any starter whose last outing was relief work · shadow",
  ],
  ["gold_capped", "Gold board, capped", "every 8pp+ gap · ¼-Kelly · 2u cap"],
  ["gold_uncapped", "Gold board, uncapped", "every 8pp+ gap · raw ¼-Kelly · no caps"],
];

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
    // The shared layout caps every page at max-w-5xl; this board earns
    // more columns than that, so it alone breaks out where the viewport
    // has the room (negative margins only kick in >= xl).
    <div className="space-y-5 xl:-mx-24 2xl:-mx-40">
      <div>
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-2xl font-bold tracking-tight">Outs Recorded</h1>
          <span className="rounded-md bg-accent/15 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-accent">
            separate market
          </span>
          <span className="rounded-md bg-surface-2 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
            paper stakes — no bets placed
          </span>
        </div>
        <p className="mt-1 max-w-3xl text-sm text-ink-secondary">
          How many outs the starter records before the hook — a separate
          product from strikeouts with its own model, its own ledger tag,
          and its own numbers. Each row reads from the side the model
          leans: its probability against the market&rsquo;s vig-free
          number, the edge between them, and the units the paper staking
          rule would put on it. Biggest edges first. Every stake is
          hypothetical — nothing is bet until calibration clears the
          same gates the strikeouts model is held to.
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

      {data?.paper_tracks && (
        <div className="rounded-xl border border-line bg-surface p-3.5">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-[11px] uppercase tracking-wider text-ink-muted">
              paper tracks
            </span>
            <span className="text-[11px] text-ink-muted">
              four staking rules graded on every settled slate —
              hypothetical, no bets placed
            </span>
          </div>
          <div className="mt-2 overflow-x-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead>
                <tr className="text-left text-[11px] uppercase tracking-wider text-ink-muted">
                  <th className="py-1 pr-3">Policy</th>
                  <th className="figure py-1 pr-3 text-right">Record</th>
                  <th className="figure py-1 pr-3 text-right">Staked</th>
                  <th className="figure py-1 text-right">P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {PAPER_POLICIES.map(([key, label, how]) => {
                  const p = data.paper_tracks!.policies[key];
                  if (!p) return null;
                  return (
                    <tr key={key} className="border-t border-line/60">
                      <td className="py-1.5 pr-3">
                        <div className="font-medium">{label}</div>
                        <div className="text-[11px] text-ink-muted">{how}</div>
                      </td>
                      <td className="figure py-1.5 pr-3 text-right">
                        {p.wins}-{p.losses}
                        {p.pushes > 0 ? `-${p.pushes}P` : ""}
                        <div className="text-[10px] font-normal text-ink-muted">
                          {p.dates} {p.dates === 1 ? "slate" : "slates"}
                        </div>
                      </td>
                      <td className="figure py-1.5 pr-3 text-right text-ink-secondary">
                        {p.staked.toFixed(2)}u
                      </td>
                      <td
                        className={cn(
                          "figure py-1.5 text-right font-semibold",
                          p.pl >= 0 ? "text-over" : "text-under",
                        )}
                      >
                        {p.pl >= 0 ? "+" : ""}
                        {p.pl.toFixed(2)}u
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          <div className="mt-1.5 text-[11px] text-ink-muted">
            since {data.paper_tracks.since} · {data.paper_tracks.basis}
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
          <table className="w-full min-w-[1040px] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-ink-muted">
                <th className="px-3 py-2.5">Pitcher</th>
                <th className="px-3 py-2.5">Line</th>
                <th className="figure px-3 py-2.5 text-right">O / U</th>
                <th
                  className="figure px-3 py-2.5 text-right"
                  title="the outs total the model has him more likely than not to reach — always on the same side of the line as the Side column"
                >
                  Median
                </th>
                <th className="px-3 py-2.5 text-right">Side</th>
                <th className="figure px-3 py-2.5 text-right">Model</th>
                <th className="figure px-3 py-2.5 text-right">Market</th>
                <th className="figure px-3 py-2.5 text-right">Edge</th>
                <th className="figure px-3 py-2.5 text-right">Units</th>
                <th className="figure px-3 py-2.5 text-right">Actual</th>
                <th className="px-3 py-2.5 text-right">Verdict</th>
              </tr>
            </thead>
            <tbody>
              {board.map((r) => {
                const gap =
                  r.fair_over === null ? null : r.p_over_cal - r.fair_over;
                // The board reads from the model's side: probabilities,
                // edge, and stake all face whichever way it leans.
                const side =
                  gap === null || gap === 0
                    ? null
                    : gap > 0
                      ? "OVER"
                      : "UNDER";
                // The Side column is the VALUE side — chosen against the
                // market's fair, not against 50% (models/edge.py). On ~18%
                // of live rows that is the side the model itself thinks
                // LESS likely: model and market agree on direction, the
                // market is just further out, so the price is on the other
                // side. Legitimate, and worth naming, because it stakes
                // against the model's own lean on a small disagreement
                // with a better-calibrated opponent (A-052).
                const leanOverModel = r.p_over_cal > 0.5;
                const pricePlay =
                  side !== null && (side === "OVER") !== leanOverModel;
                const pricePlayWhy = pricePlay
                  ? `model has him ${leanOverModel ? "OVER" : "UNDER"} at ` +
                    `${pct(r.p_over_cal)}` +
                    (r.median_outs != null ? ` (median ${r.median_outs})` : "") +
                    `; the market has him ${leanOverModel ? "over" : "under"} ` +
                    `at ${pct(r.fair_over)}, further out — so the price is on ` +
                    `the ${side}. This side is the one the model itself calls ` +
                    `less likely.`
                  : undefined;
                const modelSide =
                  side === "UNDER" ? 1 - r.p_over_cal : r.p_over_cal;
                const marketSide =
                  r.fair_over === null
                    ? null
                    : side === "UNDER"
                      ? 1 - r.fair_over
                      : r.fair_over;
                const edge = gap === null ? null : Math.abs(gap);
                const stake = r.paper_stake_units ?? null;
                // Why the entry bar refused a staked row. The bar reads a
                // half-trust blend of model and market, which is why this
                // number is smaller than the raw Edge column beside it —
                // spelling that out is the point of the tooltip.
                const gateWhy =
                  r.gate_edge != null && r.gate_threshold != null
                    ? `blended edge ${(r.gate_edge * 100).toFixed(1)}pp is under the ` +
                      `${(r.gate_threshold * 100).toFixed(1)}pp entry bar — the ` +
                      `production rule would not take this bet (the shadow ` +
                      `paper policy stakes it anyway)`
                    : "the production entry bar refused this stake";
                // Role caption (A-054). His previous appearance was a
                // relief outing — the one fact the model has no feature
                // for (exp_o, stop rates and p5_pitches are built over
                // prior STARTS only). The words carry it; hue is accent.
                const reliefSince = r.role?.relief_apps_since_last_start;
                const roleWhy = r.relief_role
                  ? `His previous appearance` +
                    (r.role?.prev_app_date ? ` (${r.role.prev_app_date})` : "") +
                    ` was a relief outing` +
                    (r.role?.prev_app_pitches != null
                      ? ` of ${r.role.prev_app_pitches} pitches`
                      : "") +
                    (reliefSince != null && reliefSince > 0
                      ? `, ${reliefSince === 1 ? "his only" : `one of ${reliefSince}`} relief outing${reliefSince === 1 ? "" : "s"} since his last start`
                      : "") +
                    (r.role?.days_since_prev_start != null
                      ? ` ${r.role.days_since_prev_start} days ago`
                      : "") +
                    `. The model only learns from prior starts and cannot see ` +
                    `this; in 2024–26 a listed starter whose last outing was ` +
                    `relief averaged 9–11 outs against 16 for everyone else. ` +
                    `The relief-role shadow policy skips this row` +
                    (r.role_skip ? `; gates as written stakes it anyway.` : `.`)
                  : undefined;
                const settled = r.actual_outs !== null;
                const wentOver = settled && r.actual_outs! > r.line;
                // Whole-number alternate lines can land exactly on the
                // line — that settles neither side (the push rule).
                const pushed = settled && r.actual_outs! === r.line;
                const leanOver = side === null ? null : side === "OVER";
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
                      {r.relief_role ? (
                        <div
                          className="mt-0.5 text-[10px] uppercase tracking-wider text-accent"
                          title={roleWhy}
                        >
                          last outing relief
                          {r.role?.prev_app_pitches != null
                            ? ` · ${r.role.prev_app_pitches} pitches`
                            : ""}
                          {r.role?.prev_app_date
                            ? ` ${r.role.prev_app_date.slice(5)}`
                            : ""}
                        </div>
                      ) : null}
                    </td>
                    <td className="figure px-3 py-2.5">{r.line}</td>
                    <td className="figure px-3 py-2.5 text-right text-ink-secondary">
                      {r.over_odds} / {r.under_odds}
                    </td>
                    <td
                      className="figure px-3 py-2.5 text-right"
                      title={
                        r.median_outs != null
                          ? `mean ${iso(r.expected_outs)} — the average sits ` +
                            `${r.expected_outs < r.median_outs ? "below" : "at or above"} ` +
                            `the median because blow-up starts pull it down; an ` +
                            `over/under only cares which side of the line he lands on`
                          : "payload predates the median column; showing the mean"
                      }
                    >
                      {r.median_outs != null ? (
                        r.median_outs
                      ) : (
                        <span className="text-ink-muted">{iso(r.expected_outs)}</span>
                      )}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      {side ? (
                        <div title={pricePlayWhy}>
                          <span
                            className={cn(
                              "rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider",
                              side === "OVER"
                                ? "bg-over/15 text-over"
                                : "bg-under/15 text-under",
                            )}
                          >
                            {side}
                          </span>
                          {pricePlay ? (
                            <div className="mt-0.5 text-[10px] uppercase tracking-wider text-accent">
                              price play
                            </div>
                          ) : null}
                        </div>
                      ) : (
                        <span className="text-ink-muted">—</span>
                      )}
                    </td>
                    <td className="figure px-3 py-2.5 text-right">
                      {pct(modelSide)}
                    </td>
                    <td className="figure px-3 py-2.5 text-right">
                      {pct(marketSide)}
                    </td>
                    <td
                      className={cn(
                        "figure px-3 py-2.5 text-right",
                        edge !== null && edge >= 0.08
                          ? "text-accent"
                          : "text-ink-secondary",
                      )}
                    >
                      {edge === null ? "—" : `+${(edge * 100).toFixed(1)}pp`}
                    </td>
                    <td className="figure px-3 py-2.5 text-right">
                      {stake && stake > 0 ? (
                        <div>
                          <span
                            className={cn(
                              "font-semibold",
                              // A rejected stake is struck through as well
                              // as labelled: the shortfall must not rest on
                              // hue alone (palette rule).
                              r.clears_gates
                                ? undefined
                                : "text-ink-muted line-through decoration-under/70",
                            )}
                          >
                            {stake}u
                          </span>
                          {r.clears_gates ? (
                            <div
                              className="text-[10px] uppercase tracking-wider text-accent"
                              title="also clears the production entry bar"
                            >
                              gates
                            </div>
                          ) : (
                            <div
                              className="text-[10px] uppercase tracking-wider text-under"
                              title={gateWhy}
                            >
                              below bar
                            </div>
                          )}
                          {r.role_skip ? (
                            <div
                              className="text-[10px] uppercase tracking-wider text-under"
                              title={roleWhy}
                            >
                              role skip
                            </div>
                          ) : null}
                        </div>
                      ) : r.gates_role_units && r.gates_role_units > 0 ? (
                        // Gates as written left this row out (the daily
                        // cap); the relief-role shadow takes it because
                        // skipping relief-role rows freed the cap.
                        <div
                          title={
                            `the relief-role shadow policy stakes ${r.gates_role_units}u here: ` +
                            `it skipped the relief-role rows, which freed the daily cap ` +
                            `that had cut this row from gates as written`
                          }
                        >
                          <span className="font-semibold text-ink-secondary">
                            {r.gates_role_units}u
                          </span>
                          <div className="text-[10px] uppercase tracking-wider text-accent">
                            role shadow
                          </div>
                        </div>
                      ) : (
                        <span className="text-ink-muted">—</span>
                      )}
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
        Median is the outs total the model has him more likely than not
        to reach, and it always lands on the same side of the line as
        the Side column — hover it for the mean, which does not: blow-up
        starts drag the average down while a spike at exactly five
        innings holds the median up, and an over/under only pays on
        which side of the line he lands, not how far. Side is the VALUE
        side — the model&rsquo;s number against the market&rsquo;s, not
        against 50% — so it can be the side the model itself thinks less
        likely: when model and market agree on direction and the market
        is simply further out, the price is on the other side. Those
        rows carry a &ldquo;price play&rdquo; tag (about one row in
        five); hover it for both numbers. Model and Market are both read
        for the side in the Side column;
        Edge is simply their difference. Units are what the capped paper
        rule would stake on the row — quarter-Kelly sized on a half-trust
        blend of model and market, 2u per-bet cap, 10u daily cap with a
        correlation haircut — which is why a bigger edge does not scale
        the stake linearly, and why a late row on a heavy slate can show
        an edge but no units. A &ldquo;gates&rdquo; tag means the row
        also clears the production entry bar; a struck-through stake
        tagged &ldquo;below bar&rdquo; means it does NOT — the paper
        policy sizes off the raw model-vs-market gap, while the entry
        bar reads a half-trust blend of the two and must beat the
        book&rsquo;s hold plus a margin, so a fat Edge here can still be
        a bet the production rule refuses. Hover the tag for the two
        numbers. A &ldquo;last outing relief&rdquo; caption under a
        pitcher means his previous appearance was a relief outing: the
        model only learns from prior starts and cannot see that, and in
        2024&ndash;26 such starters averaged 9 to 11 outs against 16 for
        the rest, so the &ldquo;Gates, relief-role skip&rdquo; paper
        policy is gates as written minus those rows (&ldquo;role
        skip&rdquo; tag). A &ldquo;role shadow&rdquo; stake is one that
        policy takes only because the skips freed the daily cap. Every stake is
        hypothetical: betting is blocked until calibration passes, and
        model probabilities are served raw — both candidate calibration
        maps were refused on an untouched holdout. On settled rows, ✓/✗
        marks only whether the model&rsquo;s side matched how the line
        settled — no bet existed behind it, so it is not a win or a
        loss. The one-bet-per-pitcher rule spans both markets when
        betting ever opens.{" "}
        {data && (
          <span className="figure">
            payload {source} · {data.generated_at?.slice(0, 16)}Z
          </span>
        )}
      </p>
    </div>
  );
}
