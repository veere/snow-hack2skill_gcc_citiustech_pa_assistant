"""
SILVER layer specs.

OWNED BY: the SILVER-* implementation steps.

Register each silver table here using the same pattern as `schemas_bronze.py`:

    from config.schemas import Col, FK, TableSpec, register, with_audit
    S = "silver"

    register(TableSpec(
        layer=S, name="slv_member_master",
        grain="One row per resolved member identity",
        desc="...",
        primary_key=("member_id",),
        foreign_keys=(FK("member_id", "dim_memberProfile", "member_id", allow_null=False),),
        columns=with_audit(
            Col("member_id", "VARCHAR(20)", False, "..."),
            ...
        ),
    ))

RULES
-----
* Register tables in dependency order - FK targets before the tables that
  reference them. Load order follows registration order.
* Every table gets `with_audit(...)` so the determinism test can hash it.
* Do not introduce a column whose name matches any entry in
  `config/mart_config.json -> governance.banned_column_substrings`.
* FK targets may live in the bronze layer; reference them by bare table name.

Planned inventory (19 tables):
  member-centric  slv_member_master, slv_member_coverage_span,
                  slv_member_financial_position, slv_member_condition_profile,
                  slv_member_medication_history, slv_member_conservative_care,
                  slv_member_imaging_history, slv_member_lab_vitals_latest
  PA-centric      slv_pa_case, slv_pa_decision_history, slv_pa_precedent_stats,
                  slv_pa_criteria_ruleset, slv_pa_criteria_evaluation,
                  slv_duplicate_pa_candidates, slv_benefit_pa_requirement
  provider/other  slv_provider_profile, slv_claims_summary,
                  slv_utilization_flags, slv_member_event_summary
"""

from __future__ import annotations

from config.schemas import (
    Col, FK, TableSpec, register, with_audit,
    MATCH_METHODS, ENROLLMENT_STATUSES, LINES_OF_BUSINESS, GENDERS,
    MEDICAL_HISTORY_TYPES, CLINICAL_RECORD_TYPES,
)

S = "silver"

# ===========================================================================
# MEMBER-CENTRIC (8 tables)
# ===========================================================================

register(TableSpec(
    layer=S, name="slv_member_master",
    grain="One row per resolved member identity",
    desc="Identity resolution across dim_memberProfile and ext_patientProfile. "
         "Deterministic match on member_id; probabilistic on name+DOB+gender when "
         "ext payer_member_id is missing or conflicting.",
    primary_key=("member_id",),
    columns=with_audit(
        Col("member_id", "VARCHAR(20)", False, "Resolved payer member identifier"),
        Col("ext_patient_id", "VARCHAR(40)", True, "Matched external patient MRN"),
        Col("match_method", "VARCHAR(20)", False, "How the identity was resolved", enum=MATCH_METHODS),
        Col("match_confidence_score", "NUMBER(5,4)", True, "Match confidence 0-1", min_value=0, max_value=1),
        Col("first_name", "VARCHAR(100)", False, "Given name"),
        Col("last_name", "VARCHAR(100)", False, "Family name"),
        Col("date_of_birth", "DATE", False, "Date of birth"),
        Col("gender", "VARCHAR(2)", False, "Gender", enum=GENDERS),
        Col("age_years", "NUMBER(4,0)", False, "Age in years", min_value=0),
        Col("city", "VARCHAR(100)", True, "City"),
        Col("state", "VARCHAR(2)", True, "State"),
        Col("zip_code", "VARCHAR(10)", True, "Postal code"),
        Col("line_of_business", "VARCHAR(30)", True, "Current LOB", enum=LINES_OF_BUSINESS),
        Col("enrollment_status", "VARCHAR(20)", True, "Current enrollment status", enum=ENROLLMENT_STATUSES),
        Col("pcp_npi", "VARCHAR(10)", True, "Primary care provider NPI"),
        Col("pcp_name", "VARCHAR(300)", True, "Primary care provider name"),
        Col("risk_score_hcc", "NUMBER(6,3)", True, "HCC risk score", min_value=0),
        Col("is_deceased", "BOOLEAN", False, "True when member is deceased"),
    ),
))

