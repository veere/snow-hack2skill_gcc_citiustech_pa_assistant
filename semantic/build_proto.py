"""
Build and validate the json_proto for the PA copilot semantic view.

Two jobs:
  1. Pull the real column list for each SERVING view so the proto cannot drift
     from the deployed schema.
  2. Execute every candidate verified query (VQR) and keep ONLY the ones that
     actually run. A VQR that does not execute teaches Cortex Analyst nothing and
     silently degrades answer quality, so an unvalidated VQR is worse than none.

Run:
    cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python semantic/build_proto.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import app_config as AC  # noqa: E402
from load import snowflake_loader as L  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent
PROTO_PATH = OUT_DIR / "sv_pa_copilot_proto.json"

# Member- and PA-centric views only. VW_PA_KPI_DAILY is account-level (no
# member_id) and VW_ASSISTANT_DICTIONARY is metadata about the views themselves;
# including either would invite Analyst to join on nothing and inflate counts.
VIEWS = [
    "VW_MEMBER_ELIGIBILITY",
    "VW_MEMBER_CLINICAL_SUMMARY",
    "VW_MEMBER_FINANCIAL",
    "VW_MEMBER_TIMELINE",
    "VW_PA_REQUEST_360",
    "VW_PA_CRITERIA_EVIDENCE",
    "VW_PA_SLA_TRACKING",
    "VW_PA_DECISION_AUDIT",
    "VW_PA_DUPLICATE_SAFETY",
    "VW_PA_PRECEDENT",
    "VW_PROVIDER_NETWORK",
    "VW_PA_WORKLIST",
]

# Candidate VQRs. Each maps to a real analyst question from
# docs/PA_analyst_research.md. Every one is executed before it is accepted.
#
# Note what is deliberately ABSENT: no query ranks or scores a likely outcome.
# VQ_PRECEDENT returns a historical approval rate, which is a factual record of
# past decisions, not a prediction about this request.
CANDIDATE_VQRS: list[tuple[str, str]] = [
    (
        "Is member MBR-00000001 eligible today and what plan are they on?",
        """SELECT member_id, member_name, age_years, gender, line_of_business,
       plan_id, enrollment_status, is_eligible_today, has_coverage_gap,
       coverage_start_date
FROM M360MART.SERVING.VW_MEMBER_ELIGIBILITY
WHERE member_id = 'MBR-00000001'""",
    ),
    (
        "What open prior authorization requests does this member have and how close are they to the regulatory deadline?",
        """SELECT w.pa_id, w.service_category, w.urgency_flag, w.pa_status,
       w.sla_state, w.days_until_deadline, w.hours_until_deadline,
       w.criteria_met_count, w.criteria_total_count, w.completeness_pct,
       w.requested_procedure_desc, w.ordering_provider_name
FROM M360MART.SERVING.VW_PA_WORKLIST w
WHERE w.member_id = 'MBR-00000001'
ORDER BY w.days_until_deadline""",
    ),
    (
        "Show the criteria evidence for prior authorization PA-2025-000001 including which criteria are indeterminate",
        """SELECT criterion_type, criterion_result, criterion_desc, evidence_text
FROM M360MART.SERVING.VW_PA_CRITERIA_EVIDENCE
WHERE pa_id = 'PA-2025-000001'
ORDER BY CASE criterion_result
           WHEN 'UNMET' THEN 1 WHEN 'INDETERMINATE' THEN 2 ELSE 3 END,
         criterion_type""",
    ),
    (
        "What is this member's prior authorization history and what were the outcomes?",
        """SELECT r.pa_id, r.service_category, r.requested_procedure_desc,
       r.primary_dx_desc, r.pa_status, r.urgency_flag,
       d.decision_type, d.denial_reason_desc, d.appeal_outcome,
       d.auto_adjudicated_flag
FROM M360MART.SERVING.VW_PA_REQUEST_360 r
LEFT JOIN M360MART.SERVING.VW_PA_DECISION_AUDIT d
       ON d.pa_id = r.pa_id AND d.decision_type = 'ORIGINAL'
WHERE r.member_id = 'MBR-00000001'
ORDER BY r.pa_id""",
    ),
    (
        "Which of this member's denied requests were appealed and what was the appeal outcome?",
        """SELECT r.pa_id, r.service_category, r.requested_procedure_desc,
       orig.denial_reason_desc, appeal.appeal_outcome
FROM M360MART.SERVING.VW_PA_REQUEST_360 r
JOIN M360MART.SERVING.VW_PA_DECISION_AUDIT orig
     ON orig.pa_id = r.pa_id AND orig.decision_type = 'ORIGINAL'
