"""Harvest structured product data from vendors' online JS configurators.

See ``todo/CONFIGURATOR_HARVEST.md``. This is the P0 surface: a live walk of
the Stober configurator that prints its product tree and — the valuable part
— the *typed requirement schema* (numeric sliders with units + ranges, and
enumerated selections) that the vendor's sizing engine accepts.

Subcommands (all drive a real headless browser, so they need network access
and a Playwright Chromium binary — ``playwright install chromium`` once)::

    ./Quickstart configurator recon   [--shop SDI]
        Bootstrap a session and list the vendor's product types + endpoint map.

    ./Quickstart configurator walk    [--shop SDI] [--type Getriebe]
                                      [--group SynchronGetriebe]
        Walk types -> groups -> series and print the requirement schema for
        the chosen group.

    ./Quickstart configurator schema  [--type ...] [--group ...] [--json]
        Print only the typed requirement schema (filterId, unit, range,
        mapped Gearhead field) for a group's series.

The parsing core (``specodex.configurators.stober``) is covered by
fixture-based unit tests; these live subcommands are the manual driver.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import List, Optional

from specodex.configurators.base import ConfiguratorError
from specodex.configurators.stober import (
    GEARBOX_TYPE,
    RequirementParam,
    StoberAdapter,
)

logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
logger = logging.getLogger("configurator")

_DEFAULT_GROUP = "SynchronGetriebe"  # Servo Gearboxes


def _fmt_req(r: RequirementParam) -> str:
    if r.kind == "range":
        span = f"[{r.minimum}, {r.maximum}]{' ' + r.unit if r.unit else ''}"
        tail = f"  -> {r.gearhead_field}" if r.gearhead_field else ""
        return f"  {r.filter_id:16} {r.name:34} {span}{tail}"
    opts = ", ".join(r.options[:6]) + (" …" if len(r.options) > 6 else "")
    return f"  {r.filter_id:16} {r.name:34} select({len(r.options)}): {opts}"


def cmd_recon(args: argparse.Namespace) -> int:
    adapter = StoberAdapter(shop=args.shop)
    with adapter.client() as client:
        client.open(adapter.bootstrap_url())
        types = adapter.product_types(client)
    print(f"Configurator: {adapter.bootstrap_url()}")
    print("API base:     https://configurator.stober.com/api/{locale}/…")
    print(f"\nProduct types ({len(types)}):")
    for t in types:
        print(f"  {t.type_id:18} {t.name}")
    return 0


def cmd_walk(args: argparse.Namespace) -> int:
    adapter = StoberAdapter(shop=args.shop)
    with adapter.client() as client:
        client.open(adapter.bootstrap_url())
        types = adapter.product_types(client)
        print(f"Product types ({len(types)}): {', '.join(t.type_id for t in types)}")

        groups = adapter.groups(client, args.type)
        print(f"\n{args.type} groups ({len(groups)}):")
        for g in groups:
            mark = " *" if g.group_id == args.group else ""
            print(f"  {g.group_id:26} {g.name}{mark}")

        sel = adapter.select_group(client, args.type, args.group)
        print(f"\nSelected {args.group}: configurationId={sel.configuration_id}")

        series = adapter.series(client, args.type, args.group)
        print(f"\nSeries ({len(series)}):")
        for s in series:
            print(f"  {s.series_id:10} {s.name}  ({s.designation})")

        reqs = adapter.series_requirements(client, args.type, args.group)
    print(f"\nRequirement schema ({len(reqs)}):")
    for r in reqs:
        print(_fmt_req(r))
    mapped = [r for r in reqs if r.gearhead_field]
    print(f"\n{len(mapped)}/{len(reqs)} requirements map to Gearhead fields.")
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    adapter = StoberAdapter(shop=args.shop)
    with adapter.client() as client:
        client.open(adapter.bootstrap_url())
        reqs = adapter.series_requirements(client, args.type, args.group)
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "filter_id": r.filter_id,
                        "name": r.name,
                        "kind": r.kind,
                        "unit": r.unit,
                        "minimum": r.minimum,
                        "maximum": r.maximum,
                        "options": list(r.options),
                        "gearhead_field": r.gearhead_field,
                    }
                    for r in reqs
                ],
                indent=2,
            )
        )
    else:
        for r in reqs:
            print(_fmt_req(r))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="configurator",
        description="Harvest product data from vendors' online JS configurators.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(p: argparse.ArgumentParser, *, with_group: bool = True) -> None:
        p.add_argument(
            "--shop", default="SDI", help="Stober shop segment (default: SDI)"
        )
        p.add_argument(
            "--type",
            default=GEARBOX_TYPE,
            help=f"Product type id (default: {GEARBOX_TYPE})",
        )
        if with_group:
            p.add_argument(
                "--group",
                default=_DEFAULT_GROUP,
                help=f"Product group id (default: {_DEFAULT_GROUP})",
            )

    p = sub.add_parser("recon", help="List product types + endpoint map (live)")
    _common(p, with_group=False)

    p = sub.add_parser("walk", help="Walk types -> groups -> series -> requirements")
    _common(p)

    p = sub.add_parser("schema", help="Print the typed requirement schema for a group")
    _common(p)
    p.add_argument("--json", action="store_true", help="Emit the schema as JSON")

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    handlers = {"recon": cmd_recon, "walk": cmd_walk, "schema": cmd_schema}
    try:
        return handlers[args.command](args)
    except ConfiguratorError as e:
        logger.error("configurator error: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
