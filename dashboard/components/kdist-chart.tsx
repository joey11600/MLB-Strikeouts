"use client";

// Model K-distribution histogram: P(K = k) bars with the book line
// marked (amber, dashed) and the actual strikeout count overlaid after
// grading. NRFI chart geometry idiom (W/H/pad + xFor/yFor).
import { useState } from "react";

const W = 560;
const H = 150;
const PAD_L = 8;
const PAD_R = 8;
const PAD_T = 14;
const PAD_B = 22;

interface Props {
  kDist: number[];
  line: number | null;
  actualK: number | null;
}

export function KDistChart({ kDist, line, actualK }: Props) {
  const [hover, setHover] = useState<number | null>(null);

  if (!kDist || kDist.length === 0) {
    return (
      <p className="py-3 text-xs text-ink-muted">
        Distribution not stored for this slate.
      </p>
    );
  }

  // Trim the tail: show through the last k with mass >= 0.2%, min 12.
  let kMax = 12;
  for (let k = kDist.length - 1; k > 0; k--) {
    if (kDist[k] >= 0.002) {
      kMax = Math.max(12, k + 1);
      break;
    }
  }
  const bars = kDist.slice(0, kMax + 1);
  const peak = Math.max(...bars, 0.01);

  const innerW = W - PAD_L - PAD_R;
  const innerH = H - PAD_T - PAD_B;
  const slot = innerW / (kMax + 1);
  const barW = Math.max(4, slot * 0.68);
  const xFor = (k: number) => PAD_L + k * slot + slot / 2;
  const hFor = (p: number) => (p / peak) * innerH;

  const tail = (k: number) => bars.slice(k).reduce((a, b) => a + b, 0)
    + kDist.slice(kMax + 1).reduce((a, b) => a + b, 0);

  const lineX = line != null ? PAD_L + (line + 0.5) * slot : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={`Model strikeout distribution${line != null ? `, book line ${line}` : ""}${actualK != null ? `, actual ${actualK} strikeouts` : ""}`}
      >
        {bars.map((p, k) => {
          const h = hFor(p);
          const isActual = actualK != null && k === actualK;
          const fill = isActual
            ? "var(--color-over)"
            : hover === k
              ? "rgba(237,237,239,0.55)"
              : "rgba(237,237,239,0.18)";
          return (
            <g key={k}>
              <rect
                x={xFor(k) - barW / 2}
                y={PAD_T + innerH - h}
                width={barW}
                height={Math.max(h, 0.5)}
                rx={2}
                fill={fill}
                onPointerEnter={() => setHover(k)}
                onPointerLeave={() => setHover(null)}
              />
              <text
                x={xFor(k)}
                y={H - 7}
                textAnchor="middle"
                fontSize="9.5"
                className="figure"
                fill={isActual ? "var(--color-over)" : "var(--color-ink-muted)"}
                fontWeight={isActual ? 700 : 400}
              >
                {k}
              </text>
            </g>
          );
        })}
        {lineX != null && lineX < W - PAD_R && (
          <>
            <line
              x1={lineX}
              x2={lineX}
              y1={PAD_T - 4}
              y2={PAD_T + innerH}
              stroke="var(--color-accent)"
              strokeWidth="1.4"
              strokeDasharray="4 3"
            />
            <text
              x={lineX}
              y={PAD_T - 4}
              textAnchor="middle"
              fontSize="9.5"
              className="figure"
              fill="var(--color-accent)"
            >
              {line}
            </text>
          </>
        )}
        {actualK != null && actualK <= kMax && (
          <circle
            cx={xFor(actualK)}
            cy={PAD_T + innerH - hFor(bars[actualK] ?? 0) - 6}
            r="2.6"
            fill="var(--color-over)"
          />
        )}
      </svg>
      {hover != null && (
        <div
          className="figure pointer-events-none absolute top-0 z-10 -translate-x-1/2 whitespace-nowrap rounded-md border border-line bg-surface-2 px-2 py-1 text-[10px] text-ink-secondary shadow-[0_6px_18px_rgba(0,0,0,0.4)]"
          style={{ left: `${((xFor(hover) / W) * 100).toFixed(1)}%` }}
          role="status"
        >
          P(K={hover}) {(bars[hover] * 100).toFixed(1)}% · P(K≥{hover}){" "}
          {(tail(hover) * 100).toFixed(1)}%
        </div>
      )}
    </div>
  );
}
