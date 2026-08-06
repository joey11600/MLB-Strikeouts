"use client";

import * as React from "react";
import type { DashboardData } from "./types";

interface DataState {
  data: DashboardData | null;
  error: string | null;
}

const DataContext = React.createContext<DataState>({ data: null, error: null });

// The Railway worker serves the live payload straight off its volume,
// so picks appear the moment the pipeline writes them — no rebuild
// wait. The static file shipped with the build is the fallback for
// when the worker is unreachable.
const LIVE_DATA_URL =
  process.env.NEXT_PUBLIC_DATA_URL ||
  "https://worker-production-036c.up.railway.app/data.json";

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
  const [state, setState] = React.useState<DataState>({ data: null, error: null });

  React.useEffect(() => {
    let alive = true;
    loadData()
      .then((data) => alive && setState({ data: data as DashboardData, error: null }))
      .catch((e) => alive && setState({ data: null, error: String(e) }));
    return () => {
      alive = false;
    };
  }, []);

  return <DataContext.Provider value={state}>{children}</DataContext.Provider>;
}

export function useDashboard(): DataState {
  return React.useContext(DataContext);
}
