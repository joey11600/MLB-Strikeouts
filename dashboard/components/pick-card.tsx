"use client";

// Expandable pick card. Interaction pattern from the 21st.dev
// Expandable Card (@aghasisahakyan1 — operator's bookmark): the whole
// card is the trigger and the corner button rotates 45° when open.
// Inline expansion (not modal) so multiple cards can stay pinned open
// for comparison, NRFI-style.
import { motion } from "framer-motion";
import type { SlatePitcher, LadderRung } from "@/lib/types";
import { cn, gameTimeET, oddsStr, pctStr, pnlStr } from "@/lib/utils";
import { KDistChart } from "./kdist-chart";

const STATUS_LABEL: Record<string, string> = {
  passed_no_edge: "PASS · no edge",
  passed_stake_too_small: "PASS · stake < 0.1u",
  passed_below_threshold: "PASS · below 10% bar",
  passed_pitcher_cap: "PASS · pitcher cap",
  passed_daily_cap: "PASS · daily cap",
};

function ResultBadge({ result, pnl, actualK }: {
  result: string | null;
  pnl?: number;
  actualK?: number | null;
}) {
  if (!result) return null;
  const cls =
    result === "WIN"
      ? "text-over bg-over-dim border-over/25"
      : result === "LOSS"
        ? "text-under bg-under-dim border-under/25"
        : "text-ink-secondary bg-surface-2 border-line";
  return (
    <span className={cn("figure rounded-badge border px-2 py-0.5 text-[10.5px] font-medium", cls)}>
      {result}
      {actualK != null && ` · ${actualK}K`}
      {pnl !== undefined && (result === "WIN" || result === "LOSS") && ` · ${pnlStr(pnl)}`}
    </span>
  );
}

function StrengthBadge({ strength }: { strength?: string | null }) {
  if (!strength || strength === "NO_PLAY") return null;
  const cls =
    strength === "STRONG"
      ? "text-over border-over/25 bg-over-dim"
      : strength === "MEDIUM"
        ? "text-accent border-accent/25 bg-accent-dim"
        : "text-lean border-lean/25 bg-lean/10";
  return (
    <span className={cn("figure rounded-badge border px-2 py-0.5 text-[10px] font-medium tracking-wider", cls)}>
      {strength}
    </span>
  );
}

function Chip({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={cn(
      "figure rounded-badge border border-line bg-surface-2 px-2 py-0.5 text-[10.5px] text-ink-secondary",
      className,
    )}>
      {children}
    </span>
  );
}

