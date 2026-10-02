"""
Deploy the SQL layer: schemas, DDL, SERVING views and RBAC.

Runs locally against Snowflake. Usage:

    python load/deploy_sql.py                    # schemas + views + rbac
    python load/deploy_sql.py --all              # everything including table DDL
    python load/deploy_sql.py --files 40_serving_views.sql

Table DDL is normally skipped because `load/run_load.py` issues CREATE OR REPLACE
from the frozen spec as part of loading, which keeps the physical tables and the
contract in lockstep. The DDL files exist as reviewable artefacts.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from load import snowflake_loader as L  # noqa: E402
from config import schemas as S  # noqa: E402

DEFAULT_FILES = [
    "00_database_schemas.sql",
    "40_serving_views.sql",
    "50_rbac.sql",
]

ALL_FILES = [
    "00_database_schemas.sql",
    "10_bronze_ddl.sql",
    "20_silver_ddl.sql",
    "30_gold_ddl.sql",
    "40_serving_views.sql",
    "50_rbac.sql",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include table DDL files")
    ap.add_argument("--files", nargs="*", help="explicit file list")
    args = ap.parse_args()

    cfg = S.load_config()
    sql_dir = Path(cfg["_repo_root"]) / cfg["paths"]["sql"]

    if args.files:
        files = args.files
    elif args.all:
        files = ALL_FILES
    else:
        files = DEFAULT_FILES

    print("=" * 78)
    print("DEPLOY SQL")
    print("=" * 78)
    print(f"auth  : {L.auth_mode()}")
    print(f"target: {cfg['snowflake']['database']}")
    print()

    failures: list[str] = []
    con = L.connect()
    try:
        for name in files:
            path = sql_dir / name
            if not path.exists():
                print(f"  SKIP    {name} (not found)")
                failures.append(f"{name}: file not found")
                continue
            try:
                n = L.execute_script(con, path.read_text(encoding="utf-8"), label=name)
                print(f"  ok      {name}  ({n} statements)")
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED  {name}: {exc}")
                failures.append(f"{name}: {exc}")

        # Report what actually exists afterwards.
        db = cfg["snowflake"]["database"]
        serving = cfg["snowflake"]["schemas"]["serving"]
        rows = L.execute(
            con,
            f"SELECT table_name FROM {db}.INFORMATION_SCHEMA.VIEWS "
            f"WHERE table_schema = '{serving}' ORDER BY 1",
        )
        print()
        print(f"  {serving} views: {len(rows)}")
        for r in rows:
            print(f"    {r[0]}")

        counts = L.execute(
            con,
            f"SELECT table_schema, COUNT(*) FROM {db}.INFORMATION_SCHEMA.TABLES "
            f"WHERE table_schema IN ('BRONZE','SILVER','GOLD') "
            f"GROUP BY 1 ORDER BY 1",
        )
        print()
        for schema, n in counts:
            print(f"  {schema}: {n} tables")
    finally:
        con.close()

    print()
    if failures:
        print(f"DEPLOY FAILED ({len(failures)}):")
        for f in failures:
            print(f"  x {f}")
        return 1
    print("DEPLOY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
