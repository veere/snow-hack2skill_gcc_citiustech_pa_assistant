"""
FROZEN bronze layer specs.

Do not edit. Four generator tracks and the entire silver layer depend on these
column names and types.

Table inventory (17):
  reference (6)  ref_icd10cm, ref_hcpcs, ref_ndc_product, ref_provider_npi,
                 ref_place_of_service, ref_synpuf_benchmark
  dimension (4)  dim_memberProfile, dim_memberPlan, dim_memberFinance,
                 dim_memberHistory
  fact (3)       fct_memberEvents, fct_memberPA, fct_memberTransactions
  external (4)   ext_patientProfile, ext_patientClinicalRecords,
                 ext_patientEhrEvents, ext_patientMedicalHistory

The ref_* tables are landed open-data code sets. They are a necessary addition
to the originally-specified table list because the approved ICD-10/HCPCS
crosswalk needs real code sets as source data.
"""

from __future__ import annotations

from config.schemas import (
    CLAIM_STATUSES,
    CLINICAL_RECORD_TYPES,
    CODE_SYSTEMS,
    Col,
    DENIAL_REASON_CODES,
    EHR_EVENT_TYPES,
    EHR_VENDORS,
    ENCOUNTER_CLASSES,
    ENROLLMENT_CHANGE_TYPES,
    ENROLLMENT_STATUSES,
    FK,
    GENDERS,
    LINES_OF_BUSINESS,
    MATCH_METHODS,
    MEDICAL_HISTORY_TYPES,
    MEMBER_EVENT_CATEGORIES,
    NETWORK_STATUSES,
    PA_CERT_TYPES,
    PA_CHANNELS,
    PA_DECIDER_ROLES,
    PA_SERVICE_CATEGORIES,
    PA_STATUSES,
    PA_URGENCY,
    PEND_REASON_CODES,
    PRODUCT_TYPES,
    TRANSACTION_TYPES,
    TableSpec,
    register,
    with_audit,
)

B = "bronze"

# ===========================================================================
# REFERENCE TABLES - landed open-data code sets
# ===========================================================================

register(TableSpec(
    layer=B, name="ref_icd10cm",
    grain="One row per ICD-10-CM diagnosis code",
    desc="Real CMS ICD-10-CM code descriptions. Source of truth for diagnosis "
         "descriptions and the crosswalk target for Synthea SNOMED conditions.",
    primary_key=("icd10_code",),
    columns=with_audit(
        Col("icd10_code", "VARCHAR(10)", False, "ICD-10-CM code, no decimal point (e.g. M5416)"),
        Col("icd10_code_dotted", "VARCHAR(12)", False, "ICD-10-CM code with decimal (e.g. M54.16)"),
        Col("short_description", "VARCHAR(120)", True, "CMS abbreviated description"),
        Col("long_description", "VARCHAR(500)", True, "CMS full description"),
        Col("chapter_code", "VARCHAR(10)", True, "ICD-10-CM chapter range (e.g. M00-M99)"),
        Col("chapter_description", "VARCHAR(200)", True, "Human-readable chapter name"),
        Col("category_code", "VARCHAR(5)", True, "First 3 characters - the category root"),
        Col("is_billable", "BOOLEAN", False, "True when the code is valid for billing (leaf-level)"),
        Col("code_edition", "VARCHAR(10)", False, "CMS release year the code set came from"),
    ),
))

register(TableSpec(
    layer=B, name="ref_hcpcs",
    grain="One row per HCPCS Level II / CPT-range procedure code",
    desc="Real CMS HCPCS Level II code set, plus the curated PA-relevant "
         "procedure codes used as PA request targets.",
    primary_key=("procedure_code",),
    columns=with_audit(
        Col("procedure_code", "VARCHAR(10)", False, "HCPCS/CPT procedure code"),
        Col("code_system", "VARCHAR(12)", False, "Which code system this belongs to", enum=CODE_SYSTEMS),
        Col("short_description", "VARCHAR(120)", True, "Abbreviated procedure description"),
        Col("long_description", "VARCHAR(500)", True, "Full procedure description"),
        Col("category", "VARCHAR(60)", True, "Coarse grouping (e.g. IMAGING, SURGERY, DME, DRUG)"),
        Col("pa_service_category", "VARCHAR(30)", True,
            "PA domain this code maps to, when it is a PA-relevant service",
            enum=PA_SERVICE_CATEGORIES),
        Col("typically_requires_pa", "BOOLEAN", False,
            "True when this service is commonly subject to prior authorization"),
        Col("coverage_code", "VARCHAR(4)", True, "CMS coverage indicator"),
        Col("pricing_indicator", "VARCHAR(4)", True, "CMS pricing indicator"),
        Col("betos_code", "VARCHAR(10)", True, "Berenson-Eggers type of service code"),
        Col("typical_allowed_amt", "NUMBER(12,2)", True,
            "Benchmark allowed amount used to estimate cost exposure"),
        Col("code_edition", "VARCHAR(16)", False, "CMS quarterly release the code set came from"),
    ),
))

register(TableSpec(
    layer=B, name="ref_ndc_product",
    grain="One row per NDC product code",
    desc="Real FDA National Drug Code directory. Supplies drug identity, "
         "therapeutic class and specialty/PA flags for pharmacy PA requests.",
    primary_key=("product_ndc",),
    columns=with_audit(
        Col("product_ndc", "VARCHAR(20)", False, "FDA product NDC (labeler-product)"),
        Col("product_type", "VARCHAR(60)", True, "e.g. HUMAN PRESCRIPTION DRUG"),
        # Widths below were raised from the initial estimates after measuring the
        # real FDA NDC directory. Combination products and conjugate vaccines list
        # every antigen, so substance_name and pharm_classes are far longer than a
        # typical single-ingredient product suggests. COPY INTO rejects an
        # over-long value rather than truncating, so these are hard limits.
        Col("proprietary_name", "VARCHAR(400)", True, "Brand name"),
        Col("nonproprietary_name", "VARCHAR(600)", True, "Generic name"),
        Col("dosage_form", "VARCHAR(120)", True, "e.g. TABLET, INJECTION, SOLUTION"),
        Col("route", "VARCHAR(200)", True, "e.g. ORAL, INTRAVENOUS, SUBCUTANEOUS"),
        Col("labeler_name", "VARCHAR(300)", True, "Manufacturer / labeler"),
        Col("substance_name", "VARCHAR(4000)", True, "Active ingredient(s)"),
        Col("active_numerator_strength", "VARCHAR(500)", True, "Strength value(s)"),
        Col("active_ingredient_unit", "VARCHAR(1200)", True, "Strength unit(s)"),
        Col("pharm_classes", "VARCHAR(4000)", True, "FDA pharmacologic class list"),
        Col("dea_schedule", "VARCHAR(10)", True, "DEA controlled-substance schedule, when applicable"),
        Col("therapeutic_class", "VARCHAR(120)", True, "Derived coarse therapeutic class"),
        Col("is_specialty_drug", "BOOLEAN", False,
            "True for high-cost specialty/biologic products that typically require PA"),
        Col("typically_requires_pa", "BOOLEAN", False, "True when the drug is commonly PA-gated"),
        Col("is_controlled_substance", "BOOLEAN", False, "True when a DEA schedule is present"),
        Col("marketing_start_date", "DATE", True, "FDA marketing start"),
        Col("marketing_end_date", "DATE", True, "FDA marketing end, when ended"),
    ),
))

