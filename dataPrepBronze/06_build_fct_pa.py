"""
Build fct_memberPA - the centrepiece fact table.

Every PA request is GROUNDED in a real Synthea condition, encounter or medication
for the same member. This grounding means the clinical evidence in the ext_*
tables genuinely corroborates or contradicts the request, rather than being
decorative.

A deliberate slice of cases is left OPEN with live SLA clocks so the worklist
is realistic.
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import dates, determinism as det  # noqa: E402
from common import identity
from common.frames import write_table  # noqa: E402
from config import schemas as S  # noqa: E402

# Curated SNOMED crosswalks and the CPT/HCPCS PA code sets.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reference_curated as curated_mod  # noqa: E402

SYNTHEA = Path(S.load_config()["_repo_root"]) / S.load_config()["paths"]["cache"] / "synthea"
BRONZE_OUT = Path(S.load_config()["_repo_root"]) / S.load_config()["paths"]["bronze_out"]

TABLE = "fct_memberPA"
RNG_BASE = TABLE

# How far back the PA case history extends. A payer's utilisation-management
# system retains roughly two years of cases; the Synthea seed records span a
# member's whole life, so filing dates are compressed into this window.
PA_HISTORY_DAYS = 730


def _load_synthea(name: str) -> pd.DataFrame:
    return pd.read_csv(SYNTHEA / f"{name}.csv", dtype=str)


def _member_id(patient_uuid: str) -> str:
    return identity.member_id(patient_uuid)


def _plan_id(patient_uuid: str, payer: str) -> str:
    return det.stable_id("PLN-", patient_uuid, payer, width=6)


# ---------------------------------------------------------------------------
# Service category mapping from Synthea source data
# ---------------------------------------------------------------------------

CONDITION_TO_SERVICE_CAT: dict[str, str] = {
    "LUNG CANCER": "RADIATION_ONCOLOGY",
    "BREAST CANCER": "RADIATION_ONCOLOGY",
    "CANCER": "RADIATION_ONCOLOGY",
    "NEOPLASM": "RADIATION_ONCOLOGY",
    "TRANSPLANT": "TRANSPLANT",
    "FRACTURE": "ELECTIVE_SURGERY",
    "OSTEOARTHRITIS": "ELECTIVE_SURGERY",
    "JOINT": "ELECTIVE_SURGERY",
    "SLEEP APNEA": "SLEEP_STUDY",
    "INSOMNIA": "SLEEP_STUDY",
    "DEPRESSION": "BEHAVIORAL_HEALTH",
    "ANXIETY": "BEHAVIORAL_HEALTH",
    "BIPOLAR": "BEHAVIORAL_HEALTH",
    "SCHIZOPHRENIA": "BEHAVIORAL_HEALTH",
    "SUBSTANCE": "BEHAVIORAL_HEALTH",
    "OPIOID": "PAIN_MANAGEMENT",
    "CHRONIC PAIN": "PAIN_MANAGEMENT",
    "BACK PAIN": "PAIN_MANAGEMENT",
    "FIBROMYALGIA": "PAIN_MANAGEMENT",
    "DIABETES": "SPECIALTY_DRUG",
    "RHEUMATOID": "SPECIALTY_DRUG",
    "CROHN": "SPECIALTY_DRUG",
    "MULTIPLE SCLEROSIS": "SPECIALTY_DRUG",
    "OBESITY": "ELECTIVE_SURGERY",
    "STROKE": "REHAB_THERAPY",
    "HIP REPLACEMENT": "ELECTIVE_SURGERY",
    "KNEE REPLACEMENT": "ELECTIVE_SURGERY",
    "HEART FAILURE": "INPATIENT_ADMIT",
    "PNEUMONIA": "INPATIENT_ADMIT",
    "COPD": "HOME_HEALTH",
    "HOME OXYGEN": "DME",
    "WHEELCHAIR": "DME",
    "PROSTHETIC": "DME",
    "GENETIC": "GENETIC_TESTING",
}

PROCEDURE_TO_SERVICE_CAT: dict[str, str] = {
    "MRI": "ADVANCED_IMAGING",
    "CT SCAN": "ADVANCED_IMAGING",
    "PET SCAN": "ADVANCED_IMAGING",
    "MAMMOGRAPHY": "ADVANCED_IMAGING",
    "KNEE REPLACEMENT": "ELECTIVE_SURGERY",
    "HIP REPLACEMENT": "ELECTIVE_SURGERY",
    "COLONOSCOPY": "ELECTIVE_SURGERY",
    "APPENDECTOMY": "ELECTIVE_SURGERY",
    "CHOLECYSTECTOMY": "ELECTIVE_SURGERY",
    "INJECTION": "SPECIALTY_DRUG",
    "INFUSION": "SPECIALTY_DRUG",
    "CHEMOTHERAPY": "RADIATION_ONCOLOGY",
    "RADIATION": "RADIATION_ONCOLOGY",
    "PHYSICAL THERAPY": "REHAB_THERAPY",
    "OCCUPATIONAL THERAPY": "REHAB_THERAPY",
}


# Approximate real-world PA request mix at a payer. Advanced imaging and
# specialty drugs dominate utilisation-management volume; transplants are
# vanishingly rare. Used only when a seed's description matches no keyword, so
# the unmatched tail follows a realistic shape instead of being spread uniformly
# across all thirteen categories (which would make TRANSPLANT as common as MRI).
SERVICE_CATEGORY_WEIGHTS: dict[str, float] = {
    "ADVANCED_IMAGING": 0.300,
    "SPECIALTY_DRUG": 0.180,
    "ELECTIVE_SURGERY": 0.120,
    "REHAB_THERAPY": 0.100,
    "DME": 0.080,
    "BEHAVIORAL_HEALTH": 0.060,
    "PAIN_MANAGEMENT": 0.040,
    "INPATIENT_ADMIT": 0.040,
    "HOME_HEALTH": 0.030,
    "SLEEP_STUDY": 0.025,
    "GENETIC_TESTING": 0.015,
    "RADIATION_ONCOLOGY": 0.008,
    "TRANSPLANT": 0.002,
}


def _classify_source(desc: str | None, source_type: str) -> str:
    """Map a seeding clinical record to a PA service category.

    Keyword match first, then a weighted draw for the unmatched tail. The draw is
    seeded from the description via `det.child_seed`, NOT Python's `hash()`:
    `hash()` on a str is salted per process, so it would return a different
    category on every run and silently break determinism.

    The weighted fallback is RESTRICTED BY SEED TYPE. A medication seed must not
    become a TRANSPLANT or ADVANCED_IMAGING request - that produced incoherent
    rows such as a TRANSPLANT authorization requesting Metformin tablets, or
    ADVANCED_IMAGING requesting an oral contraceptive. A drug seed can only
    become a drug-shaped request.
    """
    if not desc:
        return "INPATIENT_ADMIT"
    up = desc.upper()
    mapping = CONDITION_TO_SERVICE_CAT if source_type == "CONDITION" else PROCEDURE_TO_SERVICE_CAT
    for kw, cat in mapping.items():
        if kw in up:
            return cat

    if source_type == "MEDICATION":
        # A prescription can only reasonably become a pharmacy-style request.
        allowed = {"SPECIALTY_DRUG": 0.85, "HOME_HEALTH": 0.15}
    elif source_type == "CONDITION":
        # A diagnosis drives diagnostic and treatment requests, not DME supply.
        allowed = {
            k: v for k, v in SERVICE_CATEGORY_WEIGHTS.items()
            if k not in {"DME", "HOME_HEALTH"}
        }
    else:
        allowed = dict(SERVICE_CATEGORY_WEIGHTS)

    cats = list(allowed.keys())
    probs = np.array(list(allowed.values()), dtype=float)
    probs = probs / probs.sum()
    local = np.random.default_rng(det.child_seed("pa_service_category", source_type, desc))
    return str(local.choice(cats, p=probs))


# ---------------------------------------------------------------------------
# Grounding: collect PA seeds from Synthea sources
# ---------------------------------------------------------------------------

def _collect_pa_seeds() -> pd.DataFrame:
    """Build a set of PA seeds from conditions, encounters and medications."""
    rng = det.rng(RNG_BASE, "seeds")

    seeds = []

    # From conditions
    conditions = _load_synthea("conditions")
    for idx, (_, row) in enumerate(conditions.iterrows()):
        if rng.random() < 0.12:
            seeds.append({
                "patient_uuid": row["PATIENT"],
                "source_type": "CONDITION",
                "source_id": f"COND-{row['PATIENT'][:8]}-{idx}",
                "source_desc": row.get("DESCRIPTION", ""),
                "source_code": row.get("CODE", ""),
                "source_date": row.get("START", ""),
                "encounter_id": row.get("ENCOUNTER", ""),
            })

    # From procedures
    procedures = _load_synthea("procedures")
    for idx, (_, row) in enumerate(procedures.iterrows()):
        if rng.random() < 0.08:
            seeds.append({
                "patient_uuid": row["PATIENT"],
                "source_type": "PROCEDURE",
                "source_id": f"PROC-{row['PATIENT'][:8]}-{idx}",
                "source_desc": row.get("DESCRIPTION", ""),
                "source_code": row.get("CODE", ""),
                "source_date": row.get("DATE", ""),
                "encounter_id": row.get("ENCOUNTER", ""),
            })

    # From medications (specialty drugs especially)
    medications = _load_synthea("medications")
    for idx, (_, row) in enumerate(medications.iterrows()):
        if rng.random() < 0.05:
            seeds.append({
                "patient_uuid": row["PATIENT"],
                "source_type": "MEDICATION",
                "source_id": f"MED-{row['PATIENT'][:8]}-{idx}",
                "source_desc": row.get("DESCRIPTION", ""),
                "source_code": row.get("CODE", ""),
                "source_date": row.get("START", ""),
                "encounter_id": row.get("ENCOUNTER", ""),
            })

    df = pd.DataFrame(seeds)
    print(f"    PA seeds: {len(df)} ({df.source_type.value_counts().to_dict()})")
    return df


# ---------------------------------------------------------------------------
# PA generation
# ---------------------------------------------------------------------------

def _load_reference_codes():
    """Load the reference code sets and the lookups the grounding needs.

    Returns
    -------
    icd10_codes : list[str]        billable codes, for secondary-diagnosis filler
    icd10_set   : set[str]         every real code, for validating a crosswalk hit
    icd10_desc  : dict[str, str]   code -> description
    hcpcs_by_category : dict[str, list[str]]  PA service category -> codes
    hcpcs_codes : list[str]        PA-relevant codes, as a fallback pool
    hcpcs_desc  : dict[str, str]   code -> description
    ndc_by_name : dict[str, str]   lowercased drug name -> product NDC
    ndc_codes   : list[str]        specialty-drug NDCs, as a fallback pool

    The previous implementation truncated each reference set to its first 500
    codes in alphabetical order, which confined every ICD-10 draw to the A00-
    infectious-disease block. Full sets are loaded here and selection is done by
    relevance instead of by position.
    """
    ref_icd10 = pd.read_parquet(BRONZE_OUT / "ref_icd10cm.parquet")
    icd10_set = set(ref_icd10["icd10_code"].dropna().astype(str))
    icd10_desc = dict(
        zip(
            ref_icd10["icd10_code"].astype(str),
            ref_icd10["long_description"].fillna(ref_icd10["short_description"]).astype(str),
        )
    )
    billable = ref_icd10[ref_icd10["is_billable"].fillna(False).astype(bool)]
    icd10_codes = billable["icd10_code"].dropna().astype(str).tolist()

    ref_hcpcs = pd.read_parquet(BRONZE_OUT / "ref_hcpcs.parquet")
    hcpcs_desc = dict(
        zip(
            ref_hcpcs["procedure_code"].astype(str),
            ref_hcpcs["short_description"].fillna("").astype(str),
        )
    )
    hcpcs_by_category: dict[str, list[str]] = {}
    for cat, grp in ref_hcpcs.dropna(subset=["pa_service_category"]).groupby(
        "pa_service_category"
    ):
        hcpcs_by_category[str(cat)] = grp["procedure_code"].astype(str).tolist()
    pa_relevant = ref_hcpcs[ref_hcpcs["typically_requires_pa"].fillna(False).astype(bool)]
    hcpcs_codes = pa_relevant["procedure_code"].astype(str).tolist()

    ref_ndc = pd.read_parquet(BRONZE_OUT / "ref_ndc_product.parquet")
    ndc_by_name: dict[str, str] = {}
    for col in ("nonproprietary_name", "proprietary_name"):
        sub = ref_ndc.dropna(subset=[col, "product_ndc"])
        for name, ndc in zip(sub[col].astype(str), sub["product_ndc"].astype(str)):
            key = name.strip().lower()
            if key and key not in ndc_by_name:
                ndc_by_name[key] = ndc
    specialty = ref_ndc[ref_ndc["is_specialty_drug"].fillna(False).astype(bool)]
    ndc_codes = specialty["product_ndc"].dropna().astype(str).tolist()
    if not ndc_codes:
        ndc_codes = ref_ndc["product_ndc"].dropna().astype(str).tolist()[:2000]

    return (
        icd10_codes, icd10_set, icd10_desc,
        hcpcs_by_category, hcpcs_codes, hcpcs_desc,
        ndc_by_name, ndc_codes,
    )


def _pick_procedure_code(
    service_cat: str,
    seed_code: str,
    hcpcs_by_category: dict[str, list[str]],
    fallback: list[str],
    rng,
    valid_codes: set[str] | None = None,
) -> tuple[str | None, str | None]:
    """Choose a requested procedure code consistent with the service category.

    Order of preference:
      1. the crosswalk of the seeding SNOMED procedure, when it exists
      2. any code carrying the requested PA service category
      3. any PA-relevant code

    The crosswalk target is checked against `valid_codes` before use. The curated
    SNOMED map deliberately references real CPT codes that are not all present in
    the curated CPT set (the map is broader than the code table), and emitting an
    unvalidated target would create orphan foreign keys against ref_hcpcs.
    """
    mapped = curated_mod.SNOMED_PROC_MAP.get(seed_code)
    if mapped and (valid_codes is None or mapped in valid_codes):
        system = "HCPCS" if mapped[0].isalpha() else "CPT"
        return mapped, system

    pool = hcpcs_by_category.get(service_cat)
    if pool:
        code = str(rng.choice(pool))
        return code, "HCPCS" if code[0].isalpha() else "CPT"

    if fallback:
        code = str(rng.choice(fallback))
        return code, "HCPCS" if code[0].isalpha() else "CPT"

    return None, None


def _match_ndc(drug_name: str | None, ndc_by_name: dict[str, str],
               fallback: list[str], rng) -> str | None:
    """Find an NDC for a prescribed drug by name.

    Synthea prescribes in RxNorm and never supplies an NDC, so a code join
    returns nothing. Matching on the ingredient name is what actually resolves.
    """
    if drug_name:
        key = drug_name.strip().lower()
        if key in ndc_by_name:
            return ndc_by_name[key]
        # Synthea descriptions look like "verapamil hydrochloride 40 MG Oral
        # Tablet" - try the leading ingredient token(s).
        tokens = key.split()
        for width in (3, 2, 1):
            if len(tokens) >= width:
                probe = " ".join(tokens[:width])
                if probe in ndc_by_name:
                    return ndc_by_name[probe]
        for name, ndc in ndc_by_name.items():
            if tokens and tokens[0] in name:
                return ndc
    return str(rng.choice(fallback)) if fallback else None


def _resolve_diagnosis(
    seed_code: str,
    seed_desc: str | None,
    icd10_set: set[str],
    icd10_desc: dict[str, str],
    icd10_codes: list[str],
    rng,
    member_dx: list[str] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve the supporting diagnosis for a PA request.

    Preference order:
      1. crosswalk of the seeding SNOMED concept (condition seeds)
      2. a diagnosis from THIS MEMBER's own condition profile
      3. an arbitrary real code, as a last resort

    Step 2 matters. Only condition seeds carry a diagnosis crosswalk; procedure and
    medication seeds do not, and falling straight through to an arbitrary code
    attached a diagnosis the member had never been given. The DIAGNOSIS_SUPPORT
    criterion then correctly reported 82% UNMET - the check was right and the data
    was wrong. Drawing from the member's real conditions completes the grounding, so
    an unmet diagnosis now reflects a genuine clinical mismatch rather than an
    artefact of generation.
    """
    mapped = curated_mod.SNOMED_DX_MAP.get(seed_code)
    if mapped:
        preferred, root = mapped
        if preferred in icd10_set:
            return preferred, icd10_desc.get(preferred, seed_desc)
        candidates = sorted(c for c in icd10_set if c.startswith(root))
        if candidates:
            billable = [c for c in candidates if len(c) > len(root)]
            chosen = billable[0] if billable else candidates[0]
            return chosen, icd10_desc.get(chosen, seed_desc)

    if member_dx:
        chosen = str(rng.choice(member_dx))
        return chosen, icd10_desc.get(chosen)

    if icd10_codes:
        chosen = str(rng.choice(icd10_codes))
        return chosen, icd10_desc.get(chosen)
    return None, None


