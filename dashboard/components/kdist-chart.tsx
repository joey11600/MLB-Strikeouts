"use client";

// Model K-distribution histogram.
//
// Three things must read at a glance, in this order:
//   1. WHICH WAY the model leans. The half of the distribution that WINS
//      the bet is tinted in the side's colour and banded, so direction
//      lands before any number does. Previously every bar was the same
//      grey and the chart said nothing about the pick it sat under.
//   2. WHERE THE BOOK LINE SITS -- amber dashed vertical, labelled.
//   3. WHERE THE PROJECTION SITS -- a caret on the axis. Deliberately a
//      different SHAPE from the line, so the two are never confused when
//      they sit close together (they usually do).
//
// Palette rule: hue never carries meaning alone. The band is labelled in
// words, the line is labelled, the caret is labelled.
//
// The band's percentage is the area of THIS curve — the raw model — and
// says so ("62% OF CURVE"). It is deliberately not the headline number on
// the card, which is the market-blended 52.9%. Two unlabelled "chance
// this wins" figures on one card is how a board stops being trusted.
//
// The line arrives from the slate JSON as a STRING ("6.5"), so it is
// coerced. It used to be used raw: `PAD_L + (line + 0.5) * slot` on a
// string is "6.50.5" * slot -> NaN, and `NaN < W - PAD_R` is false, so
// the marker silently vanished with no error anywhere.
import { useState } from "react";

const W = 560;
const H = 168;
const PAD_L = 8;
const PAD_R = 8;
const PAD_T = 22;
const PAD_B = 34;

interface Props {
  kDist: number[];
  line: number | string | null;
  actualK: number | null;
  side?: string;
  /** Model's expected strikeouts — drawn as the axis caret. */
  projection?: number | null;
  /** "won" = green, "lost" = red (every bet on the card lost), null = neutral */
  outcome?: "won" | "lost" | null;
}

