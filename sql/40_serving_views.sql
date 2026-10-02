-- M360MART SERVING views
-- 14 views over GOLD tables + VW_ASSISTANT_DICTIONARY
-- Direct identifiers (SSN, full address, email, phone) are EXCLUDED
-- COMMENT on every view and column for Cortex agent grounding

-- 1. VW_PA_WORKLIST
CREATE OR REPLACE VIEW M360MART.SERVING.VW_PA_WORKLIST
  COMMENT = 'Active PA cases prioritized by urgency and SLA countdown. Use for the analyst work queue.'
AS
SELECT
  pa_id, member_id, member_name, line_of_business, service_category,
  urgency_flag, pa_status, sla_state, days_until_deadline, hours_until_deadline,
  criteria_met_count, criteria_total_count, completeness_pct, priority_score,
  ordering_provider_name, requested_procedure_desc
FROM M360MART.GOLD.GOLD_PA_WORKLIST;

-- 2. VW_PA_REQUEST_360
CREATE OR REPLACE VIEW M360MART.SERVING.VW_PA_REQUEST_360
  COMMENT = 'Fully denormalized PA request with member, provider and clinical context. One-stop view for PA review.'
AS
SELECT
  pa_id, member_id, member_name, member_age, member_gender,
  line_of_business, plan_id, service_category, pa_status, urgency_flag,
  requested_procedure_desc, primary_dx_desc,
  ordering_provider_name, ordering_provider_specialty, network_status,
  estimated_allowed_amt, sla_state, criteria_met_count, criteria_total_count,
  deductible_remaining_amt
FROM M360MART.GOLD.GOLD_PA_REQUEST_360;

-- 3. VW_PA_CRITERIA_EVIDENCE
CREATE OR REPLACE VIEW M360MART.SERVING.VW_PA_CRITERIA_EVIDENCE
  COMMENT = 'Criterion-level evidence for each PA. MET/UNMET/INDETERMINATE with human-readable evidence. Non-decision-making.'
AS
SELECT
  criteria_evidence_sk, pa_id, member_id, criterion_type, criterion_result,
  evidence_text, criterion_desc
FROM M360MART.GOLD.GOLD_PA_CRITERIA_EVIDENCE;

-- 4. VW_PA_SLA_TRACKING
CREATE OR REPLACE VIEW M360MART.SERVING.VW_PA_SLA_TRACKING
  COMMENT = 'SLA tracking for all PA cases. Shows TAT, deadline, and breach status.'
AS
SELECT
  pa_id, line_of_business, urgency_flag, sla_state, tat_business_days,
  regulatory_deadline_datetime_utc, days_until_deadline, clock_paused_days, is_open
FROM M360MART.GOLD.GOLD_PA_SLA_TRACKING;

-- 5. VW_PA_DECISION_AUDIT
CREATE OR REPLACE VIEW M360MART.SERVING.VW_PA_DECISION_AUDIT
  COMMENT = 'Decision audit trail including original decisions and appeal outcomes.'
AS
SELECT
  decision_audit_sk, pa_id, decision_type, pa_status, decision_by_role,
  denial_reason_desc, appeal_outcome, auto_adjudicated_flag
FROM M360MART.GOLD.GOLD_PA_DECISION_AUDIT;

-- 6. VW_PA_PRECEDENT
CREATE OR REPLACE VIEW M360MART.SERVING.VW_PA_PRECEDENT
  COMMENT = 'Historical decision counts by requested service, diagnosis and LOB. service_code unifies CPT/HCPCS with NDC so drug precedent is findable. approval_rate_pct divides by DECIDED requests and is NULL when nothing was decided - it is a record of past decisions, never a prediction.'
AS
SELECT
  precedent_sk, service_code, service_code_system,
  requested_procedure_code, requested_ndc, primary_dx_icd10, line_of_business,
  total_requests, decided_count, approved_count, denied_count, approval_rate_pct
FROM M360MART.GOLD.GOLD_PA_PRECEDENT;

