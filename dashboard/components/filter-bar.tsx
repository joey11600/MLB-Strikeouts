"use client";

// NRFI-pattern control row: segmented tabs + free-text find, persisted
// to URL params and localStorage (non-default values only).
import * as React from "react";
import { cn } from "@/lib/utils";

export interface SlateFilters {
  side: "ALL" | "OVER" | "UNDER";
  show: "ALL" | "BETS" | "GRADED";
  find: string;
}

export const DEFAULT_FILTERS: SlateFilters = { side: "ALL", show: "ALL", find: "" };
const STORAGE_KEY = "kt-slate-filters";

export function loadStoredFilters(): SlateFilters {
  if (typeof window === "undefined") return DEFAULT_FILTERS;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_FILTERS;
    return { ...DEFAULT_FILTERS, ...JSON.parse(raw) };
  } catch {
    return DEFAULT_FILTERS;
  }
}

export function persistFilters(filters: SlateFilters) {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  const setOrDelete = (k: string, v: string, def: string) => {
    if (v && v !== def) url.searchParams.set(k, v);
    else url.searchParams.delete(k);
  };
  setOrDelete("side", filters.side, DEFAULT_FILTERS.side);
  setOrDelete("show", filters.show, DEFAULT_FILTERS.show);
  setOrDelete("find", filters.find, DEFAULT_FILTERS.find);
  window.history.replaceState(null, "", url.toString());
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(filters));
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: readonly T[];
  onChange: (v: T) => void;
  label: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className="flex items-center rounded-md border border-line bg-surface p-0.5"
    >
      {options.map((opt) => (
        <button
          key={opt}
          role="tab"
          aria-selected={value === opt}
          onClick={() => onChange(opt)}
          className={cn(
            "rounded px-2.5 py-1 text-[11px] font-semibold transition-colors",
            value === opt
              ? "bg-surface-3 text-ink"
              : "text-ink-muted hover:text-ink-secondary",
          )}
        >
          {opt}
        </button>
      ))}
    </div>
  );
}

export function FilterBar({
  filters,
  onChange,
}: {
  filters: SlateFilters;
  onChange: (f: SlateFilters) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Segmented
        label="Side"
        value={filters.side}
        options={["ALL", "OVER", "UNDER"] as const}
        onChange={(side) => onChange({ ...filters, side })}
      />
      <Segmented
        label="Show"
        value={filters.show}
        options={["ALL", "BETS", "GRADED"] as const}
        onChange={(show) => onChange({ ...filters, show })}
      />
      <input
        type="text"
        value={filters.find}
        onChange={(e) => onChange({ ...filters, find: e.target.value })}
        placeholder="Find pitcher or team…"
        aria-label="Find pitcher or team"
        className="h-[30px] w-44 rounded-md border border-line bg-surface px-2.5 text-[12px] text-ink placeholder:text-ink-muted focus:border-line-strong focus:outline-none"
      />
    </div>
  );
}
