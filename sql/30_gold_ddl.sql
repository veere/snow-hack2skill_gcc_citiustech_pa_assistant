-- M360MART GOLD layer DDL
-- Generated from config/schemas_gold.py by sql/generate_ddl.py - do not edit manually.
-- All types are explicit (never inferred from data).

CREATE OR REPLACE TABLE M360MART.GOLD.gold_pa_worklist (
    pa_id                      VARCHAR(24) NOT NULL COMMENT 'PA case',
    member_id                  VARCHAR(20) NOT NULL COMMENT 'Member',
    member_name                VARCHAR(200) COMMENT 'Member name',
    line_of_business           VARCHAR(30) NOT NULL COMMENT 'LOB',
    service_category           VARCHAR(30) NOT NULL COMMENT 'Service domain',
    urgency_flag               VARCHAR(20) NOT NULL COMMENT 'Urgency',
    pa_status                  VARCHAR(24) NOT NULL COMMENT 'Current status',
    sla_state                  VARCHAR(20) NOT NULL COMMENT 'ON_TRACK, AT_RISK, or BREACHED',
    days_until_deadline        NUMBER(8,2) COMMENT 'Days remaining',
    hours_until_deadline       NUMBER(10,2) COMMENT 'Hours remaining',
    criteria_met_count         NUMBER(4,0) COMMENT 'Criteria met',
    criteria_total_count       NUMBER(4,0) COMMENT 'Total criteria',
    completeness_pct           NUMBER(5,2) COMMENT 'Documentation completeness %',
    priority_score             NUMBER(6,2) NOT NULL COMMENT 'Priority (higher = more urgent)',
    ordering_provider_name     VARCHAR(300) COMMENT 'Provider',
    requested_procedure_desc   VARCHAR(500) COMMENT 'What was requested',
    source_system              VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc            TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_pa_worklist PRIMARY KEY (pa_id)
)
COMMENT = 'One row per open PA case, prioritized for analyst work | Active PA worklist with SLA countdown, priority, documentation completeness.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_pa_request_360 (
    pa_id                         VARCHAR(24) NOT NULL COMMENT 'PA case',
    member_id                     VARCHAR(20) NOT NULL COMMENT 'Member',
    member_name                   VARCHAR(200) COMMENT 'Member name',
    member_age                    NUMBER(4,0) COMMENT 'Age',
    member_gender                 VARCHAR(2) COMMENT 'Gender',
    line_of_business              VARCHAR(30) NOT NULL COMMENT 'LOB',
    plan_id                       VARCHAR(30) NOT NULL COMMENT 'Plan',
    service_category              VARCHAR(30) NOT NULL COMMENT 'Service domain',
    pa_status                     VARCHAR(24) NOT NULL COMMENT 'Status',
    urgency_flag                  VARCHAR(20) NOT NULL COMMENT 'Urgency',
    requested_procedure_desc      VARCHAR(500) COMMENT 'Procedure description',
    primary_dx_desc               VARCHAR(500) COMMENT 'Primary diagnosis',
    ordering_provider_name        VARCHAR(300) COMMENT 'Provider',
    ordering_provider_specialty   VARCHAR(150) COMMENT 'Specialty',
    network_status                VARCHAR(20) COMMENT 'Network',
    estimated_allowed_amt         NUMBER(12,2) COMMENT 'Estimated cost',
    sla_state                     VARCHAR(20) COMMENT 'SLA state',
    criteria_met_count            NUMBER(4,0) COMMENT 'Criteria met',
    criteria_total_count          NUMBER(4,0) COMMENT 'Total criteria',
    deductible_remaining_amt      NUMBER(12,2) COMMENT 'Deductible remaining',
    source_system                 VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc               TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                   VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_pa_request_360 PRIMARY KEY (pa_id)
)
COMMENT = 'One row per PA case, fully denormalized | Complete PA request with member, provider, clinical and financial context.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_pa_criteria_evidence (
    criteria_evidence_sk   VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    pa_id                  VARCHAR(24) NOT NULL COMMENT 'PA case',
    member_id              VARCHAR(20) NOT NULL COMMENT 'Member',
    criterion_type         VARCHAR(30) NOT NULL COMMENT 'Criterion',
    criterion_result       VARCHAR(20) NOT NULL COMMENT 'Result',
    evidence_text          VARCHAR(1000) COMMENT 'Human-readable evidence',
    criterion_desc         VARCHAR(500) COMMENT 'Criterion description',
    source_system          VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc        TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash            VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_pa_criteria_evidence PRIMARY KEY (criteria_evidence_sk)
)
COMMENT = 'One row per PA per criterion with evidence text | Criterion-level evidence for each PA. Reports MET/UNMET/INDETERMINATE with human-readable evidence. Never recommends a decision.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_pa_sla_tracking (
    pa_id                              VARCHAR(24) NOT NULL COMMENT 'PA case',
    line_of_business                   VARCHAR(30) NOT NULL COMMENT 'LOB',
    urgency_flag                       VARCHAR(20) NOT NULL COMMENT 'Urgency',
    sla_state                          VARCHAR(20) NOT NULL COMMENT 'ON_TRACK/AT_RISK/BREACHED',
    tat_business_days                  NUMBER(8,2) COMMENT 'Business days elapsed',
    regulatory_deadline_datetime_utc   TIMESTAMP_NTZ NOT NULL COMMENT 'Deadline',
    days_until_deadline                NUMBER(8,2) COMMENT 'Days remaining',
    clock_paused_days                  NUMBER(6,0) NOT NULL COMMENT 'Clock paused days',
    is_open                            BOOLEAN NOT NULL COMMENT 'Is case open',
    source_system                      VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc                    TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                        VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_pa_sla_tracking PRIMARY KEY (pa_id)
)
COMMENT = 'One row per PA with SLA metrics | SLA tracking dashboard data.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_pa_decision_audit (
    decision_audit_sk       VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    pa_id                   VARCHAR(24) NOT NULL COMMENT 'PA case',
    decision_type           VARCHAR(20) NOT NULL COMMENT 'ORIGINAL or APPEAL',
    pa_status               VARCHAR(24) COMMENT 'Outcome',
    decision_by_role        VARCHAR(24) COMMENT 'Decider',
    denial_reason_desc      VARCHAR(300) COMMENT 'Denial reason',
    appeal_outcome          VARCHAR(30) COMMENT 'Appeal outcome',
    auto_adjudicated_flag   BOOLEAN COMMENT 'Auto-adjudicated',
    source_system           VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc         TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash             VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_pa_decision_audit PRIMARY KEY (decision_audit_sk)
)
COMMENT = 'One row per PA decision including appeals | Decision audit trail for compliance reporting.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_pa_precedent (
    precedent_sk               VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    service_code               VARCHAR(20) COMMENT 'Unified service code: CPT/HCPCS, else NDC for drugs',
    service_code_system        VARCHAR(20) COMMENT 'Code system for service_code',
    requested_procedure_code   VARCHAR(10) COMMENT 'Procedure (NULL for drug requests)',
    requested_ndc              VARCHAR(20) COMMENT 'NDC (NULL for procedure requests)',
    primary_dx_icd10           VARCHAR(10) COMMENT 'Diagnosis',
    line_of_business           VARCHAR(30) COMMENT 'LOB',
    total_requests             NUMBER(10,0) NOT NULL COMMENT 'All requests',
    decided_count              NUMBER(10,0) NOT NULL COMMENT 'Reached approve/deny',
    approved_count             NUMBER(10,0) NOT NULL COMMENT 'Approved',
    denied_count               NUMBER(10,0) NOT NULL COMMENT 'Denied',
    approval_rate_pct          NUMBER(5,2) COMMENT 'approved / decided %. NULL when nothing decided - never fabricated as 0.',
    source_system              VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc            TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_pa_precedent PRIMARY KEY (precedent_sk)
)
COMMENT = 'One row per service code x diagnosis x LOB precedent | Historical decision counts for precedent lookup. Records what was decided before; it is not a prediction about a pending request.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_pa_duplicate_safety_flags (
    pa_id                     VARCHAR(24) NOT NULL COMMENT 'PA case',
    has_potential_duplicate   BOOLEAN NOT NULL COMMENT 'Potential duplicate detected',
    duplicate_pa_id           VARCHAR(24) COMMENT 'Most similar duplicate PA',
    duplicate_similarity      NUMBER(5,4) COMMENT 'Similarity score',
    high_cost_flag            BOOLEAN NOT NULL COMMENT 'High-cost review needed',
    network_concern_flag      BOOLEAN NOT NULL COMMENT 'Out-of-network concern',
    source_system             VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc           TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash               VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_pa_duplicate_safety_flags PRIMARY KEY (pa_id)
)
COMMENT = 'One row per PA with duplicate/safety indicators | Duplicate and safety flags for each PA.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_member_eligibility_snapshot (
    member_id             VARCHAR(20) NOT NULL COMMENT 'Member',
    member_name           VARCHAR(200) COMMENT 'Name',
    date_of_birth         DATE COMMENT 'DOB',
    age_years             NUMBER(4,0) COMMENT 'Age',
    gender                VARCHAR(2) COMMENT 'Gender',
    enrollment_status     VARCHAR(20) COMMENT 'Status',
    line_of_business      VARCHAR(30) COMMENT 'LOB',
    plan_id               VARCHAR(30) COMMENT 'Current plan',
    coverage_start_date   DATE COMMENT 'Current coverage start',
    is_eligible_today     BOOLEAN NOT NULL COMMENT 'Eligible as of reference date',
    has_coverage_gap      BOOLEAN NOT NULL COMMENT 'Has any gap in coverage history',
    source_system         VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc       TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash           VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_member_eligibility_snapshot PRIMARY KEY (member_id)
)
COMMENT = 'One row per member with current eligibility state | Eligibility snapshot for quick eligibility checks.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_member_clinical_summary (
    member_id                   VARCHAR(20) NOT NULL COMMENT 'Member',
    active_condition_count      NUMBER(6,0) NOT NULL COMMENT 'Active conditions',
    chronic_condition_count     NUMBER(6,0) NOT NULL COMMENT 'Chronic conditions',
    active_medication_count     NUMBER(6,0) NOT NULL COMMENT 'Active medications',
    bmi_value                   NUMBER(6,2) COMMENT 'Latest BMI',
    hba1c_value                 NUMBER(5,2) COMMENT 'Latest HbA1c',
    total_conservative_visits   NUMBER(6,0) NOT NULL COMMENT 'Conservative care visits',
    imaging_study_count         NUMBER(6,0) NOT NULL COMMENT 'Imaging studies',
    source_system               VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc             TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                 VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_member_clinical_summary PRIMARY KEY (member_id)
)
COMMENT = 'One row per member with clinical summary | Clinical summary for PA review context.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_member_financial_position (
    member_id                  VARCHAR(20) NOT NULL COMMENT 'Member',
    deductible_remaining_amt   NUMBER(12,2) COMMENT 'Deductible remaining',
    oop_remaining_amt          NUMBER(12,2) COMMENT 'OOP remaining',
    ytd_plan_paid_amt          NUMBER(14,2) COMMENT 'YTD plan paid',
    ytd_member_paid_amt        NUMBER(14,2) COMMENT 'YTD member paid',
    total_claims_count         NUMBER(10,0) COMMENT 'Total claims',
    source_system              VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc            TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash                VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_member_financial_position PRIMARY KEY (member_id)
)
COMMENT = 'One row per member with current financial position | Current-year financial position for cost-sharing context.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_member_timeline (
    timeline_sk        VARCHAR(40) NOT NULL COMMENT 'Surrogate key',
    member_id          VARCHAR(20) NOT NULL COMMENT 'Member',
    event_date         DATE NOT NULL COMMENT 'Date of the event',
    event_type         VARCHAR(30) NOT NULL COMMENT 'Type of event',
    event_desc         VARCHAR(500) COMMENT 'Description',
    related_pa_id      VARCHAR(24) COMMENT 'Related PA if any',
    related_claim_id   VARCHAR(40) COMMENT 'Related claim if any',
    amount             NUMBER(14,2) COMMENT 'Financial amount if applicable',
    source_system      VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc    TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash        VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_member_timeline PRIMARY KEY (timeline_sk)
)
COMMENT = 'One row per chronological event for a member | Unified chronological stream of all member events: clinical, claims, PA, contacts.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_provider_network_summary (
    provider_npi            VARCHAR(10) NOT NULL COMMENT 'NPI',
    provider_name           VARCHAR(300) COMMENT 'Name',
    provider_specialty      VARCHAR(150) COMMENT 'Specialty',
    network_status          VARCHAR(20) COMMENT 'Network',
    gold_card_eligible      BOOLEAN NOT NULL COMMENT 'Gold-card eligible',
    approval_rate_6mo_pct   NUMBER(5,2) COMMENT '6mo approval rate',
    total_pa_requests_6mo   NUMBER(8,0) NOT NULL COMMENT '6mo PA requests',
    source_system           VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc         TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash             VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_provider_network_summary PRIMARY KEY (provider_npi)
)
COMMENT = 'One row per provider with network and gold-card summary | Provider network summary for PA routing decisions.';

