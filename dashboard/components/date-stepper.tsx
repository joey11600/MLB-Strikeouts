"use client";

// NRFI-pattern date stepper: ◂ [select] ▸ with a LIVE / PAST / SCHEDULED
// state badge; PAST is clickable and jumps back to the newest slate.
import { cn, pastDelta } from "@/lib/utils";

interface Props {
  dates: string[]; // newest first
  current: string;
  todayEt: string;
  onChange: (d: string) => void;
}

export function DateStepper({ dates, current, todayEt, onChange }: Props) {
  const idx = dates.indexOf(current);
  const prev = idx >= 0 && idx < dates.length - 1 ? dates[idx + 1] : null;
  const next = idx > 0 ? dates[idx - 1] : null;

  const state: "live" | "past" | "future" =
    current === todayEt ? "live" : current < todayEt ? "past" : "future";

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => prev && onChange(prev)}
        disabled={!prev}
        aria-label="Previous date"
        className="flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-ink-secondary transition-colors hover:border-line-strong hover:text-ink disabled:opacity-30"
      >
        ◂
      </button>
      <select
        value={current}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Slate date"
        className="figure h-8 rounded-md border border-line bg-surface px-2 text-[13px] text-ink outline-none transition-colors hover:border-line-strong"
      >
        {dates.map((d) => (
          <option key={d} value={d}>
            {d}
          </option>
        ))}
      </select>
      <button
        type="button"
        onClick={() => next && onChange(next)}
        disabled={!next}
        aria-label="Next date"
        className="flex h-8 w-8 items-center justify-center rounded-md border border-line bg-surface text-ink-secondary transition-colors hover:border-line-strong hover:text-ink disabled:opacity-30"
      >
        ▸
      </button>

      {state === "live" && (
        <span className="figure flex items-center gap-1.5 rounded-badge border border-over/25 bg-over-dim px-2 py-1 text-[10px] font-medium text-over">
          <span className="live-dot h-1.5 w-1.5 rounded-full bg-over shadow-[0_0_8px_rgba(16,185,129,0.5)]" />
          LIVE
        </span>
      )}
      {state === "past" && (
        <button
          type="button"
          onClick={() => dates[0] && onChange(dates[0])}
          title="Jump to newest slate"
          className={cn(
            "figure rounded-badge border border-line bg-surface-2 px-2 py-1 text-[10px] font-medium text-ink-secondary",
            "transition-colors hover:border-line-strong hover:text-ink",
          )}
        >
          PAST · {pastDelta(current, todayEt)}
        </button>
      )}
      {state === "future" && (
        <span className="figure rounded-badge border border-line bg-surface-2 px-2 py-1 text-[10px] font-medium text-ink-secondary">
          SCHEDULED
        </span>
      )}
    </div>
  );
}
