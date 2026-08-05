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

PNL_FIELD_NAMES = {
    "profit_loss_units",
    "daily_pnl",
    "cumulative_pnl",
    "total",
    "total_risked",
}


class BasisViolation(Exception):
    pass


def validate_tagged_value(value, path: str) -> list[str]:
    """Check that a tagged P&L value has the correct shape and basis."""
    errors = []

    if isinstance(value, (int, float)):
        errors.append(
            f"BARE FLOAT at {path}: {value} — must be "
            f'{{"value": {value}, "basis": "{BASIS_LABEL}"}}'
        )
        return errors

    if not isinstance(value, dict):
        errors.append(f"UNEXPECTED TYPE at {path}: {type(value).__name__}")
        return errors

    if "value" not in value:
        errors.append(f"MISSING 'value' key at {path}")

    if "basis" not in value:
        errors.append(f"MISSING 'basis' key at {path}")
    elif value["basis"] != BASIS_LABEL:
        errors.append(
            f"WRONG BASIS at {path}: got '{value['basis']}', "
            f"expected '{BASIS_LABEL}'"
        )

    return errors


def walk_and_validate(obj, path: str = "$") -> list[str]:
    """Recursively walk JSON and validate all P&L fields."""
    errors = []

    if isinstance(obj, dict):
        for key, val in obj.items():
            child_path = f"{path}.{key}"
            if key in PNL_FIELD_NAMES:
                errors.extend(validate_tagged_value(val, child_path))
            else:
                errors.extend(walk_and_validate(val, child_path))

    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            errors.extend(walk_and_validate(item, f"{path}[{i}]"))

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
