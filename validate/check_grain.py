"""
Grain and integrity checks for the SERVING views.

Each expectation below was derived by investigating the data, not assumed. Three
things that LOOK like defects are in fact correct, and are asserted as such so the
check does not cry wolf:

  * VW_PA_DECISION_AUDIT is EVENT grain, not case grain. A PA that was appealed has
    two rows - ORIGINAL then APPEAL. 311 of 4,893 cases were appealed, giving
    5,204 rows. The composite (pa_id, decision_type) is the real key. Anything
    joining this view without aggregating will fan out, which is a caller bug.
  * VW_MEMBER_FINANCIAL only covers CURRENTLY COVERED members: a terminated member
    has no current-year accumulator. So ~860 of 1,171 is right, and the app must
    say "no current accumulator on record" rather than implying a zero balance.
  * VW_PA_PRECEDENT.requested_procedure_code is NULL for drug requests, which carry
    an NDC instead (SPECIALTY_DRUG 100%, HOME_HEALTH 84%). The unified
    `service_code` is the column that must never be NULL.

Run:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python validate/check_grain.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402

# (view, key columns) - the TRUE grain, composite where the view is event-level
GRAIN: list[tuple[str, tuple[str, ...]]] = [
    ("VW_PA_REQUEST_360", ("pa_id",)),
    ("VW_PA_SLA_TRACKING", ("pa_id",)),
    ("VW_PA_DECISION_AUDIT", ("pa_id", "decision_type")),
    ("VW_PA_DUPLICATE_SAFETY", ("pa_id",)),
    ("VW_PA_WORKLIST", ("pa_id",)),
    ("VW_MEMBER_ELIGIBILITY", ("member_id",)),
    ("VW_MEMBER_FINANCIAL", ("member_id",)),
    ("VW_MEMBER_CLINICAL_SUMMARY", ("member_id",)),
    ("VW_PROVIDER_NETWORK", ("provider_npi",)),
    ("VW_PA_PRECEDENT", ("precedent_sk",)),
]

# Columns that must be fully populated, because a NULL would make the copilot
# either wrong or silent on a question an analyst actually asks. line_of_business
# in particular selects which regulatory turnaround rule applies.
NOT_NULL_REQUIRED: list[tuple[str, str]] = [
    ("VW_MEMBER_ELIGIBILITY", "enrollment_status"),
    ("VW_MEMBER_ELIGIBILITY", "line_of_business"),
    ("VW_MEMBER_ELIGIBILITY", "is_eligible_today"),
    ("VW_MEMBER_ELIGIBILITY", "member_name"),
    ("VW_PA_PRECEDENT", "service_code"),
    ("VW_PA_PRECEDENT", "primary_dx_icd10"),
    ("VW_PA_REQUEST_360", "service_category"),
    ("VW_PA_REQUEST_360", "pa_status"),
    ("VW_PA_CRITERIA_EVIDENCE", "criterion_result"),
    ("VW_PA_SLA_TRACKING", "sla_state"),
]


def main() -> int:
    con = L.connect()
    problems: list[str] = []
    try:
        print("=" * 78)
        print("1. GRAIN - unique on the declared key?")
        print("=" * 78)
        for view, keys in GRAIN:
            key_expr = ", ".join(keys)
            n_rows, n_keys = L.execute(
                con,
                f"SELECT COUNT(*), COUNT(DISTINCT {key_expr}) FROM {AC.fqn(view)}"
                if len(keys) == 1
                else f"SELECT COUNT(*), COUNT(DISTINCT CONCAT_WS('|', {key_expr})) FROM {AC.fqn(view)}",
            )[0]
            ok = n_rows == n_keys
            print(f"  {'ok     ' if ok else 'FAN-OUT'} {view:30s} {n_rows:7,d} rows / "
                  f"{n_keys:7,d} distinct ({key_expr})")
            if not ok:
                problems.append(f"{view} not unique on ({key_expr})")

        print()
        print("=" * 78)
        print("2. REQUIRED NOT-NULL columns")
        print("=" * 78)
        for view, col in NOT_NULL_REQUIRED:
            n_rows, n_nn = L.execute(
                con, f"SELECT COUNT(*), COUNT({col}) FROM {AC.fqn(view)}"
            )[0]
            ok = n_rows == n_nn
            print(f"  {'ok  ' if ok else 'NULL'} {view:30s}.{col:24s} {n_nn:7,d} / {n_rows:,d}")
            if not ok:
                problems.append(f"{view}.{col} has {n_rows - n_nn:,d} NULLs")

        print()
        print("=" * 78)
        print("3. FINANCIAL COVERAGE - should track ACTIVE members, not all members")
        print("=" * 78)
        active, fin, fin_active = L.execute(
            con,
            f"""SELECT
                  (SELECT COUNT(*) FROM {AC.fqn('VW_MEMBER_ELIGIBILITY')} WHERE is_eligible_today),
                  (SELECT COUNT(*) FROM {AC.fqn('VW_MEMBER_FINANCIAL')}),
                  (SELECT COUNT(*) FROM {AC.fqn('VW_MEMBER_FINANCIAL')} f
                    JOIN {AC.fqn('VW_MEMBER_ELIGIBILITY')} e ON e.member_id = f.member_id
                   WHERE e.is_eligible_today)""",
        )[0]
        print(f"  active members            {active:,d}")
        print(f"  members with financial    {fin:,d}")
        print(f"  active AND financial      {fin_active:,d}")
        pct = 100.0 * fin_active / active if active else 0
        print(f"  active coverage           {pct:.1f}%")
        if pct < 95:
            problems.append(f"only {pct:.1f}% of ACTIVE members have a financial accumulator")

        print()
        print("=" * 78)
        print("4. APPROVAL RATE HONESTY - never fabricated when nothing was decided")
        print("=" * 78)
        bad_rate, undecided_with_rate = L.execute(
            con,
            f"""SELECT COUNT_IF(decided_count = 0 AND approval_rate_pct IS NOT NULL),
                       COUNT_IF(decided_count = 0)
                FROM {AC.fqn('VW_PA_PRECEDENT')}""",
        )[0]
        print(f"  groups with nothing decided        {undecided_with_rate:,d}")
        print(f"  of those, wrongly showing a rate   {bad_rate:,d}")
        if bad_rate:
            problems.append(f"{bad_rate:,d} precedent rows fabricate a rate with 0 decided")

        print()
        print("=" * 78)
        print("5. SLA STATE CONSISTENCY - an overdue request must read BREACHED")
        print("=" * 78)
        # A branch-ordering bug once tested `days_left < 2` before `days_left < 0`, so
        # every overdue OPEN request was labelled AT_RISK and the BREACHED branch was
        # dead code. The copilot then told an analyst "0 past deadline" while ten of
        # their cases were overdue. This asserts the invariant directly.
        n_open, overdue, mislabelled, on_track_overdue = L.execute(
            con,
            f"""SELECT COUNT(*),
                       COUNT_IF(days_until_deadline < 0),
                       COUNT_IF(days_until_deadline < 0 AND sla_state <> 'BREACHED'),
                       COUNT_IF(days_until_deadline < 0 AND sla_state = 'ON_TRACK')
                FROM {AC.fqn('VW_PA_SLA_TRACKING')} WHERE is_open""",
        )[0]
        print(f"  open requests                        {n_open:,d}")
        print(f"  past their deadline                  {overdue:,d}")
        print(f"  overdue but NOT marked BREACHED      {mislabelled:,d}")
        print(f"  overdue but marked ON_TRACK          {on_track_overdue:,d}")
        if mislabelled:
            problems.append(
                f"{mislabelled:,d} open request(s) are past their deadline but "
                f"sla_state is not BREACHED"
            )

        # The reverse must hold too: nothing still inside its window may read BREACHED.
        #
        # A tolerance is needed because days_until_deadline is STORED rounded to 2dp. A
        # request whose true remaining time is -0.001 days is genuinely breached but
        # surfaces as exactly 0.00, so a naive `>= 0` test reported it as a false
        # positive. Only a clearly positive remaining window is a real contradiction.
        early_breach = L.execute(
            con,
            f"""SELECT COUNT(*) FROM {AC.fqn('VW_PA_SLA_TRACKING')}
                WHERE is_open AND sla_state = 'BREACHED'
                  AND days_until_deadline > 0.01""",
        )[0][0]
        print(f"  marked BREACHED with >0.01d left     {early_breach:,d}")
        if early_breach:
            problems.append(
                f"{early_breach:,d} open request(s) read BREACHED while still inside "
                f"their deadline window"
            )

        # Every open request must have a deadline to track against; without one the SLA
        # position is unknowable and the copilot would have nothing to report.
        no_deadline = L.execute(
            con,
            f"""SELECT COUNT(*) FROM {AC.fqn('VW_PA_SLA_TRACKING')}
                WHERE is_open AND (regulatory_deadline_datetime_utc IS NULL
                                   OR days_until_deadline IS NULL)""",
        )[0][0]
        print(f"  open requests with no deadline       {no_deadline:,d}")
        if no_deadline:
            problems.append(f"{no_deadline:,d} open request(s) have no regulatory deadline")

        print()
        print("=" * 78)
        if problems:
            print(f"{len(problems)} PROBLEM(S)")
            for p in problems:
                print(f"  - {p}")
            return 1
        print("ALL CHECKS PASSED")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
