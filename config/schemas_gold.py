"""
GOLD layer specs.

OWNED BY: the GOLD-* implementation steps.

Register each gold table here using the same pattern as `schemas_bronze.py`.
Gold tables are wide, denormalised and built to answer an analyst's question in
a single row read - no joins required by the consuming app.

RULES
-----
* Register tables in dependency order.
* Every table gets `with_audit(...)`.
* HARD GOVERNANCE RULE: the assistant is exploratory and NON-decision-making.
  No gold table may contain a column that recommends, predicts or scores a
  decision. `gold_pa_criteria_evidence` reports MET / UNMET / INDETERMINATE per
  criterion with an evidence pointer and a met-count, and stops there. The
  validator fails the build on any column name matching
  `config/mart_config.json -> governance.banned_column_substrings`.
* Prefer explicit, readable column names - these become the app's vocabulary and
  the grounding surface for a conversational agent.

Planned inventory (13 tables):
  PA-centric      gold_pa_worklist, gold_pa_request_360,
                  gold_pa_criteria_evidence, gold_pa_sla_tracking,
                  gold_pa_decision_audit, gold_pa_precedent,
                  gold_pa_duplicate_safety_flags
  member-centric  gold_member_eligibility_snapshot,
                  gold_member_clinical_summary,
                  gold_member_financial_position, gold_member_timeline
  other           gold_provider_network_summary, gold_pa_kpi_daily
"""

from __future__ import annotations

from config.schemas import (
    Col, FK, TableSpec, register, with_audit,
    PA_STATUSES, PA_SERVICE_CATEGORIES, PA_URGENCY, LINES_OF_BUSINESS,
    CRITERION_TYPES, CRITERION_RESULTS, DENIAL_REASON_CODES,
    PEND_REASON_CODES, PA_DECIDER_ROLES, NETWORK_STATUSES, GENDERS,
    ENROLLMENT_STATUSES,
)

G = "gold"

# ===========================================================================
# PA-CENTRIC (7 tables)
# ===========================================================================

