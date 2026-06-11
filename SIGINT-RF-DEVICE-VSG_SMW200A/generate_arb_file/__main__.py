"""CLI entry-point: ``python -m generate_arb_file [signal] [options]``."""

from __future__ import annotations

import argparse
import sys

from .generate_arb_file import (
    CATALOG,
    _OUTPUT_DIR_DEFAULT,
    generate,
    generate_all,
    timestamped_path,
)


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m generate_arb_file",
        description="Generate R&S SMW200A ARB .wv files from the signal catalog.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            f"  python -m generate_arb_file atc_am    # → {_OUTPUT_DIR_DEFAULT}/arb_signals.wv\n"
            "  python -m generate_arb_file atc_am -o /tmp/x.wv\n"
            f"  python -m generate_arb_file --all     # all signals → {_OUTPUT_DIR_DEFAULT}/\n"
            "  python -m generate_arb_file --list    # list catalog entries\n"
        ),
    )
    parser.add_argument(
        "signal",
        nargs="?",
        metavar="SIGNAL",
        help="Signal name from the catalog.",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="PATH",
        help=f"Output .wv path (default: {_OUTPUT_DIR_DEFAULT}/arb_signals.wv).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help=f"Generate ALL catalog signals into {_OUTPUT_DIR_DEFAULT}/ (or --output dir).",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available catalog signals and exit.",
    )

    args = parser.parse_args(argv)

    # --list
    if args.list:
        col = max(len(k) for k in CATALOG)
        print(f"{'SIGNAL':<{col}}  CARRIER MHz  POWER dBm  DESCRIPTION")
        print("-" * (col + 48))
        for name, entry in CATALOG.items():
            print(
                f"{name:<{col}}  "
                f"{entry.carrier_hz / 1e6:>11.3f}  "
                f"{entry.power_dbm:>9}  "
                f"{entry.description}"
            )
        return 0

    # single signal
    if args.signal and not args.all:
        try:
            out = args.output or timestamped_path()
            path = generate(args.signal, out)
            print(f"OK  {args.signal} → {path}")
            return 0
        except KeyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        except Exception as exc:  # noqa: BLE001
            print(f"Error generating {args.signal!r}: {exc}", file=sys.stderr)
            return 1

    if not args.signal and not args.all:
        parser.print_help()
        return 0

    # all signals
    out_dir = args.output or str(_OUTPUT_DIR_DEFAULT)
    results = generate_all(out_dir)
    ok_count = sum(1 for _, r in results if not isinstance(r, Exception))
    fail_count = len(results) - ok_count

    print(f"\nGenerated {ok_count}/{len(results)} signals → {out_dir}/\n")
    for name, result in results:
        if isinstance(result, Exception):
            print(f"  FAIL  {name:<20} {result}")
        else:
            print(f"  OK    {name:<20} {result.name}")

    if fail_count:
        print(f"\n{fail_count} signal(s) failed.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