register(TableSpec(
    layer=B, name="ref_provider_npi",
    grain="One row per provider NPI (individual or organization)",
    desc="Provider reference. Real NPPES records where the API returned them, "
         "otherwise deterministic Luhn-valid synthetic NPIs. The "
         "is_synthetic_npi flag makes the distinction explicit and auditable.",
    primary_key=("npi",),
    columns=with_audit(
        Col("npi", "VARCHAR(10)", False, "10-digit National Provider Identifier"),
        Col("entity_type", "VARCHAR(2)", False, "1 = individual practitioner, 2 = organization"),
        Col("provider_name", "VARCHAR(300)", False, "Display name (person or organization)"),
        Col("first_name", "VARCHAR(100)", True, "Given name, individuals only"),
        Col("last_name", "VARCHAR(100)", True, "Family name, individuals only"),
        Col("credential", "VARCHAR(60)", True, "e.g. MD, DO, NP, PA-C"),
        Col("gender", "VARCHAR(2)", True, "Provider gender, individuals only", enum=GENDERS),
        Col("primary_taxonomy_code", "VARCHAR(20)", True, "NUCC taxonomy code"),
        Col("primary_specialty", "VARCHAR(150)", True, "Specialty description"),
        Col("organization_name", "VARCHAR(300)", True, "Affiliated organization"),
        Col("practice_city", "VARCHAR(100)", True, "Practice location city"),
        Col("practice_state", "VARCHAR(2)", True, "Practice location state"),
        Col("practice_zip", "VARCHAR(10)", True, "Practice location ZIP"),
        Col("practice_phone", "VARCHAR(30)", True, "Practice phone"),
        Col("enumeration_date", "DATE", True, "Date the NPI was issued"),
        Col("is_sole_proprietor", "BOOLEAN", True, "NPPES sole-proprietor flag"),
        Col("is_synthetic_npi", "BOOLEAN", False,
            "True when the NPI was generated rather than sourced from NPPES"),
    ),
))

register(TableSpec(
    layer=B, name="ref_place_of_service",
    grain="One row per CMS place-of-service code",
    desc="CMS place-of-service code set. Needed for site-of-service criteria.",
    primary_key=("pos_code",),
    columns=with_audit(
        Col("pos_code", "VARCHAR(4)", False, "Two-digit place-of-service code"),
        Col("pos_name", "VARCHAR(120)", False, "Short name (e.g. Office, Inpatient Hospital)"),
        Col("pos_description", "VARCHAR(600)", True, "Full CMS description"),
        Col("facility_type", "VARCHAR(30)", True, "FACILITY or NON_FACILITY"),
        Col("is_inpatient", "BOOLEAN", False, "True for inpatient settings"),
    ),
))

register(TableSpec(
    layer=B, name="ref_synpuf_benchmark",
    grain="One row per cost-sharing metric per benchmark cohort",
    desc="CALIBRATION ONLY. Percentile distributions derived from the CMS "
         "DE-SynPUF beneficiary summary, used to shape synthetic accumulators "
         "so member financial values follow a realistic real-world spread "
         "rather than an invented one. Never a row source for member data.",
    primary_key=("benchmark_id",),
    columns=with_audit(
        Col("benchmark_id", "VARCHAR(60)", False, "Cohort + metric identifier"),
        Col("cohort", "VARCHAR(60)", False, "Benchmark cohort (e.g. ALL, AGE_65_74, ESRD)"),
        Col("metric_name", "VARCHAR(80)", False,
            "Metric being described (e.g. inpatient_beneficiary_responsibility)"),
        Col("p10", "NUMBER(14,2)", True, "10th percentile"),
        Col("p25", "NUMBER(14,2)", True, "25th percentile"),
        Col("p50", "NUMBER(14,2)", True, "Median"),
        Col("p75", "NUMBER(14,2)", True, "75th percentile"),
        Col("p90", "NUMBER(14,2)", True, "90th percentile"),
        Col("mean_value", "NUMBER(14,2)", True, "Arithmetic mean"),
        Col("stddev_value", "NUMBER(14,2)", True, "Standard deviation"),
        Col("observation_count", "NUMBER(12,0)", True, "Beneficiaries contributing to the metric"),
        Col("source_year", "VARCHAR(10)", True, "SynPUF data year"),
    ),
))

# ===========================================================================
# DIMENSION TABLES - member master data
# ===========================================================================

register(TableSpec(
    layer=B, name="dim_memberProfile",
    grain="One row per member (the payer's own view of the person)",
    desc="Member demographic master, derived from Synthea patients.csv with all "
         "dates shifted by the deterministic date anchor. This is the payer-side "
         "identity; ext_patientProfile is the provider-side identity for the same "
         "human and the two are reconciled in silver.",
    primary_key=("member_id",),
    columns=with_audit(
        Col("member_id", "VARCHAR(20)", False,
            "Payer-assigned member identifier (MBR-########). Generated by "
            "common.identity, which guarantees uniqueness - a truncated hash alone "
            "collides at this population size and Snowflake does not enforce the PK."),
        Col("synthea_patient_id", "VARCHAR(64)", True,
            "Originating Synthea patient UUID. Lineage only - never exposed in SERVING."),
        Col("subscriber_id", "VARCHAR(20)", True, "Subscriber this member belongs to"),
        Col("relationship_to_subscriber", "VARCHAR(20)", True, "SELF, SPOUSE, CHILD, OTHER"),
        Col("mbi", "VARCHAR(20)", True, "Medicare Beneficiary Identifier, when Medicare Advantage"),
        Col("ssn_last4", "VARCHAR(4)", True, "Last 4 of SSN. Excluded from SERVING views."),
        Col("first_name", "VARCHAR(100)", False, "Given name"),
        Col("middle_name", "VARCHAR(100)", True, "Middle name"),
        Col("last_name", "VARCHAR(100)", False, "Family name"),
        Col("name_suffix", "VARCHAR(20)", True, "Generational suffix"),
        Col("date_of_birth", "DATE", False, "Date of birth (date-anchor shifted)"),
        Col("age_years", "NUMBER(5,0)", False, "Age in whole years as of the pipeline reference date",
            min_value=0, max_value=120),
        Col("date_of_death", "DATE", True, "Date of death when deceased"),
        Col("deceased_flag", "BOOLEAN", False, "True when a date of death is present"),
        Col("gender", "VARCHAR(2)", False, "Administrative gender", enum=GENDERS),
        Col("race", "VARCHAR(60)", True, "Race as recorded in the source"),
        Col("ethnicity", "VARCHAR(60)", True, "Ethnicity as recorded in the source"),
        Col("marital_status", "VARCHAR(20)", True, "Marital status"),
        Col("preferred_language", "VARCHAR(40)", True,
            "Preferred spoken language - drives correspondence requirements"),
        Col("interpreter_needed_flag", "BOOLEAN", False, "True when language is not English"),
        Col("address_line1", "VARCHAR(200)", True, "Street address. Excluded from SERVING views."),
        Col("city", "VARCHAR(100)", True, "City"),
        Col("county", "VARCHAR(100)", True, "County"),
        Col("state", "VARCHAR(2)", True, "State - drives state-specific PA turnaround rules"),
        Col("zip_code", "VARCHAR(10)", True, "Postal code"),
        Col("latitude", "NUMBER(11,7)", True, "Latitude. Excluded from SERVING views."),
        Col("longitude", "NUMBER(11,7)", True, "Longitude. Excluded from SERVING views."),
        Col("phone", "VARCHAR(30)", True, "Contact phone. Excluded from SERVING views."),
        Col("email", "VARCHAR(200)", True, "Contact email. Excluded from SERVING views."),
        Col("annual_income_amt", "NUMBER(12,2)", True, "Reported annual income", min_value=0),
        Col("healthcare_expenses_lifetime_amt", "NUMBER(14,2)", True,
            "Lifetime healthcare expenses from source", min_value=0),
        Col("healthcare_coverage_lifetime_amt", "NUMBER(14,2)", True,
            "Lifetime covered amount from source", min_value=0),
        Col("is_current", "BOOLEAN", False, "True for the active profile record"),
    ),
))

