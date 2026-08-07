"use client";

import * as React from "react";
import { Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useDashboard } from "@/lib/data-context";
import { DateStepper } from "@/components/date-stepper";
import {
  FilterBar,
  DEFAULT_FILTERS,
  loadStoredFilters,
  persistFilters,
  type SlateFilters,
} from "@/components/filter-bar";
import { PickCard } from "@/components/pick-card";
import { HeroStats } from "@/components/stat-tiles";
import { Scoreboard } from "@/components/scoreboard";

function SlateView() {
  const { data, error } = useDashboard();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [filters, setFilters] = React.useState<SlateFilters>(DEFAULT_FILTERS);
  const [expanded, setExpanded] = React.useState<Set<string>>(new Set());

  React.useEffect(() => {
    const fromUrl: Partial<SlateFilters> = {};
    const side = searchParams.get("side");
    const show = searchParams.get("show");
    const find = searchParams.get("find");
    if (side) fromUrl.side = side as SlateFilters["side"];
    if (show) fromUrl.show = show as SlateFilters["show"];
    if (find) fromUrl.find = find;
    setFilters(
      Object.keys(fromUrl).length > 0
        ? { ...DEFAULT_FILTERS, ...fromUrl }
        : loadStoredFilters(),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateFilters = (f: SlateFilters) => {
    setFilters(f);
    persistFilters(f);
  };

  if (error) {
    return (
      <div className="rounded-card border border-line bg-surface p-8 text-center text-sm text-ink-muted">
        Could not load data.json — run{" "}
        <code className="figure rounded bg-surface-2 px-1.5 py-0.5 text-xs">
          python tools/dashboard_data.py
        </code>
      </div>
    );
  }
  if (!data) {
    return (
      <div className="py-24 text-center text-sm text-ink-muted">Loading…</div>
    );
  }

  const dates = data.available_dates;
  const urlDate = searchParams.get("date");
  const current =
    urlDate && dates.includes(urlDate) ? urlDate : dates[0] ?? data.today_et;
  const slate = data.slates[current];

  const setDate = (d: string) => {
    const url = new URL(window.location.href);
    if (d === dates[0]) url.searchParams.delete("date");
    else url.searchParams.set("date", d);
    router.replace(url.pathname + url.search, { scroll: false });
  };

  const toggle = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  const q = filters.find.trim().toLowerCase();
  const pitchers = (slate?.pitchers ?? []).filter((p) => {
    const hasBet =
      (p.pick?.bet_placed && (p.pick?.units_risked ?? 0) > 0) ||
      p.ladder.some((r) => r.status === "bet" || r.pick?.bet_placed);
    if (filters.show === "BETS" && !hasBet) return false;
    if (filters.show === "GRADED" && !p.pick?.graded_result) return false;
    if (filters.side !== "ALL") {
      const side = p.pick?.pick_side || p.best_side;
      if (side !== filters.side) return false;
    }
    if (q) {
      const hay =
        `${p.pitcher_name} ${p.pitcher_team} ${p.opponent_team}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const topKey = (() => {
    let best: string | null = null;
    let bestEdge = -1;
    for (const p of slate?.pitchers ?? []) {
      const hasBet = p.pick?.bet_placed && (p.pick?.units_risked ?? 0) > 0;
      if (hasBet && (p.edge_best ?? 0) > bestEdge) {
        bestEdge = p.edge_best ?? 0;
        best = `${p.pitcher_id}-${p.line}`;
      }
    }
    return best;
  })();

  const totalUnits = (slate?.pitchers ?? []).reduce((acc, p) => {
    let u = p.pick?.bet_placed ? (p.pick?.units_risked ?? 0) : 0;
    for (const r of p.ladder) {
      if (r.status === "bet" || r.pick?.bet_placed) u += r.pick?.units_risked ?? r.units_risked ?? 0;
    }
    return acc + u;
  }, 0);

  return (
    <div className="space-y-5">
      <HeroStats
        record={data.record}
        pnl={data.pnl.total.value}
        roi={data.pnl.roi}
        clv={data.performance.clv}
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <DateStepper
          dates={dates}
          current={current}
          todayEt={data.today_et}
          onChange={setDate}
        />
        <FilterBar filters={filters} onChange={updateFilters} />
      </div>

      {slate?.reconstructed && (
        <p className="rounded-card border border-accent/20 bg-accent-dim px-4 py-2.5 text-xs leading-relaxed text-ink-secondary">
          <span className="font-semibold text-accent">Reconstructed slate.</span>{" "}
          {slate.note}
        </p>
      )}

      {!slate || slate.pitchers.length === 0 ? (
        <div className="rounded-card border border-line bg-surface p-10 text-center text-sm text-ink-muted">
          No slate stored for {current}.<br />
          Run{" "}
          <code className="figure rounded bg-surface-2 px-1.5 py-0.5 text-xs">
            python run.py predict
          </code>
        </div>
      ) : (
        <>
          {/* Scoreboard shows the WHOLE board, unfiltered, so the filter
              chips above never hide a play from the at-a-glance view. */}
          <Scoreboard
            slate={slate}
            onSelect={(key) => {
              if (!expanded.has(key)) toggle(key);
              // Deferred past React's commit so the expand has laid out
              // before we measure. setTimeout rather than
              // requestAnimationFrame on purpose: rAF is suspended while
              // the tab is not compositing, so a frame-based deferral
              // silently never runs in a background tab.
              //
              // Smooth scrolling is also frame-driven, so it is used only
              // when the user has not asked for reduced motion — that
              // check earns its keep twice here.
              setTimeout(() => {
                const el = document.getElementById(`pick-${key}`);
                if (!el) return;
                const smooth = !window.matchMedia(
                  "(prefers-reduced-motion: reduce)",
                ).matches;
                el.scrollIntoView({
                  behavior: smooth ? "smooth" : "auto",
                  block: "center",
                });
              }, 0);
            }}
          />

          <div className="flex items-baseline justify-between">
            <span className="text-[11px] font-semibold uppercase tracking-[0.12em] text-ink-muted">
              {current} board
            </span>
            <span className="figure text-[11px] text-ink-secondary">
              {pitchers.length}/{slate.pitcher_count} pitchers ·{" "}
              {slate.bet_count} bet · {totalUnits.toFixed(1)}u risked
            </span>
          </div>
          <div className="stagger flex flex-col gap-2.5">
            {pitchers.map((p) => {
              const key = `${p.pitcher_id}-${p.line}`;
              return (
                // Anchor so a scoreboard stub can jump to its card.
                <div key={key} id={`pick-${key}`}>
                <PickCard
                  p={p}
                  expanded={expanded.has(key)}
                  onToggle={() => toggle(key)}
                  isTop={key === topKey}
                />
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<div className="py-24 text-center text-sm text-ink-muted">Loading…</div>}>
      <SlateView />
    </Suspense>
  );
}
