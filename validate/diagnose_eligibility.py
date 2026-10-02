"""
Confirm the blast radius of the enrollment_status defect.

The gold builder derives BOTH columns from the same lookup:

    "enrollment_status": m.get("enrollment_status")
    "is_eligible_today": m.get("enrollment_status") == "ACTIVE"

`Series.get()` returns None for a missing label instead of raising, so if the
silver column is named differently every member silently becomes
status=NULL / eligible=False. This checks what the silver layer actually calls the
column and what the gold table now holds.

Run:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/diagnose_eligibility.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402

SILVER_DIR = Path(__file__).resolve().parent.parent / "dataFilesSilver"


def main() -> int:
    print("=" * 78)
    print("1. What does SILVER actually call the enrollment column?")
    print("=" * 78)
    for pq in sorted(SILVER_DIR.glob("*member*.parquet")):
        cols = pd.read_parquet(pq).columns.tolist()
        hits = [c for c in cols if "enroll" in c.lower() or "status" in c.lower()]
        if hits:
            print(f"  {pq.name:44s} -> {hits}")

    print()
    print("=" * 78)
    print("2. Candidate source table column list")
    print("=" * 78)
    cand = SILVER_DIR / "silver_member_master.parquet"
    if cand.exists():
        df = pd.read_parquet(cand)
        print(f"  silver_member_master: {len(df):,d} rows")
        for c in df.columns:
            print(f"    {c}")
    else:
        print("  silver_member_master.parquet not found; listing member parquets:")
        for pq in sorted(SILVER_DIR.glob("*member*.parquet")):
            print("   ", pq.name)

    print()
    print("=" * 78)
    print("3. What GOLD currently holds")
    print("=" * 78)
    con = L.connect()
    try:
        r = L.execute(
            con,
            f"""SELECT COUNT(*) AS n_rows,
                       COUNT(enrollment_status) AS enroll_nn,
                       COUNT_IF(is_eligible_today) AS eligible_true,
                       COUNT_IF(NOT is_eligible_today) AS eligible_false,
                       COUNT(line_of_business) AS lob_nn,
                       COUNT(plan_id) AS plan_nn
                FROM {AC.DATABASE}.GOLD.GOLD_MEMBER_ELIGIBILITY_SNAPSHOT""",
        )[0]
        print(f"  rows                 {r[0]:,d}")
        print(f"  enrollment_status    {r[1]:,d} non-null   <-- 0 confirms the defect")
        print(f"  is_eligible_today T  {r[2]:,d}")
        print(f"  is_eligible_today F  {r[3]:,d}   <-- all-False confirms it propagated")
        print(f"  line_of_business     {r[4]:,d} non-null")
        print(f"  plan_id              {r[5]:,d} non-null")

        print()
        print("=" * 78)
        print("4. Ground truth from SILVER, to know what the values SHOULD be")
        print("=" * 78)
        rows = L.execute(
            con,
            f"""SELECT table_name, column_name
                FROM {AC.DATABASE}.INFORMATION_SCHEMA.COLUMNS
                WHERE table_schema='SILVER'
                  AND (LOWER(column_name) LIKE '%enroll%' OR LOWER(column_name) LIKE '%status%')
                ORDER BY 1,2""",
        )
        for t, c in rows:
            print(f"  SILVER.{t}.{c}")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