register(TableSpec(
    layer=B, name="dim_memberPlan",
    grain="One row per member per plan coverage span",
    desc="Plan enrolment and benefit configuration. Carries the line of "
         "business, carve-outs and the regulatory turnaround pair that every "
         "downstream SLA calculation depends on.",
    primary_key=("member_plan_sk",),
    foreign_keys=(FK("member_id", "dim_memberProfile", "member_id", allow_null=False),),
    columns=with_audit(
        Col("member_plan_sk", "VARCHAR(40)", False, "Surrogate key for the member-plan span"),
        Col("member_id", "VARCHAR(20)", False, "Member this coverage belongs to"),
        Col("plan_id", "VARCHAR(30)", False, "Plan identifier"),
        Col("plan_name", "VARCHAR(200)", False, "Marketing plan name"),
        Col("payer_id", "VARCHAR(30)", False, "Payer identifier"),
        Col("payer_name", "VARCHAR(200)", False, "Payer name"),
        Col("line_of_business", "VARCHAR(30)", False,
            "Regulatory line of business - selects the applicable TAT rule",
            enum=LINES_OF_BUSINESS),
        Col("product_type", "VARCHAR(10)", False, "Network product design", enum=PRODUCT_TYPES),
        Col("metal_tier", "VARCHAR(20)", True, "Marketplace actuarial tier, when applicable"),
        Col("group_number", "VARCHAR(30)", True, "Employer group number"),
        Col("group_name", "VARCHAR(200)", True, "Employer group name"),
        Col("coverage_start_date", "DATE", False, "First day of coverage"),
        Col("coverage_end_date", "DATE", True, "Last day of coverage, NULL when open-ended"),
        Col("is_current", "BOOLEAN", False, "True for the span covering the reference date"),
        Col("enrollment_status", "VARCHAR(20)", False, "Coverage state", enum=ENROLLMENT_STATUSES),
        Col("pcp_npi", "VARCHAR(10)", True, "Assigned primary care provider NPI"),
        Col("pcp_name", "VARCHAR(300)", True, "Assigned primary care provider name"),
        Col("pharmacy_carveout_flag", "BOOLEAN", False,
            "True when pharmacy benefits are administered by a separate PBM"),
        Col("pharmacy_pbm_name", "VARCHAR(120)", True, "PBM name when carved out"),
        Col("behavioral_health_carveout_flag", "BOOLEAN", False,
            "True when behavioral health is carved out to a vendor"),
        Col("behavioral_health_vendor_name", "VARCHAR(120)", True, "BH vendor when carved out"),
        Col("state_of_issue", "VARCHAR(2)", False, "State the plan was issued in"),
        Col("regulatory_tat_standard_days", "NUMBER(5,0)", False,
            "Calendar days allowed for a standard PA decision under this LOB", min_value=1),
        Col("regulatory_tat_urgent_hours", "NUMBER(5,0)", False,
            "Hours allowed for an expedited PA decision under this LOB", min_value=1),
        Col("tat_authority", "VARCHAR(200)", False,
            "Regulation the turnaround limits come from"),
        Col("pa_ruleset_id", "VARCHAR(30)", False,
            "Identifier of the PA-required service list this plan uses"),
    ),
))

