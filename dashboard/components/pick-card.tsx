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
  passed_gap_gate: "PASS · gap gate (needs E[K] ≥ line +1.5)",
  passed_not_next_rung: "PASS · beyond next 2 rungs",
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

interface LadderTableProps {
  rungs: LadderRung[];
  line: number | null;
  side: string;
  primaryPick: SlatePitcher["pick"];
}

function LadderTable({ rungs, line, side, primaryPick }: LadderTableProps) {
  if ((!rungs || rungs.length === 0) && line == null) {
    return <p className="py-2 text-xs text-ink-muted">No alt lines were posted for this pitcher.</p>;
  }

  // Older slates evaluated the ladder without the primary-equivalent
  // rung (ceil of the line) — synthesize a marker row so the sequence
  // reads complete. Newer slates carry it with real alt-board odds.
  const all = [...(rungs ?? [])];
  const eq = line != null ? Math.ceil(line) : null;
  if (eq != null && !all.some((r) => r.milestone === eq)) {
    all.push({
      milestone: eq,
      odds: "",
      edge: null,
      units_risked: 0,
      status: "primary_equivalent",
    } as LadderRung);
  }

  const isBetRung = (r: LadderRung) =>
    r.status === "bet" || (r.pick?.bet_placed ?? false);
  const sorted = all.sort((a, b) => {
    if (isBetRung(a) !== isBetRung(b)) return isBetRung(a) ? -1 : 1;
    return a.milestone - b.milestone;
  });

  const primaryIsOver = side === "OVER";
  const primaryBet = (primaryPick?.bet_placed && (primaryPick?.units_risked ?? 0) > 0) ?? false;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead>
          <tr className="border-b border-line text-[10px] uppercase tracking-wider text-ink-muted">
            <th className="py-1.5 pl-2 pr-3 font-medium">Line</th>
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
            const isBet = isBetRung(r);
            const isEquivalent = r.status === "primary_equivalent";
            // On UNDER cards, non-bet rungs display as their under-side
            // twin: "6+ K" (six or more) flips to UNDER 5.5 (five or
            // fewer), with under-probabilities. Bet rungs are real over
            // bets and keep their over framing. DK posts no under
            // prices on the alt board, so the over price shows muted
            // for provenance — never fabricated.
            const flipRow = !primaryIsOver && !isBet;
            const label = flipRow
              ? `UNDER ${(r.milestone - 0.5).toFixed(1)}`
              : `${r.milestone}+ K`;
            const modelP = r.model_prob ?? r.raw_model_prob;
            const fairP = r.fair_prob;
            return (
              <tr
                key={r.milestone}
                className={cn(
                  "border-b border-line/50",
                  isBet &&
                    "border-l-2 border-l-accent bg-accent-dim/60 [&>td:first-child]:pl-1.5",
                  !isBet && !isEquivalent && "opacity-55",
                  isEquivalent && "opacity-80",
                )}
              >
                <td className={cn(
                  "figure py-1.5 pl-2 pr-3 font-medium",
                  isBet && "text-accent",
                  flipRow && !isEquivalent && "text-under/90",
                )}>
                  {label}
                </td>
                <td className={cn("figure py-1.5 pr-3", flipRow && "text-ink-muted")}>
                  {r.odds
                    ? flipRow
                      ? `o ${oddsStr(r.odds)}`
                      : oddsStr(r.odds)
                    : "—"}
                </td>
                <td className="figure py-1.5 pr-3">
                  {pctStr(flipRow && modelP != null ? 1 - modelP : modelP)}
                </td>
                <td className="figure py-1.5 pr-3">
                  {pctStr(flipRow && fairP != null ? 1 - fairP : fairP)}
                </td>
                <td className={cn(
                  "figure py-1.5 pr-3",
                  !flipRow && (r.edge ?? 0) > 0 ? "text-over" : "text-ink-muted",
                )}>
                  {flipRow
                    ? "—"
                    : r.edge != null
                      ? `${r.edge >= 0 ? "+" : ""}${(r.edge * 100).toFixed(1)}%`
                      : "—"}
                </td>
                <td className="py-1.5 pr-3">
                  {isEquivalent ? (
                    <span className="figure text-[10.5px] text-ink-secondary">
                      = primary bet ({primaryIsOver ? "OVER" : "UNDER"} {line})
                    </span>
                  ) : isBet ? (
                    <span className="figure font-semibold text-accent">
                      BET {r.units_risked > 0 ? `${r.units_risked.toFixed(2)}u` : ""}
                    </span>
                  ) : flipRow ? (
                    <span className="figure text-[10.5px] text-ink-muted">
                      no under market
                    </span>
                  ) : (
                    <span className="figure text-[10.5px] text-ink-muted">
                      {STATUS_LABEL[r.status] ?? r.status}
                    </span>
                  )}
                </td>
                <td className="py-1.5">
                  {isEquivalent && primaryBet && primaryPick?.graded_result ? (
                    <ResultBadge
                      result={primaryPick.graded_result}
                      pnl={primaryPick.profit_loss_units.value}
                    />
                  ) : r.pick?.graded_result ? (
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
      {!primaryIsOver && (
        <p className="figure mt-1.5 text-[10px] text-ink-muted">
          DraftKings sells only the over side of strikeout alt lines — unders
          shown with model probabilities for context (o = the posted over price).
        </p>
      )}
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
          <div className="mb-1.5 text-[9.5px] uppercase tracking-wider text-ink-muted">
            All probabilities are{" "}
            <span className={cn("font-semibold", side === "UNDER" ? "text-under" : "text-over")}>
              P({side || "OVER"} {p.line})
            </span>{" "}
            — the chance this side wins
          </div>
          <div className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-5">
            {(() => {
              const flip = (v: number | null | undefined) =>
                v == null ? null : side === "UNDER" ? 1 - v : v;
              return [
                ["Model (raw)", pctStr(flip(p.p_over_raw))],
                ["Calibrated", pctStr(flip(p.p_over_calibrated))],
                ["Blended", pctStr(flip(p.blended_prob_over))],
                ["Market fair", pctStr(flip(p.fair_over))],
                [
                  "Edge / bar",
                  p.edge_best != null
                    ? `${(p.edge_best * 100).toFixed(1)}% / ${((p.threshold ?? 0) * 100).toFixed(1)}%`
                    : "—",
                ],
              ] as [string, string][];
            })().map(([label, value]) => (
              <div key={label}>
                <div className="text-[9.5px] uppercase tracking-wider text-ink-muted">{label}</div>
                <div className="figure text-[13px] font-medium">{value}</div>
              </div>
            ))}
          </div>

          <div className="mb-1 flex items-baseline justify-between">
            <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
              Model K distribution · P({side || "OVER"} {p.line}) marked
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
            side={side || undefined}
            outcome={(() => {
              // Green if any bet on this card won; red only when every
              // graded bet (primary + ladder) lost; neutral otherwise.
              const graded: string[] = [];
              if (pick?.bet_placed && pick.graded_result) graded.push(pick.graded_result);
              for (const r of p.ladder) {
                if (r.pick?.bet_placed && r.pick.graded_result) graded.push(r.pick.graded_result);
              }
              if (graded.some((g) => g === "WIN")) return "won";
              if (graded.length > 0 && graded.every((g) => g === "LOSS")) return "lost";
              return null;
            })()}
          />

          <div className="mt-3">
            <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
              Ladder — every rung evaluated
            </div>
            <LadderTable
              rungs={p.ladder}
              line={p.line}
              side={side}
              primaryPick={pick}
            />
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