def _load_member_diagnoses() -> dict[str, list[str]]:
    """member_id -> the ICD-10 codes actually recorded for that member.

    Read from ext_patientMedicalHistory so a PA seeded from a procedure or a
    medication can still be given a diagnosis the member genuinely has.
    """
    path = BRONZE_OUT / "ext_patientMedicalHistory.parquet"
    if not path.exists():
        return {}
    mh = pd.read_parquet(
        path, columns=["payer_member_id", "condition_icd10_code", "history_type"]
    )
    mh = mh[
        (mh["history_type"] == "CHRONIC_CONDITION")
        & mh["payer_member_id"].notna()
        & mh["condition_icd10_code"].notna()
    ]
    out: dict[str, list[str]] = {}
    for member, code in zip(
        mh["payer_member_id"].astype(str), mh["condition_icd10_code"].astype(str)
    ):
        out.setdefault(member, []).append(code)
    return out


def _compute_regulatory_deadline(
    received_dt: pd.Timestamp, lob: str, urgency: str
) -> pd.Timestamp:
    tat = S.TAT_RULES.get(lob, S.TAT_RULES["COMMERCIAL"])
    if urgency == "EMERGENT":
        return received_dt + timedelta(hours=24)
    elif urgency == "EXPEDITED":
        return received_dt + timedelta(hours=int(tat["expedited_hours"]))
    else:
        return received_dt + timedelta(days=int(tat["standard_days"]))


