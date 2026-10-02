-- M360MART SILVER layer DDL
-- Generated from config/schemas_silver.py by sql/generate_ddl.py - do not edit manually.
-- All types are explicit (never inferred from data).

CREATE OR REPLACE TABLE M360MART.SILVER.slv_member_master (
    member_id                VARCHAR(20) NOT NULL COMMENT 'Resolved payer member identifier',
    ext_patient_id           VARCHAR(40) COMMENT 'Matched external patient MRN',
    match_method             VARCHAR(20) NOT NULL COMMENT 'How the identity was resolved',
    match_confidence_score   NUMBER(5,4) COMMENT 'Match confidence 0-1',
    first_name               VARCHAR(100) NOT NULL COMMENT 'Given name',
    last_name                VARCHAR(100) NOT NULL COMMENT 'Family name',
    date_of_birth            DATE NOT NULL COMMENT 'Date of birth',
    gender                   VARCHAR(2) NOT NULL COMMENT 'Gender',
    age_years                NUMBER(4,0) NOT NULL COMMENT 'Age in years',
    city                     VARCHAR(100) COMMENT 'City',
    state                    VARCHAR(2) COMMENT 'State',
    zip_code                 VARCHAR(10) COMMENT 'Postal code',
    line_of_business         VARCHAR(30) COMMENT 'Current LOB',
    enrollment_status        VARCHAR(20) COMMENT 'Current enrollment status',
    pcp_npi                  VARCHAR(10) COMMENT 'Primary care provider NPI',
    pcp_name                 VARCHAR(300) COMMENT 'Primary care provider name',
    risk_score_hcc           NUMBER(6,3) COMMENT 'HCC risk score',
    is_deceased              BOOLEAN NOT NULL COMMENT 'True when member is deceased',
    source_system            VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc          TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash              VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_member_master PRIMARY KEY (member_id)
)
COMMENT = 'One row per resolved member identity | Identity resolution across dim_memberProfile and ext_patientProfile. Deterministic match on member_id; probabilistic on name+DOB+gender when ext payer_member_id is missing or conflicting.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_member_coverage_span (
    coverage_span_sk      VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    member_id             VARCHAR(20) NOT NULL COMMENT 'Member',
    plan_id               VARCHAR(30) NOT NULL COMMENT 'Plan',
    line_of_business      VARCHAR(30) NOT NULL COMMENT 'LOB',
    coverage_start_date   DATE NOT NULL COMMENT 'Span start',
    coverage_end_date     DATE COMMENT 'Span end, NULL if currently active',
    is_current            BOOLEAN NOT NULL COMMENT 'True for the active span',
    gap_before_days       NUMBER(6,0) NOT NULL COMMENT 'Days of no coverage before this span',
    has_gap_before        BOOLEAN NOT NULL COMMENT 'True when there was a coverage gap',
    enrollment_status     VARCHAR(20) NOT NULL COMMENT 'Status during this span',
    span_duration_days    NUMBER(8,0) COMMENT 'Duration in days',
    source_system         VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc       TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash           VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_member_coverage_span PRIMARY KEY (coverage_span_sk)
)
COMMENT = 'One row per non-overlapping coverage period per member | Coverage timeline with gap detection. Non-overlapping spans derived from dim_memberPlan and dim_memberHistory. Gap flags enable the analyst to answer whether a member was covered on a specific date.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_member_financial_position (
    member_finance_sk           VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    member_id                   VARCHAR(20) NOT NULL COMMENT 'Member',
    plan_id                     VARCHAR(30) NOT NULL COMMENT 'Plan',
    benefit_year                NUMBER(4,0) NOT NULL COMMENT 'Benefit year',
    deductible_individual_amt   NUMBER(12,2) NOT NULL COMMENT 'Annual deductible',
    deductible_met_amt          NUMBER(12,2) NOT NULL COMMENT 'Deductible satisfied',
    deductible_remaining_amt    NUMBER(12,2) NOT NULL COMMENT 'Deductible remaining',
    deductible_met_flag         BOOLEAN NOT NULL COMMENT 'True when fully met',
    oop_max_individual_amt      NUMBER(12,2) NOT NULL COMMENT 'OOP maximum',
    oop_met_amt                 NUMBER(12,2) NOT NULL COMMENT 'OOP satisfied',
    oop_remaining_amt           NUMBER(12,2) NOT NULL COMMENT 'OOP remaining',
    oop_max_met_flag            BOOLEAN NOT NULL COMMENT 'True when OOP max reached',
    ytd_allowed_amt             NUMBER(14,2) NOT NULL COMMENT 'Year-to-date allowed',
    ytd_plan_paid_amt           NUMBER(14,2) NOT NULL COMMENT 'Year-to-date plan paid',
    ytd_member_paid_amt         NUMBER(14,2) NOT NULL COMMENT 'Year-to-date member paid',
    is_current                  BOOLEAN NOT NULL COMMENT 'True for current year',
    source_system               VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc             TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                 VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_member_financial_position PRIMARY KEY (member_finance_sk)
)
COMMENT = 'One row per member per benefit year with current financial position | Denormalized from dim_memberFinance. Provides deductible, OOP, and YTD spending position for eligibility and cost-sharing questions.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_member_condition_profile (
    condition_profile_sk   VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    member_id              VARCHAR(20) NOT NULL COMMENT 'Member',
    icd10_code             VARCHAR(10) COMMENT 'ICD-10-CM code',
    condition_desc         VARCHAR(500) COMMENT 'Condition description',
    onset_date             DATE COMMENT 'Onset date',
    resolved_date          DATE COMMENT 'Resolution date',
    is_active              BOOLEAN NOT NULL COMMENT 'True when currently active',
    is_chronic             BOOLEAN NOT NULL COMMENT 'True for chronic conditions',
    condition_category     VARCHAR(60) COMMENT 'Coarse grouping',
    source_type            VARCHAR(30) NOT NULL COMMENT 'Where this came from',
    source_system          VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc        TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash            VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_member_condition_profile PRIMARY KEY (condition_profile_sk)
)
COMMENT = 'One row per member per active or resolved condition | Condition profile from ext_patientClinicalRecords and ext_patientMedicalHistory. Includes ICD-10 codes and chronicity flags for diagnosis-support criteria.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_member_medication_history (
    medication_history_sk        VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    member_id                    VARCHAR(20) NOT NULL COMMENT 'Member',
    medication_name              VARCHAR(300) COMMENT 'Medication name',
    generic_name                 VARCHAR(300) COMMENT 'Generic name',
    therapeutic_class            VARCHAR(120) COMMENT 'Therapeutic class',
    ndc_code                     VARCHAR(20) COMMENT 'NDC code',
    rxnorm_code                  VARCHAR(30) COMMENT 'RxNorm code',
    prescribed_date              DATE COMMENT 'Date prescribed',
    discontinued_date            DATE COMMENT 'Date discontinued',
    discontinue_reason           VARCHAR(200) COMMENT 'Why stopped',
    therapy_duration_days        NUMBER(8,0) COMMENT 'Days on therapy',
    adherence_pdc_pct            NUMBER(5,2) COMMENT 'PDC adherence percentage',
    is_first_line                BOOLEAN COMMENT 'True for first-line therapy',
    is_specialty                 BOOLEAN COMMENT 'True for specialty drugs',
    requires_pa                  BOOLEAN COMMENT 'True when PA-gated',
    step_therapy_adequate_flag   BOOLEAN COMMENT 'True when this trial meets step-therapy requirements (adequate duration + adherence)',
    source_system                VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc              TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                  VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_member_medication_history PRIMARY KEY (medication_history_sk)
)
COMMENT = 'One row per member per medication trial | Medication history with adherence PDC. Step therapy is evaluated by checking whether a first-line drug in the same therapeutic class was tried with adequate adherence before the specialty drug was requested.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_member_conservative_care (
    conservative_care_sk        VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    member_id                   VARCHAR(20) NOT NULL COMMENT 'Member',
    body_system                 VARCHAR(60) NOT NULL COMMENT 'Body system',
    pt_visit_count              NUMBER(6,0) NOT NULL COMMENT 'Physical therapy visits',
    ot_visit_count              NUMBER(6,0) NOT NULL COMMENT 'Occupational therapy visits',
    chiro_visit_count           NUMBER(6,0) NOT NULL COMMENT 'Chiropractic visits',
    injection_count             NUMBER(6,0) NOT NULL COMMENT 'Injection procedures',
    total_conservative_visits   NUMBER(6,0) NOT NULL COMMENT 'Total conservative care visits',
    first_visit_date            DATE COMMENT 'Earliest conservative care date',
    last_visit_date             DATE COMMENT 'Most recent conservative care date',
    duration_days               NUMBER(8,0) COMMENT 'Days between first and last visit',
    source_system               VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc             TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                 VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_member_conservative_care PRIMARY KEY (conservative_care_sk)
)
COMMENT = 'One row per member per body system with conservative care visit counts | PT/OT/chiropractic/injection visits by body system. Used to evaluate conservative-care-duration criteria for surgical and imaging PAs.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_member_imaging_history (
    imaging_history_sk   VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    member_id            VARCHAR(20) NOT NULL COMMENT 'Member',
    modality             VARCHAR(30) NOT NULL COMMENT 'Imaging modality (XRAY, CT, MRI, PET, ULTRASOUND)',
    body_region          VARCHAR(60) NOT NULL COMMENT 'Anatomical region',
    study_count          NUMBER(6,0) NOT NULL COMMENT 'Number of studies',
    first_study_date     DATE COMMENT 'Earliest study',
    last_study_date      DATE COMMENT 'Most recent study',
    source_system        VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc      TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash          VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_member_imaging_history PRIMARY KEY (imaging_history_sk)
)
COMMENT = 'One row per member per imaging modality per body region | Prior imaging by modality and anatomical region. Used to evaluate imaging-prerequisite criteria (e.g., X-ray before MRI for the same region).';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_member_lab_vitals_latest (
    member_id         VARCHAR(20) NOT NULL COMMENT 'Member',
    bmi_value         NUMBER(6,2) COMMENT 'Latest BMI',
    bmi_date          DATE COMMENT 'Date of BMI measurement',
    hba1c_value       NUMBER(5,2) COMMENT 'Latest HbA1c percentage',
    hba1c_date        DATE COMMENT 'Date of HbA1c',
    crp_value         NUMBER(10,3) COMMENT 'Latest CRP mg/L',
    crp_date          DATE COMMENT 'Date of CRP',
    esr_value         NUMBER(8,2) COMMENT 'Latest ESR mm/hr',
    esr_date          DATE COMMENT 'Date of ESR',
    egfr_value        NUMBER(8,2) COMMENT 'Latest eGFR mL/min',
    egfr_date         DATE COMMENT 'Date of eGFR',
    height_cm         NUMBER(6,2) COMMENT 'Height in cm',
    weight_kg         NUMBER(7,2) COMMENT 'Weight in kg',
    source_system     VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc   TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash       VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_member_lab_vitals_latest PRIMARY KEY (member_id)
)
COMMENT = 'One row per member with the latest lab values and vitals relevant to PA criteria | Latest BMI, HbA1c, CRP, ESR, eGFR values. BMI is the threshold input for bariatric surgery criteria; labs support medical-necessity evaluation.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_pa_case (
    pa_id                              VARCHAR(24) NOT NULL COMMENT 'PA case identifier',
    member_id                          VARCHAR(20) NOT NULL COMMENT 'Member',
    plan_id                            VARCHAR(30) NOT NULL COMMENT 'Plan',
    line_of_business                   VARCHAR(30) NOT NULL COMMENT 'LOB',
    service_category                   VARCHAR(30) NOT NULL COMMENT 'Service domain',
    pa_status                          VARCHAR(24) NOT NULL COMMENT 'Current status',
    is_open                            BOOLEAN NOT NULL COMMENT 'True when the case is still open',
    urgency_flag                       VARCHAR(20) NOT NULL COMMENT 'Review speed',
    received_datetime_utc              TIMESTAMP_NTZ NOT NULL COMMENT 'Received timestamp',
    decision_datetime_utc              TIMESTAMP_NTZ COMMENT 'Decision timestamp',
    regulatory_deadline_datetime_utc   TIMESTAMP_NTZ NOT NULL COMMENT 'Hard deadline',
    clock_paused_days                  NUMBER(6,0) NOT NULL COMMENT 'Days excluded from SLA clock',
    tat_business_days                  NUMBER(8,2) COMMENT 'Business days elapsed',
    sla_state                          VARCHAR(20) NOT NULL COMMENT 'ON_TRACK, AT_RISK, or BREACHED',
    days_until_deadline                NUMBER(8,2) COMMENT 'Days remaining to deadline',
    pend_reason_code                   VARCHAR(10) COMMENT 'Pend reason',
    denial_reason_code                 VARCHAR(10) COMMENT 'Denial reason',
    auto_adjudicated_flag              BOOLEAN NOT NULL COMMENT 'Auto-adjudicated',
    gold_card_exempt_flag              BOOLEAN NOT NULL COMMENT 'Gold-card exempt',
    source_system                      VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc                    TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                        VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_pa_case PRIMARY KEY (pa_id)
)
COMMENT = 'One row per PA case with enriched SLA tracking | PA case enriched with business-day TAT clock, pend pause accounting, and SLA state (on-track, at-risk, breached).';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_pa_decision_history (
    decision_history_sk     VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    pa_id                   VARCHAR(24) NOT NULL COMMENT 'PA case',
    decision_type           VARCHAR(20) NOT NULL COMMENT 'ORIGINAL or APPEAL',
    decision_datetime_utc   TIMESTAMP_NTZ COMMENT 'When decided',
    pa_status               VARCHAR(24) COMMENT 'Status at decision',
    decision_by_role        VARCHAR(24) COMMENT 'Decider role',
    denial_reason_code      VARCHAR(10) COMMENT 'Denial code',
    denial_reason_desc      VARCHAR(300) COMMENT 'Denial description',
    appeal_outcome          VARCHAR(30) COMMENT 'Appeal outcome if applicable',
    source_system           VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc         TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash             VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_pa_decision_history PRIMARY KEY (decision_history_sk)
)
COMMENT = 'One row per PA decision or appeal outcome | Decision audit trail including original decision and appeal outcomes.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_pa_precedent_stats (
    precedent_sk                  VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    service_code                  VARCHAR(20) COMMENT 'Unified requested service code: CPT/HCPCS, else NDC',
    service_code_system           VARCHAR(20) COMMENT 'Which code system service_code belongs to',
    requested_procedure_code      VARCHAR(10) COMMENT 'Procedure code (NULL for drug requests)',
    requested_ndc                 VARCHAR(20) COMMENT 'NDC (NULL for procedure requests)',
    primary_dx_icd10              VARCHAR(10) COMMENT 'Diagnosis code',
    line_of_business              VARCHAR(30) COMMENT 'LOB',
    ordering_provider_specialty   VARCHAR(150) COMMENT 'Specialty',
    total_requests                NUMBER(10,0) NOT NULL COMMENT 'All requests in this combination',
    decided_count                 NUMBER(10,0) NOT NULL COMMENT 'Requests that reached approve/deny',
    approved_count                NUMBER(10,0) NOT NULL COMMENT 'Approved or partially approved',
    denied_count                  NUMBER(10,0) NOT NULL COMMENT 'Denied count',
    approval_rate_pct             NUMBER(5,2) COMMENT 'approved / decided as a percentage. NULL when nothing was decided yet.',
    source_system                 VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc               TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                   VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_pa_precedent_stats PRIMARY KEY (precedent_sk)
)
COMMENT = 'One row per service code x diagnosis x LOB x specialty combination | Historical decision counts by requested service, diagnosis, LOB and specialty. Reports what was decided in the past; it is NOT a prediction about any pending request.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_pa_criteria_ruleset (
    criteria_ruleset_sk   VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    ruleset_id            VARCHAR(40) NOT NULL COMMENT 'Ruleset identifier',
    ruleset_version       VARCHAR(20) NOT NULL COMMENT 'Version',
    service_category      VARCHAR(30) NOT NULL COMMENT 'Service domain',
    criterion_type        VARCHAR(30) NOT NULL COMMENT 'Type of criterion',
    criterion_desc        VARCHAR(500) NOT NULL COMMENT 'Human-readable description',
    threshold_value       VARCHAR(100) COMMENT 'Threshold parameter',
    threshold_unit        VARCHAR(40) COMMENT 'Unit for the threshold',
    is_mandatory          BOOLEAN NOT NULL COMMENT 'True when the criterion must be met',
    source_system         VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc       TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash           VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_pa_criteria_ruleset PRIMARY KEY (criteria_ruleset_sk)
)
COMMENT = 'One row per criterion per service category (versioned) | Structured medical-necessity rules.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_pa_criteria_evaluation (
    criteria_eval_sk        VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    pa_id                   VARCHAR(24) NOT NULL COMMENT 'PA case',
    member_id               VARCHAR(20) NOT NULL COMMENT 'Member',
    criterion_type          VARCHAR(30) NOT NULL COMMENT 'Criterion',
    criterion_result        VARCHAR(20) NOT NULL COMMENT 'Result',
    evidence_summary        VARCHAR(1000) COMMENT 'Human-readable evidence',
    evidence_source_table   VARCHAR(60) COMMENT 'Evidence source table',
    evidence_source_id      VARCHAR(80) COMMENT 'Evidence row ID',
    criteria_met_count      NUMBER(4,0) COMMENT 'Criteria met for this PA',
    criteria_total_count    NUMBER(4,0) COMMENT 'Total criteria for this PA',
    source_system           VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc         TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash             VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_pa_criteria_evaluation PRIMARY KEY (criteria_eval_sk)
)
COMMENT = 'One row per PA per criterion | Evaluates each criterion against clinical evidence. Result is MET, UNMET, or INDETERMINATE.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_duplicate_pa_candidates (
    duplicate_sk       VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    pa_id_1            VARCHAR(24) NOT NULL COMMENT 'First PA',
    pa_id_2            VARCHAR(24) NOT NULL COMMENT 'Second PA',
    member_id          VARCHAR(20) NOT NULL COMMENT 'Member',
    service_category   VARCHAR(30) NOT NULL COMMENT 'Shared service category',
    overlap_days       NUMBER(6,0) COMMENT 'Days of date overlap',
    similarity_score   NUMBER(5,4) NOT NULL COMMENT 'Similarity 0-1',
    source_system      VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc    TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash        VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_duplicate_pa_candidates PRIMARY KEY (duplicate_sk)
)
COMMENT = 'One row per pair of potentially duplicate PA requests | Duplicate detection: same member, same service category, overlapping dates.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_benefit_pa_requirement (
    benefit_pa_sk           VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    line_of_business        VARCHAR(30) NOT NULL COMMENT 'LOB',
    service_category        VARCHAR(30) NOT NULL COMMENT 'Service domain',
    requires_pa             BOOLEAN NOT NULL COMMENT 'True when PA is required',
    pa_threshold_amt        NUMBER(12,2) COMMENT 'Dollar threshold for PA',
    auto_approve_eligible   BOOLEAN NOT NULL COMMENT 'Eligible for auto-approval',
    gold_card_eligible      BOOLEAN NOT NULL COMMENT 'Eligible for gold-carding',
    source_system           VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc         TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash             VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_benefit_pa_requirement PRIMARY KEY (benefit_pa_sk)
)
COMMENT = 'One row per service category per LOB with PA requirement flag | Which services require PA under each LOB.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_provider_profile (
    provider_npi               VARCHAR(10) NOT NULL COMMENT 'Provider NPI',
    provider_name              VARCHAR(300) COMMENT 'Provider name',
    provider_specialty         VARCHAR(150) COMMENT 'Specialty',
    network_status             VARCHAR(20) COMMENT 'Network standing',
    total_pa_requests_6mo      NUMBER(8,0) NOT NULL COMMENT 'PA requests in last 6 months',
    approved_pa_6mo            NUMBER(8,0) NOT NULL COMMENT 'Approved PAs in last 6 months',
    approval_rate_6mo_pct      NUMBER(5,2) COMMENT '6-month approval rate',
    gold_card_eligible         BOOLEAN NOT NULL COMMENT 'Meets TX HB3459 gold-card threshold',
    gold_card_min_volume_met   BOOLEAN NOT NULL COMMENT 'Meets minimum volume for gold-card',
    source_system              VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc            TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_provider_profile PRIMARY KEY (provider_npi)
)
COMMENT = 'One row per provider NPI with network status and gold-card eligibility | Provider profile with computed gold-card eligibility per TX HB3459.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_claims_summary (
    member_id               VARCHAR(20) NOT NULL COMMENT 'Member',
    total_claims            NUMBER(10,0) NOT NULL COMMENT 'Total claim lines',
    professional_claims     NUMBER(10,0) NOT NULL COMMENT 'Professional claims',
    institutional_claims    NUMBER(10,0) NOT NULL COMMENT 'Institutional claims',
    pharmacy_claims         NUMBER(10,0) NOT NULL COMMENT 'Pharmacy claims',
    total_billed_amt        NUMBER(14,2) NOT NULL COMMENT 'Total billed',
    total_allowed_amt       NUMBER(14,2) NOT NULL COMMENT 'Total allowed',
    total_plan_paid_amt     NUMBER(14,2) NOT NULL COMMENT 'Total plan paid',
    total_member_paid_amt   NUMBER(14,2) NOT NULL COMMENT 'Total member paid',
    denied_claims           NUMBER(10,0) NOT NULL COMMENT 'Denied claim lines',
    er_visit_count          NUMBER(8,0) NOT NULL COMMENT 'Emergency visits',
    inpatient_admit_count   NUMBER(8,0) NOT NULL COMMENT 'Inpatient admissions',
    source_system           VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc         TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash             VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_claims_summary PRIMARY KEY (member_id)
)
COMMENT = 'One row per member with claims aggregates | Claims summary per member for utilization and cost analysis.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_utilization_flags (
    member_id               VARCHAR(20) NOT NULL COMMENT 'Member',
    high_er_utilizer_flag   BOOLEAN NOT NULL COMMENT '3+ ER visits in 12 months',
    opioid_mgmt_flag        BOOLEAN NOT NULL COMMENT 'Active opioid management',
    case_mgmt_open_flag     BOOLEAN NOT NULL COMMENT 'Active case management',
    readmission_risk_flag   BOOLEAN NOT NULL COMMENT 'Readmission risk elevated',
    high_cost_flag          BOOLEAN NOT NULL COMMENT 'Top 5% by allowed amount',
    er_visits_12mo          NUMBER(6,0) NOT NULL COMMENT 'ER visits in last 12 months',
    inpatient_admits_12mo   NUMBER(6,0) NOT NULL COMMENT 'Inpatient admits in last 12 months',
    source_system           VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc         TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash             VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_utilization_flags PRIMARY KEY (member_id)
)
COMMENT = 'One row per member with utilization risk flags | Utilization flags for care management triggers.';

CREATE OR REPLACE TABLE M360MART.SILVER.slv_member_event_summary (
    member_id                VARCHAR(20) NOT NULL COMMENT 'Member',
    total_events             NUMBER(10,0) NOT NULL COMMENT 'Total interactions',
    call_count               NUMBER(8,0) NOT NULL COMMENT 'Phone contacts',
    portal_count             NUMBER(8,0) NOT NULL COMMENT 'Portal interactions',
    mail_count               NUMBER(8,0) NOT NULL COMMENT 'Mail interactions',
    appeal_count             NUMBER(6,0) NOT NULL COMMENT 'Appeals filed',
    grievance_count          NUMBER(6,0) NOT NULL COMMENT 'Grievances filed',
    pa_related_event_count   NUMBER(8,0) NOT NULL COMMENT 'Events linked to a PA',
    avg_sentiment_score      NUMBER(4,3) COMMENT 'Average sentiment',
    escalated_count          NUMBER(6,0) NOT NULL COMMENT 'Escalated contacts',
    source_system            VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc          TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash              VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_slv_member_event_summary PRIMARY KEY (member_id)
)
COMMENT = 'One row per member with event/interaction aggregates | Member interaction summary across channels.';
