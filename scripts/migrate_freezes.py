"""One-shot migration: backfill legacy data/freezes/*.json with the new
action/action_outcome/invoker_instruction_text fields introduced by the
multi-action pipeline.

Legacy freezes have only `verdict_label` + `overall_state` (no `action` /
`action_outcome`). This script:

  - Reads each JSON freeze.
  - If `action` is already present, skips (idempotent).
  - Otherwise:
      - sets `action = "verify"` (legacy freezes were always verify-mode).
      - sets `action_source = "inferred"`.
      - sets `pivoted_from = None`.
      - sets `invoker_instruction_text = ""`.
      - derives `action_outcome` from the legacy `verdict_label`:
          Supported       → verified_supported
          Refuted         → verified_refuted
          Conflicting     → verified_conflicting
          NotEnoughEvidence → verified_nei
        (When overall_state == "no_checkable_claim", set action_outcome
        to "declined".)
  - Writes the file in place (atomic via tempfile + rename).

Run from the repo root:
  python scripts/migrate_freezes.py
  python scripts/migrate_freezes.py --dry-run
  python scripts/migrate_freezes.py --root data/freezes
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

_VERDICT_TO_OUTCOME = {
    "Supported": "verified_supported",
    "Refuted": "verified_refuted",
    "Conflicting": "verified_conflicting",
    "NotEnoughEvidence": "verified_nei",
}


def _migrate_one(path: Path, dry_run: bool) -> str:
    """Return one of: 'skipped', 'migrated', 'error: <reason>'."""
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return f"error: read/parse: {exc}"

    if not isinstance(data, dict):
        return "error: top-level not an object"
    if "action" in data and "action_outcome" in data:
        return "skipped"

    overall_state = data.get("overall_state", "checked")
    verdict_label = data.get("verdict_label", "NotEnoughEvidence")

    if overall_state == "no_checkable_claim":
        data["action"] = "decline"
        data["action_outcome"] = "declined"
    else:
        data["action"] = "verify"
        data["action_outcome"] = _VERDICT_TO_OUTCOME.get(verdict_label, "verified_nei")

    data["action_source"] = "inferred"
    data["pivoted_from"] = None
    data["invoker_instruction_text"] = ""

    if dry_run:
        return "migrated (dry-run)"

    # Atomic write — tempfile in same dir, then os.replace.
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".migrate_", suffix=".json.tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return f"error: write: {exc}"
    return "migrated"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="migrate_freezes")
    parser.add_argument("--root", default="data/freezes", help="Directory containing freeze JSONs.")
    parser.add_argument("--dry-run", action="store_true", help="Don't write; just report what would change.")
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    files = sorted(p for p in root.glob("*.json") if p.is_file())
    counts: dict[str, int] = {}
    for p in files:
        result = _migrate_one(p, args.dry_run)
        counts[result] = counts.get(result, 0) + 1
        if result.startswith("error"):
            print(f"{p.name}: {result}", file=sys.stderr)

    print(f"Scanned {len(files)} freeze files under {root}:")
    for k in sorted(counts):
        print(f"  {k:40s} {counts[k]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
