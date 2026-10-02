"""
Round 2 diagnostics: confirm or clear the two remaining suspected defects.

Round 1 established that VW_PA_DECISION_AUDIT's multiple rows per pa_id are
LEGITIMATE (one row per decision event: ORIGINAL, then APPEAL for the 311 appealed
cases). That was a bug in my verified query, not in the data.

Still open:
  * Is enrollment_status genuinely all NULL in VW_MEMBER_ELIGIBILITY? Round 1's
    GROUP BY collapsed to a single NULL row, which would mean the app cannot show
    an eligibility badge.
  * Why do 36% of VW_PA_PRECEDENT rows have a NULL procedure code?

Run:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/diagnose_defects2.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402


def q(con, title: str, sql: str) -> None:
    print()
    print("-" * 78)
    print(title)
    print("-" * 78)
    try:
        rows = L.execute(con, sql)
    except Exception as exc:  # noqa: BLE001
        print("  FAILED:", " ".join(str(exc).split())[:200])
        return
    if not rows:
        print("  (no rows)")
        return
    for r in rows[:24]:
        print("  " + " | ".join("NULL" if v is None else str(v)[:40] for v in r))


def main() -> int:
    con = L.connect()
    try:
        q(
            con,
            "A. Null profile of VW_MEMBER_ELIGIBILITY (rows, then non-null count per column)",
            f"""SELECT COUNT(*) AS rows,
                       COUNT(member_name)       AS name_nn,
                       COUNT(age_years)         AS age_nn,
                       COUNT(gender)            AS gender_nn,
                       COUNT(enrollment_status) AS enroll_nn,
                       COUNT(line_of_business)  AS lob_nn,
                       COUNT(plan_id)           AS plan_nn,
                       COUNT(is_eligible_today) AS elig_nn,
                       COUNT(has_coverage_gap)  AS gap_nn
                FROM {AC.fqn('VW_MEMBER_ELIGIBILITY')}""",
        )
        q(
            con,
            "B. Distinct enrollment_status values actually present",
            f"""SELECT COALESCE(enrollment_status,'<NULL>') AS status, COUNT(*) AS members
                FROM {AC.fqn('VW_MEMBER_ELIGIBILITY')} GROUP BY 1 ORDER BY 2 DESC""",
        )
        q(
            con,
            "C. Same column in the GOLD table underneath - does the defect originate there?",
            f"""SELECT COALESCE(enrollment_status,'<NULL>') AS status, COUNT(*) AS n
                FROM {AC.DATABASE}.GOLD.GOLD_MEMBER_ELIGIBILITY GROUP BY 1 ORDER BY 2 DESC""",
        )
        q(
            con,
            "D. Which GOLD tables exist (to fix my wrong table name from round 1)",
            f"""SELECT table_name FROM {AC.DATABASE}.INFORMATION_SCHEMA.TABLES
                WHERE table_schema='GOLD' ORDER BY 1""",
        )
        q(
            con,
            "E. Precedent: null procedure code by line_of_business",
            f"""SELECT line_of_business, COUNT(*) AS rows,
                       COUNT_IF(requested_procedure_code IS NULL) AS null_code
                FROM {AC.fqn('VW_PA_PRECEDENT')} GROUP BY 1 ORDER BY 2 DESC""",
        )
        q(
            con,
            "F. Do the PA requests themselves have procedure codes? (source of the nulls)",
            f"""SELECT COUNT(*) AS requests,
                       COUNT(requested_procedure_desc) AS desc_nn
                FROM {AC.fqn('VW_PA_REQUEST_360')}""",
        )
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
