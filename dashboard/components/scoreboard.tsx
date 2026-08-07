"use client";

import * as React from "react";
import type { Slate, SlatePitcher } from "@/lib/types";
import { cn } from "@/lib/utils";

/* Daily scoreboard — the whole board in one glance.
 *
 * Two questions, one grid, answered at opposite ends of the day:
 *   10:30am  "what do I take?"     -> the gold stubs
 *   10:30pm  "how did we do?"      -> the K numbers and marks
 *
 * Every pitcher priced gets a stub, not just the bets. On most days
 * nothing clears the bar, and a board that only showed bets would be
 * empty on exactly the days the model did the most work.
 *
 * The gold treatment is deliberately rare. It fires only on an
 * actionable play, so when the operator sees it, it means something. It
 * is never colour alone — a ★ and the stake ride with it, per the
 * palette rule that hue must never carry meaning by itself.
 */

function lastName(full: string): string {
  const parts = (full || "").trim().split(/\s+/);
  return parts.length > 1 ? parts.slice(1).join(" ") : full;
}

interface StubProps {
  p: SlatePitcher;
  onClick: () => void;
}

function Stub({ p, onClick }: StubProps) {
  const pick = p.pick;
  const units =
    (pick?.bet_placed ? pick.units_risked ?? 0 : 0) +
    p.ladder.reduce(
      (a, r) =>
        r.status === "bet" || r.pick?.bet_placed
          ? a + (r.pick?.units_risked ?? r.units_risked ?? 0)
          : a,
      0,
    );
  const isPlay = units > 0;
  const side = pick?.pick_side || p.best_side || "";
  const k = pick?.actual_strikeouts ?? p.actual_strikeouts;
  const isFinal = p.live?.final ?? k != null;
  const graded = pick?.graded_result ?? null;

  // Only judge a lean once the total can no longer change. A pitcher
  // sitting on 3 K against a 5.5 line has not "gone under" — he is in
  // the fourth inning. Same rule the grader uses before it settles money.
  const cleared =
    isFinal && k != null && p.line != null
      ? k > p.line
        ? "OVER"
        : "UNDER"
      : null;
  const leanHit = cleared && side ? cleared === side : null;

  // Outcome colour is about the RESULT; the amber ring is about
  // actionability. They occupy different parts of the stub on purpose so
  // a losing play still reads as "this was a play".
  const outcome =
    graded === "WIN"
      ? "text-over"
      : graded === "LOSS"
        ? "text-under"
        : leanHit === true
          ? "text-over"
          : leanHit === false
            ? "text-under"
            : "text-ink";

  return (
    <button
      type="button"
      onClick={onClick}
      title={`${p.pitcher_name} — ${side} ${p.line ?? ""}${
        isPlay ? ` · PLAY ${units.toFixed(2)}u` : " · no bet"
      }${k != null ? ` · ${k} K` : ""}`}
      className={cn(
        "group relative flex min-w-0 flex-col gap-1 rounded-card border px-2.5 py-2 text-left transition-all",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent",
        isPlay
          ? // The gold stub. Ring + outer bloom + a warm inner wash, so
            // it reads as lit rather than merely outlined.
            "border-accent/70 bg-accent-dim shadow-[0_0_0_1px_rgba(245,158,11,0.35),0_0_20px_-4px_rgba(245,158,11,0.65)] hover:shadow-[0_0_0_1px_rgba(245,158,11,0.5),0_0_26px_-2px_rgba(245,158,11,0.8)]"
          : "border-line bg-surface hover:border-line-strong hover:bg-surface-2",
      )}
    >
      <div className="flex items-center gap-1">
        {isPlay && (
          <span aria-hidden className="text-[10px] leading-none text-accent">
            ★
          </span>
        )}
        <span
          className={cn(
            "truncate text-[11.5px] font-semibold tracking-tight",
            isPlay ? "text-accent" : "text-ink-secondary",
          )}
        >
          {lastName(p.pitcher_name)}
        </span>
      </div>

      <div className="flex items-baseline gap-1">
        <span
          className={cn(
            "figure text-[11px] font-bold",
            side === "OVER"
              ? "text-over"
              : side === "UNDER"
                ? "text-under"
                : "text-ink-muted",
          )}
        >
          {side === "OVER" ? "▲" : side === "UNDER" ? "▼" : "·"}
          {p.line ?? "—"}
        </span>
        {isPlay && (
          <span className="figure text-[9.5px] font-semibold text-accent">
            {units.toFixed(2)}u
          </span>
        )}
      </div>

      <div className="flex items-baseline justify-between gap-1">
        {k != null ? (
          <>
            <span className={cn("figure text-[17px] font-bold tabular-nums leading-none", outcome)}>
              {k}
              <span className="ml-0.5 text-[9px] font-normal opacity-60">K</span>
            </span>
            <span className={cn("figure text-[9px] uppercase tracking-wider", outcome)}>
              {graded ?? (leanHit === true ? "✓" : leanHit === false ? "✗" : "")}
            </span>
          </>
        ) : (
          <span className="figure text-[9.5px] uppercase tracking-wider text-ink-muted">
            {p.live?.game_state && !/scheduled|pre-game/i.test(p.live.game_state)
              ? p.live.game_state
              : "pending"}
          </span>
        )}
      </div>

      {!isFinal && k != null && (
        <span
          aria-hidden
          className="absolute right-1.5 top-1.5 h-1.5 w-1.5 animate-pulse rounded-full bg-accent"
        />
      )}
    </button>
  );
}