register(TableSpec(
    layer=B, name="dim_memberFinance",
    grain="One row per member per plan per benefit year",
    desc="Cost-sharing design and accumulator position. Percentile shapes are "
         "calibrated against ref_synpuf_benchmark so member financial exposure "
         "follows a realistic distribution.",
    primary_key=("member_finance_sk",),
    foreign_keys=(FK("member_id", "dim_memberProfile", "member_id", allow_null=False),),
    columns=with_audit(
        Col("member_finance_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("plan_id", "VARCHAR(30)", False, "Plan"),
        Col("benefit_year", "NUMBER(4,0)", False, "Benefit year", min_value=2000, max_value=2100),
        Col("deductible_individual_amt", "NUMBER(12,2)", False, "Annual individual deductible", min_value=0),
        Col("deductible_met_amt", "NUMBER(12,2)", False, "Deductible satisfied year to date", min_value=0),
        Col("deductible_remaining_amt", "NUMBER(12,2)", False, "Deductible still owed", min_value=0),
        Col("deductible_met_flag", "BOOLEAN", False, "True when the deductible is fully satisfied"),
        Col("oop_max_individual_amt", "NUMBER(12,2)", False, "Annual individual out-of-pocket maximum", min_value=0),
        Col("oop_met_amt", "NUMBER(12,2)", False, "Out-of-pocket satisfied year to date", min_value=0),
        Col("oop_remaining_amt", "NUMBER(12,2)", False, "Remaining exposure before the OOP max", min_value=0),
        Col("oop_max_met_flag", "BOOLEAN", False, "True when the OOP maximum is reached"),
        Col("coinsurance_pct", "NUMBER(5,2)", False, "Member coinsurance percentage", min_value=0, max_value=100),
        Col("pcp_copay_amt", "NUMBER(10,2)", True, "Primary care visit copay", min_value=0),
        Col("specialist_copay_amt", "NUMBER(10,2)", True, "Specialist visit copay", min_value=0),
        Col("er_copay_amt", "NUMBER(10,2)", True, "Emergency room copay", min_value=0),
        Col("urgent_care_copay_amt", "NUMBER(10,2)", True, "Urgent care copay", min_value=0),
        Col("inpatient_copay_per_day_amt", "NUMBER(10,2)", True, "Inpatient per-diem copay", min_value=0),
        Col("inpatient_copay_max_days", "NUMBER(5,0)", True, "Days the per-diem copay applies for", min_value=0),
        Col("rx_deductible_amt", "NUMBER(10,2)", True, "Separate pharmacy deductible", min_value=0),
        Col("rx_tier1_copay_amt", "NUMBER(10,2)", True, "Generic tier copay", min_value=0),
        Col("rx_tier2_copay_amt", "NUMBER(10,2)", True, "Preferred brand tier copay", min_value=0),
        Col("rx_tier3_copay_amt", "NUMBER(10,2)", True, "Non-preferred brand tier copay", min_value=0),
        Col("rx_specialty_coinsurance_pct", "NUMBER(5,2)", True,
            "Specialty drug coinsurance percentage", min_value=0, max_value=100),
        Col("premium_monthly_amt", "NUMBER(10,2)", True, "Monthly premium", min_value=0),
        Col("premium_paid_through_date", "DATE", True, "Premium paid through"),
        Col("premium_delinquent_flag", "BOOLEAN", False,
            "True when premium is unpaid - relevant to eligibility questions"),
        Col("grace_period_end_date", "DATE", True, "End of premium grace period when delinquent"),
        Col("hsa_fsa_balance_amt", "NUMBER(12,2)", True, "HSA/FSA balance available", min_value=0),
        Col("ytd_allowed_amt", "NUMBER(14,2)", False, "Year-to-date allowed amount", min_value=0),
        Col("ytd_plan_paid_amt", "NUMBER(14,2)", False, "Year-to-date plan paid", min_value=0),
        Col("ytd_member_paid_amt", "NUMBER(14,2)", False, "Year-to-date member paid", min_value=0),
        Col("is_current", "BOOLEAN", False, "True for the current benefit year"),
    ),
))

register(TableSpec(
    layer=B, name="dim_memberHistory",
    grain="One row per member per enrolment/status change event",
    desc="Append-only change log for member status. Includes coverage gaps and "
         "retroactive terminations, which are a common root cause of PA "
         "eligibility denials and therefore something an analyst must be able "
         "to see directly.",
    primary_key=("member_history_sk",),
    foreign_keys=(FK("member_id", "dim_memberProfile", "member_id", allow_null=False),),
    columns=with_audit(
        Col("member_history_sk", "VARCHAR(40)", False, "Surrogate key"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("sequence_number", "NUMBER(6,0)", False, "Ordering of events within a member", min_value=1),
        Col("change_type", "VARCHAR(30)", False, "Nature of the change", enum=ENROLLMENT_CHANGE_TYPES),
        Col("effective_start_date", "DATE", False, "Date the change takes effect"),
        Col("effective_end_date", "DATE", True, "Date the state ended, NULL when still in force"),
        Col("processed_date", "DATE", False,
            "Date the change was actually processed - later than effective_start_date on retro actions"),
        Col("enrollment_status", "VARCHAR(20)", False, "Resulting status", enum=ENROLLMENT_STATUSES),
        Col("changed_attribute", "VARCHAR(60)", True, "Attribute that changed"),
        Col("prior_value", "VARCHAR(200)", True, "Value before the change"),
        Col("new_value", "VARCHAR(200)", True, "Value after the change"),
        Col("change_reason_code", "VARCHAR(20)", True, "Reason code"),
        Col("change_reason_desc", "VARCHAR(200)", True, "Reason description"),
        Col("plan_id", "VARCHAR(30)", True, "Plan in force after the change"),
        Col("retroactive_flag", "BOOLEAN", False,
            "True when processed_date is after effective_start_date"),
        Col("retroactive_days", "NUMBER(6,0)", True, "How far back the change was applied", min_value=0),
        Col("coverage_gap_days", "NUMBER(6,0)", True,
            "Days of no coverage created immediately before this event", min_value=0),
        Col("creates_coverage_gap_flag", "BOOLEAN", False, "True when this event opened a gap"),
    ),
))

# ===========================================================================
# FACT TABLES
# ===========================================================================

register(TableSpec(
    layer=B, name="fct_memberPA",
    grain="One row per prior authorization request",
    desc="The centrepiece fact. No public dataset contains real PA records, so "
         "these are synthesised - but every request is GROUNDED in an actual "
         "Synthea condition, encounter or medication for the same member. That "
         "grounding is what makes the clinical evidence in ext_* genuinely "
         "corroborate or contradict the request, rather than being decorative. "
         "A deliberate slice of cases is left open with live SLA clocks.",
    primary_key=("pa_id",),
    foreign_keys=(
        FK("member_id", "dim_memberProfile", "member_id", allow_null=False),
        FK("ordering_provider_npi", "ref_provider_npi", "npi"),
        FK("rendering_provider_npi", "ref_provider_npi", "npi"),
        FK("primary_dx_icd10", "ref_icd10cm", "icd10_code"),
    ),
    columns=with_audit(
        Col("pa_id", "VARCHAR(24)", False, "Prior authorization case identifier (PA-YYYY-######)"),
        Col("member_id", "VARCHAR(20)", False, "Member the request is for"),
        Col("plan_id", "VARCHAR(30)", False, "Plan in force at submission"),
        Col("line_of_business", "VARCHAR(30)", False,
            "LOB at submission - determines which TAT rule applies", enum=LINES_OF_BUSINESS),

        # --- intake ---
        Col("submitted_datetime_utc", "TIMESTAMP_NTZ", False, "When the provider submitted"),
        Col("received_datetime_utc", "TIMESTAMP_NTZ", False,
            "When the payer logged receipt - this starts the regulatory clock"),
        Col("request_channel", "VARCHAR(20)", False, "How the request arrived", enum=PA_CHANNELS),
        Col("certification_type", "VARCHAR(20)", False, "Initial, extension, renewal or appeal",
            enum=PA_CERT_TYPES),
        Col("urgency_flag", "VARCHAR(20)", False, "Requested review speed", enum=PA_URGENCY),
        Col("urgency_clinically_justified_flag", "BOOLEAN", True,
            "Whether the clinical picture supports the requested urgency. NULL when not assessed."),

        # --- what is being requested ---
        Col("service_category", "VARCHAR(30)", False, "PA domain", enum=PA_SERVICE_CATEGORIES),
        Col("requested_procedure_code", "VARCHAR(10)", True, "Requested CPT/HCPCS/ICD-10-PCS code"),
        Col("requested_code_system", "VARCHAR(12)", True, "Code system of the requested procedure",
            enum=CODE_SYSTEMS),
        Col("requested_procedure_desc", "VARCHAR(500)", True, "Requested procedure description"),
        Col("requested_ndc", "VARCHAR(20)", True, "Requested drug NDC, for pharmacy PA"),
        Col("requested_drug_name", "VARCHAR(300)", True, "Requested drug name, for pharmacy PA"),
        Col("primary_dx_icd10", "VARCHAR(10)", False, "Primary supporting diagnosis"),
        Col("primary_dx_desc", "VARCHAR(500)", True, "Primary diagnosis description"),
        Col("secondary_dx_icd10_list", "VARIANT", True, "Array of additional supporting diagnoses"),
        Col("place_of_service_code", "VARCHAR(4)", True, "Requested place of service"),
        Col("place_of_service_desc", "VARCHAR(120)", True, "Place of service description"),
        Col("requested_units", "NUMBER(10,0)", True, "Units, visits or days requested", min_value=0),
        Col("requested_start_date", "DATE", True, "Requested service start"),
        Col("requested_end_date", "DATE", True, "Requested service end"),

        # --- who is requesting ---
        Col("ordering_provider_npi", "VARCHAR(10)", True, "Ordering provider NPI"),
        Col("ordering_provider_name", "VARCHAR(300)", True, "Ordering provider name"),
        Col("ordering_provider_specialty", "VARCHAR(150)", True, "Ordering provider specialty"),
        Col("rendering_provider_npi", "VARCHAR(10)", True, "Rendering provider NPI"),
        Col("rendering_facility_npi", "VARCHAR(10)", True, "Rendering facility NPI"),
        Col("rendering_facility_name", "VARCHAR(300)", True, "Rendering facility name"),
        Col("network_status_at_submission", "VARCHAR(20)", False,
            "Network standing of the requesting provider at submission", enum=NETWORK_STATUSES),
        Col("single_case_agreement_flag", "BOOLEAN", False,
            "True when an out-of-network provider has an SCA or gap exception"),

        # --- money ---
        Col("estimated_allowed_amt", "NUMBER(12,2)", True,
            "Benchmark allowed amount for the requested service", min_value=0),
        Col("high_cost_review_flag", "BOOLEAN", False,
            "True when the estimated amount crosses the additional-review threshold"),

        # --- criteria linkage ---
        Col("criteria_ruleset_id", "VARCHAR(40)", True,
            "Ruleset used to review this request - joins to slv_pa_criteria_ruleset"),
        Col("criteria_ruleset_version", "VARCHAR(20)", True, "Ruleset version applied"),

        # --- lifecycle ---
        Col("pa_status", "VARCHAR(24)", False, "Current case status", enum=PA_STATUSES),
        Col("is_open_flag", "BOOLEAN", False,
            "True when the case is still open and its SLA clock is running"),
        Col("decision_datetime_utc", "TIMESTAMP_NTZ", True, "When a final decision was rendered"),
        Col("decision_by_role", "VARCHAR(24)", True, "Who decided", enum=PA_DECIDER_ROLES),
        Col("decision_by_user_id", "VARCHAR(40)", True, "Reviewer identifier"),
        Col("denial_reason_code", "VARCHAR(10)", True, "Denial reason", enum=DENIAL_REASON_CODES),
        Col("denial_reason_desc", "VARCHAR(300)", True,
            "Specific denial reason - CMS-0057-F requires specificity, not 'does not meet criteria'"),
        Col("approved_units", "NUMBER(10,0)", True,
            "Units approved - less than requested on a partial approval", min_value=0),
        Col("auth_number", "VARCHAR(30)", True,
            "Authorization number issued on approval. Joins to fct_memberTransactions.related_auth_number."),
        Col("auth_effective_date", "DATE", True, "Authorization valid from"),
        Col("auth_expiration_date", "DATE", True, "Authorization valid until"),

        # --- pend handling (the clock pauses here) ---
        Col("pend_reason_code", "VARCHAR(10)", True, "Why the case was pended", enum=PEND_REASON_CODES),
        Col("pend_reason_desc", "VARCHAR(300)", True, "Pend reason description"),
        Col("pend_letter_sent_date", "DATE", True, "Date the request-for-information went out"),
        Col("pend_response_received_date", "DATE", True, "Date the provider responded"),
        Col("clock_paused_days", "NUMBER(6,0)", False,
            "Days excluded from the regulatory clock while pended", min_value=0),

        # --- automation ---
        Col("auto_adjudicated_flag", "BOOLEAN", False, "True when decided without human review"),
        Col("auto_adjudication_reason", "VARCHAR(200)", True,
            "Why the rules engine auto-decided, or why it routed to manual review"),
        Col("gold_card_exempt_flag", "BOOLEAN", False,
            "True when the provider was PA-exempt for this service at submission"),

        # --- SLA ---
        Col("regulatory_deadline_datetime_utc", "TIMESTAMP_NTZ", False,
            "Hard decision deadline, computed from LOB, urgency and receipt time"),
        Col("tat_elapsed_days", "NUMBER(8,2)", True,
            "Days elapsed against the clock, excluding pend pauses", min_value=0),
        Col("sla_breach_flag", "BOOLEAN", False, "True when the deadline has already passed"),

        # --- documentation completeness ---
        Col("letter_of_medical_necessity_flag", "BOOLEAN", False, "True when an LMN was submitted"),
        Col("attachment_count", "NUMBER(5,0)", False, "Number of clinical attachments", min_value=0),
        Col("clinical_notes_attached_flag", "BOOLEAN", False, "True when progress notes were submitted"),
        Col("lab_results_attached_flag", "BOOLEAN", False, "True when lab results were submitted"),
        Col("imaging_attached_flag", "BOOLEAN", False, "True when imaging results were submitted"),

        # --- appeal ---
        Col("appeal_flag", "BOOLEAN", False, "True when the decision was appealed"),
        Col("appeal_filed_date", "DATE", True, "Date the appeal was filed"),
        Col("appeal_outcome", "VARCHAR(30)", True, "UPHELD, OVERTURNED, PARTIALLY_OVERTURNED or WITHDRAWN"),
        Col("appeal_decision_date", "DATE", True, "Date the appeal was decided"),

        # --- grounding lineage ---
        Col("grounded_on_source", "VARCHAR(40)", True,
            "Which real source record seeded this request (CONDITION, ENCOUNTER, MEDICATION, PROCEDURE)"),
        Col("grounded_on_source_id", "VARCHAR(80)", True,
            "Identifier of the seeding source record - makes the grounding auditable"),
    ),
))

register(TableSpec(
    layer=B, name="fct_memberTransactions",
    grain="One row per claim line, payment, adjustment or reversal",
    desc="Financial and utilisation transaction history. Claim lines carry the "
         "diagnosis and procedure detail that silver mines for conservative-care, "
         "imaging-prerequisite and step-therapy evidence. related_auth_number "
         "links post-service claims back to the authorization that permitted them.",
    primary_key=("transaction_id",),
    foreign_keys=(
        FK("member_id", "dim_memberProfile", "member_id", allow_null=False),
        FK("related_auth_number", "fct_memberPA", "auth_number"),
    ),
    columns=with_audit(
        Col("transaction_id", "VARCHAR(40)", False, "Unique transaction identifier"),
        Col("claim_id", "VARCHAR(40)", True, "Claim this line belongs to"),
        Col("claim_line_number", "NUMBER(6,0)", True, "Line number within the claim", min_value=1),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("plan_id", "VARCHAR(30)", True, "Plan in force on the service date"),
        Col("transaction_type", "VARCHAR(30)", False, "Kind of transaction", enum=TRANSACTION_TYPES),
        Col("claim_status", "VARCHAR(20)", True, "Adjudication outcome", enum=CLAIM_STATUSES),

        Col("service_start_date", "DATE", True, "First date of service"),
        Col("service_end_date", "DATE", True, "Last date of service"),
        Col("received_date", "DATE", True, "Date the claim was received"),
        Col("adjudicated_date", "DATE", True, "Date the claim was adjudicated"),
        Col("paid_date", "DATE", True, "Date payment was issued"),

        Col("billing_provider_npi", "VARCHAR(10)", True, "Billing provider NPI"),
        Col("rendering_provider_npi", "VARCHAR(10)", True, "Rendering provider NPI"),
        Col("facility_npi", "VARCHAR(10)", True, "Facility NPI"),
        Col("provider_network_status", "VARCHAR(20)", True, "Network standing", enum=NETWORK_STATUSES),
        Col("place_of_service_code", "VARCHAR(4)", True, "Place of service"),

        Col("procedure_code", "VARCHAR(10)", True, "Procedure code billed"),
        Col("procedure_code_system", "VARCHAR(12)", True, "Procedure code system", enum=CODE_SYSTEMS),
        Col("procedure_desc", "VARCHAR(500)", True, "Procedure description"),
        Col("modifier_1", "VARCHAR(4)", True, "First procedure modifier"),
        Col("modifier_2", "VARCHAR(4)", True, "Second procedure modifier"),
        Col("revenue_code", "VARCHAR(6)", True, "Institutional revenue code"),
        Col("drg_code", "VARCHAR(10)", True, "Diagnosis-related group, inpatient only"),
        Col("units_of_service", "NUMBER(10,2)", True, "Units billed", min_value=0),

        Col("primary_dx_icd10", "VARCHAR(10)", True, "Primary diagnosis on the line"),
        Col("dx_icd10_list", "VARIANT", True, "Array of all diagnoses on the claim"),

        Col("ndc_code", "VARCHAR(20)", True, "Drug NDC, pharmacy claims only"),
        Col("drug_name", "VARCHAR(300)", True, "Drug name, pharmacy claims only"),
        Col("quantity_dispensed", "NUMBER(12,3)", True, "Quantity dispensed", min_value=0),
        Col("days_supply", "NUMBER(6,0)", True, "Days supply dispensed", min_value=0),
        Col("fill_number", "NUMBER(4,0)", True, "Refill sequence, 0 = original fill", min_value=0),
        Col("is_generic", "BOOLEAN", True, "True for generic dispense"),
        Col("formulary_tier", "NUMBER(2,0)", True, "Formulary tier at time of fill", min_value=0),

        Col("billed_amt", "NUMBER(14,2)", True, "Provider billed charge", min_value=0),
        Col("allowed_amt", "NUMBER(14,2)", True, "Contractually allowed amount", min_value=0),
        Col("plan_paid_amt", "NUMBER(14,2)", True, "Amount the plan paid", min_value=0),
        Col("member_deductible_amt", "NUMBER(14,2)", True, "Applied to deductible", min_value=0),
        Col("member_coinsurance_amt", "NUMBER(14,2)", True, "Member coinsurance", min_value=0),
        Col("member_copay_amt", "NUMBER(14,2)", True, "Member copay", min_value=0),
        Col("member_responsibility_amt", "NUMBER(14,2)", True,
            "Total member responsibility on the line", min_value=0),
        Col("cob_paid_amt", "NUMBER(14,2)", True, "Paid by another carrier under COB", min_value=0),
        Col("adjustment_amt", "NUMBER(14,2)", True, "Adjustment applied - may be negative"),
        Col("outstanding_amt", "NUMBER(14,2)", True, "Balance still outstanding"),

        Col("related_auth_number", "VARCHAR(30)", True,
            "Authorization that permitted this service, when one existed"),
        Col("pa_required_flag", "BOOLEAN", True, "True when the service required PA"),
        Col("pa_on_file_flag", "BOOLEAN", True, "True when a matching authorization was found"),
        Col("denial_code", "VARCHAR(20)", True, "Claim denial code"),
        Col("denial_desc", "VARCHAR(300)", True, "Claim denial description"),
    ),
))

register(TableSpec(
    layer=B, name="fct_memberEvents",
    grain="One row per member interaction or communication event",
    desc="Member-facing contact history across all channels. related_pa_id ties "
         "correspondence to the PA case it concerns, which is how an analyst sees "
         "whether a pend letter actually went out and whether the member or "
         "provider responded.",
    primary_key=("event_id",),
    foreign_keys=(
        FK("member_id", "dim_memberProfile", "member_id", allow_null=False),
        FK("related_pa_id", "fct_memberPA", "pa_id"),
    ),
    columns=with_audit(
        Col("event_id", "VARCHAR(40)", False, "Unique event identifier"),
        Col("member_id", "VARCHAR(20)", False, "Member"),
        Col("event_datetime_utc", "TIMESTAMP_NTZ", False, "When the event occurred"),
        Col("event_category", "VARCHAR(30)", False, "Kind of interaction", enum=MEMBER_EVENT_CATEGORIES),
        Col("event_subtype", "VARCHAR(60)", True, "Finer classification"),
        Col("channel", "VARCHAR(30)", False, "Channel used (PHONE, PORTAL, MAIL, FAX, SMS, EMAIL)"),
        Col("direction", "VARCHAR(12)", False, "INBOUND or OUTBOUND"),
        Col("related_pa_id", "VARCHAR(24)", True, "PA case this interaction concerns"),
        Col("related_claim_id", "VARCHAR(40)", True, "Claim this interaction concerns"),
        Col("handled_by_queue", "VARCHAR(60)", True, "Servicing queue"),
        Col("agent_id", "VARCHAR(40)", True, "Handling agent identifier"),
        Col("duration_seconds", "NUMBER(8,0)", True, "Interaction duration for voice contacts", min_value=0),
        Col("disposition_code", "VARCHAR(20)", True, "Outcome code"),
        Col("disposition_desc", "VARCHAR(300)", True, "Outcome description"),
        Col("topic", "VARCHAR(80)", True, "What the contact was about"),
        Col("resolved_flag", "BOOLEAN", False, "True when resolved on this contact"),
        Col("escalated_flag", "BOOLEAN", False, "True when escalated"),
        Col("sentiment_score", "NUMBER(4,3)", True,
            "Synthetic sentiment between -1 and 1", min_value=-1, max_value=1),
        Col("notes_text", "VARCHAR(2000)", True, "Short interaction note"),
    ),
))

# ===========================================================================
# EXTERNAL TABLES - shared by value-based-care partner providers
#
# These arrive from provider/EHR systems, not from the payer's own systems.
# They deliberately have their own identity space (MRN, not member_id) and a
# realistic share of missing or conflicting payer_member_id values, so identity
# resolution in silver is real work rather than a trivial join. That mirrors how
# payer-provider data sharing actually behaves.
# ===========================================================================

register(TableSpec(
    layer=B, name="ext_patientProfile",
    grain="One row per patient per contributing provider organization",
    desc="Provider-side patient identity under a VBC data-sharing agreement. The "
         "same human may appear here and in dim_memberProfile under different "
         "identifiers; payer_member_id is absent or conflicting for a realistic "
         "minority of rows.",
    primary_key=("ext_patient_id",),
    columns=with_audit(
        Col("ext_patient_id", "VARCHAR(40)", False, "Provider medical record number (MRN-#######)"),
        Col("source_org_id", "VARCHAR(30)", False, "Contributing provider organization identifier"),
        Col("source_org_name", "VARCHAR(300)", False, "Contributing provider organization name"),
        Col("source_ehr_vendor", "VARCHAR(40)", False, "EHR platform the data came from", enum=EHR_VENDORS),
        Col("data_sharing_agreement_id", "VARCHAR(40)", False, "Governing data-sharing agreement"),
        Col("vbc_contract_id", "VARCHAR(40)", True, "Value-based-care contract, when attributed"),
        Col("payer_member_id", "VARCHAR(20)", True,
            "Payer member id as asserted by the provider. NULL or wrong on a "
            "realistic minority of rows - this is what forces identity resolution."),
        Col("payer_member_id_asserted_flag", "BOOLEAN", False,
            "True when the provider supplied a member id at all"),
        Col("match_method", "VARCHAR(20)", True, "How the record was matched to a member",
            enum=MATCH_METHODS),
        Col("match_confidence_score", "NUMBER(5,4)", True,
            "Match confidence between 0 and 1", min_value=0, max_value=1),
        Col("first_name", "VARCHAR(100)", True, "Given name as recorded by the provider"),
        Col("last_name", "VARCHAR(100)", True, "Family name as recorded by the provider"),
        Col("date_of_birth", "DATE", True, "Date of birth as recorded by the provider"),
        Col("gender", "VARCHAR(2)", True, "Gender as recorded by the provider", enum=GENDERS),
        Col("address_line1", "VARCHAR(200)", True, "Street address. Excluded from SERVING views."),
        Col("city", "VARCHAR(100)", True, "City"),
        Col("state", "VARCHAR(2)", True, "State"),
        Col("zip_code", "VARCHAR(10)", True, "Postal code"),
        Col("phone", "VARCHAR(30)", True, "Phone. Excluded from SERVING views."),
        Col("primary_care_provider_npi", "VARCHAR(10)", True, "Treating PCP NPI"),
        Col("primary_care_provider_name", "VARCHAR(300)", True, "Treating PCP name"),
        Col("attributed_flag", "BOOLEAN", False, "True when attributed to the VBC contract"),
        Col("attribution_start_date", "DATE", True, "Attribution period start"),
        Col("attribution_end_date", "DATE", True, "Attribution period end"),
        Col("risk_score_hcc", "NUMBER(6,3)", True, "HCC risk score", min_value=0),
        Col("risk_tier", "VARCHAR(20)", True, "LOW, MODERATE, HIGH or VERY_HIGH"),
        Col("last_encounter_date", "DATE", True, "Most recent encounter at this organization"),
        Col("consent_on_file_flag", "BOOLEAN", False, "True when patient consent to share is recorded"),
        Col("consent_scope", "VARCHAR(60)", True, "Scope of the recorded consent"),
    ),
))

register(TableSpec(
    layer=B, name="ext_patientClinicalRecords",
    grain="One row per discrete clinical record (a diagnosis, procedure, lab, vital or imaging result)",
    desc="Clinical detail from provider EHRs. Source codes are the native "
         "SNOMED-CT / LOINC values; the icd10_* and cpt_hcpcs_* columns are the "
         "crosswalked equivalents, because PA is transacted on ICD-10 and "
         "CPT/HCPCS and an analyst works in those code sets, not SNOMED.",
    primary_key=("clinical_record_id",),
    foreign_keys=(FK("ext_patient_id", "ext_patientProfile", "ext_patient_id", allow_null=False),),
    columns=with_audit(
        Col("clinical_record_id", "VARCHAR(40)", False, "Unique clinical record identifier"),
        Col("ext_patient_id", "VARCHAR(40)", False, "Provider patient identifier"),
        Col("payer_member_id", "VARCHAR(20)", True, "Payer member id when resolvable"),
        Col("source_org_id", "VARCHAR(30)", False, "Contributing organization"),
        Col("record_type", "VARCHAR(30)", False, "Kind of clinical record", enum=CLINICAL_RECORD_TYPES),
        Col("record_date", "DATE", False, "Clinical date of the record"),
        Col("recorded_datetime_utc", "TIMESTAMP_NTZ", True, "When it was charted"),
        Col("encounter_ref", "VARCHAR(64)", True, "Encounter this record belongs to"),

        Col("source_code", "VARCHAR(30)", True, "Native code as recorded in the EHR"),
        Col("source_code_system", "VARCHAR(12)", True, "Native code system", enum=CODE_SYSTEMS),
        Col("source_code_desc", "VARCHAR(500)", True, "Native code description"),

        Col("icd10_code", "VARCHAR(10)", True, "Crosswalked ICD-10-CM diagnosis code"),
        Col("icd10_desc", "VARCHAR(500)", True, "Crosswalked ICD-10-CM description"),
        Col("cpt_hcpcs_code", "VARCHAR(10)", True, "Crosswalked CPT/HCPCS procedure code"),
        Col("cpt_hcpcs_desc", "VARCHAR(500)", True, "Crosswalked CPT/HCPCS description"),
        Col("crosswalk_method", "VARCHAR(30)", True,
            "How the crosswalk was derived (CURATED_MAP, CATEGORY_DEFAULT, UNMAPPED)"),

        Col("clinical_status", "VARCHAR(20)", True, "ACTIVE, RESOLVED or REMISSION"),
        Col("onset_date", "DATE", True, "Onset date for conditions"),
        Col("resolved_date", "DATE", True, "Resolution date for conditions"),
        Col("is_chronic_flag", "BOOLEAN", False, "True for chronic conditions"),

        Col("result_value_numeric", "NUMBER(18,6)", True, "Numeric result for labs and vitals"),
        Col("result_value_text", "VARCHAR(500)", True, "Text result when not numeric"),
        Col("result_unit", "VARCHAR(40)", True, "Unit of measure"),
        Col("reference_range_low", "NUMBER(18,6)", True, "Lower bound of the normal range"),
        Col("reference_range_high", "NUMBER(18,6)", True, "Upper bound of the normal range"),
        Col("abnormal_flag", "VARCHAR(10)", True, "NORMAL, HIGH, LOW or CRITICAL"),

        Col("body_site", "VARCHAR(120)", True, "Anatomical site"),
        Col("body_region", "VARCHAR(60)", True,
            "Coarse body region - used to match imaging prerequisites and conservative care to the request"),
        Col("laterality", "VARCHAR(20)", True, "LEFT, RIGHT, BILATERAL or NA"),
        Col("severity", "VARCHAR(20)", True, "MILD, MODERATE or SEVERE"),
        Col("performing_provider_npi", "VARCHAR(10)", True, "Provider who performed or recorded it"),
    ),
))

register(TableSpec(
    layer=B, name="ext_patientEhrEvents",
    grain="One row per EHR event message",
    desc="Near-real-time event stream from partner EHRs, modelled on ADT and "
         "order messaging. ingestion_latency_hours preserves the fact that these "
         "feeds lag, which matters when an analyst asks whether an admission has "
         "actually been reported yet.",
    primary_key=("ehr_event_id",),
    foreign_keys=(FK("ext_patient_id", "ext_patientProfile", "ext_patient_id", allow_null=False),),
    columns=with_audit(
        Col("ehr_event_id", "VARCHAR(40)", False, "Unique event identifier"),
        Col("ext_patient_id", "VARCHAR(40)", False, "Provider patient identifier"),
        Col("payer_member_id", "VARCHAR(20)", True, "Payer member id when resolvable"),
        Col("source_org_id", "VARCHAR(30)", False, "Contributing organization"),
        Col("source_ehr_vendor", "VARCHAR(40)", False, "EHR platform", enum=EHR_VENDORS),
        Col("event_datetime_utc", "TIMESTAMP_NTZ", False, "When the event occurred clinically"),
        Col("received_datetime_utc", "TIMESTAMP_NTZ", False, "When the payer received the message"),
        Col("ingestion_latency_hours", "NUMBER(8,2)", False,
            "Hours between occurrence and receipt", min_value=0),
        Col("event_type", "VARCHAR(30)", False, "Kind of event", enum=EHR_EVENT_TYPES),
        Col("adt_message_type", "VARCHAR(10)", True, "HL7 ADT message type (A01, A03, A04, A08)"),

        Col("encounter_id", "VARCHAR(64)", True, "Encounter identifier"),
        Col("encounter_class", "VARCHAR(20)", True, "Encounter setting", enum=ENCOUNTER_CLASSES),
        Col("encounter_reason_code", "VARCHAR(30)", True, "Reason code for the encounter"),
        Col("encounter_reason_desc", "VARCHAR(500)", True, "Reason description"),

        Col("department", "VARCHAR(120)", True, "Department or unit"),
        Col("location", "VARCHAR(200)", True, "Facility or location name"),
        Col("attending_provider_npi", "VARCHAR(10)", True, "Attending provider NPI"),

        Col("admission_datetime_utc", "TIMESTAMP_NTZ", True, "Admission timestamp for inpatient events"),
        Col("discharge_datetime_utc", "TIMESTAMP_NTZ", True, "Discharge timestamp"),
        Col("length_of_stay_days", "NUMBER(8,2)", True, "Length of stay in days", min_value=0),
        Col("discharge_disposition", "VARCHAR(120)", True, "Where the patient went at discharge"),

        Col("order_type", "VARCHAR(60)", True, "Order category for order events"),
        Col("order_code", "VARCHAR(30)", True, "Ordered item code"),
        Col("order_desc", "VARCHAR(500)", True, "Ordered item description"),
        Col("order_status", "VARCHAR(30)", True, "PLACED, IN_PROGRESS, COMPLETED or CANCELLED"),

        Col("referral_specialty", "VARCHAR(150)", True, "Specialty referred to"),
        Col("referral_reason", "VARCHAR(500)", True, "Reason for referral"),
    ),
))

register(TableSpec(
    layer=B, name="ext_patientMedicalHistory",
    grain="One row per longitudinal history item (a condition, medication, allergy, immunization or history entry)",
    desc="Longitudinal patient history. The medication rows carry trial dates and "
         "PDC adherence, which is exactly what a step-therapy criterion needs; "
         "bmi_value supports the bariatric BMI thresholds. This is the primary "
         "evidence source for medical-necessity criteria evaluation.",
    primary_key=("medical_history_id",),
    foreign_keys=(FK("ext_patient_id", "ext_patientProfile", "ext_patient_id", allow_null=False),),
    columns=with_audit(
        Col("medical_history_id", "VARCHAR(40)", False, "Unique history record identifier"),
        Col("ext_patient_id", "VARCHAR(40)", False, "Provider patient identifier"),
        Col("payer_member_id", "VARCHAR(20)", True, "Payer member id when resolvable"),
        Col("source_org_id", "VARCHAR(30)", False, "Contributing organization"),
        Col("history_type", "VARCHAR(30)", False, "Kind of history entry", enum=MEDICAL_HISTORY_TYPES),
        Col("onset_date", "DATE", True, "Start date"),
        Col("end_date", "DATE", True, "End date, NULL when ongoing"),
        Col("is_active", "BOOLEAN", False, "True when currently active"),

        # condition
        Col("condition_snomed_code", "VARCHAR(30)", True, "SNOMED-CT condition code"),
        Col("condition_icd10_code", "VARCHAR(10)", True, "Crosswalked ICD-10-CM condition code"),
        Col("condition_desc", "VARCHAR(500)", True, "Condition description"),
        Col("condition_category", "VARCHAR(60)", True, "Coarse condition grouping"),

        # medication
        Col("medication_rxnorm_code", "VARCHAR(30)", True, "RxNorm concept code"),
        Col("medication_ndc_code", "VARCHAR(20)", True, "NDC product code"),
        Col("medication_name", "VARCHAR(300)", True, "Medication as prescribed"),
        Col("medication_generic_name", "VARCHAR(300)", True, "Generic name"),
        Col("therapeutic_class", "VARCHAR(120)", True,
            "Therapeutic class - step therapy is evaluated within a class"),
        Col("dose_amount", "VARCHAR(60)", True, "Dose amount"),
        Col("dose_unit", "VARCHAR(40)", True, "Dose unit"),
        Col("route", "VARCHAR(60)", True, "Route of administration"),
        Col("frequency", "VARCHAR(60)", True, "Dosing frequency"),
        Col("sig_text", "VARCHAR(500)", True, "Prescription directions"),
        Col("prescriber_npi", "VARCHAR(10)", True, "Prescribing provider NPI"),
        Col("prescribed_date", "DATE", True, "Date prescribed"),
        Col("discontinued_date", "DATE", True, "Date discontinued"),
        Col("discontinue_reason", "VARCHAR(200)", True,
            "Why it was stopped - 'inadequate response' is what satisfies a fail-first criterion"),
        Col("dispense_count", "NUMBER(6,0)", True, "Number of dispenses", min_value=0),
        Col("days_supply_total", "NUMBER(8,0)", True, "Cumulative days supply", min_value=0),
        Col("therapy_duration_days", "NUMBER(8,0)", True,
            "Days between first and last dispense - measures trial duration", min_value=0),
        Col("adherence_pdc_pct", "NUMBER(5,2)", True,
            "Proportion of days covered - distinguishes a genuine failed trial from non-adherence",
            min_value=0, max_value=100),
        Col("is_specialty_drug", "BOOLEAN", True, "True for specialty/biologic products"),
        Col("requires_pa_flag", "BOOLEAN", True, "True when the drug is PA-gated"),
        Col("formulary_tier", "NUMBER(2,0)", True, "Formulary tier", min_value=0),
        Col("is_first_line_therapy", "BOOLEAN", True,
            "True when this drug is a first-line option in its class"),

        # allergy
        Col("allergy_substance", "VARCHAR(300)", True, "Allergen"),
        Col("allergy_reaction", "VARCHAR(300)", True, "Observed reaction"),
        Col("allergy_severity", "VARCHAR(20)", True, "MILD, MODERATE or SEVERE"),
        Col("allergy_criticality", "VARCHAR(20)", True, "LOW, HIGH or UNABLE_TO_ASSESS"),

        # immunization
        Col("immunization_cvx_code", "VARCHAR(10)", True, "CVX vaccine code"),
        Col("immunization_desc", "VARCHAR(300)", True, "Vaccine description"),

        # social / anthropometric
        Col("smoking_status", "VARCHAR(40)", True, "Smoking status"),
        Col("bmi_value", "NUMBER(6,2)", True,
            "Body mass index - the threshold input for bariatric criteria", min_value=0, max_value=100),
        Col("bmi_recorded_date", "DATE", True, "Date the BMI was recorded"),
        Col("height_cm", "NUMBER(6,2)", True, "Height in centimetres", min_value=0),
        Col("weight_kg", "NUMBER(7,2)", True, "Weight in kilograms", min_value=0),

        Col("notes_text", "VARCHAR(1000)", True, "Free-text note"),
    ),
))



