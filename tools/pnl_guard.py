"""FlatUnits / CumulativeUnits compile-time guard.

Every P&L value that reaches the dashboard must be tagged with a basis
label. A bare float is a bug — it could be flat-basis or moving-basis,
and the renderer can't tell the difference.

This module:
  1. Validates dashboard JSON before it's written.
  2. Rejects any P&L field that is a bare number (not tagged).
  3. Rejects any tagged value whose basis doesn't match the
     canonical BASIS_LABEL.
  4. Catches moving-basis contamination at build time, not at render.

Usage:
    python tools/pnl_guard.py                  # validate dashboard/data.json
    python tools/pnl_guard.py --file FILE      # validate a specific file

Called automatically by dashboard_data.py before writing output.
"""
import json
import sys
from pathlib import Path

BASIS_LABEL = "flat_100u"

# Counterfactual units from tools/shadow.py: bets the model WOULD have
# placed under a different trust weight. Real money never moved on any
# of them. They carry their own basis and live under the "shadow"
# subtree, and the guard enforces the separation in BOTH directions --
# a real basis inside shadow, or a shadow basis outside it, is a
# violation. Mixing the two would let a counterfactual profit be read
# as money the operator actually made, which is the single most
# dangerous confusion this file exists to prevent.
SHADOW_BASIS_LABEL = "shadow_flat_100u"
SHADOW_SUBTREE = "shadow"

PNL_FIELD_NAMES = {
    "profit_loss_units",
    "daily_pnl",
    "cumulative_pnl",
    "total",
    "total_risked",
    "pnl",
    "units_staked",
}


class BasisViolation(Exception):
    pass


def validate_tagged_value(value, path: str, expected: str = BASIS_LABEL) -> list[str]:
    """Check that a tagged P&L value has the correct shape and basis."""
    errors = []

    if isinstance(value, (int, float)):
        errors.append(
            f"BARE FLOAT at {path}: {value} — must be "
            f'{{"value": {value}, "basis": "{expected}"}}'
        )
        return errors

    if not isinstance(value, dict):
        errors.append(f"UNEXPECTED TYPE at {path}: {type(value).__name__}")
        return errors

    if "value" not in value:
        errors.append(f"MISSING 'value' key at {path}")

    if "basis" not in value:
        errors.append(f"MISSING 'basis' key at {path}")
    elif value["basis"] != expected:
        errors.append(
            f"WRONG BASIS at {path}: got '{value['basis']}', "
            f"expected '{expected}'"
        )

    return errors


def walk_and_validate(obj, path: str = "$", in_shadow: bool = False) -> list[str]:
    """Recursively walk JSON and validate all P&L fields.

    in_shadow flips the expected basis once we descend into the shadow
    subtree. Enforced both ways: real units inside shadow, and shadow
    units anywhere outside it, are equally violations.
    """
    errors = []
    expected = SHADOW_BASIS_LABEL if in_shadow else BASIS_LABEL

    if isinstance(obj, dict):
        for key, val in obj.items():
            child_path = f"{path}.{key}"
            child_shadow = in_shadow or key == SHADOW_SUBTREE
            # Some names are containers in one place and tagged values in
            # another: the real payload's `pnl` holds {total,
            # total_risked, roi}, while shadow's `pnl` IS the tagged
            # value. A dict carrying NEITHER 'value' nor 'basis' is a
            # container -- recurse. Carrying either one means it was
            # meant to be tagged, so validate it strictly and keep
            # catching half-tagged values.
            is_container = (
                isinstance(val, dict)
                and "value" not in val
                and "basis" not in val
            )
            if key in PNL_FIELD_NAMES and not is_container:
                errors.extend(validate_tagged_value(
                    val, child_path,
                    SHADOW_BASIS_LABEL if child_shadow else BASIS_LABEL))
            else:
                errors.extend(walk_and_validate(val, child_path, child_shadow))

    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            errors.extend(walk_and_validate(item, f"{path}[{i}]", in_shadow))

    return errors


def validate_dashboard_json(data: dict) -> list[str]:
    """Validate a dashboard data dict. Returns list of errors (empty = pass)."""
    errors = []

    if data.get("basis") != BASIS_LABEL:
        errors.append(
            f"Top-level basis mismatch: got '{data.get('basis')}', "
            f"expected '{BASIS_LABEL}'"
        )

    errors.extend(walk_and_validate(data))
    return errors


def guard(data: dict):
    """Raise BasisViolation if any P&L value fails the guard."""
    errors = validate_dashboard_json(data)
    if errors:
        msg = f"FlatUnits guard FAILED — {len(errors)} violation(s):\n"
        msg += "\n".join(f"  - {e}" for e in errors)
        raise BasisViolation(msg)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate dashboard P&L basis tags")
    parser.add_argument("--file", default="dashboard/data.json", help="JSON file to validate")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"File not found: {path}")
        sys.exit(1)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    errors = validate_dashboard_json(data)
    if errors:
        print(f"FAIL — {len(errors)} violation(s):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print(f"PASS — all P&L values tagged with '{BASIS_LABEL}'")


if __name__ == "__main__":
    main()
