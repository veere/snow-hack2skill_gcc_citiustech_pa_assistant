"""
FROZEN SCHEMA CONTRACT - Member360 PA Datamart
==============================================

This module is the single authoritative definition of every table in the mart.
It is imported by:

  * the bronze/silver/gold generator scripts  (to build DataFrames with the
    exact expected columns, in the exact expected order)
  * the validation framework                 (PK/FK/enum/null/type assertions)
  * the DDL emitters                          (typed Snowflake DDL, never inferred)
  * the Snowflake loader                      (COPY INTO column matching)

WHY THIS EXISTS
---------------
Four generator tracks run in parallel and their outputs must join cleanly in
silver and gold. Without one frozen contract the layers drift - a column gets
renamed in bronze, silver still joins on the old name, and the break surfaces
three layers downstream. Freezing the contract up front is what makes the
parallel build safe.

RULES
-----
1. BRONZE IS FROZEN. Do not edit bronze specs. Downstream code depends on them.
2. Silver/gold specs live in `schemas_silver.py` / `schemas_gold.py` and are
   registered into the same REGISTRY by the implementors who own those layers.
3. Column order in a spec is the physical column order. Generators must emit
   DataFrames whose columns match `spec.col_names()` exactly.
4. Every table carries AUDIT_COLS for lineage and determinism checking.
5. No table anywhere may contain a column that recommends or predicts a
   decision. See `config/mart_config.json -> governance.banned_column_substrings`.

NAMING
------
Table identifiers are written unquoted exactly as specified by the requirement
(e.g. `dim_memberProfile`). Snowflake resolves unquoted identifiers to uppercase,
so the physical name is DIM_MEMBERPROFILE and every unquoted reference continues
to resolve. No quoted identifiers are used anywhere, so nothing downstream has to
carry quoting through the app.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Core spec types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Col:
    """One column. `sf_type` is the literal Snowflake type used in DDL."""

    name: str
    sf_type: str
    nullable: bool = True
    desc: str = ""
    enum: tuple[str, ...] = ()
    # Inclusive numeric bounds, used by the validator. None = unbounded.
    min_value: float | None = None
    max_value: float | None = None

    @property
    def is_enum(self) -> bool:
        return len(self.enum) > 0


@dataclass(frozen=True)
class FK:
    """Referential expectation. Enforced by the validator, not by Snowflake."""

    column: str
    target_table: str
    target_column: str
    # When True a NULL is legitimate (e.g. a claim with no linked auth).
    allow_null: bool = True


@dataclass(frozen=True)
class TableSpec:
    layer: str
    name: str
    grain: str  # plain-language description of exactly what one row represents
    primary_key: tuple[str, ...]
    columns: tuple[Col, ...]
    foreign_keys: tuple[FK, ...] = ()
    desc: str = ""
    # Columns that must be unique together beyond the PK.
    unique_sets: tuple[tuple[str, ...], ...] = ()

    def col_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def col(self, name: str) -> Col:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(f"{self.layer}.{self.name} has no column {name!r}")

    def enum_cols(self) -> list[Col]:
        return [c for c in self.columns if c.is_enum]

    def not_null_cols(self) -> list[str]:
        return [c.name for c in self.columns if not c.nullable]

    def parquet_filename(self) -> str:
        return f"{self.name}.parquet"

    def ddl(self, database: str, schema: str, *, or_replace: bool = True) -> str:
        """Emit typed CREATE TABLE DDL. Types are explicit - never inferred."""
        verb = "CREATE OR REPLACE TABLE" if or_replace else "CREATE TABLE IF NOT EXISTS"
        width = max(len(c.name) for c in self.columns) + 2
        lines = []
        for c in self.columns:
            null_kw = "" if c.nullable else " NOT NULL"
            comment = c.desc.replace("'", "''")
            lines.append(
                f"    {c.name.ljust(width)} {c.sf_type}{null_kw}"
                f" COMMENT '{comment}'"
            )
        body = ",\n".join(lines)
        tbl_comment = f"{self.grain} | {self.desc}".replace("'", "''")
        pk = ""
        if self.primary_key:
            pk = (
                f",\n    CONSTRAINT pk_{self.name.lower()} "
                f"PRIMARY KEY ({', '.join(self.primary_key)})"
            )
        return (
            f"{verb} {database}.{schema}.{self.name} (\n{body}{pk}\n)\n"
            f"COMMENT = '{tbl_comment}';"
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, dict[str, TableSpec]] = {
    "bronze": {},
    "silver": {},
    "gold": {},
}


def register(spec: TableSpec) -> TableSpec:
    if spec.layer not in REGISTRY:
        raise ValueError(f"unknown layer {spec.layer!r}")
    if spec.name in REGISTRY[spec.layer]:
        raise ValueError(f"duplicate spec {spec.layer}.{spec.name}")
    REGISTRY[spec.layer][spec.name] = spec
    return spec


def get(layer: str, name: str) -> TableSpec:
    return REGISTRY[layer][name]


def layer_tables(layer: str) -> list[TableSpec]:
    """Specs for a layer, in registration (load) order."""
    return list(REGISTRY[layer].values())


def all_specs() -> list[TableSpec]:
    return [s for layer in REGISTRY.values() for s in layer.values()]


# ---------------------------------------------------------------------------
# Shared audit columns - present on every table
# ---------------------------------------------------------------------------

AUDIT_COLS: tuple[Col, ...] = (
    Col("source_system", "VARCHAR(40)", False,
        "Originating system for this row (SYNTHEA, CMS_SYNPUF, FDA_NDC, "
        "CMS_ICD10, CMS_HCPCS, NPPES, SYNTHETIC, DERIVED)"),
    Col("ingested_at_utc", "TIMESTAMP_NTZ", False,
        "UTC timestamp the row was produced by the local pipeline"),
    Col("record_hash", "VARCHAR(64)", False,
        "SHA-256 over the row's business columns. Stable across reruns with the "
        "same seed - this is what the determinism test compares."),
)


def with_audit(*cols: Col) -> tuple[Col, ...]:
    """Append the standard audit trailer to a column list."""
    return tuple(cols) + AUDIT_COLS


# ---------------------------------------------------------------------------
# Controlled vocabularies
#
# Defined once here so generators, validators and docs cannot disagree. These
# are the real code sets and status values used in payer PA operations.
# ---------------------------------------------------------------------------

LINES_OF_BUSINESS = ("MEDICARE_ADVANTAGE", "MEDICAID", "COMMERCIAL", "MARKETPLACE")

PRODUCT_TYPES = ("HMO", "PPO", "EPO", "POS")

GENDERS = ("M", "F", "U")

# PA service categories - the high-volume PA domains.
PA_SERVICE_CATEGORIES = (
    "ADVANCED_IMAGING",
    "SPECIALTY_DRUG",
    "ELECTIVE_SURGERY",
    "DME",
    "BEHAVIORAL_HEALTH",
    "INPATIENT_ADMIT",
    "REHAB_THERAPY",
    "GENETIC_TESTING",
    "HOME_HEALTH",
    "SLEEP_STUDY",
    "PAIN_MANAGEMENT",
    "RADIATION_ONCOLOGY",
    "TRANSPLANT",
)

PA_STATUSES = (
    "RECEIVED",
    "PENDING_CLINICAL",
    "PENDED_FOR_INFO",
    "PEER_TO_PEER",
    "APPROVED",
    "PARTIALLY_APPROVED",
    "DENIED",
    "WITHDRAWN",
    "EXPIRED",
)

# Statuses that mean the case is still open and its SLA clock is live.
PA_OPEN_STATUSES = (
    "RECEIVED",
    "PENDING_CLINICAL",
    "PENDED_FOR_INFO",
    "PEER_TO_PEER",
)

PA_URGENCY = ("STANDARD", "EXPEDITED", "EMERGENT")

PA_CERT_TYPES = ("INITIAL", "EXTENSION", "RENEWAL", "APPEAL")

PA_CHANNELS = ("PORTAL", "FAX", "PHONE", "X12_278", "FHIR_PAS")

PA_DECIDER_ROLES = ("AUTO", "NURSE", "MEDICAL_DIRECTOR")

# Denial reasons. Codes are internal; descriptions mirror industry categories.
DENIAL_REASONS = (
    ("DN01", "Does not meet medical necessity criteria"),
    ("DN02", "Insufficient clinical documentation"),
    ("DN03", "Step therapy not completed"),
    ("DN04", "Conservative treatment not exhausted"),
    ("DN05", "Service not covered under plan"),
    ("DN06", "Out-of-network provider without authorization"),
    ("DN07", "Duplicate request"),
    ("DN08", "Authorization request window expired"),
    ("DN09", "Member not eligible on date of service"),
    ("DN10", "Incorrect or unsupported coding"),
    ("DN11", "Experimental or investigational service"),
    ("DN12", "Frequency or quantity limit exceeded"),
)
DENIAL_REASON_CODES = tuple(c for c, _ in DENIAL_REASONS)

PEND_REASONS = (
    ("PD01", "Missing clinical or progress notes"),
    ("PD02", "Missing imaging or lab results"),
    ("PD03", "Missing prior treatment history"),
    ("PD04", "Incomplete provider information"),
    ("PD05", "Missing pathology or biopsy results"),
    ("PD06", "Operative report not submitted"),
    ("PD07", "Missing medication trial history"),
    ("PD08", "Height, weight or BMI not documented"),
    ("PD09", "Functional assessment not included"),
    ("PD10", "Missing letter of medical necessity"),
)
PEND_REASON_CODES = tuple(c for c, _ in PEND_REASONS)

NETWORK_STATUSES = ("IN_NETWORK", "OUT_OF_NETWORK", "SCA")

# Criterion types in the medical-necessity ruleset.
CRITERION_TYPES = (
    "STEP_THERAPY",
    "CONSERVATIVE_CARE_DURATION",
    "FAILED_FIRST_LINE",
    "IMAGING_PREREQUISITE",
    "BMI_THRESHOLD",
    "LAB_THRESHOLD",
    "DIAGNOSIS_SUPPORT",
    "DURATION_QUANTITY_LIMIT",
    "AGE_GENDER_GATE",
    "PLACE_OF_SERVICE",
    "NO_DUPLICATE",
)

# Deliberately three-valued. "INDETERMINATE" means the data needed to judge the
# criterion is absent - which is itself the thing an analyst needs to know. It is
# never collapsed into a pass or a fail.
CRITERION_RESULTS = ("MET", "UNMET", "INDETERMINATE")

CODE_SYSTEMS = ("CPT", "HCPCS", "ICD10PCS", "ICD10CM", "SNOMED-CT", "LOINC", "RXNORM", "NDC", "CVX")

CLINICAL_RECORD_TYPES = (
    "DIAGNOSIS", "PROCEDURE", "LAB_RESULT", "VITAL_SIGN", "IMAGING_RESULT", "ASSESSMENT",
)

MEDICAL_HISTORY_TYPES = (
    "CHRONIC_CONDITION", "MEDICATION", "ALLERGY", "IMMUNIZATION",
    "SURGICAL_HISTORY", "FAMILY_HISTORY", "SOCIAL_HISTORY", "CARE_PLAN",
)

EHR_EVENT_TYPES = (
    "ENCOUNTER_START", "ENCOUNTER_END", "ADMISSION", "DISCHARGE", "TRANSFER",
    "ED_ARRIVAL", "ORDER_PLACED", "ORDER_RESULTED", "MED_ADMINISTERED",
    "REFERRAL_CREATED", "DOCUMENT_SIGNED", "CARE_PLAN_UPDATED",
)

MEMBER_EVENT_CATEGORIES = (
    "CALL_INBOUND", "CALL_OUTBOUND", "PORTAL_LOGIN", "SECURE_MESSAGE",
    "LETTER_SENT", "FAX_RECEIVED", "SMS_SENT", "EMAIL_SENT",
    "APPEAL_FILED", "GRIEVANCE_FILED", "CASE_MGMT_OUTREACH",
)

TRANSACTION_TYPES = (
    "CLAIM_PROFESSIONAL", "CLAIM_INSTITUTIONAL", "CLAIM_PHARMACY",
    "PAYMENT", "ADJUSTMENT", "REVERSAL", "REFUND", "CAPITATION",
)

CLAIM_STATUSES = ("PAID", "DENIED", "PENDED", "REVERSED")

ENROLLMENT_CHANGE_TYPES = (
    "ENROLL", "TERM", "REINSTATE", "PLAN_CHANGE", "ADDRESS_CHANGE",
    "PCP_CHANGE", "RETRO_TERM", "DEATH",
)

ENROLLMENT_STATUSES = ("ACTIVE", "TERMED", "SUSPENDED")

EHR_VENDORS = ("Epic", "Cerner", "Athenahealth", "Allscripts", "eClinicalWorks", "NextGen")

MATCH_METHODS = ("DETERMINISTIC", "PROBABILISTIC", "UNMATCHED")

ENCOUNTER_CLASSES = ("inpatient", "outpatient", "emergency", "ambulatory", "wellness", "urgentcare", "virtual")


# ---------------------------------------------------------------------------
# Regulatory turnaround time by line of business.
#
# Sourced from CMS-0057-F (7 calendar days standard / 72 hours expedited,
# effective 2026-01-01), 42 CFR 422.568 (Medicare Advantage), 42 CFR 438.210
# (Medicaid managed care) and 29 CFR 2560.503-1 (ERISA commercial).
# See docs/PA_analyst_research.md for full citations.
# ---------------------------------------------------------------------------

TAT_RULES: dict[str, dict[str, int | str]] = {
    "MEDICARE_ADVANTAGE": {
        "standard_days": 7,
        "expedited_hours": 72,
        "authority": "CMS-0057-F (supersedes 42 CFR 422.568 14-day standard from 2026-01-01)",
    },
    "MEDICAID": {
        "standard_days": 7,
        "expedited_hours": 72,
        "authority": "CMS-0057-F (supersedes 42 CFR 438.210 14-day standard from 2026-01-01)",
    },
    "MARKETPLACE": {
        "standard_days": 7,
        "expedited_hours": 72,
        "authority": "CMS-0057-F (QHP issuers on FFEs)",
    },
    "COMMERCIAL": {
        "standard_days": 15,
        "expedited_hours": 72,
        "authority": "29 CFR 2560.503-1 (ERISA pre-service)",
    },
}

# Gold-carding threshold - Texas HB 3459 / Texas Insurance Code Ch. 4201 Subch. N.
GOLD_CARD_APPROVAL_THRESHOLD_PCT = 90.0
GOLD_CARD_LOOKBACK_MONTHS = 6
GOLD_CARD_MIN_VOLUME = 5  # below this, an approval rate is not meaningful


# ---------------------------------------------------------------------------
# Config file access
# ---------------------------------------------------------------------------

import json  # noqa: E402
from pathlib import Path  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPO_ROOT / "config" / "mart_config.json"


def load_config() -> dict:
    """Read mart_config.json. Environment variables override Snowflake settings
    so another environment can switch auth method without editing any file."""
    import os

    with CONFIG_PATH.open(encoding="utf-8") as fh:
        cfg = json.load(fh)

    sf = cfg["snowflake"]
    env_map = {
        "SNOWFLAKE_CONNECTION_NAME": "connection_name",
        "SNOWFLAKE_DATABASE": "database",
        "SNOWFLAKE_WAREHOUSE": "warehouse",
    }
    for env_key, cfg_key in env_map.items():
        if os.environ.get(env_key):
            sf[cfg_key] = os.environ[env_key]

    cfg["_repo_root"] = str(REPO_ROOT)
    return cfg


def banned_column_substrings() -> list[str]:
    return load_config()["governance"]["banned_column_substrings"]


def assert_no_banned_columns() -> None:
    """Governance gate. The assistant must stay non-decision-making, so no table
    anywhere may carry a column that recommends or predicts a decision."""
    banned = [b.lower() for b in banned_column_substrings()]
    violations: list[str] = []
    for spec in all_specs():
        for col in spec.columns:
            low = col.name.lower()
            for bad in banned:
                if bad in low:
                    violations.append(f"{spec.layer}.{spec.name}.{col.name} (matched {bad!r})")
    if violations:
        raise AssertionError(
            "Non-decision-making guardrail violated. These columns imply a "
            "decision recommendation and must be removed or renamed:\n  "
            + "\n  ".join(violations)
        )


# ---------------------------------------------------------------------------
# Populate the registry.
#
# Imported at the bottom so the layer modules can import the types defined above
# without a circular-import problem.
# ---------------------------------------------------------------------------

from config import schemas_bronze as _schemas_bronze  # noqa: E402,F401
from config import schemas_silver as _schemas_silver  # noqa: E402,F401
from config import schemas_gold as _schemas_gold  # noqa: E402,F401
