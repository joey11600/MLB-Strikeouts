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
import { PitcherAvatar } from "./pitcher-avatar";

const STATUS_LABEL: Record<string, string> = {
  passed_no_edge: "PASS · no edge",
  passed_stake_too_small: "PASS · stake < 0.1u",
  passed_below_threshold: "PASS · below 10% bar",
  passed_pitcher_cap: "PASS · pitcher cap",
  passed_daily_cap: "PASS · daily cap",
  passed_gap_gate: "PASS · gap gate (needs E[K] ≥ line +1.5)",
  passed_not_next_rung: "PASS · beyond next 2 rungs",
  not_bet_rule_change: "current rules would bet — placed before rule change",
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

/** The outcome, sized to be read at a glance across a full board.
 *
 *  Result and SIDE both want green/red, so they are separated by FORM,
 *  not hue: the side is an outline arrow on the line itself, the result
 *  is this filled slab. Fill weight says "this is what happened";
 *  colour only reinforces it. The K count and a "vs 5.5" reference sit
 *  together so "did it clear?" needs no arithmetic.
 */
function ResultSlab({
  k, line, graded, pnl, isFinal, leanHit, gameState,
}: {
  k: number | null;
  line: number | null;
  graded: string | null;
  pnl?: number;
  isFinal: boolean;
  leanHit: boolean | null;
  gameState?: string;
}) {
  if (k == null) {
    if (gameState && !/scheduled|pre-game|warmup/i.test(gameState)) {
      return (
        <span className="figure whitespace-nowrap rounded-badge border border-line px-2 py-1 text-[10px] uppercase tracking-wider text-ink-muted">
          {gameState}
        </span>
      );
    }
    return null;
  }

  // A settled BET is the loudest thing on the card. An unbet row still
  // shows its number, quietly — the board is evidence either way.
  const tone =
    graded === "WIN"
      ? "border-over bg-over-dim text-over"
      : graded === "LOSS"
        ? "border-under bg-under-dim text-under"
        : graded
          ? "border-line-strong bg-surface-2 text-ink-secondary"
          : leanHit === true
            ? "border-over/60 bg-over-dim text-ink"
            : leanHit === false
              ? "border-under/60 bg-under-dim text-ink"
              : "border-line-strong bg-surface-2 text-ink";

  return (
    <div
      className={cn(
        "flex shrink-0 flex-col items-end rounded-card border px-2.5 py-1.5 leading-none",
        tone,
        !isFinal && "animate-pulse",
      )}
      title={
        graded
          ? `Bet graded ${graded}`
          : leanHit == null
            ? undefined
            : `Model leaned ${leanHit ? "correctly" : "wrong"} (not bet)`
      }
    >
      <div className="flex items-baseline gap-1">
        <span className="figure text-[22px] font-bold tabular-nums">{k}</span>
        <span className="figure text-[11px] opacity-70">K</span>
      </div>
      {/* A graded BET says WIN/LOSS. An unbet row says whether the
          model's lean was right, with a mark rather than colour alone —
          most of the board is unbet, and "was it right?" is the whole
          reason to look at it. */}
      <div className="figure mt-1 whitespace-nowrap text-[9.5px] uppercase tracking-wider opacity-80">
        {!isFinal
          ? "in game"
          : graded
            ? graded
            : leanHit === true
              ? `✓ vs ${line}`
              : leanHit === false
                ? `✗ vs ${line}`
                : line != null
                  ? `vs ${line}`
                  : "final"}
      </div>
      {graded && pnl !== undefined && (graded === "WIN" || graded === "LOSS") && (
        <div className="figure mt-0.5 text-[10px] font-semibold tabular-nums">
          {pnlStr(pnl)}
        </div>
      )}
    </div>
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
  currentRulesPrimary?: number;
}

function LadderTable({ rungs, line, side, primaryPick, currentRulesPrimary }: LadderTableProps) {
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
  // Strict line order — bets are highlighted in place, never re-sorted.
  const sorted = all.sort((a, b) => a.milestone - b.milestone);

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
            // The equivalent rung IS the primary bet (or its inverse on
            // an under card) — when the primary was placed, it gets the
            // side-colored bet highlight and shows the primary's stake.
            const isPrimaryBetRow = isEquivalent && primaryBet;
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
                  isPrimaryBetRow &&
                    (primaryIsOver
                      ? "border-l-2 border-l-over bg-over-dim/50 [&>td:first-child]:pl-1.5"
                      : "border-l-2 border-l-under bg-under-dim/50 [&>td:first-child]:pl-1.5"),
                  !isBet && !isEquivalent && "opacity-55",
                  isEquivalent && !isPrimaryBetRow && "opacity-80",
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
                  {isPrimaryBetRow ? (
                    <span
                      className={cn(
                        "figure font-semibold",
                        primaryIsOver ? "text-over" : "text-under",
                      )}
                    >
                      BET {(primaryPick?.units_risked ?? 0).toFixed(2)}u · primary (
                      {primaryIsOver ? "OVER" : "UNDER"} {line})
                      {currentRulesPrimary != null &&
                        currentRulesPrimary !== (primaryPick?.units_risked ?? 0) && (
                          <span className="ml-1.5 font-normal text-ink-muted">
                            · now {currentRulesPrimary}u
                          </span>
                        )}
                    </span>
                  ) : isEquivalent ? (
                    <span className="figure text-[10.5px] text-ink-secondary">
                      = primary line ({primaryIsOver ? "OVER" : "UNDER"} {line}, not bet)
                    </span>
                  ) : r.status === "not_bet_rule_change" ? (
                    <span className="figure text-[11px] font-medium text-accent">
                      current rules: BET {r.current_rules_units != null ? `${r.current_rules_units}u` : ""}
                      <span className="ml-1.5 font-normal text-ink-muted">
                        · placed before rule change
                      </span>
                    </span>
                  ) : isBet ? (
                    <span className="figure font-semibold text-accent">
                      BET {r.units_risked > 0 ? `${r.units_risked.toFixed(2)}u` : ""}
                      {r.current_rules_units != null &&
                        r.current_rules_units !== r.units_risked && (
                          <span className="ml-1.5 font-normal text-ink-muted">
                            · now {r.current_rules_units}u
                          </span>
                        )}
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

  // The rail always carries the SIDE, bet or not. It used to light up
  // only for placed bets, so a no-bet slate rendered twenty identical
  // grey cards and the single most important distinction — which way
  // the model leans — was invisible. Betting changes the INTENSITY, not
  // the hue: a bet is a saturated rail, a lean is a quiet one.
  const spine =
    side === "OVER"
      ? hasBet
        ? "before:bg-gradient-to-b before:from-over before:to-over/30"
        : "before:bg-gradient-to-b before:from-over/45 before:to-over/5"
      : side === "UNDER"
        ? hasBet
          ? "before:bg-gradient-to-b before:from-under before:to-under/30"
          : "before:bg-gradient-to-b before:from-under/45 before:to-under/5"
        : "before:bg-white/10";

  const matchup = `${p.pitcher_team} ${p.is_home ? "vs" : "@"} ${p.opponent_team}`;
  const displayOdds =
    side === "UNDER" ? p.under_odds : p.over_odds;

  // Result: the number you scan a 20-card board for. Actual K against
  // the posted line, so "did it clear?" is answerable without arithmetic.
  const actualK = pick?.actual_strikeouts ?? p.actual_strikeouts;
  const live = p.live;
  const isFinal = live?.final ?? actualK != null;
  // Requires isFinal: an in-game K total can still move, so tinting the
  // slab on it would call a result that has not happened.
  const cleared =
    isFinal && actualK != null && p.line != null
      ? actualK > p.line
        ? "over"
        : "under"
      : null;
  // Did the model's lean come true? Distinct from bet W/L — most rows
  // are not bets, and the board is still telling us something.
  const leanHit = cleared != null && side ? cleared === side.toLowerCase() : null;

  return (
    <div
      className={cn(
        "relative overflow-hidden rounded-card border bg-surface transition-all",
        "before:absolute before:left-0 before:top-0 before:h-full before:w-[3px] before:z-10",
        spine,
        hasBet
          ? // Same gold treatment as the scoreboard stub, so a play reads
            // as a play in both places and the eye learns one signal
            // rather than two. The side rail survives on top of it (z-10)
            // — gold says TAKE THIS, the rail still says which way.
            "border-accent/60 shadow-[0_0_0_1px_rgba(245,158,11,0.28),0_0_24px_-6px_rgba(245,158,11,0.55)]"
          : "border-line opacity-80",
        expanded && !hasBet && "border-line-strong",
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-3 px-4 py-3.5 text-left hover:bg-white/[0.02]"
      >
        <PitcherAvatar
          pitcherId={p.pitcher_id}
          name={p.pitcher_name}
          side={side}
          className="h-10 w-10"
        />
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
          {/* One hierarchy, not four equal chips. Side+line leads (it is
              the pick), then the model's own numbers, then the market's
              price, then the shortfall in muted text. Previously all
              four rendered at the same weight, so nothing led and the
              eye had nowhere to land. */}
          <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
            <span
              className={cn(
                "figure text-[14px] font-bold tracking-tight",
                side === "OVER"
                  ? "text-over"
                  : side === "UNDER"
                    ? "text-under"
                    : "text-ink-secondary",
              )}
            >
              {/* Arrow, not colour alone: hue never carries meaning by
                  itself, and these two are the whole point of the card. */}
              {side === "OVER" ? "▲" : side === "UNDER" ? "▼" : ""} {side}{" "}
              {p.line ?? "—"}
            </span>

            {p.blended_prob_over != null && (
              <span className="figure text-[12px] text-ink">
                {pctStr(
                  side === "UNDER" ? 1 - p.blended_prob_over : p.blended_prob_over,
                )}
              </span>
            )}

            {p.expected_k != null && (
              <span className="figure text-[11.5px] text-ink-secondary">
                proj {p.expected_k.toFixed(1)}
              </span>
            )}

            {/* Only the price for the side we are on. Both sides live in
                the expanded view; showing them here forced a mental
                lookup of which number applied. */}
            <span className="figure text-[11.5px] text-ink-secondary">
              {oddsStr(displayOdds)}
            </span>

            {hasBet && pick ? (
              <>
                <StrengthBadge strength={pick.pick_strength} />
                {(pick.units_risked ?? 0) > 0 && (
                  <Chip className="border-accent/30 bg-accent-dim text-accent">
                    {pick.units_risked.toFixed(2)}u
                  </Chip>
                )}
              </>
            ) : p.edge_best != null && p.threshold != null ? (
              // ink-muted (#55555E) contrasts only 2.56:1 on the card and
              // fails AA. ink-secondary reads 5.3:1 and still sits well
              // below the primary figures, so the hierarchy survives.
              <span
                className="figure text-[11px] text-ink-secondary"
                title={`Edge ${(p.edge_best * 100).toFixed(1)}% against a ${(p.threshold * 100).toFixed(1)}% bar`}
              >
                {((p.edge_best - p.threshold) * 100).toFixed(1)} pts short
              </span>
            ) : null}

            {ladderBets > 0 && (
              <Chip className="border-accent/25 bg-accent-dim text-accent">
                LADDER ×{ladderBets}
              </Chip>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center gap-2.5">
          <ResultSlab
            k={actualK ?? null}
            line={p.line ?? null}
            graded={pick?.graded_result ?? null}
            pnl={pick?.profit_loss_units.value}
            isFinal={isFinal}
            leanHit={leanHit}
            gameState={live?.game_state}
          />
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
            {(() => {
              const betRungs = p.ladder.filter(
                (r) => r.status === "bet" || r.pick?.bet_placed,
              );
              const ladderUnits = betRungs.reduce(
                (acc, r) => acc + (r.pick?.units_risked ?? r.units_risked ?? 0),
                0,
              );
              const primaryUnits =
                pick?.bet_placed ? (pick?.units_risked ?? 0) : 0;
              const total = primaryUnits + ladderUnits;
              return (
                <div className="mb-1 flex items-baseline justify-between">
                  <span className="text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
                    Ladder — every rung evaluated
                  </span>
                  <span className="figure text-[10.5px]">
                    {total > 0 ? (
                      <span className="text-ink-secondary">
                        {primaryUnits > 0 && (
                          <span className={side === "UNDER" ? "text-under" : "text-over"}>
                            primary {primaryUnits.toFixed(2)}u
                          </span>
                        )}
                        {primaryUnits > 0 && ladderUnits > 0 && " + "}
                        {ladderUnits > 0 && (
                          <span className="text-accent">
                            ladder {ladderUnits.toFixed(2)}u ({betRungs.length} rung
                            {betRungs.length !== 1 ? "s" : ""})
                          </span>
                        )}
                        {" = "}
                        {total.toFixed(2)}u on this pitcher
                      </span>
                    ) : (
                      <span className="text-ink-muted">no bets</span>
                    )}
                  </span>
                </div>
              );
            })()}
            <LadderTable
              rungs={p.ladder}
              line={p.line}
              side={side}
              primaryPick={pick}
              currentRulesPrimary={p.current_rules_primary_units}
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