export function KDistChart({
  kDist,
  line,
  actualK,
  side,
  projection,
  outcome,
}: Props) {
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
  const baseY = PAD_T + innerH;

  const tail = (k: number) =>
    bars.slice(k).reduce((a, b) => a + b, 0) +
    kDist.slice(kMax + 1).reduce((a, b) => a + b, 0);

  const lineNum = line == null || line === "" ? null : Number(line);
  const hasLine = lineNum != null && Number.isFinite(lineNum);
  const lineX = hasLine ? PAD_L + (lineNum! + 0.5) * slot : null;

  const s = (side || "").trim().toUpperCase();
  const isUnder = s.startsWith("UNDER") || s === "U";
  const isOver = s.startsWith("OVER") || s === "O";
  const sided = hasLine && (isUnder || isOver);
  const sideColor = isUnder ? "var(--color-under)" : "var(--color-over)";

  // A k WINS the bet when it falls on the side we backed. The line is
  // always a half-number, so no push case reaches here.
  const winsBet = (k: number) =>
    sided && (isUnder ? k < lineNum! : k > lineNum!);

  const winProb = sided
    ? bars.reduce((acc, p, k) => (winsBet(k) ? acc + p : acc), 0) +
      (isOver ? kDist.slice(kMax + 1).reduce((a, b) => a + b, 0) : 0)
    : null;

  // The winning band spans from the line to whichever edge that side runs to.
  const bandX = sided
    ? isUnder
      ? { x: PAD_L, w: Math.max(lineX! - PAD_L, 0) }
      : { x: lineX!, w: Math.max(W - PAD_R - lineX!, 0) }
    : null;

  const projNum =
    projection == null || !Number.isFinite(Number(projection))
      ? null
      : Number(projection);
  const projX = projNum != null ? PAD_L + (projNum + 0.5) * slot : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        role="img"
        aria-label={
          `Model strikeout distribution` +
          (hasLine ? `, book line ${lineNum}` : "") +
          (sided && winProb != null
            ? `, ${s} wins on ${(winProb * 100).toFixed(0)} percent of outcomes`
            : "") +
          (projNum != null ? `, projection ${projNum.toFixed(1)}` : "") +
          (actualK != null ? `, actual ${actualK} strikeouts` : "")
        }
      >
        {/* The winning half, washed. Direction before numbers. */}
        {bandX && bandX.w > 0 && (
          <rect
            x={bandX.x}
            y={PAD_T}
            width={bandX.w}
            height={innerH}
            fill={sideColor}
            opacity={0.07}
          />
        )}

        {bars.map((p, k) => {
          const h = hFor(p);
          const isActual = actualK != null && k === actualK;
          const actualColor =
            outcome === "lost"
              ? "var(--color-under)"
              : outcome === "won"
                ? "var(--color-over)"
                : "rgba(237,237,239,0.85)";
          const win = winsBet(k);

          let fill = "rgba(237,237,239,0.16)";
          let fillOpacity = 1;
          if (isActual) {
            fill = actualColor;
          } else if (hover === k) {
            fill = win ? sideColor : "rgba(237,237,239,0.55)";
            fillOpacity = win ? 0.95 : 1;
          } else if (win) {
            fill = sideColor;
            fillOpacity = 0.62;
          }

          return (
            <g key={k}>
              <rect
                x={xFor(k) - barW / 2}
                y={PAD_T + innerH - h}
                width={barW}
                height={Math.max(h, 0.5)}
                rx={2}
                fill={fill}
                fillOpacity={fillOpacity}
                onPointerEnter={() => setHover(k)}
                onPointerLeave={() => setHover(null)}
              />
              <text
                x={xFor(k)}
                y={H - 19}
                textAnchor="middle"
                fontSize="9.5"
                className="figure"
                fill={
                  isActual
                    ? actualColor
                    : win
                      ? sideColor
                      : "var(--color-ink-muted)"
                }
                fontWeight={isActual ? 700 : win ? 600 : 400}
              >
                {k}
              </text>
            </g>
          );
        })}

        {/* Book line: the threshold the bet is settled against. */}
        {lineX != null && lineX > PAD_L && lineX < W - PAD_R && (
          <line
            x1={lineX}
            x2={lineX}
            y1={PAD_T - 6}
            y2={baseY}
            stroke="var(--color-accent)"
            strokeWidth="1.4"
            strokeDasharray="4 3"
          />
        )}

        {/* Projection: a caret, not a line — different shape so the two
            never read as the same thing when they sit close together. */}
        {projX != null && projX > PAD_L && projX < W - PAD_R && (
          <>
            <path
              d={`M ${projX} ${baseY + 1} l 4.5 6 l -9 0 z`}
              fill={sided ? sideColor : "rgba(237,237,239,0.7)"}
            />
            <text
              x={Math.min(Math.max(projX, PAD_L + 26), W - PAD_R - 26)}
              y={H - 2}
              textAnchor="middle"
              fontSize="8.5"
              className="figure"
              fill="var(--color-ink-muted)"
            >
              proj {projNum!.toFixed(1)}
            </text>
          </>
        )}

        {/* Words, so hue is never doing the work alone. */}
        {sided && winProb != null && bandX && bandX.w > 54 && (
          <text
            x={isUnder ? bandX.x + 4 : bandX.x + bandX.w - 4}
            y={PAD_T - 8}
            textAnchor={isUnder ? "start" : "end"}
            fontSize="9.5"
            className="figure"
            fill={sideColor}
            fontWeight={700}
          >
            {isUnder ? "◀ " : ""}
            {s} {lineNum} WINS · {(winProb * 100).toFixed(0)}% OF CURVE
            {isOver ? " ▶" : ""}
          </text>
        )}
        {hasLine && !sided && (
          <text
            x={Math.min(Math.max(lineX!, PAD_L + 24), W - PAD_R - 24)}
            y={PAD_T - 8}
            textAnchor="middle"
            fontSize="9.5"
            className="figure"
            fill="var(--color-accent)"
            fontWeight={600}
          >
            {lineNum}
          </text>
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
          {sided && (
            <>
              {" · "}
              <span style={{ color: winsBet(hover) ? sideColor : undefined }}>
                {winsBet(hover) ? "wins" : "loses"}
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}