register(TableSpec(
    layer=S, name="slv_member_coverage_span",
    grain="One row per non-overlapping coverage period per member",
    desc="Coverage timeline with gap detection. Non-overlapping spans derived from "
         "dim_memberPlan and dim_memberHistory. Gap flags enable the analyst to answer "
         "whether a member was covered on a specific date.",
    primary_key=("coverage_span_sk",),
    columns=with_audit(
        Col("coverage_span_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("plan_id", "VARCHAR(30)", False, "Plan"),
        Col("line_of_business", "VARCHAR(30)", False, "LOB", enum=LINES_OF_BUSINESS),
        Col("coverage_start_date", "DATE", False, "Span start"),
        Col("coverage_end_date", "DATE", True, "Span end, NULL if currently active"),
        Col("is_current", "BOOLEAN", False, "True for the active span"),
        Col("gap_before_days", "NUMBER(6,0)", False, "Days of no coverage before this span", min_value=0),
        Col("has_gap_before", "BOOLEAN", False, "True when there was a coverage gap"),
        Col("enrollment_status", "VARCHAR(20)", False, "Status during this span", enum=ENROLLMENT_STATUSES),
        Col("span_duration_days", "NUMBER(8,0)", True, "Duration in days", min_value=0),
    ),
))

register(TableSpec(
    layer=S, name="slv_member_financial_position",
    grain="One row per member per benefit year with current financial position",
    desc="Denormalized from dim_memberFinance. Provides deductible, OOP, and YTD "
         "spending position for eligibility and cost-sharing questions.",
    primary_key=("member_finance_sk",),
    columns=with_audit(
        Col("member_finance_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("plan_id", "VARCHAR(30)", False, "Plan"),
        Col("benefit_year", "NUMBER(4,0)", False, "Benefit year", min_value=2000, max_value=2100),
        Col("deductible_individual_amt", "NUMBER(12,2)", False, "Annual deductible", min_value=0),
        Col("deductible_met_amt", "NUMBER(12,2)", False, "Deductible satisfied", min_value=0),
        Col("deductible_remaining_amt", "NUMBER(12,2)", False, "Deductible remaining", min_value=0),
        Col("deductible_met_flag", "BOOLEAN", False, "True when fully met"),
        Col("oop_max_individual_amt", "NUMBER(12,2)", False, "OOP maximum", min_value=0),
        Col("oop_met_amt", "NUMBER(12,2)", False, "OOP satisfied", min_value=0),
        Col("oop_remaining_amt", "NUMBER(12,2)", False, "OOP remaining", min_value=0),
        Col("oop_max_met_flag", "BOOLEAN", False, "True when OOP max reached"),
        Col("ytd_allowed_amt", "NUMBER(14,2)", False, "Year-to-date allowed", min_value=0),
        Col("ytd_plan_paid_amt", "NUMBER(14,2)", False, "Year-to-date plan paid", min_value=0),
        Col("ytd_member_paid_amt", "NUMBER(14,2)", False, "Year-to-date member paid", min_value=0),
        Col("is_current", "BOOLEAN", False, "True for current year"),
    ),
))

register(TableSpec(
    layer=S, name="slv_member_condition_profile",
    grain="One row per member per active or resolved condition",
    desc="Condition profile from ext_patientClinicalRecords and ext_patientMedicalHistory. "
         "Includes ICD-10 codes and chronicity flags for diagnosis-support criteria.",
    primary_key=("condition_profile_sk",),
    columns=with_audit(
        Col("condition_profile_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("icd10_code", "VARCHAR(10)", True, "ICD-10-CM code"),
        Col("condition_desc", "VARCHAR(500)", True, "Condition description"),
        Col("onset_date", "DATE", True, "Onset date"),
        Col("resolved_date", "DATE", True, "Resolution date"),
        Col("is_active", "BOOLEAN", False, "True when currently active"),
        Col("is_chronic", "BOOLEAN", False, "True for chronic conditions"),
        Col("condition_category", "VARCHAR(60)", True, "Coarse grouping"),
        Col("source_type", "VARCHAR(30)", False, "Where this came from"),
    ),
))

register(TableSpec(
    layer=S, name="slv_member_medication_history",
    grain="One row per member per medication trial",
    desc="Medication history with adherence PDC. Step therapy is evaluated by checking "
         "whether a first-line drug in the same therapeutic class was tried with adequate "
         "adherence before the specialty drug was requested.",
    primary_key=("medication_history_sk",),
    columns=with_audit(
        Col("medication_history_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("medication_name", "VARCHAR(300)", True, "Medication name"),
        Col("generic_name", "VARCHAR(300)", True, "Generic name"),
        Col("therapeutic_class", "VARCHAR(120)", True, "Therapeutic class"),
        Col("ndc_code", "VARCHAR(20)", True, "NDC code"),
        Col("rxnorm_code", "VARCHAR(30)", True, "RxNorm code"),
        Col("prescribed_date", "DATE", True, "Date prescribed"),
        Col("discontinued_date", "DATE", True, "Date discontinued"),
        Col("discontinue_reason", "VARCHAR(200)", True, "Why stopped"),
        Col("therapy_duration_days", "NUMBER(8,0)", True, "Days on therapy", min_value=0),
        Col("adherence_pdc_pct", "NUMBER(5,2)", True, "PDC adherence percentage", min_value=0, max_value=100),
        Col("is_first_line", "BOOLEAN", True, "True for first-line therapy"),
        Col("is_specialty", "BOOLEAN", True, "True for specialty drugs"),
        Col("requires_pa", "BOOLEAN", True, "True when PA-gated"),
        Col("step_therapy_adequate_flag", "BOOLEAN", True,
            "True when this trial meets step-therapy requirements (adequate duration + adherence)"),
    ),
))

register(TableSpec(
    layer=S, name="slv_member_conservative_care",
    grain="One row per member per body system with conservative care visit counts",
    desc="PT/OT/chiropractic/injection visits by body system. Used to evaluate "
         "conservative-care-duration criteria for surgical and imaging PAs.",
    primary_key=("conservative_care_sk",),
    columns=with_audit(
        Col("conservative_care_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("body_system", "VARCHAR(60)", False, "Body system"),
        Col("pt_visit_count", "NUMBER(6,0)", False, "Physical therapy visits", min_value=0),
        Col("ot_visit_count", "NUMBER(6,0)", False, "Occupational therapy visits", min_value=0),
        Col("chiro_visit_count", "NUMBER(6,0)", False, "Chiropractic visits", min_value=0),
        Col("injection_count", "NUMBER(6,0)", False, "Injection procedures", min_value=0),
        Col("total_conservative_visits", "NUMBER(6,0)", False, "Total conservative care visits", min_value=0),
        Col("first_visit_date", "DATE", True, "Earliest conservative care date"),
        Col("last_visit_date", "DATE", True, "Most recent conservative care date"),
        Col("duration_days", "NUMBER(8,0)", True, "Days between first and last visit", min_value=0),
    ),
))

register(TableSpec(
    layer=S, name="slv_member_imaging_history",
    grain="One row per member per imaging modality per body region",
    desc="Prior imaging by modality and anatomical region. Used to evaluate "
         "imaging-prerequisite criteria (e.g., X-ray before MRI for the same region).",
    primary_key=("imaging_history_sk",),
    columns=with_audit(
        Col("imaging_history_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("modality", "VARCHAR(30)", False, "Imaging modality (XRAY, CT, MRI, PET, ULTRASOUND)"),
        Col("body_region", "VARCHAR(60)", False, "Anatomical region"),
        Col("study_count", "NUMBER(6,0)", False, "Number of studies", min_value=0),
        Col("first_study_date", "DATE", True, "Earliest study"),
        Col("last_study_date", "DATE", True, "Most recent study"),
    ),
))

register(TableSpec(
    layer=S, name="slv_member_lab_vitals_latest",
    grain="One row per member with the latest lab values and vitals relevant to PA criteria",
    desc="Latest BMI, HbA1c, CRP, ESR, eGFR values. BMI is the threshold input for "
         "bariatric surgery criteria; labs support medical-necessity evaluation.",
    primary_key=("member_id",),
    columns=with_audit(
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("bmi_value", "NUMBER(6,2)", True, "Latest BMI", min_value=0, max_value=100),
        Col("bmi_date", "DATE", True, "Date of BMI measurement"),
        Col("hba1c_value", "NUMBER(5,2)", True, "Latest HbA1c percentage", min_value=0),
        Col("hba1c_date", "DATE", True, "Date of HbA1c"),
        Col("crp_value", "NUMBER(10,3)", True, "Latest CRP mg/L"),
        Col("crp_date", "DATE", True, "Date of CRP"),
        Col("esr_value", "NUMBER(8,2)", True, "Latest ESR mm/hr"),
        Col("esr_date", "DATE", True, "Date of ESR"),
        Col("egfr_value", "NUMBER(8,2)", True, "Latest eGFR mL/min"),
        Col("egfr_date", "DATE", True, "Date of eGFR"),
        Col("height_cm", "NUMBER(6,2)", True, "Height in cm", min_value=0),
        Col("weight_kg", "NUMBER(7,2)", True, "Weight in kg", min_value=0),
    ),
))

# ===========================================================================
# PA-CENTRIC (7 tables)
# ===========================================================================

from config.schemas import (  # noqa: E402
    PA_STATUSES, PA_OPEN_STATUSES, PA_SERVICE_CATEGORIES, PA_URGENCY,
    PA_CHANNELS, PA_CERT_TYPES, PA_DECIDER_ROLES, DENIAL_REASON_CODES,
    PEND_REASON_CODES, CRITERION_TYPES, CRITERION_RESULTS, NETWORK_STATUSES,
)

register(TableSpec(
    layer=S, name="slv_pa_case",
    grain="One row per PA case with enriched SLA tracking",
    desc="PA case enriched with business-day TAT clock, pend pause accounting, "
         "and SLA state (on-track, at-risk, breached).",
    primary_key=("pa_id",),
    columns=with_audit(
        Col("pa_id", "VARCHAR(24)", False, "PA case identifier"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("plan_id", "VARCHAR(30)", False, "Plan"),
        Col("line_of_business", "VARCHAR(30)", False, "LOB", enum=LINES_OF_BUSINESS),
        Col("service_category", "VARCHAR(30)", False, "Service domain", enum=PA_SERVICE_CATEGORIES),
        Col("pa_status", "VARCHAR(24)", False, "Current status", enum=PA_STATUSES),
        Col("is_open", "BOOLEAN", False, "True when the case is still open"),
        Col("urgency_flag", "VARCHAR(20)", False, "Review speed", enum=PA_URGENCY),
        Col("received_datetime_utc", "TIMESTAMP_NTZ", False, "Received timestamp"),
        Col("decision_datetime_utc", "TIMESTAMP_NTZ", True, "Decision timestamp"),
        Col("regulatory_deadline_datetime_utc", "TIMESTAMP_NTZ", False, "Hard deadline"),
        Col("clock_paused_days", "NUMBER(6,0)", False, "Days excluded from SLA clock", min_value=0),
        Col("tat_business_days", "NUMBER(8,2)", True, "Business days elapsed", min_value=0),
        Col("sla_state", "VARCHAR(20)", False, "ON_TRACK, AT_RISK, or BREACHED"),
        Col("days_until_deadline", "NUMBER(8,2)", True, "Days remaining to deadline"),
        Col("pend_reason_code", "VARCHAR(10)", True, "Pend reason", enum=PEND_REASON_CODES),
        Col("denial_reason_code", "VARCHAR(10)", True, "Denial reason", enum=DENIAL_REASON_CODES),
        Col("auto_adjudicated_flag", "BOOLEAN", False, "Auto-adjudicated"),
        Col("gold_card_exempt_flag", "BOOLEAN", False, "Gold-card exempt"),
    ),
))

register(TableSpec(
    layer=S, name="slv_pa_decision_history",
    grain="One row per PA decision or appeal outcome",
    desc="Decision audit trail including original decision and appeal outcomes.",
    primary_key=("decision_history_sk",),
    columns=with_audit(
        Col("decision_history_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("pa_id", "VARCHAR(24)", False, "PA case"),
        Col("decision_type", "VARCHAR(20)", False, "ORIGINAL or APPEAL"),
        Col("decision_datetime_utc", "TIMESTAMP_NTZ", True, "When decided"),
        Col("pa_status", "VARCHAR(24)", True, "Status at decision", enum=PA_STATUSES),
        Col("decision_by_role", "VARCHAR(24)", True, "Decider role", enum=PA_DECIDER_ROLES),
        Col("denial_reason_code", "VARCHAR(10)", True, "Denial code", enum=DENIAL_REASON_CODES),
        Col("denial_reason_desc", "VARCHAR(300)", True, "Denial description"),
        Col("appeal_outcome", "VARCHAR(30)", True, "Appeal outcome if applicable"),
    ),
))

register(TableSpec(
    layer=S, name="slv_pa_precedent_stats",
    grain="One row per service code x diagnosis x LOB x specialty combination",
    desc=(
        "Historical decision counts by requested service, diagnosis, LOB and specialty. "
        "Reports what was decided in the past; it is NOT a prediction about any "
        "pending request."
    ),
    primary_key=("precedent_sk",),
    columns=with_audit(
        Col("precedent_sk", "VARCHAR(40)", False, "Surrogate key"),
        # Unified lookup key. requested_procedure_code is NULL for the ~47% of PA
        # requests that are drugs (SPECIALTY_DRUG is 100% NDC-keyed, HOME_HEALTH
        # 84%), so a procedure-code-only key makes drug precedent unfindable.
        Col("service_code", "VARCHAR(20)", True, "Unified requested service code: CPT/HCPCS, else NDC"),
        Col("service_code_system", "VARCHAR(20)", True, "Which code system service_code belongs to"),
        Col("requested_procedure_code", "VARCHAR(10)", True, "Procedure code (NULL for drug requests)"),
        Col("requested_ndc", "VARCHAR(20)", True, "NDC (NULL for procedure requests)"),
        Col("primary_dx_icd10", "VARCHAR(10)", True, "Diagnosis code"),
        Col("line_of_business", "VARCHAR(30)", True, "LOB", enum=LINES_OF_BUSINESS),
        Col("ordering_provider_specialty", "VARCHAR(150)", True, "Specialty"),
        Col("total_requests", "NUMBER(10,0)", False, "All requests in this combination", min_value=1),
        # decided_count is the honest denominator for an approval rate. total_requests
        # includes RECEIVED / PENDING_CLINICAL / PENDED_FOR_INFO / PEER_TO_PEER /
        # EXPIRED / WITHDRAWN - 32% of all requests - none of which reached a
        # determination. Dividing by total_requests systematically understates the rate.
        Col("decided_count", "NUMBER(10,0)", False, "Requests that reached approve/deny", min_value=0),
        Col("approved_count", "NUMBER(10,0)", False, "Approved or partially approved", min_value=0),
        Col("denied_count", "NUMBER(10,0)", False, "Denied count", min_value=0),
        # Nullable on purpose: with nothing decided there is no rate. Emitting 0.0
        # would read as "0% approved" and is materially misleading to cite.
        Col("approval_rate_pct", "NUMBER(5,2)", True,
            "approved / decided as a percentage. NULL when nothing was decided yet.",
            min_value=0, max_value=100),
    ),
))

register(TableSpec(
    layer=S, name="slv_pa_criteria_ruleset",
    grain="One row per criterion per service category (versioned)",
    desc="Structured medical-necessity rules.",
    primary_key=("criteria_ruleset_sk",),
    columns=with_audit(
        Col("criteria_ruleset_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("ruleset_id", "VARCHAR(40)", False, "Ruleset identifier"),
        Col("ruleset_version", "VARCHAR(20)", False, "Version"),
        Col("service_category", "VARCHAR(30)", False, "Service domain", enum=PA_SERVICE_CATEGORIES),
        Col("criterion_type", "VARCHAR(30)", False, "Type of criterion", enum=CRITERION_TYPES),
        Col("criterion_desc", "VARCHAR(500)", False, "Human-readable description"),
        Col("threshold_value", "VARCHAR(100)", True, "Threshold parameter"),
        Col("threshold_unit", "VARCHAR(40)", True, "Unit for the threshold"),
        Col("is_mandatory", "BOOLEAN", False, "True when the criterion must be met"),
    ),
))

register(TableSpec(
    layer=S, name="slv_pa_criteria_evaluation",
    grain="One row per PA per criterion",
    desc="Evaluates each criterion against clinical evidence. "
         "Result is MET, UNMET, or INDETERMINATE.",
    primary_key=("criteria_eval_sk",),
    columns=with_audit(
        Col("criteria_eval_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("pa_id", "VARCHAR(24)", False, "PA case"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("criterion_type", "VARCHAR(30)", False, "Criterion", enum=CRITERION_TYPES),
        Col("criterion_result", "VARCHAR(20)", False, "Result", enum=CRITERION_RESULTS),
        Col("evidence_summary", "VARCHAR(1000)", True, "Human-readable evidence"),
        Col("evidence_source_table", "VARCHAR(60)", True, "Evidence source table"),
        Col("evidence_source_id", "VARCHAR(80)", True, "Evidence row ID"),
        Col("criteria_met_count", "NUMBER(4,0)", True, "Criteria met for this PA", min_value=0),
        Col("criteria_total_count", "NUMBER(4,0)", True, "Total criteria for this PA", min_value=0),
    ),
))

register(TableSpec(
    layer=S, name="slv_duplicate_pa_candidates",
    grain="One row per pair of potentially duplicate PA requests",
    desc="Duplicate detection: same member, same service category, overlapping dates.",
    primary_key=("duplicate_sk",),
    columns=with_audit(
        Col("duplicate_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("pa_id_1", "VARCHAR(24)", False, "First PA"),
        Col("pa_id_2", "VARCHAR(24)", False, "Second PA"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("service_category", "VARCHAR(30)", False, "Shared service category", enum=PA_SERVICE_CATEGORIES),
        Col("overlap_days", "NUMBER(6,0)", True, "Days of date overlap", min_value=0),
        Col("similarity_score", "NUMBER(5,4)", False, "Similarity 0-1", min_value=0, max_value=1),
    ),
))

register(TableSpec(
    layer=S, name="slv_benefit_pa_requirement",
    grain="One row per service category per LOB with PA requirement flag",
    desc="Which services require PA under each LOB.",
    primary_key=("benefit_pa_sk",),
    columns=with_audit(
        Col("benefit_pa_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("line_of_business", "VARCHAR(30)", False, "LOB", enum=LINES_OF_BUSINESS),
        Col("service_category", "VARCHAR(30)", False, "Service domain", enum=PA_SERVICE_CATEGORIES),
        Col("requires_pa", "BOOLEAN", False, "True when PA is required"),
        Col("pa_threshold_amt", "NUMBER(12,2)", True, "Dollar threshold for PA", min_value=0),
        Col("auto_approve_eligible", "BOOLEAN", False, "Eligible for auto-approval"),
        Col("gold_card_eligible", "BOOLEAN", False, "Eligible for gold-carding"),
    ),
))

# ===========================================================================
# PROVIDER / OTHER (4 tables)
# ===========================================================================

register(TableSpec(
    layer=S, name="slv_provider_profile",
    grain="One row per provider NPI with network status and gold-card eligibility",
    desc="Provider profile with computed gold-card eligibility per TX HB3459.",
    primary_key=("provider_npi",),
    columns=with_audit(
        Col("provider_npi", "VARCHAR(10)", False, "Provider NPI"),
        Col("provider_name", "VARCHAR(300)", True, "Provider name"),
        Col("provider_specialty", "VARCHAR(150)", True, "Specialty"),
        Col("network_status", "VARCHAR(20)", True, "Network standing", enum=NETWORK_STATUSES),
        Col("total_pa_requests_6mo", "NUMBER(8,0)", False, "PA requests in last 6 months", min_value=0),
        Col("approved_pa_6mo", "NUMBER(8,0)", False, "Approved PAs in last 6 months", min_value=0),
        Col("approval_rate_6mo_pct", "NUMBER(5,2)", True, "6-month approval rate", min_value=0, max_value=100),
        Col("gold_card_eligible", "BOOLEAN", False, "Meets TX HB3459 gold-card threshold"),
        Col("gold_card_min_volume_met", "BOOLEAN", False, "Meets minimum volume for gold-card"),
    ),
))

register(TableSpec(
    layer=S, name="slv_claims_summary",
    grain="One row per member with claims aggregates",
    desc="Claims summary per member for utilization and cost analysis.",
    primary_key=("member_id",),
    columns=with_audit(
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("total_claims", "NUMBER(10,0)", False, "Total claim lines", min_value=0),
        Col("professional_claims", "NUMBER(10,0)", False, "Professional claims", min_value=0),
        Col("institutional_claims", "NUMBER(10,0)", False, "Institutional claims", min_value=0),
        Col("pharmacy_claims", "NUMBER(10,0)", False, "Pharmacy claims", min_value=0),
        Col("total_billed_amt", "NUMBER(14,2)", False, "Total billed", min_value=0),
        Col("total_allowed_amt", "NUMBER(14,2)", False, "Total allowed", min_value=0),
        Col("total_plan_paid_amt", "NUMBER(14,2)", False, "Total plan paid", min_value=0),
        Col("total_member_paid_amt", "NUMBER(14,2)", False, "Total member paid", min_value=0),
        Col("denied_claims", "NUMBER(10,0)", False, "Denied claim lines", min_value=0),
        Col("er_visit_count", "NUMBER(8,0)", False, "Emergency visits", min_value=0),
        Col("inpatient_admit_count", "NUMBER(8,0)", False, "Inpatient admissions", min_value=0),
    ),
))

register(TableSpec(
    layer=S, name="slv_utilization_flags",
    grain="One row per member with utilization risk flags",
    desc="Utilization flags for care management triggers.",
    primary_key=("member_id",),
    columns=with_audit(
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("high_er_utilizer_flag", "BOOLEAN", False, "3+ ER visits in 12 months"),
        Col("opioid_mgmt_flag", "BOOLEAN", False, "Active opioid management"),
        Col("case_mgmt_open_flag", "BOOLEAN", False, "Active case management"),
        Col("readmission_risk_flag", "BOOLEAN", False, "Readmission risk elevated"),
        Col("high_cost_flag", "BOOLEAN", False, "Top 5% by allowed amount"),
        Col("er_visits_12mo", "NUMBER(6,0)", False, "ER visits in last 12 months", min_value=0),
        Col("inpatient_admits_12mo", "NUMBER(6,0)", False, "Inpatient admits in last 12 months", min_value=0),
    ),
))

register(TableSpec(
    layer=S, name="slv_member_event_summary",
    grain="One row per member with event/interaction aggregates",
    desc="Member interaction summary across channels.",
    primary_key=("member_id",),
    columns=with_audit(
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("total_events", "NUMBER(10,0)", False, "Total interactions", min_value=0),
        Col("call_count", "NUMBER(8,0)", False, "Phone contacts", min_value=0),
        Col("portal_count", "NUMBER(8,0)", False, "Portal interactions", min_value=0),
        Col("mail_count", "NUMBER(8,0)", False, "Mail interactions", min_value=0),
        Col("appeal_count", "NUMBER(6,0)", False, "Appeals filed", min_value=0),
        Col("grievance_count", "NUMBER(6,0)", False, "Grievances filed", min_value=0),
        Col("pa_related_event_count", "NUMBER(8,0)", False, "Events linked to a PA", min_value=0),
        Col("avg_sentiment_score", "NUMBER(4,3)", True, "Average sentiment", min_value=-1, max_value=1),
        Col("escalated_count", "NUMBER(6,0)", False, "Escalated contacts", min_value=0),
    ),
))