register(TableSpec(
    layer=G, name="gold_pa_worklist",
    grain="One row per open PA case, prioritized for analyst work",
    desc="Active PA worklist with SLA countdown, priority, documentation completeness.",
    primary_key=("pa_id",),
    columns=with_audit(
        Col("pa_id", "VARCHAR(24)", False, "PA case"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("member_name", "VARCHAR(200)", True, "Member name"),
        Col("line_of_business", "VARCHAR(30)", False, "LOB", enum=LINES_OF_BUSINESS),
        Col("service_category", "VARCHAR(30)", False, "Service domain", enum=PA_SERVICE_CATEGORIES),
        Col("urgency_flag", "VARCHAR(20)", False, "Urgency", enum=PA_URGENCY),
        Col("pa_status", "VARCHAR(24)", False, "Current status", enum=PA_STATUSES),
        Col("sla_state", "VARCHAR(20)", False, "ON_TRACK, AT_RISK, or BREACHED"),
        Col("days_until_deadline", "NUMBER(8,2)", True, "Days remaining"),
        Col("hours_until_deadline", "NUMBER(10,2)", True, "Hours remaining"),
        Col("criteria_met_count", "NUMBER(4,0)", True, "Criteria met", min_value=0),
        Col("criteria_total_count", "NUMBER(4,0)", True, "Total criteria", min_value=0),
        Col("completeness_pct", "NUMBER(5,2)", True, "Documentation completeness %", min_value=0, max_value=100),
        Col("priority_score", "NUMBER(6,2)", False, "Priority (higher = more urgent)", min_value=0),
        Col("ordering_provider_name", "VARCHAR(300)", True, "Provider"),
        Col("requested_procedure_desc", "VARCHAR(500)", True, "What was requested"),
    ),
))

register(TableSpec(
    layer=G, name="gold_pa_request_360",
    grain="One row per PA case, fully denormalized",
    desc="Complete PA request with member, provider, clinical and financial context.",
    primary_key=("pa_id",),
    columns=with_audit(
        Col("pa_id", "VARCHAR(24)", False, "PA case"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("member_name", "VARCHAR(200)", True, "Member name"),
        Col("member_age", "NUMBER(4,0)", True, "Age", min_value=0),
        Col("member_gender", "VARCHAR(2)", True, "Gender", enum=GENDERS),
        Col("line_of_business", "VARCHAR(30)", False, "LOB", enum=LINES_OF_BUSINESS),
        Col("plan_id", "VARCHAR(30)", False, "Plan"),
        Col("service_category", "VARCHAR(30)", False, "Service domain", enum=PA_SERVICE_CATEGORIES),
        Col("pa_status", "VARCHAR(24)", False, "Status", enum=PA_STATUSES),
        Col("urgency_flag", "VARCHAR(20)", False, "Urgency", enum=PA_URGENCY),
        Col("requested_procedure_desc", "VARCHAR(500)", True, "Procedure description"),
        Col("primary_dx_desc", "VARCHAR(500)", True, "Primary diagnosis"),
        Col("ordering_provider_name", "VARCHAR(300)", True, "Provider"),
        Col("ordering_provider_specialty", "VARCHAR(150)", True, "Specialty"),
        Col("network_status", "VARCHAR(20)", True, "Network", enum=NETWORK_STATUSES),
        Col("estimated_allowed_amt", "NUMBER(12,2)", True, "Estimated cost", min_value=0),
        Col("sla_state", "VARCHAR(20)", True, "SLA state"),
        Col("criteria_met_count", "NUMBER(4,0)", True, "Criteria met", min_value=0),
        Col("criteria_total_count", "NUMBER(4,0)", True, "Total criteria", min_value=0),
        Col("deductible_remaining_amt", "NUMBER(12,2)", True, "Deductible remaining", min_value=0),
    ),
))

register(TableSpec(
    layer=G, name="gold_pa_criteria_evidence",
    grain="One row per PA per criterion with evidence text",
    desc="Criterion-level evidence for each PA. Reports MET/UNMET/INDETERMINATE "
         "with human-readable evidence. Never recommends a decision.",
    primary_key=("criteria_evidence_sk",),
    columns=with_audit(
        Col("criteria_evidence_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("pa_id", "VARCHAR(24)", False, "PA case"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("criterion_type", "VARCHAR(30)", False, "Criterion", enum=CRITERION_TYPES),
        Col("criterion_result", "VARCHAR(20)", False, "Result", enum=CRITERION_RESULTS),
        Col("evidence_text", "VARCHAR(1000)", True, "Human-readable evidence"),
        Col("criterion_desc", "VARCHAR(500)", True, "Criterion description"),
    ),
))

register(TableSpec(
    layer=G, name="gold_pa_sla_tracking",
    grain="One row per PA with SLA metrics",
    desc="SLA tracking dashboard data.",
    primary_key=("pa_id",),
    columns=with_audit(
        Col("pa_id", "VARCHAR(24)", False, "PA case"),
        Col("line_of_business", "VARCHAR(30)", False, "LOB", enum=LINES_OF_BUSINESS),
        Col("urgency_flag", "VARCHAR(20)", False, "Urgency", enum=PA_URGENCY),
        Col("sla_state", "VARCHAR(20)", False, "ON_TRACK/AT_RISK/BREACHED"),
        Col("tat_business_days", "NUMBER(8,2)", True, "Business days elapsed", min_value=0),
        Col("regulatory_deadline_datetime_utc", "TIMESTAMP_NTZ", False, "Deadline"),
        Col("days_until_deadline", "NUMBER(8,2)", True, "Days remaining"),
        Col("clock_paused_days", "NUMBER(6,0)", False, "Clock paused days", min_value=0),
        Col("is_open", "BOOLEAN", False, "Is case open"),
    ),
))

register(TableSpec(
    layer=G, name="gold_pa_decision_audit",
    grain="One row per PA decision including appeals",
    desc="Decision audit trail for compliance reporting.",
    primary_key=("decision_audit_sk",),
    columns=with_audit(
        Col("decision_audit_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("pa_id", "VARCHAR(24)", False, "PA case"),
        Col("decision_type", "VARCHAR(20)", False, "ORIGINAL or APPEAL"),
        Col("pa_status", "VARCHAR(24)", True, "Outcome", enum=PA_STATUSES),
        Col("decision_by_role", "VARCHAR(24)", True, "Decider", enum=PA_DECIDER_ROLES),
        Col("denial_reason_desc", "VARCHAR(300)", True, "Denial reason"),
        Col("appeal_outcome", "VARCHAR(30)", True, "Appeal outcome"),
        Col("auto_adjudicated_flag", "BOOLEAN", True, "Auto-adjudicated"),
    ),
))

register(TableSpec(
    layer=G, name="gold_pa_precedent",
    grain="One row per service code x diagnosis x LOB precedent",
    desc=(
        "Historical decision counts for precedent lookup. Records what was decided "
        "before; it is not a prediction about a pending request."
    ),
    primary_key=("precedent_sk",),
    columns=with_audit(
        Col("precedent_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("service_code", "VARCHAR(20)", True, "Unified service code: CPT/HCPCS, else NDC for drugs"),
        Col("service_code_system", "VARCHAR(20)", True, "Code system for service_code"),
        Col("requested_procedure_code", "VARCHAR(10)", True, "Procedure (NULL for drug requests)"),
        Col("requested_ndc", "VARCHAR(20)", True, "NDC (NULL for procedure requests)"),
        Col("primary_dx_icd10", "VARCHAR(10)", True, "Diagnosis"),
        Col("line_of_business", "VARCHAR(30)", True, "LOB", enum=LINES_OF_BUSINESS),
        Col("total_requests", "NUMBER(10,0)", False, "All requests", min_value=1),
        Col("decided_count", "NUMBER(10,0)", False, "Reached approve/deny", min_value=0),
        Col("approved_count", "NUMBER(10,0)", False, "Approved", min_value=0),
        Col("denied_count", "NUMBER(10,0)", False, "Denied", min_value=0),
        Col("approval_rate_pct", "NUMBER(5,2)", True,
            "approved / decided %. NULL when nothing decided - never fabricated as 0.",
            min_value=0, max_value=100),
    ),
))

register(TableSpec(
    layer=G, name="gold_pa_duplicate_safety_flags",
    grain="One row per PA with duplicate/safety indicators",
    desc="Duplicate and safety flags for each PA.",
    primary_key=("pa_id",),
    columns=with_audit(
        Col("pa_id", "VARCHAR(24)", False, "PA case"),
        Col("has_potential_duplicate", "BOOLEAN", False, "Potential duplicate detected"),
        Col("duplicate_pa_id", "VARCHAR(24)", True, "Most similar duplicate PA"),
        Col("duplicate_similarity", "NUMBER(5,4)", True, "Similarity score", min_value=0, max_value=1),
        Col("high_cost_flag", "BOOLEAN", False, "High-cost review needed"),
        Col("network_concern_flag", "BOOLEAN", False, "Out-of-network concern"),
    ),
))

# ===========================================================================
# MEMBER / PROVIDER / KPI (6 tables)
# ===========================================================================

register(TableSpec(
    layer=G, name="gold_member_eligibility_snapshot",
    grain="One row per member with current eligibility state",
    desc="Eligibility snapshot for quick eligibility checks.",
    primary_key=("member_id",),
    columns=with_audit(
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("member_name", "VARCHAR(200)", True, "Name"),
        Col("date_of_birth", "DATE", True, "DOB"),
        Col("age_years", "NUMBER(4,0)", True, "Age", min_value=0),
        Col("gender", "VARCHAR(2)", True, "Gender", enum=GENDERS),
        Col("enrollment_status", "VARCHAR(20)", True, "Status", enum=ENROLLMENT_STATUSES),
        Col("line_of_business", "VARCHAR(30)", True, "LOB", enum=LINES_OF_BUSINESS),
        Col("plan_id", "VARCHAR(30)", True, "Current plan"),
        Col("coverage_start_date", "DATE", True, "Current coverage start"),
        Col("is_eligible_today", "BOOLEAN", False, "Eligible as of reference date"),
        Col("has_coverage_gap", "BOOLEAN", False, "Has any gap in coverage history"),
    ),
))

register(TableSpec(
    layer=G, name="gold_member_clinical_summary",
    grain="One row per member with clinical summary",
    desc="Clinical summary for PA review context.",
    primary_key=("member_id",),
    columns=with_audit(
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("active_condition_count", "NUMBER(6,0)", False, "Active conditions", min_value=0),
        Col("chronic_condition_count", "NUMBER(6,0)", False, "Chronic conditions", min_value=0),
        Col("active_medication_count", "NUMBER(6,0)", False, "Active medications", min_value=0),
        Col("bmi_value", "NUMBER(6,2)", True, "Latest BMI", min_value=0, max_value=100),
        Col("hba1c_value", "NUMBER(5,2)", True, "Latest HbA1c", min_value=0),
        Col("total_conservative_visits", "NUMBER(6,0)", False, "Conservative care visits", min_value=0),
        Col("imaging_study_count", "NUMBER(6,0)", False, "Imaging studies", min_value=0),
    ),
))

register(TableSpec(
    layer=G, name="gold_member_financial_position",
    grain="One row per member with current financial position",
    desc="Current-year financial position for cost-sharing context.",
    primary_key=("member_id",),
    columns=with_audit(
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("deductible_remaining_amt", "NUMBER(12,2)", True, "Deductible remaining", min_value=0),
        Col("oop_remaining_amt", "NUMBER(12,2)", True, "OOP remaining", min_value=0),
        Col("ytd_plan_paid_amt", "NUMBER(14,2)", True, "YTD plan paid", min_value=0),
        Col("ytd_member_paid_amt", "NUMBER(14,2)", True, "YTD member paid", min_value=0),
        Col("total_claims_count", "NUMBER(10,0)", True, "Total claims", min_value=0),
    ),
))

register(TableSpec(
    layer=G, name="gold_member_timeline",
    grain="One row per chronological event for a member",
    desc="Unified chronological stream of all member events: clinical, claims, PA, contacts.",
    primary_key=("timeline_sk",),
    columns=with_audit(
        Col("timeline_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("event_date", "DATE", False, "Date of the event"),
        Col("event_type", "VARCHAR(30)", False, "Type of event"),
        Col("event_desc", "VARCHAR(500)", True, "Description"),
        Col("related_pa_id", "VARCHAR(24)", True, "Related PA if any"),
        Col("related_claim_id", "VARCHAR(40)", True, "Related claim if any"),
        Col("amount", "NUMBER(14,2)", True, "Financial amount if applicable"),
    ),
))

register(TableSpec(
    layer=G, name="gold_provider_network_summary",
    grain="One row per provider with network and gold-card summary",
    desc="Provider network summary for PA routing decisions.",
    primary_key=("provider_npi",),
    columns=with_audit(
        Col("provider_npi", "VARCHAR(10)", False, "NPI"),
        Col("provider_name", "VARCHAR(300)", True, "Name"),
        Col("provider_specialty", "VARCHAR(150)", True, "Specialty"),
        Col("network_status", "VARCHAR(20)", True, "Network", enum=NETWORK_STATUSES),
        Col("gold_card_eligible", "BOOLEAN", False, "Gold-card eligible"),
        Col("approval_rate_6mo_pct", "NUMBER(5,2)", True, "6mo approval rate", min_value=0, max_value=100),
        Col("total_pa_requests_6mo", "NUMBER(8,0)", False, "6mo PA requests", min_value=0),
    ),
))

register(TableSpec(
    layer=G, name="gold_pa_kpi_daily",
    grain="One row per date with aggregate PA KPIs",
    desc="Daily PA volume and SLA compliance metrics.",
    primary_key=("kpi_date",),
    columns=with_audit(
        Col("kpi_date", "DATE", False, "Date"),
        Col("total_received", "NUMBER(8,0)", False, "PAs received", min_value=0),
        Col("total_decided", "NUMBER(8,0)", False, "PAs decided", min_value=0),
        Col("total_approved", "NUMBER(8,0)", False, "Approved", min_value=0),
        Col("total_denied", "NUMBER(8,0)", False, "Denied", min_value=0),
        Col("open_cases", "NUMBER(8,0)", False, "Open cases", min_value=0),
        Col("breached_cases", "NUMBER(8,0)", False, "SLA breached", min_value=0),
        Col("avg_tat_days", "NUMBER(8,2)", True, "Average TAT in days", min_value=0),
        Col("auto_adj_pct", "NUMBER(5,2)", True, "Auto-adjudication rate %", min_value=0, max_value=100),
    ),
))
