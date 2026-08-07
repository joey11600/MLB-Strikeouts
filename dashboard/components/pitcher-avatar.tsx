"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

/* Pitcher headshot, keyed on the MLBAM id we already store.
 *
 * No new data and no new request path: the id in every slate row IS
 * MLB's person id, so the URL is derivable.
 *
 * Notes that matter:
 *  - An unknown id returns 200 with a generic silhouette rather than a
 *    404, so there is nothing to catch. onError still covers a network
 *    failure or a blocked CDN, where we fall back to initials rather
 *    than a broken-image glyph.
 *  - The ring carries the SIDE, reinforcing the same signal the card
 *    rail and the arrow already give. Three quiet cues beat one loud one.
 *  - Plain <img>, not next/image: the dashboard is a static export, so
 *    there is no optimiser at runtime to resize a remote file.
 */

const SIZE = 120; // MLB's smallest square spot; ~7 KB each

export function PitcherAvatar({
  pitcherId,
  name,
  side,
  className,
}: {
  pitcherId: number | null;
  name: string;
  side?: string;
  className?: string;
}) {
  const [failed, setFailed] = React.useState(false);

  const initials = React.useMemo(() => {
    const parts = (name || "").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : ""))
      .toUpperCase();
  }, [name]);

  const ring =
    side === "OVER"
      ? "ring-over/50"
      : side === "UNDER"
        ? "ring-under/50"
        : "ring-line-strong";

  const base = cn(
    "relative shrink-0 overflow-hidden rounded-full bg-surface-2 ring-1",
    ring,
    className,
  );

  if (pitcherId == null || failed) {
    return (
      <span
        className={cn(base, "flex items-center justify-center")}
        aria-hidden
      >
        <span className="figure text-[10px] font-semibold text-ink-muted">
          {initials}
        </span>
      </span>
    );
  }

  return (
    <span className={base}>
      <img
        src={`https://midfield.mlbstatic.com/v1/people/${pitcherId}/spots/${SIZE}`}
        alt=""
        aria-hidden
        loading="lazy"
        decoding="async"
        width={SIZE}
        height={SIZE}
        onError={() => setFailed(true)}
        // Headshots are shot on a bright neutral card. Knocking back the
        // saturation and lifting contrast a touch stops 20 of them
        // shouting over a near-black board.
        className="h-full w-full object-cover object-top opacity-90 saturate-[0.85] contrast-[1.05]"
      />
    </span>
  );
}