JOIN M360MART.SERVING.VW_PA_DECISION_AUDIT appeal
     ON appeal.pa_id = r.pa_id AND appeal.decision_type = 'APPEAL'
WHERE r.member_id = 'MBR-00000001'
ORDER BY r.pa_id""",
    ),
    (
        "How have requests for this service and diagnosis been decided historically?",
        """SELECT service_code, service_code_system, primary_dx_icd10, line_of_business,
       total_requests, decided_count, approved_count, denied_count,
       approval_rate_pct
FROM M360MART.SERVING.VW_PA_PRECEDENT
WHERE service_code = 'SVCCODE'
  AND decided_count > 0
ORDER BY decided_count DESC""",
    ),
    (
        "Is the ordering provider in network and are they gold-carded?",
        """SELECT provider_npi, provider_name, provider_specialty, network_status,
       gold_card_eligible, approval_rate_6mo_pct, total_pa_requests_6mo
FROM M360MART.SERVING.VW_PROVIDER_NETWORK
WHERE provider_name = 'X'""",
    ),
    (
        "Does this member have any potential duplicate requests or high cost flags?",
        """SELECT s.pa_id, s.has_potential_duplicate, s.duplicate_pa_id,
       s.duplicate_similarity, s.high_cost_flag, s.network_concern_flag
FROM M360MART.SERVING.VW_PA_DUPLICATE_SAFETY s
JOIN M360MART.SERVING.VW_PA_REQUEST_360 r ON r.pa_id = s.pa_id
WHERE r.member_id = 'MBR-00000001'
  AND (s.has_potential_duplicate OR s.high_cost_flag OR s.network_concern_flag)""",
    ),
    (
        "What is this member's deductible and out of pocket remaining?",
        """SELECT member_id, deductible_remaining_amt, oop_remaining_amt,
       ytd_plan_paid_amt, ytd_member_paid_amt, total_claims_count
FROM M360MART.SERVING.VW_MEMBER_FINANCIAL
WHERE member_id = 'MBR-00000001'""",
    ),
    (
        "What is this member's clinical picture - conditions, medications, BMI and HbA1c?",
        """SELECT member_id, active_condition_count, chronic_condition_count,
       active_medication_count, bmi_value, hba1c_value,
       total_conservative_visits, imaging_study_count
FROM M360MART.SERVING.VW_MEMBER_CLINICAL_SUMMARY
WHERE member_id = 'MBR-00000001'""",
    ),
    (
        "Show this member's recent clinical and claim timeline",
        """SELECT event_date, event_type, event_desc, related_pa_id,
       related_claim_id, amount
FROM M360MART.SERVING.VW_MEMBER_TIMELINE
WHERE member_id = 'MBR-00000001'
ORDER BY event_date DESC
LIMIT 50""",
    ),
    (
        "Which of this member's requests have breached or are at risk of breaching turnaround time?",
        """SELECT t.pa_id, t.line_of_business, t.urgency_flag, t.sla_state,
       t.tat_business_days, t.regulatory_deadline_datetime_utc,
       t.days_until_deadline, t.clock_paused_days, t.is_open
FROM M360MART.SERVING.VW_PA_SLA_TRACKING t
JOIN M360MART.SERVING.VW_PA_REQUEST_360 r ON r.pa_id = t.pa_id
WHERE r.member_id = 'MBR-00000001' AND t.is_open
ORDER BY t.days_until_deadline""",
    ),
    (
        "How many criteria are unmet or indeterminate across this member's open requests?",
        """SELECT c.pa_id, c.criterion_result, COUNT(*) AS criterion_count