function LadderTable({ rungs }: { rungs: LadderRung[] }) {
  if (!rungs || rungs.length === 0) {
    return <p className="py-2 text-xs text-ink-muted">No alt lines were posted for this pitcher.</p>;
  }
  const sorted = [...rungs].sort((a, b) => a.milestone - b.milestone);
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-line text-[10px] uppercase tracking-wider text-ink-muted">
            <th className="py-1.5 pr-3 font-medium">Rung</th>
            <th className="py-1.5 pr-3 font-medium">Odds</th>
            <th className="py-1.5 pr-3 font-medium">Model</th>
            <th className="py-1.5 pr-3 font-medium">Fair</th>
            <th className="py-1.5 pr-3 font-medium">Edge</th>
            <th className="py-1.5 pr-3 font-medium">Action</th>
            <th className="py-1.5 font-medium">Result</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((r) => {
            const isBet = r.status === "bet" || (r.pick?.bet_placed ?? false);
            return (
              <tr
                key={r.milestone}
                className={cn(
                  "border-b border-line/50",
                  isBet ? "" : "opacity-55",
                )}
              >
                <td className="figure py-1.5 pr-3 font-medium">{r.milestone}+ K</td>
                <td className="figure py-1.5 pr-3">{oddsStr(r.odds)}</td>
                <td className="figure py-1.5 pr-3">{pctStr(r.model_prob ?? r.raw_model_prob)}</td>
                <td className="figure py-1.5 pr-3">{pctStr(r.fair_prob)}</td>
                <td className={cn(
                  "figure py-1.5 pr-3",
                  (r.edge ?? 0) > 0 ? "text-over" : "text-ink-muted",
                )}>
                  {r.edge != null ? `${r.edge >= 0 ? "+" : ""}${(r.edge * 100).toFixed(1)}%` : "—"}
                </td>
                <td className="py-1.5 pr-3">
                  {isBet ? (
                    <span className="figure font-medium text-accent">
                      BET {r.units_risked > 0 ? `${r.units_risked.toFixed(2)}u` : ""}
                    </span>
                  ) : (
                    <span className="figure text-[10.5px] text-ink-muted">
                      {STATUS_LABEL[r.status] ?? r.status}
                    </span>
                  )}
                </td>
                <td className="py-1.5">
                  {r.pick?.graded_result ? (
                    <ResultBadge
                      result={r.pick.graded_result}
                      pnl={r.pick.profit_loss_units.value}
                    />
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
  );
}

interface Props {
  p: SlatePitcher;
  expanded: boolean;
  onToggle: () => void;
  isTop?: boolean;
}

export function PickCard({ p, expanded, onToggle, isTop }: Props) {
  const pick = p.pick;
  const hasBet =
    (pick?.bet_placed && (pick?.units_risked ?? 0) > 0) ||
    p.ladder.some((r) => r.status === "bet" || r.pick?.bet_placed);
  const side = pick?.pick_side || p.best_side || "";
  const ladderBets = p.ladder.filter((r) => r.status === "bet" || r.pick?.bet_placed).length;

  const spine =
    hasBet && side === "OVER"
      ? "before:bg-gradient-to-b before:from-over before:to-over/30"
      : hasBet && side === "UNDER"
        ? "before:bg-gradient-to-b before:from-under before:to-under/30"
        : "before:bg-white/10";

  const matchup = `${p.pitcher_team} ${p.is_home ? "vs" : "@"} ${p.opponent_team}`;
  const displayOdds =
    side === "UNDER" ? p.under_odds : p.over_odds;

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-card border border-line bg-surface transition-colors",
        "before:absolute before:left-0 before:top-0 before:h-full before:w-[3px]",
        spine,
        hasBet ? "" : "opacity-80",
        expanded && "border-line-strong",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-3 px-4 py-3.5 text-left hover:bg-white/[0.02]"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {isTop && (
              <span className="figure rounded-badge bg-accent px-1.5 py-0.5 text-[10px] font-bold text-black">
                #1
              </span>
            )}
            <span className="truncate text-[15px] font-bold tracking-tight">
              {p.pitcher_name}
            </span>
            <span className="text-xs text-ink-muted">{matchup}</span>
            {gameTimeET(p.start_time_utc) && (
              <span className="figure text-[11px] text-ink-secondary">
                {gameTimeET(p.start_time_utc)}
              </span>
            )}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            {hasBet && pick ? (
              <>
                <span
                  className={cn(
                    "figure text-[13px] font-semibold",
                    side === "OVER" ? "text-over" : "text-under",
                  )}
                >
                  {side} {p.line}
                </span>
                <StrengthBadge strength={pick.pick_strength} />
                <Chip>{oddsStr(displayOdds)}</Chip>
                {(pick.units_risked ?? 0) > 0 && <Chip>{pick.units_risked.toFixed(2)}u</Chip>}
              </>
            ) : (
              <>
                <span className="figure text-[13px] text-ink-secondary">
                  line {p.line ?? "—"}
                </span>
                <Chip>{oddsStr(p.over_odds)} / {oddsStr(p.under_odds)}</Chip>
                <span className="figure text-[10.5px] text-ink-muted">no bet</span>
              </>
            )}
            {ladderBets > 0 && (
              <Chip className="border-accent/25 bg-accent-dim text-accent">
                LADDER ×{ladderBets}
              </Chip>
            )}
            {p.expected_k != null && (
              <Chip>E[K] {p.expected_k.toFixed(1)}</Chip>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2.5">
          {pick?.graded_result ? (
            <ResultBadge
              result={pick.graded_result}
              pnl={pick.profit_loss_units.value}
              actualK={pick.actual_strikeouts ?? p.actual_strikeouts}
            />
          ) : p.actual_strikeouts != null ? (
            <Chip>{p.actual_strikeouts}K</Chip>
          ) : null}
          <motion.span
            animate={{ rotate: expanded ? 45 : 0 }}
            transition={{ duration: 0.3 }}
            className="flex h-7 w-7 items-center justify-center rounded-full border border-line text-ink-secondary"
            aria-hidden
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M5 12h14" />
              <path d="M12 5v14" />
            </svg>
          </motion.span>
        </div>
      </button>

      {expanded && (
        <div className="border-t border-line px-4 pb-4 pt-3">
          <div className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-5">
            {[
              ["Model (raw)", pctStr(p.p_over_raw)],
              ["Calibrated", pctStr(p.p_over_calibrated)],
              ["Blended", pctStr(p.blended_prob_over)],
              ["Market fair", pctStr(p.fair_over)],
              [
                "Edge / bar",
                p.edge_best != null
                  ? `${(p.edge_best * 100).toFixed(1)}% / ${((p.threshold ?? 0) * 100).toFixed(1)}%`
                  : "—",
              ],
            ].map(([label, value]) => (
              <div key={label as string}>
                <div className="text-[9.5px] uppercase tracking-wider text-ink-muted">{label}</div>
                <div className="figure text-[13px] font-medium">{value}</div>
              </div>
            ))}
          </div>

          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
              Model K distribution · P(over {p.line}) marked
            </span>
            {pick?.lineup_source && (
              <span
                className={cn(
                  "text-[10px] uppercase tracking-wider",
                  pick.lineup_source === "confirmed" ? "text-over" : "text-accent",
                )}
              >
                {pick.lineup_source} lineup
              </span>
            )}
          </div>
          <KDistChart
            kDist={p.k_dist}
            line={p.line}
            actualK={p.actual_strikeouts ?? pick?.actual_strikeouts ?? null}
          />

          <div className="mt-3">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
              Ladder — every rung evaluated
            </div>
            <LadderTable rungs={p.ladder} />
          </div>

          {pick?.clv_pct != null && (
            <p className="figure mt-2 text-[11px] text-ink-secondary">
              CLV {pick.clv_pct >= 0 ? "+" : ""}
              {(pick.clv_pct * 100).toFixed(1)}% vs close
            </p>
          )}
        </div>
      )}
    </div>
  );
}
