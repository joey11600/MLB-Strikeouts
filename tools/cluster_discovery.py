"""Loss cluster discovery — read-only scanner.

Buckets graded bets by (side, prob_band), (side, prob_band, line_band),
etc. at multiple resolutions. Surfaces underperforming clusters ranked
by hit rate and P&L.

Never writes, never sends alerts. Port of NRFI Terminal's version.

Phase 4 will implement.
"""


def main():
    raise NotImplementedError("Phase 4")


if __name__ == "__main__":
    main()