-- 7. VW_PA_DUPLICATE_SAFETY
CREATE OR REPLACE VIEW M360MART.SERVING.VW_PA_DUPLICATE_SAFETY
  COMMENT = 'Duplicate detection and safety flags for PA cases.'
AS
SELECT
  pa_id, has_potential_duplicate, duplicate_pa_id, duplicate_similarity,
  high_cost_flag, network_concern_flag
FROM M360MART.GOLD.GOLD_PA_DUPLICATE_SAFETY_FLAGS;

-- 8. VW_MEMBER_ELIGIBILITY
CREATE OR REPLACE VIEW M360MART.SERVING.VW_MEMBER_ELIGIBILITY
  COMMENT = 'Member eligibility snapshot. No SSN, address, email or phone exposed.'
AS
SELECT
  member_id, member_name, date_of_birth, age_years, gender,
  enrollment_status, line_of_business, plan_id, coverage_start_date,
  is_eligible_today, has_coverage_gap
FROM M360MART.GOLD.GOLD_MEMBER_ELIGIBILITY_SNAPSHOT;

-- 9. VW_MEMBER_CLINICAL_SUMMARY
CREATE OR REPLACE VIEW M360MART.SERVING.VW_MEMBER_CLINICAL_SUMMARY
  COMMENT = 'Clinical summary per member: condition counts, medications, BMI, labs, conservative care.'
AS
SELECT
  member_id, active_condition_count, chronic_condition_count, active_medication_count,
  bmi_value, hba1c_value, total_conservative_visits, imaging_study_count
FROM M360MART.GOLD.GOLD_MEMBER_CLINICAL_SUMMARY;

-- 10. VW_MEMBER_FINANCIAL
CREATE OR REPLACE VIEW M360MART.SERVING.VW_MEMBER_FINANCIAL
  COMMENT = 'Current-year financial position: deductible, OOP, YTD spending.'
AS
SELECT
  member_id, deductible_remaining_amt, oop_remaining_amt,
  ytd_plan_paid_amt, ytd_member_paid_amt, total_claims_count
FROM M360MART.GOLD.GOLD_MEMBER_FINANCIAL_POSITION;

-- 11. VW_MEMBER_TIMELINE
CREATE OR REPLACE VIEW M360MART.SERVING.VW_MEMBER_TIMELINE
  COMMENT = 'Unified chronological stream of all member events: PA, claims, contacts.'
AS
SELECT
  timeline_sk, member_id, event_date, event_type, event_desc,
  related_pa_id, related_claim_id, amount
FROM M360MART.GOLD.GOLD_MEMBER_TIMELINE;

-- 12. VW_PROVIDER_NETWORK
CREATE OR REPLACE VIEW M360MART.SERVING.VW_PROVIDER_NETWORK
  COMMENT = 'Provider network summary with gold-card eligibility and approval rates.'
AS
SELECT
  provider_npi, provider_name, provider_specialty, network_status,
  gold_card_eligible, approval_rate_6mo_pct, total_pa_requests_6mo
FROM M360MART.GOLD.GOLD_PROVIDER_NETWORK_SUMMARY;

-- 13. VW_PA_KPI_DAILY
CREATE OR REPLACE VIEW M360MART.SERVING.VW_PA_KPI_DAILY
  COMMENT = 'Daily PA volume and SLA compliance KPIs.'
AS
SELECT
  kpi_date, total_received, total_decided, total_approved, total_denied,
  open_cases, breached_cases, avg_tat_days, auto_adj_pct
FROM M360MART.GOLD.GOLD_PA_KPI_DAILY;

-- 14. VW_ASSISTANT_DICTIONARY
CREATE OR REPLACE VIEW M360MART.SERVING.VW_ASSISTANT_DICTIONARY
  COMMENT = 'Data dictionary of all SERVING views and their columns. Use to ground conversational agents.'
AS
SELECT
  TABLE_NAME AS view_name,
  COLUMN_NAME AS column_name,
  DATA_TYPE AS data_type,
  COMMENT AS column_description
FROM M360MART.INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'SERVING'
  AND TABLE_NAME NOT LIKE 'VW_ASSISTANT%'
ORDER BY TABLE_NAME, ORDINAL_POSITION;
