import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Outs Recorded — Strikeouts Terminal",
  description:
    "Total outs recorded — a separate market with its own model, its own ledger, and its own numbers. Never blended with strikeouts.",
};

// A deliberately static page: the outs model has no served board yet,
// and pretending otherwise would be a fabricated number. What this page
// does today is stake out the SEPARATION (operator directive
// 2026-08-24): its own model, its own ledger rows (market="OUTS"), its
// own future payload — nothing here ever mixes into the strikeouts
// pages, and nothing there mixes into this one.
export default function OutsPage() {
  return (
    <div className="space-y-5">
      <div>
        <div className="flex items-center gap-2.5">
          <h1 className="text-2xl font-bold tracking-tight">Outs Recorded</h1>
          <span className="rounded-md bg-accent/15 px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wider text-accent">
            separate market
          </span>
        </div>
        <p className="mt-1 max-w-2xl text-sm text-ink-secondary">
          How many outs the starter records before the manager pulls him —
          a bet on the length of the leash, not on strikeouts. It is a
          sibling product to the strikeouts model: same discipline, same
          money rules, <span className="font-semibold text-ink">entirely
          separate numbers</span>. No figure on this page will ever be
          combined with a strikeouts figure, and vice versa.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <section className="rounded-xl border border-line bg-surface p-5">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
            Where it stands
          </h2>
          <ul className="mt-3 space-y-2.5 text-sm text-ink-secondary">
            <li>
              <span className="font-semibold text-over">Research model built.</span>{" "}
              An inning-by-inning model of when starts end. It beats the
              honest baseline by 5&ndash;7% in every out-of-sample test
              direction, and it reproduces the real pattern that ~65% of
              starts end on a full inning.
            </li>
            <li>
              <span className="font-semibold text-over">Prices captured daily</span>{" "}
              since 2026-08-08 &mdash; the one input that can never be
              backfilled. Every closing snapshot banks toward scoring the
              model against the market.
            </li>
            <li>
              <span className="font-semibold text-under">Not calibrated for money yet.</span>{" "}
              Its probability estimates still miss by more than the edge a
              bet would need, so it prices nothing. That gate opens only
              after its own calibration passes on captured prices.
            </li>
          </ul>
        </section>

        <section className="rounded-xl border border-line bg-surface p-5">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
            The separation, enforced
          </h2>
          <ul className="mt-3 space-y-2.5 text-sm text-ink-secondary">
            <li>
              Every ledger row carries a{" "}
              <span className="figure text-ink">market</span> tag; a
              pick&rsquo;s identity is (game, pitcher,{" "}
              <span className="figure text-ink">market</span>, line).
            </li>
            <li>
              The strikeouts site reads the ledger through a filter that
              admits only strikeout rows &mdash; the first outs pick
              cannot leak into the P&amp;L, performance, or model pages
              by construction.
            </li>
            <li>
              One bet per pitcher per slate across the two markets: a
              strikeouts bet and an outs bet on the same arm settle off
              the same start and are treated as one exposure.
            </li>
            <li>
              When the outs model earns a board, it renders here &mdash;
              its own slate, its own record, its own calibration page.
            </li>
          </ul>
        </section>
      </div>

      <p className="text-xs text-ink-muted">
        Nothing on this page is a pick. The outs model bets nothing until
        it passes the same gates the strikeouts model is held to.
      </p>
    </div>
  );
}