export function Scoreboard({
  slate,
  onSelect,
}: {
  slate: Slate;
  onSelect?: (key: string) => void;
}) {
  const all = slate.pitchers;

  const stats = React.useMemo(() => {
    let plays = 0;
    let units = 0;
    let settled = 0;
    let live = 0;
    let leanRight = 0;
    let leanWrong = 0;
    let w = 0;
    let l = 0;

    for (const p of all) {
      const u =
        (p.pick?.bet_placed ? p.pick.units_risked ?? 0 : 0) +
        p.ladder.reduce(
          (a, r) =>
            r.status === "bet" || r.pick?.bet_placed
              ? a + (r.pick?.units_risked ?? r.units_risked ?? 0)
              : a,
          0,
        );
      if (u > 0) {
        plays += 1;
        units += u;
      }
      const g = p.pick?.graded_result;
      if (g === "WIN") w += 1;
      if (g === "LOSS") l += 1;

      const k = p.pick?.actual_strikeouts ?? p.actual_strikeouts;
      if (k != null) {
        const final = p.live?.final ?? true;
        if (final) settled += 1;
        else live += 1;
        // Tally leans on SETTLED lines only — an in-game total is not a
        // result yet, and counting it would inflate the model's record
        // with games still being played.
        const side = p.pick?.pick_side || p.best_side || "";
        if (final && p.line != null && side) {
          const cleared = k > p.line ? "OVER" : "UNDER";
          if (cleared === side) leanRight += 1;
          else leanWrong += 1;
        }
      }
    }
    return { plays, units, settled, live, leanRight, leanWrong, w, l };
  }, [all]);

  // Plays first — the whole point is that what to take is unmissable.
  // Then settled results, then everything still to come.
  const ordered = React.useMemo(() => {
    const rank = (p: SlatePitcher) => {
      const u =
        (p.pick?.bet_placed ? p.pick.units_risked ?? 0 : 0) +
        p.ladder.reduce(
          (a, r) =>
            r.status === "bet" || r.pick?.bet_placed
              ? a + (r.pick?.units_risked ?? r.units_risked ?? 0)
              : a,
          0,
        );
      if (u > 0) return 0;
      const k = p.pick?.actual_strikeouts ?? p.actual_strikeouts;
      if (k != null) return 1;
      return 2;
    };
    return [...all].sort(
      (a, b) => rank(a) - rank(b) || (b.edge_best ?? 0) - (a.edge_best ?? 0),
    );
  }, [all]);

  const leanTotal = stats.leanRight + stats.leanWrong;

  return (
    <section
      aria-label="Daily scoreboard"
      className="rounded-card border border-line bg-surface p-3.5"
    >
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1.5">
        <h2 className="text-[11px] font-semibold uppercase tracking-[0.14em] text-ink">
          Scoreboard
          <span className="ml-2 font-normal text-ink-muted">{slate.date}</span>
        </h2>
        <div className="figure flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]">
          <span className="text-ink-secondary">{all.length} priced</span>
          {stats.plays > 0 ? (
            <span className="font-semibold text-accent">
              ★ {stats.plays} to take · {stats.units.toFixed(2)}u
            </span>
          ) : (
            <span className="text-ink-muted">no play clears the bar</span>
          )}
          {stats.live > 0 && (
            <span className="text-accent">{stats.live} in game</span>
          )}
          <span className="text-ink-secondary">{stats.settled} final</span>
          {stats.w + stats.l > 0 && (
            <span className="text-ink">
              bets {stats.w}–{stats.l}
            </span>
          )}
          {leanTotal > 0 && (
            <span
              className="text-ink-secondary"
              title="How often the model's side was correct across the WHOLE board, bet or not. Not money — read it as evidence."
            >
              model {stats.leanRight}/{leanTotal}
            </span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-1.5 min-[420px]:grid-cols-3 sm:grid-cols-4 lg:grid-cols-6 xl:grid-cols-8">
        {ordered.map((p) => (
          <Stub
            key={`${p.pitcher_id}-${p.line}`}
            p={p}
            onClick={() => onSelect?.(`${p.pitcher_id}-${p.line}`)}
          />
        ))}
      </div>

      {stats.plays === 0 && (
        <p className="mt-2.5 text-[10.5px] leading-relaxed text-ink-secondary">
          Nothing clears the edge bar today, so there is nothing to take —
          that is the filter working, not a missing board. Every pitcher above
          was still priced and is still scored, which is what the model is
          judged on.
        </p>
      )}
    </section>
  );
}
