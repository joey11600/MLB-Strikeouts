"use client";

// Adapted from the 21st.dev component @ssicevs/market-snapshot (installed
// via MCP retrieval): animated line with pointer scrubbing, hovered value
// reading into the header with its delta, and a period switcher with a
// sliding underline. Re-purposed from stock prices to cumulative P&L and
// re-tokened to Dark Terminal.
import { useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import type { DailyPoint } from "@/lib/types";
import { pnlStr } from "@/lib/utils";

const EASE = [0.16, 1, 0.3, 1] as const;
const HAIRLINE = "rgba(255,255,255,0.06)";
const W = 640;
const H = 190;
const PAD_X = 10;
const PAD_TOP = 12;
const PAD_BOT = 34;

const PERIODS = ["7D", "30D", "Season"] as const;
type Period = (typeof PERIODS)[number];

export function PnlChart({ daily }: { daily: DailyPoint[] }) {
  const reduced = useReducedMotion();
  const svgRef = useRef<SVGSVGElement>(null);
  const [period, setPeriod] = useState<Period>("Season");
  const [hover, setHover] = useState<number | null>(null);

  const data = useMemo(() => {
    if (period === "Season") return daily;
    const n = period === "7D" ? 7 : 30;
    return daily.slice(-n);
  }, [daily, period]);

  if (data.length === 0) {
    return (
      <div className="rounded-card border border-line bg-surface p-6 text-sm text-ink-muted">
        No graded slates yet — the curve starts with the first result.
      </div>
    );
  }

  const cums = data.map((d) => d.cumulative_pnl.value);
  const dailies = data.map((d) => d.daily_pnl.value);
  const hi = Math.max(...cums, ...dailies, 0.5);
  const lo = Math.min(...cums, ...dailies, -0.5);
  const span = hi - lo || 1;

  const innerW = W - 2 * PAD_X;
  const innerH = H - PAD_TOP - PAD_BOT;
  const x = (i: number) =>
    data.length === 1
      ? W / 2
      : PAD_X + (i / (data.length - 1)) * innerW;
  const y = (v: number) => PAD_TOP + (1 - (v - lo) / span) * innerH;
  const zeroY = y(0);

  const path = cums
    .map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(v).toFixed(1)}`)
    .join(" ");

  const active = hover ?? data.length - 1;
  const point = data[active];
  const cum = point.cumulative_pnl.value;
  const day = point.daily_pnl.value;
  const lineColor = cum >= 0 ? "var(--color-over)" : "var(--color-under)";

  const barW = Math.max(3, Math.min(22, (innerW / data.length) * 0.45));

  const onMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const bounds = svgRef.current?.getBoundingClientRect();
    if (!bounds) return;
    setHover(
      Math.max(
        0,
        Math.min(
          data.length - 1,
          Math.round(((event.clientX - bounds.left) / bounds.width) * (data.length - 1)),
        ),
      ),
    );
  };

  return (
    <div className="overflow-hidden rounded-card border border-line bg-surface">
      <div className="flex items-start justify-between px-5 pb-1 pt-5">
        <div>
          <p className="text-[11px] font-medium text-ink-muted">
            Cumulative P&L {hover != null && <span className="figure">· {point.date}</span>}
          </p>
          <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
            <AnimatePresence mode="popLayout" initial={false}>
              <motion.span
                key={cum.toFixed(2)}
                className="figure text-2xl font-semibold tracking-tight"
                style={{ color: lineColor }}
                initial={reduced ? false : { opacity: 0, y: 4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.18 }}
              >
                {pnlStr(cum)}
              </motion.span>
            </AnimatePresence>
            <span
              className="figure text-[11px] font-semibold"
              style={{ color: day >= 0 ? "var(--color-over)" : "var(--color-under)" }}
            >
              day {pnlStr(day)} · {point.w}W-{point.l}L
            </span>
          </div>
        </div>
        <span className="figure text-[11px] font-medium text-ink-muted">
          flat 100u
        </span>
      </div>

      <div className="relative px-2">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${W} ${H}`}
          className="w-full touch-none"
          onPointerMove={onMove}
          onPointerLeave={() => setHover(null)}
          role="img"
          aria-label={`Cumulative profit and loss ${pnlStr(cum)} over ${period}`}
        >
          <line
            x1={PAD_X}
            x2={W - PAD_X}
            y1={zeroY}
            y2={zeroY}
            stroke={HAIRLINE}
            strokeDasharray="3 5"
          />
          {data.map((d, i) => {
            const v = d.daily_pnl.value;
            const by = y(Math.max(v, 0));
            const bh = Math.abs(y(v) - zeroY);
            return (
              <rect
                key={d.date}
                x={x(i) - barW / 2}
                y={v >= 0 ? by : zeroY}
                width={barW}
                height={Math.max(bh, 1)}
                rx={2}
                fill={v >= 0 ? "var(--color-over)" : "var(--color-under)"}
                opacity={i === active && hover != null ? 0.55 : 0.22}
              />
            );
          })}
          <motion.path
            key={period}
            d={path}
            fill="none"
            stroke={lineColor}
            strokeWidth="2"
            strokeLinecap="round"
            initial={reduced ? false : { pathLength: 0, opacity: 0 }}
            animate={{ pathLength: 1, opacity: 1 }}
            transition={{ duration: 0.65, ease: EASE }}
          />
          {hover != null && (
            <>
              <line
                x1={x(active)}
                x2={x(active)}
                y1={PAD_TOP}
                y2={H - PAD_BOT + 14}
                stroke="rgba(255,255,255,0.16)"
              />
            </>
          )}
          <circle
            cx={x(active)}
            cy={y(cum)}
            r="3.5"
            fill={lineColor}
            stroke="var(--color-surface)"
            strokeWidth="1.5"
          />
          <text
            x={PAD_X}
            y={H - 8}
            className="figure"
            fontSize="10"
            fill="var(--color-ink-muted)"
          >
            {data[0].date}
          </text>
          <text
            x={W - PAD_X}
            y={H - 8}
            textAnchor="end"
            className="figure"
            fontSize="10"
            fill="var(--color-ink-muted)"
          >
            {data[data.length - 1].date}
          </text>
        </svg>
      </div>

      <div className="border-t border-line px-4 pb-3.5 pt-2.5">
        <div className="flex items-center gap-1">
          {PERIODS.map((item) => {
            const activePeriod = item === period;
            return (
              <button
                key={item}
                type="button"
                onClick={() => {
                  setPeriod(item);
                  setHover(null);
                }}
                className="relative rounded-md px-2.5 py-1.5 text-[11px] font-semibold transition-colors"
                style={{
                  color: activePeriod ? "var(--color-ink)" : "var(--color-ink-muted)",
                  background: activePeriod ? "rgba(255,255,255,0.055)" : "transparent",
                }}
              >
                {item}
                {activePeriod && (
                  <motion.span
                    layoutId="pnl-period-tab"
                    className="absolute inset-x-1.5 -bottom-[3px] h-[2px] rounded-t-full"
                    style={{ background: lineColor }}
                    transition={{ type: "spring", stiffness: 400, damping: 34 }}
                  />
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
