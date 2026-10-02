"""
Load parquet files to Snowflake.

Usage:
    python load/run_load.py --layer bronze
    python load/run_load.py --layer silver
    python load/run_load.py --layer gold
    python load/run_load.py --layer all
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from load import snowflake_loader as loader  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Load parquet to Snowflake")
    parser.add_argument("--layer", required=True, choices=["bronze", "silver", "gold", "all"])
    parser.add_argument("--no-recreate", action="store_true", help="Truncate instead of recreate")
    args = parser.parse_args()

    layers = ["bronze", "silver", "gold"] if args.layer == "all" else [args.layer]

    print("=" * 78)
    print(f"Loading to Snowflake: {', '.join(l.upper() for l in layers)}")
    print(f"  auth: {loader.auth_mode()}")
    print("=" * 78)

    con = loader.connect()
    try:
        all_records = []
        for layer in layers:
            records = loader.load_layer(con, layer, recreate=not args.no_recreate)
            all_records.extend(records)

        print("\n" + "=" * 78)
        ok = loader.print_recon(all_records)
        if not ok:
            print("\n  LOAD FAILED: row count mismatches detected")
            return 1
        print("  Load complete - all row counts reconciled.")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
