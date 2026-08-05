"""Cluster demotion applicator.

Reads data/cluster_demotions.json (operator-maintained), sets
bet_placed='N' on matching ungraded rows. Does NOT change pick_side,
pick_strength, or pick_label for transparency. Already-graded rows
are never touched. Idempotent and reversible.

Phase 4 will implement.
"""


def main():
    raise NotImplementedError("Phase 4")


if __name__ == "__main__":
    main()
