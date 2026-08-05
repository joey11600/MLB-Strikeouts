"use client";

import { useDashboard } from "@/lib/data-context";
import { relTime } from "@/lib/utils";

export function HeaderMeta() {
  const { data } = useDashboard();
  if (!data) return <span />;
  return (
    <span className="figure">generated {relTime(data.generated_at)}</span>
  );
}
