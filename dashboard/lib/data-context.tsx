"use client";

import * as React from "react";
import type { DashboardData } from "./types";

interface DataState {
  data: DashboardData | null;
  error: string | null;
  /** ms epoch of the last SUCCESSFUL load, null until the first one. */
  updatedAt: number | null;
}

const DataContext = React.createContext<DataState>({
  data: null,
  error: null,
  updatedAt: null,
});

// The Railway worker serves the live payload straight off its volume,
// so picks appear the moment the pipeline writes them — no rebuild
// wait. The static file shipped with the build is the fallback for
// when the worker is unreachable.
const LIVE_DATA_URL =
  process.env.NEXT_PUBLIC_DATA_URL ||
  "https://worker-production-036c.up.railway.app/data.json";

// The worker polls live games every 30s, so a minute is the coarsest
// interval that still surfaces a finished start promptly, and it is
// cheap: one conditional-free GET of a ~600KB payload per open tab.
const REFRESH_MS = 60_000;

async function loadData(): Promise<unknown> {
  try {
    const live = await fetch(LIVE_DATA_URL, { cache: "no-store" });
    if (live.ok) return await live.json();
  } catch {
    // fall through to the bundled snapshot
  }
  const stat = await fetch("/data.json", { cache: "no-store" });
  if (!stat.ok) throw new Error(`HTTP ${stat.status}`);
  return await stat.json();
}

export function DataProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<DataState>({
    data: null,
    error: null,
    updatedAt: null,
  });

  React.useEffect(() => {
    let alive = true;
    // One flight at a time. A slow worker plus a tab that fires focus
    // and the interval together would otherwise stack requests and let
    // an older response land last.
    let inFlight = false;

    const refresh = async () => {
      if (!alive || inFlight) return;
      inFlight = true;
      try {
        const data = (await loadData()) as DashboardData;
        if (alive) setState({ data, error: null, updatedAt: Date.now() });
      } catch (e) {
        // A failed REFRESH must not replace a board that is already on
        // screen — the terminal runs unattended for hours and a blip
        // against the worker is not a reason to show an error page.
        // Only surface the error when there is nothing to fall back to.
        if (alive) {
          setState((prev) =>
            prev.data
              ? prev
              : { data: null, error: String(e), updatedAt: null },
          );
        }
      } finally {
        inFlight = false;
      }
    };

    void refresh();

    // Left open overnight the board used to show the date it was opened
    // on, forever: it loaded once and never again, so yesterday's slate
    // — and any game that happened to be live at load time — stayed on
    // screen until someone reloaded by hand (A-039). With this, the
    // board also rolls to today on its own when the 09:00 ET job
    // publishes the new slate.
    const timer = setInterval(() => void refresh(), REFRESH_MS);

    // Background tabs get their timers throttled hard, so returning to
    // the tab has to refresh on its own rather than waiting for a tick
    // that may be minutes late. This is the case the operator actually
    // hits: the terminal sits in a background tab all night.
    const onVisible = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    const onFocus = () => void refresh();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onFocus);

    return () => {
      alive = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onFocus);
    };
  }, []);

  return <DataContext.Provider value={state}>{children}</DataContext.Provider>;
}

export function useDashboard(): DataState {
  return React.useContext(DataContext);
}
