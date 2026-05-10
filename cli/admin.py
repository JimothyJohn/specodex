"""Admin CLI — blacklist management and dev ↔ prod data movement.

Invoked via ``./Quickstart admin <subcommand>`` or directly as
``python -m cli.admin <subcommand>``.

All destructive operations default to dry-run. Pass ``--apply`` to write.
``purge`` additionally requires a typed confirmation string.

Subcommands:
    blacklist list
    blacklist add    <manufacturer>
    blacklist remove <manufacturer>

    diff    --source dev --target prod --type drive [--manufacturer ABB] [--json]

    promote --source dev --target prod --type drive [--manufacturer ABB] [--apply]
    demote  --source prod --target dev --type drive [--manufacturer ABB] [--apply]

    purge   --stage prod [--type drive] [--manufacturer ABB]
            --confirm "yes delete prod drives" [--apply]

    audit-units [--table ...] [-o out.jsonl]
            Scan for value;unit strings with >1 semicolon (greedy-regex flaw).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from cli import audit_units
from specodex.admin.blacklist import Blacklist
from specodex.admin.operations import (
    PRODUCT_MODELS,
    demote,
    diff,
    format_diff_table,
    format_promote_summary,
    format_purge_summary,
    make_client,
    promote,
    purge,
)

STAGES = ("dev", "staging", "prod")


def _err(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)


# ── blacklist subcommand ───────────────────────────────────────────


def cmd_blacklist(args: argparse.Namespace) -> int:
    bl = Blacklist()
    if args.blacklist_action == "list":
        names = bl.names()
        if not names:
            print("(blacklist is empty)")
        else:
            print(f"Blacklisted manufacturers ({len(names)}):")
            for n in names:
                print(f"  {n}")
        return 0

    if args.blacklist_action == "add":
        if bl.add(args.manufacturer):
            bl.save()
            print(f"Added: {args.manufacturer}")
            return 0
        print(f"Already blacklisted: {args.manufacturer}")
        return 0

    if args.blacklist_action == "remove":
        if bl.remove(args.manufacturer):
            bl.save()
            print(f"Removed: {args.manufacturer}")
            return 0
        print(f"Not on blacklist: {args.manufacturer}")
        return 0

    _err(f"Unknown blacklist action: {args.blacklist_action}")
    return 2


# ── diff subcommand ────────────────────────────────────────────────


def cmd_diff(args: argparse.Namespace) -> int:
    source = make_client(args.source)
    target = make_client(args.target)
    result = diff(
        source=source,
        target=target,
        product_type=args.type,
        source_stage=args.source,
        target_stage=args.target,
        manufacturer=args.manufacturer,
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(format_diff_table(result))
    return 0


# ── promote / demote subcommands ───────────────────────────────────


def _validate_stages(source: str, target: str) -> Optional[str]:
    if source == target:
        return f"source and target must differ (both are {source!r})"
    if source not in STAGES or target not in STAGES:
        return f"stages must be one of {STAGES}"
    return None


def cmd_promote(args: argparse.Namespace) -> int:
    err = _validate_stages(args.source, args.target)
    if err:
        _err(err)
        return 2
    if not 0.0 <= args.min_quality <= 1.0:
        _err(f"--min-quality must be in [0.0, 1.0], got {args.min_quality}")
        return 2
    bl = Blacklist()
    source = make_client(args.source)
    target = make_client(args.target)
    result = promote(
        source=source,
        target=target,
        product_type=args.type,
        blacklist=bl,
        manufacturer=args.manufacturer,
        apply=args.apply,
        min_quality=args.min_quality,
    )
    label = f"Promote {args.source} → {args.target}"
    print(format_promote_summary(label, result))
    if not args.apply:
        print("\n(dry run — re-run with --apply to write)")
    return 0


def cmd_demote(args: argparse.Namespace) -> int:
    err = _validate_stages(args.source, args.target)
    if err:
        _err(err)
        return 2
    source = make_client(args.source)
    target = make_client(args.target)
    result = demote(
        source=source,
        target=target,
        product_type=args.type,
        manufacturer=args.manufacturer,
        apply=args.apply,
    )
    label = f"Demote {args.source} → {args.target}"
    print(format_promote_summary(label, result))
    if not args.apply:
        print("\n(dry run — re-run with --apply to write)")
    return 0


# ── purge subcommand ───────────────────────────────────────────────


def _expected_purge_confirm(
    stage: str, ptype: Optional[str], mfg: Optional[str]
) -> str:
    parts = ["yes delete", stage]
    if ptype:
        parts.append(ptype)
    if mfg:
        parts.append(mfg)
    return " ".join(parts)


def cmd_purge(args: argparse.Namespace) -> int:
    if args.stage not in STAGES:
        _err(f"--stage must be one of {STAGES}")
        return 2
    if not args.type and not args.manufacturer:
        _err("purge requires --type and/or --manufacturer")
        return 2

    expected = _expected_purge_confirm(args.stage, args.type, args.manufacturer)
    if args.apply and args.confirm != expected:
        _err(
            "purge --apply requires --confirm matching the exact scope string.\n"
            f"  expected: {expected!r}\n"
            f"  got:      {args.confirm!r}"
        )
        return 2

    client = make_client(args.stage)
    result = purge(
        client=client,
        product_type=args.type,
        manufacturer=args.manufacturer,
        apply=args.apply,
    )
    print(format_purge_summary(result))
    if not args.apply:
        print(f"\n(dry run — to apply, re-run with:\n  --apply --confirm {expected!r})")
    return 0


# ── parser ─────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="admin",
        description="Datasheetminer admin — blacklist + dev/prod data movement",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    product_types = sorted(PRODUCT_MODELS.keys())

    # blacklist
    p = sub.add_parser("blacklist", help="Manage the manufacturer blacklist")
    bl_sub = p.add_subparsers(dest="blacklist_action", required=True)
    bl_sub.add_parser("list", help="Show all blacklisted manufacturers")
    p_add = bl_sub.add_parser("add", help="Add a manufacturer to the blacklist")
    p_add.add_argument("manufacturer")
    p_rm = bl_sub.add_parser("remove", help="Remove a manufacturer from the blacklist")
    p_rm.add_argument("manufacturer")

    # diff
    p = sub.add_parser("diff", help="Show product_id delta between two stages")
    p.add_argument("--source", default="dev", choices=STAGES)
    p.add_argument("--target", default="prod", choices=STAGES)
    p.add_argument("--type", required=True, choices=product_types)
    p.add_argument("--manufacturer", default=None)
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a table")

    # promote
    p = sub.add_parser(
        "promote", help="Copy products source → target, filtered by blacklist"
    )
    p.add_argument("--source", default="dev", choices=STAGES)
    p.add_argument("--target", default="prod", choices=STAGES)
    p.add_argument("--type", required=True, choices=product_types)
    p.add_argument("--manufacturer", default=None)
    p.add_argument(
        "--min-quality",
        type=float,
        default=0.0,
        help="Drop products whose spec-field completeness score is below this "
        "threshold (0.0–1.0). Default 0.0 (no quality filter — same behavior as before).",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually write to the target table (default: dry run)",
    )

    # demote
    p = sub.add_parser(
        "demote",
        help="Copy products source → target with no blacklist check (rollback path)",
    )
    p.add_argument("--source", default="prod", choices=STAGES)
    p.add_argument("--target", default="dev", choices=STAGES)
    p.add_argument("--type", required=True, choices=product_types)
    p.add_argument("--manufacturer", default=None)
    p.add_argument("--apply", action="store_true")

    # purge
    p = sub.add_parser(
        "purge", help="Bulk delete products by type and/or manufacturer in one stage"
    )
    p.add_argument("--stage", required=True, choices=STAGES)
    p.add_argument("--type", default=None, choices=product_types)
    p.add_argument("--manufacturer", default=None)
    p.add_argument(
        "--confirm",
        default="",
        help='Required with --apply. Must equal "yes delete <stage> [<type>] [<manufacturer>]"',
    )
    p.add_argument("--apply", action="store_true")

    # audit-units
    p = sub.add_parser(
        "audit-units",
        help="Scan for value;unit strings with >1 semicolon (greedy-regex flaw)",
    )
    p.add_argument(
        "--table", default=None, help="Table name (default: from env/config)"
    )
    p.add_argument(
        "--region", default=None, help="AWS region (default: from env/config)"
    )
    p.add_argument("-o", "--output", default=None, help="Write findings to JSONL file")

    # backfill-motor-mounts (SCHEMA Phase 2)
    p = sub.add_parser(
        "backfill-motor-mounts",
        help="Derive motor_mount_pattern from frame_size on existing motor rows",
    )
    p.add_argument(
        "--stage",
        required=True,
        choices=STAGES,
        help="Which stage to walk. dev only by convention — promote separately.",
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="Actually update DynamoDB (default: dry run; prints summary only)",
    )
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a summary")

    return parser


def cmd_audit_units(args: argparse.Namespace) -> int:
    from specodex.config import REGION, TABLE_NAME

    import os

    table = args.table or os.environ.get("DYNAMODB_TABLE_NAME") or TABLE_NAME
    region = args.region or os.environ.get("AWS_REGION") or REGION
    return audit_units.audit(table, region, args.output)


def cmd_backfill_motor_mounts(args: argparse.Namespace) -> int:
    from specodex.admin.motor_mount_backfill import backfill_motor_mounts

    client = make_client(args.stage)
    result = backfill_motor_mounts(client, apply=args.apply)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        print(f"motor_mount_pattern backfill — stage={args.stage} apply={args.apply}")
        print(f"  considered:      {result.considered}")
        print(f"  already_set:     {result.already_set}")
        print(f"  no frame_size:   {result.no_frame_size}")
        print(f"  unmatched frame: {result.unmatched_frame}")
        print(f"  matched:         {result.matched}")
        if args.apply:
            print(f"  written:         {result.written}")
        if result.samples:
            print("\n  sample mappings:")
            for s in result.samples:
                print(f"    {s['frame_size']!r:>16} → {s['motor_mount_pattern']!r}")
        if not args.apply and result.matched > 0:
            print("\n(dry run — re-run with --apply to write)")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "blacklist": cmd_blacklist,
        "diff": cmd_diff,
        "promote": cmd_promote,
        "demote": cmd_demote,
        "purge": cmd_purge,
        "audit-units": cmd_audit_units,
        "backfill-motor-mounts": cmd_backfill_motor_mounts,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
