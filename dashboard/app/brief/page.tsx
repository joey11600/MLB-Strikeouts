"use client";

// Filming page: today's bets only, big type, zero chrome.
import Link from "next/link";
import { useDashboard } from "@/lib/data-context";
import { cn, oddsStr } from "@/lib/utils";

export default function BriefPage() {
  const { data } = useDashboard();
  if (!data) {
    return <div className="py-24 text-center text-sm text-ink-muted">Loading…</div>;
  }

  const date = data.available_dates[0];
  const slate = date ? data.slates[date] : null;
  const bets = (slate?.pitchers ?? []).filter(
    (p) =>
      (p.pick?.bet_placed && (p.pick?.units_risked ?? 0) > 0) ||
      p.ladder.some((r) => r.status === "bet" || r.pick?.bet_placed),
  );

  return (
    <div className="mx-auto max-w-lg space-y-4">
      <div className="flex items-center justify-between">
        <Link href="/" className="text-xs text-ink-muted transition-colors hover:text-ink">
          ◂ Slate
        </Link>
        <span className="figure text-xs text-ink-muted">{date}</span>
      </div>

      {bets.length === 0 ? (
        <p className="py-16 text-center text-ink-muted">No bets on the board.</p>
      ) : (
        <div className="stagger flex flex-col gap-3">
          {bets.map((p) => {
            const side = p.pick?.pick_side || p.best_side || "";
            const ladderRungs = p.ladder.filter(
              (r) => r.status === "bet" || r.pick?.bet_placed,
            );
            return (
              <div
                key={`${p.pitcher_id}-${p.line}`}
                className={cn(
                  "relative overflow-hidden rounded-card border border-line bg-surface p-5",
                  "before:absolute before:left-0 before:top-0 before:h-full before:w-[3px]",
                  side === "OVER"
                    ? "before:bg-gradient-to-b before:from-over before:to-over/30"
                    : "before:bg-gradient-to-b before:from-under before:to-under/30",
                )}
              >
                <div className="flex items-baseline justify-between">
                  <span className="text-xl font-bold tracking-tight">{p.pitcher_name}</span>
                  <span className="text-xs text-ink-muted">
                    {p.pitcher_team} {p.is_home ? "vs" : "@"} {p.opponent_team}
                  </span>
                </div>
                {p.pick?.bet_placed && (p.pick?.units_risked ?? 0) > 0 && (
                  <div className="mt-2 flex items-baseline gap-3">
                    <span
                      className={cn(
                        "figure text-2xl font-semibold",
                        side === "OVER" ? "text-over" : "text-under",
                      )}
                    >
                      {side} {p.line}
                    </span>
                    <span className="figure text-lg text-ink">
                      {oddsStr(side === "UNDER" ? p.under_odds : p.over_odds)}
                    </span>
                    <span className="figure ml-auto text-lg text-ink-muted">
                      {p.pick.units_risked.toFixed(1)}u
                    </span>
                  </div>
                )}
                {ladderRungs.length > 0 && (
                  <div className="mt-3 border-t border-line pt-2.5">
                    <span className="figure text-[10px] uppercase tracking-[0.15em] text-accent">
                      Ladder
                    </span>
                    {ladderRungs.map((r) => (
                      <div key={r.milestone} className="mt-1 flex items-baseline gap-3">
                        <span className="figure text-lg font-medium text-over">
                          OVER {r.milestone}+
                        </span>
                        <span className="figure text-ink">{oddsStr(r.odds)}</span>
                        <span className="figure ml-auto text-ink-muted">
                          {(r.pick?.units_risked ?? r.units_risked).toFixed(1)}u
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      <p className="figure pt-2 text-center text-[10px] text-ink-muted">flat 100u basis</p>
    </div>
  );
}
