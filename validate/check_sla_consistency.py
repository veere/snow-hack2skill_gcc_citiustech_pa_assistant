"""
Check consistency between VW_PA_WORKLIST and VW_PA_SLA_TRACKING.

The worklist reported 40 open PAs for a member while the SLA tracker's `is_open`
filter returned none for the same member. Both views claim to describe open cases,
so one of them is wrong - and the copilot answers SLA questions from the tracker.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402


def main() -> int:
    con = L.connect()
    try:
        n_worklist, n_sla_open, overlap = L.execute(
            con,
            f"""SELECT
                  (SELECT COUNT(*) FROM {AC.fqn('VW_PA_WORKLIST')}),
                  (SELECT COUNT(*) FROM {AC.fqn('VW_PA_SLA_TRACKING')} WHERE is_open),
                  (SELECT COUNT(*) FROM {AC.fqn('VW_PA_WORKLIST')} w
                     JOIN {AC.fqn('VW_PA_SLA_TRACKING')} t ON t.pa_id = w.pa_id
                    WHERE t.is_open)""",
        )[0]
        print("=" * 74)
        print("WORKLIST vs SLA_TRACKING")
        print("=" * 74)
        print(f"  worklist rows (open cases)        {n_worklist:,d}")
        print(f"  sla_tracking rows with is_open    {n_sla_open:,d}")
        print(f"  pa_ids in BOTH                    {overlap:,d}")

        only_worklist = L.execute(
            con,
            f"""SELECT COUNT(*) FROM {AC.fqn('VW_PA_WORKLIST')} w
                LEFT JOIN {AC.fqn('VW_PA_SLA_TRACKING')} t
                       ON t.pa_id = w.pa_id AND t.is_open
                WHERE t.pa_id IS NULL""",
        )[0][0]
        print(f"  in worklist but NOT sla-open      {only_worklist:,d}")

        print()
        print("  is_open distribution in sla_tracking:")
        for val, n in L.execute(
            con,
            f"SELECT is_open, COUNT(*) FROM {AC.fqn('VW_PA_SLA_TRACKING')} GROUP BY 1 ORDER BY 1",
        ):
            print(f"    is_open={val!s:6s} {n:,d}")

        print()
        print("  sla_state distribution (worklist rows only):")
        for state, n in L.execute(
            con,
            f"""SELECT w.sla_state, COUNT(*) FROM {AC.fqn('VW_PA_WORKLIST')} w
                GROUP BY 1 ORDER BY 2 DESC""",
        ):
            print(f"    {str(state):22s} {n:,d}")

        print()
        print("  sample: worklist pa_ids and their sla_tracking is_open value")
        for row in L.execute(
            con,
            f"""SELECT w.pa_id, w.pa_status, w.sla_state, t.is_open, t.sla_state,
                       t.days_until_deadline
                FROM {AC.fqn('VW_PA_WORKLIST')} w
                LEFT JOIN {AC.fqn('VW_PA_SLA_TRACKING')} t ON t.pa_id = w.pa_id
                LIMIT 8""",
        ):
            print("    " + " | ".join("NULL" if v is None else str(v)[:22] for v in row))
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