CREATE OR REPLACE TABLE M360MART.GOLD.gold_pa_kpi_daily (
    kpi_date          DATE NOT NULL COMMENT 'Date',
    total_received    NUMBER(8,0) NOT NULL COMMENT 'PAs received',
    total_decided     NUMBER(8,0) NOT NULL COMMENT 'PAs decided',
    total_approved    NUMBER(8,0) NOT NULL COMMENT 'Approved',
    total_denied      NUMBER(8,0) NOT NULL COMMENT 'Denied',
    open_cases        NUMBER(8,0) NOT NULL COMMENT 'Open cases',
    breached_cases    NUMBER(8,0) NOT NULL COMMENT 'SLA breached',
    avg_tat_days      NUMBER(8,2) COMMENT 'Average TAT in days',
    auto_adj_pct      NUMBER(5,2) COMMENT 'Auto-adjudication rate %',
    source_system     VARCHAR(40) NOT NULL COMMENT 'Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)',
    ingested_at_utc   TIMESTAMP_NTZ NOT NULL COMMENT 'UTC timestamp the row was produced by the local pipeline',
    record_hash       VARCHAR(64) NOT NULL COMMENT 'SHA-256 over the row''s business columns. Stable across reruns with the same seed - this is what the determinism test compares.',
    CONSTRAINT pk_gold_pa_kpi_daily PRIMARY KEY (kpi_date)
)
COMMENT = 'One row per date with aggregate PA KPIs | Daily PA volume and SLA compliance metrics.';
