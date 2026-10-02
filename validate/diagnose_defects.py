"""
Diagnose the three defects that check_grain.py surfaced.

The question for each is the same: is this a BUG in the gold layer, or a
legitimate grain that the semantic view and the app must model explicitly? Those
have very different fixes, and guessing wrong bakes a wrong answer into the
copilot.

Run:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/diagnose_defects.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402


def show(con, title: str, sql: str) -> None:
    print()
    print("-" * 78)
    print(title)
    print("-" * 78)
    try:
        rows = L.execute(con, sql)
    except Exception as exc:  # noqa: BLE001
        print("  query failed:", str(exc).split("\n")[0][:120])
        return
    if not rows:
        print("  (no rows)")
        return
    for r in rows[:20]:
        print("  " + " | ".join("NULL" if v is None else str(v)[:44] for v in r))
    if len(rows) > 20:
        print(f"  ... {len(rows) - 20} more")


def main() -> int:
    con = L.connect()
    try:
        # ---------- Defect 1: decision audit fan-out
        show(
            con,
            "1a. Do multi-row pa_ids differ by decision_type? (legitimate event grain?)",
            f"""
            WITH multi AS (
              SELECT pa_id FROM {AC.fqn('VW_PA_DECISION_AUDIT')}
              GROUP BY pa_id HAVING COUNT(*) > 1
            )
            SELECT d.pa_id, d.decision_type, d.pa_status, d.appeal_outcome,
                   d.decision_by_role
            FROM {AC.fqn('VW_PA_DECISION_AUDIT')} d
            JOIN multi m ON m.pa_id = d.pa_id
            ORDER BY d.pa_id, d.decision_type
            LIMIT 12
            """,
        )
        show(
            con,
            "1b. Distribution of rows-per-pa_id and the decision_type combinations",
            f"""
            SELECT n_rows, COUNT(*) AS pa_count
            FROM (SELECT pa_id, COUNT(*) n_rows FROM {AC.fqn('VW_PA_DECISION_AUDIT')} GROUP BY 1)
            GROUP BY n_rows ORDER BY n_rows
            """,
        )
        show(
            con,
            "1c. Is the duplicate always an appeal? (decision_type breakdown on multi-row cases)",
            f"""
            WITH multi AS (
              SELECT pa_id FROM {AC.fqn('VW_PA_DECISION_AUDIT')}
              GROUP BY pa_id HAVING COUNT(*) > 1
            )
            SELECT d.decision_type, COUNT(*) AS rows_on_multi_pa
            FROM {AC.fqn('VW_PA_DECISION_AUDIT')} d
            JOIN multi m ON m.pa_id = d.pa_id
            GROUP BY 1 ORDER BY 2 DESC
            """,
        )

        # ---------- Defect 2: financial coverage
        show(
            con,
            "2a. Which members lack a financial row - are they the terminated ones?",
            f"""
            SELECT e.enrollment_status,
                   COUNT(*) AS members,
                   COUNT_IF(f.member_id IS NULL) AS missing_financial,
                   ROUND(100.0 * COUNT_IF(f.member_id IS NULL) / COUNT(*), 1) AS pct_missing
            FROM {AC.fqn('VW_MEMBER_ELIGIBILITY')} e
            LEFT JOIN {AC.fqn('VW_MEMBER_FINANCIAL')} f ON f.member_id = e.member_id
            GROUP BY 1 ORDER BY 2 DESC
            """,
        )
        show(
            con,
            "2b. Do members with OPEN PAs have financial rows? (this is what the app needs)",
            f"""
            SELECT COUNT(DISTINCT w.member_id) AS members_with_open_pa,
                   COUNT(DISTINCT f.member_id) AS of_those_with_financial
            FROM {AC.fqn('VW_PA_WORKLIST')} w
            LEFT JOIN {AC.fqn('VW_MEMBER_FINANCIAL')} f ON f.member_id = w.member_id
            """,
        )

        # ---------- Defect 3: precedent null procedure codes
        show(
            con,
            "3a. Are NULL-procedure precedent rows concentrated in drug categories?",
            f"""
            SELECT line_of_business,
                   COUNT(*) AS rows,
                   COUNT_IF(requested_procedure_code IS NULL) AS null_code,
                   ROUND(100.0 * COUNT_IF(requested_procedure_code IS NULL) / COUNT(*), 1) AS pct_null
            FROM {AC.fqn('VW_PA_PRECEDENT')}
            GROUP BY 1 ORDER BY 2 DESC
            """,
        )
        show(
            con,
            "3b. Upstream: how many PA requests themselves lack a procedure code, by category?",
            f"""
            SELECT service_category,
                   COUNT(*) AS requests,
                   COUNT_IF(requested_procedure_code IS NULL) AS null_code,
                   ROUND(100.0 * COUNT_IF(requested_procedure_code IS NULL) / COUNT(*), 1) AS pct_null
            FROM {AC.DATABASE}.GOLD.GOLD_PA_REQUEST_360
            GROUP BY 1 ORDER BY 4 DESC
            """,
        )
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