FROM M360MART.SERVING.VW_PA_CRITERIA_EVIDENCE c
JOIN M360MART.SERVING.VW_PA_WORKLIST w ON w.pa_id = c.pa_id
WHERE c.member_id = 'MBR-00000001'
GROUP BY c.pa_id, c.criterion_result
ORDER BY c.pa_id, c.criterion_result""",
    ),
]


def main() -> int:
    con = L.connect()
    try:
        # ---- real columns, straight from the deployed views
        rows = L.execute(
            con,
            f"""
            SELECT table_name, column_name
            FROM {AC.DATABASE}.INFORMATION_SCHEMA.COLUMNS
            WHERE table_schema = '{AC.SERVING_SCHEMA}'
              AND table_name IN ({','.join(f"'{v}'" for v in VIEWS)})
            ORDER BY table_name, ordinal_position
            """,
        )
        cols: dict[str, list[str]] = {}
        for table, col in rows:
            cols.setdefault(table, []).append(col)

        missing = [v for v in VIEWS if v not in cols]
        if missing:
            raise RuntimeError(f"views not found in {AC.SERVING_SCHEMA}: {missing}")

        print(f"Resolved {len(cols)} views, {sum(len(c) for c in cols.values())} columns")

        # ---- pick anchors that actually exercise the queries.
        # The member must have open PAs, a financial accumulator AND appeal history,
        # otherwise several VQRs execute cleanly against nothing and get rejected for
        # returning 0 rows. Only ~73% of members have a financial row (terminated
        # members have no current-year accumulator), so this cannot be left to chance.
        member_id = L.execute(
            con,
            f"""
            SELECT w.member_id
            FROM {AC.fqn('VW_PA_WORKLIST')} w
            JOIN {AC.fqn('VW_MEMBER_FINANCIAL')} f ON f.member_id = w.member_id
            JOIN {AC.fqn('VW_PA_REQUEST_360')} r ON r.member_id = w.member_id
            JOIN {AC.fqn('VW_PA_DECISION_AUDIT')} d
                 ON d.pa_id = r.pa_id AND d.decision_type = 'APPEAL'
            GROUP BY w.member_id
            ORDER BY COUNT(DISTINCT w.pa_id) DESC, w.member_id
            LIMIT 1
            """,
        )[0][0]
        pa_id = L.execute(
            con, f"SELECT pa_id FROM {AC.fqn('VW_PA_WORKLIST')} WHERE member_id = '{member_id}' LIMIT 1"
        )[0][0]
        proc = L.execute(
            con,
            f"SELECT service_code FROM {AC.fqn('VW_PA_PRECEDENT')} "
            "WHERE decided_count > 0 ORDER BY decided_count DESC LIMIT 1",
        )[0][0]
        prov = L.execute(
            con, f"SELECT provider_name FROM {AC.fqn('VW_PROVIDER_NETWORK')} LIMIT 1"
        )[0][0]
        print(f"Anchors: member={member_id} pa={pa_id} service_code={proc} provider={prov!r}")
        print()

        # ---- validate every VQR by running it
        accepted: list[dict] = []
        rejected: list[tuple[str, str]] = []
        for question, sql in CANDIDATE_VQRS:
            real = (
                sql.replace("MBR-00000001", member_id)
                .replace("PA-2025-000001", pa_id)
                .replace("'SVCCODE'", f"'{proc}'")
                .replace("provider_name = 'X'", f"provider_name = {prov!r}")
            )
            try:
                out = L.execute(con, real)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).split("\n")[0][:90]
                print(f"  FAIL            {question[:64]}\n           -> {msg}")
                rejected.append((question, msg))
                continue

            # A VQR that runs but returns nothing teaches Cortex Analyst a query
            # shape with no evidence behind it. Exclude it rather than ship a
            # verified query that demonstrably answers nothing.
            if not out:
                print(f"  EMPTY   0 rows  {question[:64]}")
                rejected.append((question, "executed but returned 0 rows"))
                continue

            print(f"  ok    {len(out):4d} rows  {question[:64]}")
            accepted.append({"sqlText": real, "correspondingQuestion": question})

        print()
        print(f"VQRs accepted {len(accepted)} / {len(CANDIDATE_VQRS)}")
        if rejected:
            print("REJECTED (excluded from the proto):")
            for q, m in rejected:
                print(f"  - {q}\n      {m}")

        proto = {
            "json_proto": {
                "name": AC.SEMANTIC_VIEW,
                "database": AC.DATABASE,
                "schema": AC.SERVING_SCHEMA,
                "tables": [
                    {
                        "database": AC.DATABASE,
                        "schema": AC.SERVING_SCHEMA,
                        "table": view,
                        "columnNames": cols[view],
                    }
                    for view in VIEWS
                ],
                "sqlSource": {"queries": accepted},
                "semanticDescription": (
                    "Member-anchored prior-authorization insight model for a PA review "
                    "analyst. Exposes member eligibility, clinical summary, financial "
                    "accumulators, event timeline, PA requests with per-criterion "
                    "evidence (MET / UNMET / INDETERMINATE), regulatory SLA tracking, "
                    "decision audit history, duplicate and safety flags, historical "
                    "decision precedent, and provider network and gold-card status. "
                    "This model reports evidence only. It must never be used to "
                    "recommend, predict or rank an approval, denial or pend decision; "
                    "the determination is made by the analyst."
                ),
                "metadata": {"warehouse": "COMPUTE_WH"},
            }
        }
        PROTO_PATH.write_text(json.dumps(proto, indent=2), encoding="utf-8")
        print()
        print(f"Wrote {PROTO_PATH}  ({PROTO_PATH.stat().st_size:,d} bytes)")
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
