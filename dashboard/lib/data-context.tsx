"use client";

import * as React from "react";
import type { DashboardData } from "./types";

interface DataState {
  data: DashboardData | null;
  error: string | null;
}

const DataContext = React.createContext<DataState>({ data: null, error: null });

export function DataProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = React.useState<DataState>({ data: null, error: null });

  React.useEffect(() => {
    let alive = true;
    fetch("/data.json", { cache: "no-store" })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => alive && setState({ data, error: null }))
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
