"""Compile data/vendors/*.json into the frontend vendor-facts bundle.

Reads every vendor profile through the validating loader
(``specodex.vendors.registry.load_vendor_profiles`` — so an uncited
fact can never reach the frontend) and writes
``app/frontend/src/data/vendors.json`` keyed by slug:

    {"<slug>": {"display_name": "...", "facts": [...]}, ...}

The output is committed, like ``generated.ts`` — regenerate after any
edit under ``data/vendors/`` and commit the diff.

Usage:
    uv run python scripts/gen_vendor_data.py [--data-dir DIR] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "app" / "frontend" / "src" / "data" / "vendors.json"

sys.path.insert(0, str(ROOT))


def generate(data_dir: Path | None, out_path: Path) -> dict:
    from specodex.vendors.registry import load_vendor_profiles

    profiles = load_vendor_profiles(data_dir)
    payload = {
        slug: {
            "display_name": profile.display_name,
            "facts": [fact.model_dump(exclude_none=True) for fact in profile.facts],
        }
        for slug, profile in sorted(profiles.items())
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    payload = generate(args.data_dir, args.out)
    total = sum(len(v["facts"]) for v in payload.values())
    print(f"Wrote {args.out} — {len(payload)} vendor(s), {total} fact(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