def build_fct_pa(seeds: pd.DataFrame) -> pd.DataFrame:
    rng = det.rng(RNG_BASE, "build")

    (
        icd10_codes, icd10_set, icd10_desc,
        hcpcs_by_category, hcpcs_codes, hcpcs_desc,
        ndc_by_name, ndc_codes,
    ) = _load_reference_codes()

    # Use real NPIs from the reference table where available, with synthetic fallback
    ref_npi = pd.read_parquet(BRONZE_OUT / "ref_provider_npi.parquet")
    real_npis = ref_npi["npi"].dropna().unique().tolist()
    if len(real_npis) >= 20:
        npi_pool = real_npis[:80]
    else:
        npi_pool = real_npis + [det.synthetic_npi("pa_provider", i) for i in range(80 - len(real_npis))]
    facility_pool = real_npis[:20] if len(real_npis) >= 20 else [det.synthetic_npi("pa_facility", i) for i in range(20)]

    ref_date = dates.reference_date()
    ref_ts = pd.Timestamp(ref_date)

    # Each member's own recorded diagnoses, so a PA seeded from a procedure or a
    # prescription can still carry a diagnosis the member actually has.
    member_dx_map = _load_member_diagnoses()

    n = len(seeds)
    # Target ~20% open cases
    open_fraction = 0.20

    rows = []
    year_counter: dict[int, int] = {}

    for i, (_, seed) in enumerate(seeds.iterrows()):
        source_date = dates.shift_scalar(seed["source_date"])
        if source_date is None:
            source_date = ref_ts - pd.Timedelta(days=int(rng.integers(30, 365)))
        elif hasattr(source_date, 'tzinfo') and source_date.tzinfo:
            source_date = source_date.tz_localize(None)

        member_id = _member_id(seed["patient_uuid"])

        # pa_id is assigned LATER, once received_dt is final. Deriving it from the
        # seed date produced identifiers like PA-1956-000001 attached to a case
        # with a live SLA clock, because open cases have their receipt time
        # re-anchored into the current SLA window further down.

        # LOB and plan
        lob = rng.choice(list(S.LINES_OF_BUSINESS), p=[0.35, 0.20, 0.30, 0.15])
        plan_id = _plan_id(seed["patient_uuid"], lob)

        # Service category
        service_cat = _classify_source(seed["source_desc"], seed["source_type"])

        # Timing
        #
        # PA is a near-real-time operational process. A payer's UM system holds
        # roughly the last two years of cases, not the member's whole clinical
        # lifetime - so the seed date (which can reach back to the 1950s for an
        # elderly member's earliest record) is compressed into a realistic
        # operational window. The clinical grounding is preserved because the
        # request still derives from that real record; only the filing date moves.
        submit_offset = int(rng.integers(-5, 15))
        raw_submitted = source_date + pd.Timedelta(days=submit_offset, hours=int(rng.integers(8, 17)))
        window_start = ref_ts - pd.Timedelta(days=PA_HISTORY_DAYS)
        if raw_submitted < window_start:
            # Place deterministically inside the window rather than clamping every
            # old seed onto the same boundary date.
            raw_submitted = window_start + pd.Timedelta(
                hours=int(rng.integers(0, PA_HISTORY_DAYS * 24))
            )
        submitted_dt = raw_submitted
        received_dt = submitted_dt + pd.Timedelta(hours=int(rng.integers(0, 24)))
        # Ensure tz-naive for all datetime arithmetic
        if hasattr(submitted_dt, 'tzinfo') and submitted_dt.tzinfo:
            submitted_dt = submitted_dt.tz_localize(None)
        if hasattr(received_dt, 'tzinfo') and received_dt.tzinfo:
            received_dt = received_dt.tz_localize(None)

        # Channel
        channel = rng.choice(list(S.PA_CHANNELS), p=[0.35, 0.25, 0.15, 0.15, 0.10])

        # Cert type
        cert_type = rng.choice(list(S.PA_CERT_TYPES), p=[0.70, 0.12, 0.10, 0.08])

        # Urgency
        urgency = rng.choice(list(S.PA_URGENCY), p=[0.65, 0.25, 0.10])
        urgency_justified = bool(rng.random() < 0.70) if urgency != "STANDARD" else None

        # --- requested service ------------------------------------------------
        # Both the procedure code and the diagnosis are derived from the seeding
        # clinical record rather than drawn at random. This is what makes the
        # grounding real: an MRI-lumbar request carries a lumbar diagnosis, so the
        # evidence in ext_patientClinicalRecords genuinely corroborates (or fails
        # to corroborate) the request. A random code would make every criteria
        # evaluation meaningless.
        proc_code = None
        proc_system = None
        proc_desc = seed.get("source_desc", "")
        ndc = None
        drug_name = None

        seed_code = str(seed.get("source_code") or "")

        if service_cat == "SPECIALTY_DRUG" or seed["source_type"] == "MEDICATION":
            drug_name = seed.get("source_desc", "Medication")
            proc_system = "NDC"
            # Prefer an NDC whose name actually matches the prescribed drug.
            ndc = _match_ndc(drug_name, ndc_by_name, ndc_codes, rng)
        else:
            # Prefer a code from the requested service category, then the
            # crosswalk of the seeding procedure, then any PA-relevant code.
            proc_code, proc_system = _pick_procedure_code(
                service_cat, seed_code, hcpcs_by_category, hcpcs_codes, rng,
                valid_codes=set(hcpcs_desc.keys()),
            )
            if proc_code and proc_code in hcpcs_desc:
                proc_desc = hcpcs_desc[proc_code]

        # --- diagnosis grounded in the seeding condition ----------------------
        primary_dx, primary_dx_desc = _resolve_diagnosis(
            seed_code, seed.get("source_desc"), icd10_set, icd10_desc, icd10_codes, rng,
            member_dx=member_dx_map.get(member_id),
        )

        secondary_dx = []
        if rng.random() < 0.4 and len(icd10_codes) > 1:
            secondary_dx = [
                str(rng.choice(icd10_codes)) for _ in range(int(rng.integers(1, 4)))
            ]

        # Place of service
        pos_map = {
            "INPATIENT_ADMIT": ("21", "Inpatient Hospital"),
            "ADVANCED_IMAGING": ("22", "On Campus-Outpatient Hospital"),
            "HOME_HEALTH": ("12", "Home"),
            "DME": ("12", "Home"),
        }
        pos_code, pos_desc = pos_map.get(service_cat, ("11", "Office"))

        # Providers
        ordering_npi = rng.choice(npi_pool)
        rendering_npi = rng.choice(npi_pool)
        facility_npi = rng.choice(facility_pool)

        ordering_name = f"Dr. Provider-{ordering_npi[-4:]}"
        ordering_specialty = rng.choice([
            "Internal Medicine", "Orthopedic Surgery", "Oncology", "Neurology",
            "Cardiology", "Psychiatry", "Pulmonology", "Rheumatology",
            "Physical Medicine", "General Surgery", "Pain Medicine",
        ])

        # Network status
        network = rng.choice(list(S.NETWORK_STATUSES), p=[0.82, 0.14, 0.04])
        sca_flag = network == "SCA"

        # Financial estimate
        cost_ranges = {
            "ADVANCED_IMAGING": (800, 5000), "SPECIALTY_DRUG": (2000, 15000),
            "ELECTIVE_SURGERY": (5000, 80000), "DME": (200, 8000),
            "BEHAVIORAL_HEALTH": (100, 500), "INPATIENT_ADMIT": (8000, 120000),
            "REHAB_THERAPY": (80, 400), "GENETIC_TESTING": (500, 5000),
            "HOME_HEALTH": (150, 1200), "SLEEP_STUDY": (1000, 4000),
            "PAIN_MANAGEMENT": (200, 3000), "RADIATION_ONCOLOGY": (3000, 25000),
            "TRANSPLANT": (50000, 500000),
        }
        lo, hi = cost_ranges.get(service_cat, (50, 2000))
        est_allowed = round(float(rng.uniform(lo, hi)), 2)
        high_cost = est_allowed > 10000

        # Requested units and dates
        units = int(rng.integers(1, 30))
        req_start = (received_dt + pd.Timedelta(days=int(rng.integers(1, 30)))).date()
        req_end_offset = int(rng.integers(1, 180))
        req_end = req_start + timedelta(days=req_end_offset)

        # Criteria ruleset
        ruleset_id = det.stable_id("RS", service_cat, lob, width=8)
        ruleset_ver = "v1.0"

        # --- Lifecycle status ---
        # Openness is decided FIRST, and an open case then has its receipt time
        # RE-ANCHORED into the live SLA window. The previous logic ANDed a 20%
        # open rate with "received within 30 days"; because seed dates spread
        # across years, the two conditions multiplied down to a handful of open
        # cases and left the analyst worklist effectively empty. Deciding first
        # and then placing the date makes the open backlog the intended size and
        # guarantees every open case has a deadline that has not yet passed.
        is_open = bool(rng.random() < open_fraction)

        if is_open:
            tat = S.TAT_RULES.get(lob, S.TAT_RULES["COMMERCIAL"])
            if urgency == "EMERGENT":
                window_hours = 24
            elif urgency == "EXPEDITED":
                window_hours = int(tat["expedited_hours"])
            else:
                window_hours = int(tat["standard_days"]) * 24
            # Place receipt somewhere inside the window so a realistic spread of
            # cases sits early, mid and near-breach. A small share is pushed just
            # past the deadline so genuine SLA breaches exist to be found.
            elapsed_frac = float(rng.uniform(0.05, 1.15))
            hours_ago = window_hours * elapsed_frac
            received_dt = ref_ts - pd.Timedelta(hours=hours_ago)
            submitted_dt = received_dt - pd.Timedelta(hours=int(rng.integers(0, 24)))

        regulatory_deadline = _compute_regulatory_deadline(received_dt, lob, urgency)

        # pa_id is assigned here, from the FINAL received_dt, so the identifier's
        # year always agrees with the case's actual filing date.
        yr = received_dt.year
        seq = year_counter.get(yr, 0) + 1
        year_counter[yr] = seq
        pa_id = f"PA-{yr}-{seq:06d}"

        if is_open:
            # Open cases: pre-decision status, weighted towards clinical review.
            pa_status = str(rng.choice(
                list(S.PA_OPEN_STATUSES), p=[0.15, 0.45, 0.30, 0.10]
            ))
            decision_dt = None
            decision_role = None
            decision_user = None
            denial_code = None
            denial_desc = None
            approved_units = None
            auth_number = None
            auth_eff = None
            auth_exp = None
        else:
            # Closed cases
            pa_status = rng.choice(
                ["APPROVED", "PARTIALLY_APPROVED", "DENIED", "WITHDRAWN", "EXPIRED"],
                p=[0.55, 0.10, 0.20, 0.08, 0.07],
            )
            days_to_decision = int(rng.integers(1, 14))
            decision_dt = received_dt + pd.Timedelta(days=days_to_decision)
            decision_role = rng.choice(list(S.PA_DECIDER_ROLES), p=[0.25, 0.45, 0.30])
            decision_user = f"REV-{rng.integers(1000, 9999)}"

            if pa_status == "DENIED":
                denial_idx = int(rng.integers(0, len(S.DENIAL_REASONS)))
                denial_code = S.DENIAL_REASONS[denial_idx][0]
                denial_desc = S.DENIAL_REASONS[denial_idx][1]
                approved_units = None
                auth_number = None
                auth_eff = None
                auth_exp = None
            elif pa_status in ("APPROVED", "PARTIALLY_APPROVED"):
                denial_code = None
                denial_desc = None
                approved_units = units if pa_status == "APPROVED" else max(1, units // 2)
                auth_number = f"AUTH-{yr}-{seq:06d}"
                auth_eff = req_start
                auth_exp = req_end
            else:
                denial_code = None
                denial_desc = None
                approved_units = None
                auth_number = None
                auth_eff = None
                auth_exp = None

        # --- Pend handling ---
        pend_code = None
        pend_desc = None
        pend_letter_date = None
        pend_response_date = None
        clock_paused_days = 0

        if pa_status in ("PENDED_FOR_INFO", "PENDING_CLINICAL") or (
            not is_open and rng.random() < 0.25
        ):
            pend_idx = int(rng.integers(0, len(S.PEND_REASONS)))
            pend_code = S.PEND_REASONS[pend_idx][0]
            pend_desc = S.PEND_REASONS[pend_idx][1]
            pend_letter_date = (received_dt + pd.Timedelta(days=int(rng.integers(1, 5)))).date()
            if not is_open or rng.random() < 0.6:
                pend_response_date = pend_letter_date + timedelta(days=int(rng.integers(2, 14)))
                clock_paused_days = (pend_response_date - pend_letter_date).days
            else:
                clock_paused_days = (ref_date - pend_letter_date).days

        # --- Auto-adjudication and gold-card ---
        auto_adj = False
        auto_reason = None
        gold_card = False

        if not is_open:
            if pa_status == "APPROVED" and rng.random() < 0.20:
                auto_adj = True
                auto_reason = "Meets all automated criteria; no manual review needed"
            elif pa_status == "DENIED" and rng.random() < 0.10:
                auto_adj = True
                auto_reason = "Member not eligible on date of service"

        if rng.random() < 0.05:
            gold_card = True

        # --- SLA ---
        is_open_flag = pa_status in S.PA_OPEN_STATUSES
        if decision_dt:
            tat_elapsed = max(0, (decision_dt - received_dt).total_seconds() / 86400 - clock_paused_days)
        elif is_open_flag:
            tat_elapsed = max(0, (ref_ts - received_dt).total_seconds() / 86400 - clock_paused_days)
        else:
            tat_elapsed = None

        sla_breach = False
        if tat_elapsed is not None:
            deadline_hours = (regulatory_deadline - received_dt).total_seconds() / 3600
            sla_breach = tat_elapsed * 24 > deadline_hours

        # --- Documentation ---
        lmn = rng.random() < 0.40
        attachment_count = int(rng.integers(0, 8))
        clinical_notes = rng.random() < 0.55
        lab_results = rng.random() < 0.30
        imaging = rng.random() < 0.20 if service_cat == "ADVANCED_IMAGING" else rng.random() < 0.10

        # --- Appeal ---
        appeal_flag = False
        appeal_filed = None
        appeal_outcome = None
        appeal_decision = None
        if not is_open and pa_status == "DENIED" and rng.random() < 0.30:
            appeal_flag = True
            appeal_filed = (decision_dt + pd.Timedelta(days=int(rng.integers(1, 60)))).date()
            appeal_outcome = rng.choice(
                ["UPHELD", "OVERTURNED", "PARTIALLY_OVERTURNED", "WITHDRAWN"],
                p=[0.45, 0.30, 0.15, 0.10],
            )
            appeal_decision = appeal_filed + timedelta(days=int(rng.integers(14, 60)))

        rows.append({
            "pa_id": pa_id,
            "member_id": member_id,
            "plan_id": plan_id,
            "line_of_business": lob,
            "submitted_datetime_utc": submitted_dt,
            "received_datetime_utc": received_dt,
            "request_channel": channel,
            "certification_type": cert_type,
            "urgency_flag": urgency,
            "urgency_clinically_justified_flag": urgency_justified,
            "service_category": service_cat,
            "requested_procedure_code": proc_code,
            "requested_code_system": proc_system,
            "requested_procedure_desc": proc_desc[:500] if proc_desc else None,
            "requested_ndc": ndc,
            "requested_drug_name": drug_name,
            "primary_dx_icd10": primary_dx,
            "primary_dx_desc": primary_dx_desc,
            "secondary_dx_icd10_list": secondary_dx if secondary_dx else None,
            "place_of_service_code": pos_code,
            "place_of_service_desc": pos_desc,
            "requested_units": units,
            "requested_start_date": req_start,
            "requested_end_date": req_end,
            "ordering_provider_npi": ordering_npi,
            "ordering_provider_name": ordering_name,
            "ordering_provider_specialty": ordering_specialty,
            "rendering_provider_npi": rendering_npi,
            "rendering_facility_npi": facility_npi,
            "rendering_facility_name": f"Facility-{facility_npi[-4:]}",
            "network_status_at_submission": network,
            "single_case_agreement_flag": sca_flag,
            "estimated_allowed_amt": est_allowed,
            "high_cost_review_flag": high_cost,
            "criteria_ruleset_id": ruleset_id,
            "criteria_ruleset_version": ruleset_ver,
            "pa_status": pa_status,
            "is_open_flag": is_open_flag,
            "decision_datetime_utc": decision_dt,
            "decision_by_role": decision_role,
            "decision_by_user_id": decision_user,
            "denial_reason_code": denial_code,
            "denial_reason_desc": denial_desc,
            "approved_units": approved_units,
            "auth_number": auth_number,
            "auth_effective_date": auth_eff,
            "auth_expiration_date": auth_exp,
            "pend_reason_code": pend_code,
            "pend_reason_desc": pend_desc,
            "pend_letter_sent_date": pend_letter_date,
            "pend_response_received_date": pend_response_date,
            "clock_paused_days": clock_paused_days,
            "auto_adjudicated_flag": auto_adj,
            "auto_adjudication_reason": auto_reason,
            "gold_card_exempt_flag": gold_card,
            "regulatory_deadline_datetime_utc": regulatory_deadline,
            "tat_elapsed_days": round(tat_elapsed, 2) if tat_elapsed is not None else None,
            "sla_breach_flag": sla_breach,
            "letter_of_medical_necessity_flag": lmn,
            "attachment_count": attachment_count,
            "clinical_notes_attached_flag": clinical_notes,
            "lab_results_attached_flag": lab_results,
            "imaging_attached_flag": imaging,
            "appeal_flag": appeal_flag,
            "appeal_filed_date": appeal_filed,
            "appeal_outcome": appeal_outcome,
            "appeal_decision_date": appeal_decision,
            "grounded_on_source": seed["source_type"],
            "grounded_on_source_id": seed["source_id"],
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Post-processing: link auth_numbers to transactions and pa_ids to events
# ---------------------------------------------------------------------------

def _link_auth_to_transactions(pa_df: pd.DataFrame) -> None:
    """Update fct_memberTransactions with related_auth_number from approved PAs."""
    txn_path = BRONZE_OUT / "fct_memberTransactions.parquet"
    if not txn_path.exists():
        print("    [skip] fct_memberTransactions not built yet - no auth linking")
        return

    approved = pa_df[pa_df["auth_number"].notna()][["member_id", "auth_number", "service_category"]].copy()
    if approved.empty:
        return

    txn = pd.read_parquet(txn_path)
    claims_mask = txn["transaction_type"].str.startswith("CLAIM")

    rng = det.rng(RNG_BASE, "link_auth")
    auth_by_member = approved.groupby("member_id")["auth_number"].apply(list).to_dict()

    auth_numbers = txn["related_auth_number"].copy()
    pa_required = txn["pa_required_flag"].copy()
    pa_on_file = txn["pa_on_file_flag"].copy()

    for idx in txn.index[claims_mask]:
        mid = txn.at[idx, "member_id"]
        if mid in auth_by_member and rng.random() < 0.25:
            auth = rng.choice(auth_by_member[mid])
            auth_numbers.at[idx] = auth
            pa_required.at[idx] = True
            pa_on_file.at[idx] = True

    txn["related_auth_number"] = auth_numbers
    txn["pa_required_flag"] = pa_required
    txn["pa_on_file_flag"] = pa_on_file

    txn.to_parquet(txn_path, index=False, engine="pyarrow", compression="snappy")
    linked = txn["related_auth_number"].notna().sum()
    print(f"    Linked {linked:,d} transactions to auth numbers")


def _link_pa_to_events(pa_df: pd.DataFrame) -> None:
    """Update fct_memberEvents with related_pa_id from PAs."""
    evt_path = BRONZE_OUT / "fct_memberEvents.parquet"
    if not evt_path.exists():
        print("    [skip] fct_memberEvents not built yet - no PA linking")
        return

    pa_by_member = pa_df.groupby("member_id")["pa_id"].apply(list).to_dict()

    evt = pd.read_parquet(evt_path)
    rng = det.rng(RNG_BASE, "link_events")

    pa_ids = evt["related_pa_id"].copy()
    for idx in evt.index:
        mid = evt.at[idx, "member_id"]
        if mid in pa_by_member and rng.random() < 0.30:
            pa_ids.at[idx] = rng.choice(pa_by_member[mid])

    evt["related_pa_id"] = pa_ids
    evt.to_parquet(evt_path, index=False, engine="pyarrow", compression="snappy")
    linked = evt["related_pa_id"].notna().sum()
    print(f"    Linked {linked:,d} events to PA IDs")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("Building fct_memberPA")
    print("=" * 78)
    print(f"  date anchor: {dates.describe()}")

    print("\n  [1/4] Collecting PA seeds from Synthea sources ...")
    seeds = _collect_pa_seeds()

    print(f"  [2/4] Generating {len(seeds):,d} PA requests ...")
    pa_df = build_fct_pa(seeds)
    print(f"         {len(pa_df):,d} PA records generated")
    print(f"         Open: {(pa_df.pa_status.isin(S.PA_OPEN_STATUSES)).sum():,d}")
    print(f"         Closed: {(~pa_df.pa_status.isin(S.PA_OPEN_STATUSES)).sum():,d}")
    print(f"         Service categories: {pa_df.service_category.nunique()}")

    write_table(pa_df, "bronze", TABLE, source_system="SYNTHETIC")

    print("\n  [3/4] Linking auth numbers to transactions ...")
    _link_auth_to_transactions(pa_df)

    print("  [4/4] Linking PA IDs to events ...")
    _link_pa_to_events(pa_df)

    print("\nfct_memberPA complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
