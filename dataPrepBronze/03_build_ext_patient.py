"""
Build the four bronze external-patient tables from Synthea source data.

    ext_patientProfile         <- patient demographics with own MRN identity space
    ext_patientClinicalRecords <- dx/proc/labs/vitals with crosswalks
    ext_patientEhrEvents       <- ADT-style event stream
    ext_patientMedicalHistory  <- conditions, meds, allergies, immunizations, BMI

Uses vectorized pandas operations throughout — no row-by-row iteration on large
CSVs. ~8% of ext_patientProfile rows have missing or conflicting payer_member_id.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import dates  # noqa: E402
from common import determinism as det  # noqa: E402
from common.fetch import cache_dir  # noqa: E402
from common import identity
from common.frames import write_table  # noqa: E402
from config import schemas as S  # noqa: E402

# Curated SNOMED crosswalks - the complete Synthea vocabulary, not a subset.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reference_curated as curated_mod  # noqa: E402

SYNTHEA_DIR = cache_dir() / "synthea"

# ---------------------------------------------------------------------------
# Crosswalk maps
# ---------------------------------------------------------------------------

SNOMED_ICD10_MAP: dict[str, tuple[str, str]] = {
    "44054006": ("I10", "Essential (primary) hypertension"),
    "15777000": ("G43.909", "Migraine, unspecified"),
    "38341003": ("I10", "Hypertensive disorder"),
    "59621000": ("I10", "Essential hypertension"),
    "73211009": ("E11.9", "Type 2 diabetes mellitus without complications"),
    "40055000": ("J06.9", "Acute upper respiratory infection"),
    "195662009": ("J20.9", "Acute bronchitis"),
    "162864005": ("R07.9", "Chest pain, unspecified"),
    "36971009": ("J44.1", "COPD with acute exacerbation"),
    "53741008": ("I25.10", "Atherosclerotic heart disease"),
    "185086009": ("Z00.00", "General adult medical examination"),
    "55822004": ("K21.0", "GERD with esophagitis"),
    "431855005": ("J06.9", "Acute upper respiratory infection"),
    "283371005": ("M54.5", "Low back pain"),
    "68496003": ("N39.0", "Urinary tract infection"),
    "10509002": ("J02.9", "Acute pharyngitis"),
    "65363002": ("M19.90", "Unspecified osteoarthritis"),
    "230690007": ("G47.33", "Obstructive sleep apnea"),
    "49436004": ("F32.9", "Major depressive disorder"),
    "87433001": ("M79.3", "Panniculitis"),
    "370143000": ("F41.1", "Generalized anxiety disorder"),
    "126906006": ("C50.919", "Malignant neoplasm of breast"),
    "254637007": ("D25.9", "Leiomyoma of uterus"),
    "39848009": ("K80.20", "Calculus of gallbladder"),
    "271737000": ("R10.9", "Unspecified abdominal pain"),
    "398254007": ("J18.9", "Pneumonia"),
    "46635009": ("E11.65", "Type 2 diabetes with hyperglycemia"),
    "267036007": ("R51.9", "Headache"),
    "75498004": ("F32.A", "Major depressive disorder, recurrent"),
    "84757009": ("M81.0", "Age-related osteoporosis"),
    "26929004": ("E78.5", "Hyperlipidemia"),
    "399211009": ("N17.9", "Acute kidney failure"),
    "62106007": ("I63.9", "Cerebral infarction"),
    "22298006": ("I48.91", "Unspecified atrial fibrillation"),
    "84114007": ("I50.9", "Heart failure"),
    "427089005": ("E78.00", "Pure hypercholesterolemia"),
}

SNOMED_CPT_MAP: dict[str, tuple[str, str]] = {
    "430193006": ("99213", "Office visit"),
    "162673000": ("99214", "Office visit, moderate"),
    "710824005": ("99396", "Preventive visit, 40-64"),
    "274804006": ("99385", "Comprehensive preventive visit"),
    "73761001": ("36415", "Venipuncture"),
    "252160004": ("72148", "MRI lumbar spine"),
    "241615005": ("71046", "Chest X-ray"),
    "169553002": ("90471", "Immunization admin"),
    "16310003": ("77065", "Screening mammography"),
    "46973005": ("36415", "Blood draw"),
    "24623002": ("93000", "Electrocardiogram"),
    "167271000": ("81001", "Urinalysis"),
    "117010004": ("36415", "Venipuncture"),
    "76601001": ("76700", "Abdominal ultrasound"),
    "29303009": ("93000", "Electrocardiogram"),
    "65546002": ("45380", "Colonoscopy with biopsy"),
    "698314001": ("90834", "Psychotherapy, 45 min"),
    "180256009": ("99283", "ED visit, moderate"),
    "75544000": ("90686", "Influenza vaccine"),
}

BMI_LOINC = "39156-5"
HEIGHT_LOINC = "8302-2"
WEIGHT_LOINC = "29463-7"

VITAL_LOINCS = {
    "8302-2", "29463-7", "39156-5", "8310-5", "8867-4", "8480-6", "8462-4",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load(filename: str, usecols: list[str] | None = None) -> pd.DataFrame:
    return pd.read_csv(SYNTHEA_DIR / filename, dtype=str, keep_default_na=False,
                       usecols=usecols)


def _stable_ids(prefix: str, *series_list: pd.Series, width: int = 12) -> pd.Series:
    """Vectorised, GUARANTEED-UNIQUE stable ID generation.

    Delegates to det.unique_stable_ids, which resolves hash collisions
    deterministically. A bare truncated hash is not safe here: at 8 digits across
    343,909 clinical records the expected number of collisions is in the hundreds,
    and 615 duplicate primary keys were in fact produced. The width is also raised
    to 12 so collisions are rare before disambiguation is needed.
    """
    parts = pd.Series("", index=series_list[0].index)
    for s in series_list:
        parts = parts + "|" + s.astype(str)
    return det.unique_stable_ids(prefix, parts, width=width)


def _condition_category_vec(desc: pd.Series) -> pd.Series:
    up = desc.fillna("").str.upper()
    cat = pd.Series("OTHER", index=up.index)
    for keywords, label in [
        (["DIABETES", "THYROID", "OBESITY"], "ENDOCRINE"),
        (["HYPERTENSION", "HEART", "ATRIAL", "CORONARY"], "CARDIOVASCULAR"),
        (["ASTHMA", "COPD", "PNEUMONIA", "BRONCH"], "RESPIRATORY"),
        (["DEPRESSION", "ANXIETY", "BIPOLAR", "SCHIZO"], "BEHAVIORAL_HEALTH"),
        (["CANCER", "NEOPLASM", "MALIGNANT", "TUMOR"], "ONCOLOGY"),
        (["ARTHRITIS", "OSTEO", "BACK PAIN", "SPINE"], "MUSCULOSKELETAL"),
        (["KIDNEY", "RENAL", "URINARY"], "RENAL"),
    ]:
        mask = pd.Series(False, index=up.index)
        for kw in keywords:
            mask = mask | up.str.contains(kw, regex=False)
        cat = cat.where(~mask, label)
    return cat


def _med_class_vec(desc: pd.Series) -> pd.Series:
    up = desc.fillna("").str.upper()
    cls = pd.Series("OTHER", index=up.index)
    for keywords, label in [
        (["STATIN", "ATORVASTATIN", "SIMVASTATIN"], "CARDIOVASCULAR"),
        (["LISINOPRIL", "AMLODIPINE", "LOSARTAN"], "CARDIOVASCULAR"),
        (["METFORMIN", "INSULIN", "GLIPIZIDE"], "ENDOCRINE"),
        (["OMEPRAZOLE", "PANTOPRAZOLE"], "GASTROINTESTINAL"),
        (["SERTRALINE", "FLUOXETINE", "DULOXETINE"], "CNS"),
        (["IBUPROFEN", "ACETAMINOPHEN", "OXYCODONE"], "PAIN_MANAGEMENT"),
        (["AMOXICILLIN", "AZITHROMYCIN", "CIPROFLOX"], "ANTI-INFECTIVE"),
        (["ALBUTEROL", "FLUTICASONE", "MONTELUKAST"], "PULMONARY"),
    ]:
        mask = pd.Series(False, index=up.index)
        for kw in keywords:
            mask = mask | up.str.contains(kw, regex=False)
        cls = cls.where(~mask, label)
    return cls


def _body_region_vec(site: pd.Series) -> pd.Series:
    up = site.fillna("").str.upper()
    region = pd.Series(None, index=up.index, dtype="object")
    for keywords, label in [
        (["HEAD", "BRAIN", "SKULL", "CRANIAL"], "HEAD"),
        (["CHEST", "THORAX", "LUNG", "CARDIAC"], "CHEST"),
        (["ABDOMEN", "LIVER", "KIDNEY", "PELVIS"], "ABDOMEN"),
        (["SPINE", "LUMBAR", "CERVICAL", "THORACIC"], "SPINE"),
        (["KNEE", "HIP", "ANKLE", "SHOULDER", "ELBOW", "WRIST"], "EXTREMITY"),
    ]:
        mask = pd.Series(False, index=up.index)
        for kw in keywords:
            mask = mask | up.str.contains(kw, regex=False)
        region = region.where(~mask, label)
    return region


# ---------------------------------------------------------------------------
# ext_patientProfile
# ---------------------------------------------------------------------------

def build_ext_patientProfile(patients: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (profile_df, org_map_df) where org_map maps patient Id to ext info."""
    print("\n--- ext_patientProfile ---")
    rng = det.rng("ext_patientProfile")
    n = len(patients)

    # Org assignment
    orgs_df = _load("organizations.csv")
    org_ids = orgs_df["Id"].tolist()[:6]
    org_names = orgs_df["NAME"].tolist()[:6]
    vendors = list(S.EHR_VENDORS)
    assignments = rng.integers(0, len(org_ids), size=n)

    org_map = pd.DataFrame({
        "patient_id": patients["Id"].values,
        "source_org_id": [f"ORG-{org_ids[a][:8]}" for a in assignments],
        "source_org_name": [org_names[a] for a in assignments],
        "source_ehr_vendor": [vendors[a % len(vendors)] for a in assignments],
    })

    # External IDs
    ext_ids = _stable_ids("MRN-", patients["Id"], pd.Series(range(n)), width=7)

    # Payer member ID with ~8% missing, ~3% conflicting.
    #
    # This MUST be the payer's own member identifier (MBR-######), derived with
    # the same det.stable_id call that 02_build_dim_member.py uses. Emitting the
    # raw Synthea UUID here produces a column that looks populated but matches
    # zero rows in dim_memberProfile, which silently breaks the payer/provider
    # identity linkage that the whole ext_* family exists to support.
    payer_ids = np.array(
        [identity.member_id(pid) for pid in patients["Id"]],
        dtype=object,
    )
    missing_mask = rng.random(n) < 0.08
    conflict_mask = (~missing_mask) & (rng.random(n) < 0.03)
    payer_ids = np.where(missing_mask, None, payer_ids)
    # Conflicts point at a DIFFERENT real member, which is what makes them a
    # genuine identity-resolution problem rather than simply a bad value.
    conflict_indices = np.where(conflict_mask)[0]
    for idx in conflict_indices:
        other = int(rng.integers(0, n))
        payer_ids[idx] = identity.member_id(patients.iloc[other]["Id"])

    match_method = np.where(
        payer_ids == None, None,  # noqa: E711
        np.where(rng.random(n) > 0.2, "DETERMINISTIC", "PROBABILISTIC"),
    )
    match_conf = np.where(
        payer_ids == None, None,  # noqa: E711
        rng.uniform(0.85, 1.0, n).round(4),
    )

    df = pd.DataFrame({
        "ext_patient_id": ext_ids.values,
        "source_org_id": org_map["source_org_id"].values,
        "source_org_name": org_map["source_org_name"].values,
        "source_ehr_vendor": org_map["source_ehr_vendor"].values,
        "data_sharing_agreement_id": ["DSA-" + org_map.iloc[i]["source_org_id"][-6:] + "-2024" for i in range(n)],
        "vbc_contract_id": [f"VBC-{org_map.iloc[i]['source_org_id'][-6:]}" if rng.random() > 0.3 else None for i in range(n)],
        "payer_member_id": payer_ids,
        "payer_member_id_asserted_flag": [p is not None for p in payer_ids],
        "match_method": match_method,
        "match_confidence_score": match_conf,
        "first_name": patients["FIRST"].values,
        "last_name": patients["LAST"].values,
        "date_of_birth": dates.shift_dates(patients["BIRTHDATE"]),
        "gender": patients["GENDER"].map({"M": "M", "F": "F"}).fillna("U").values,
        "address_line1": patients["ADDRESS"].values,
        "city": patients["CITY"].values,
        # Synthea writes the full state name ("Massachusetts"); the column is a
        # 2-character state code and downstream TAT rules key on the code.
        "state": patients["STATE"].map(curated_mod.state_code).values,
        "zip_code": patients["ZIP"].str[:10].values,
        "phone": None,
        "primary_care_provider_npi": None,
        "primary_care_provider_name": None,
        "attributed_flag": rng.random(n) > 0.3,
        "attribution_start_date": dates.shift_dates(pd.Series(["2019-01-01"] * n)),
        "attribution_end_date": None,
        "risk_score_hcc": rng.uniform(0.3, 3.5, n).round(3),
        "risk_tier": rng.choice(["LOW", "MODERATE", "HIGH", "VERY_HIGH"], n, p=[0.35, 0.35, 0.2, 0.1]),
        "last_encounter_date": None,
        "consent_on_file_flag": rng.random(n) > 0.05,
        "consent_scope": rng.choice(["FULL_CLINICAL", "LIMITED_DEMOGRAPHICS"], n, p=[0.8, 0.2]),
    })

    # Build lookup from patient_id -> ext_patient_id
    org_map["ext_patient_id"] = ext_ids.values
    org_map["payer_member_id"] = payer_ids

    print(f"  {n} patients, {missing_mask.sum()} missing payer_member_id, {conflict_mask.sum()} conflicting")
    return df, org_map


# ---------------------------------------------------------------------------
# ext_patientClinicalRecords
# ---------------------------------------------------------------------------

def build_ext_patientClinicalRecords(org_map: pd.DataFrame) -> pd.DataFrame:
    print("\n--- ext_patientClinicalRecords ---")
    rng = det.rng("ext_patientClinicalRecords")

    pat_ext = dict(zip(org_map["patient_id"], org_map["ext_patient_id"]))
    pat_org = dict(zip(org_map["patient_id"], org_map["source_org_id"]))
    ext_payer = dict(zip(org_map["ext_patient_id"], org_map["payer_member_id"]))

    # Load ref tables for description enrichment and crosswalk validation.
    icd_lookup: dict[str, str] = {}
    icd10_set: set[str] = set()
    hcpcs_lookup: dict[str, str] = {}
    hcpcs_valid: set[str] = set()
    try:
        from common.frames import read_table as read_tbl
        ref_icd = read_tbl("bronze", "ref_icd10cm")
        icd_lookup = dict(
            zip(ref_icd["icd10_code"].astype(str), ref_icd["long_description"].astype(str))
        )
        # Full real code set. Every crosswalk target is validated against this,
        # so the mart can never contain an ICD-10 code that does not exist.
        icd10_set = set(ref_icd["icd10_code"].dropna().astype(str))
    except Exception:
        icd_lookup = {}
        icd10_set = set()
    try:
        ref_hcpcs = read_tbl("bronze", "ref_hcpcs")
        hcpcs_lookup = dict(
            zip(ref_hcpcs["procedure_code"].astype(str), ref_hcpcs["long_description"].astype(str))
        )
        hcpcs_valid = set(ref_hcpcs["procedure_code"].dropna().astype(str))
    except Exception:
        hcpcs_lookup = {}
        hcpcs_valid = set()

    frames: list[pd.DataFrame] = []
    counter = [0]  # mutable counter for stable IDs

    def _add_counter_col(df: pd.DataFrame) -> pd.Series:
        start = counter[0]
        counter[0] += len(df)
        return pd.Series(range(start, start + len(df)), index=df.index)

    # --- Conditions -> DIAGNOSIS ---
    cond = _load("conditions.csv")
    cond = cond[cond["PATIENT"].isin(pat_ext)]
    if len(cond) > 0:
        seq = _add_counter_col(cond)
        # Resolve through the curated crosswalk, validating each target against
        # the real CMS code set so the emitted ICD-10 is always a real code and
        # crosswalk_method honestly records how specific the mapping is.
        _dx_res = cond["CODE"].map(lambda c: curated_mod.resolve_icd10(c, icd10_set))
        icd_codes = _dx_res.map(lambda r: r[0])
        dx_methods = _dx_res.map(lambda r: r[1])
        icd_descs = icd_codes.map(lambda c: icd_lookup.get(str(c), None) if c else None)
        is_chronic = cond["STOP"].eq("") | cond["STOP"].isna()

        dx = pd.DataFrame({
            "clinical_record_id": _stable_ids("CR-", pd.Series("DX", index=cond.index), cond["PATIENT"], seq.astype(str)),
            "ext_patient_id": cond["PATIENT"].map(pat_ext),
            "payer_member_id": cond["PATIENT"].map(pat_ext).map(ext_payer),
            "source_org_id": cond["PATIENT"].map(pat_org),
            "record_type": "DIAGNOSIS",
            "record_date": dates.shift_series(cond["START"]),
            "recorded_datetime_utc": dates.shift_series(cond["START"]),
            "encounter_ref": cond["ENCOUNTER"].where(cond["ENCOUNTER"].ne(""), None),
            "source_code": cond["CODE"],
            "source_code_system": "SNOMED-CT",
            "source_code_desc": cond["DESCRIPTION"].str[:500],
            "icd10_code": icd_codes,
            "icd10_desc": icd_descs,
            "cpt_hcpcs_code": None,
            "cpt_hcpcs_desc": None,
            "crosswalk_method": dx_methods,
            "clinical_status": np.where(is_chronic, "ACTIVE", "RESOLVED"),
            "onset_date": dates.shift_series(cond["START"]),
            "resolved_date": dates.shift_series(cond["STOP"].where(cond["STOP"].ne(""), None)),
            "is_chronic_flag": is_chronic,
            "result_value_numeric": None,
            "result_value_text": None,
            "result_unit": None,
            "reference_range_low": None,
            "reference_range_high": None,
            "abnormal_flag": None,
            "body_site": None,
            "body_region": None,
            "laterality": None,
            "severity": rng.choice(["MILD", "MODERATE", "SEVERE"], len(cond), p=[0.4, 0.4, 0.2]),
            "performing_provider_npi": None,
        })
        frames.append(dx)
        print(f"  diagnoses: {len(dx)}")

    # --- Procedures -> PROCEDURE ---
    proc = _load("procedures.csv")
    proc = proc[proc["PATIENT"].isin(pat_ext)]
    if len(proc) > 0:
        seq = _add_counter_col(proc)
        _pr_res = proc["CODE"].map(lambda c: curated_mod.resolve_procedure(c, hcpcs_valid))
        cpt_codes = _pr_res.map(lambda r: r[0])
        cpt_methods = _pr_res.map(lambda r: r[1])
        cpt_descs = cpt_codes.map(lambda c: hcpcs_lookup.get(str(c), None) if c else None)

        px = pd.DataFrame({
            "clinical_record_id": _stable_ids("CR-", pd.Series("PX", index=proc.index), proc["PATIENT"], seq.astype(str)),
            "ext_patient_id": proc["PATIENT"].map(pat_ext),
            "payer_member_id": proc["PATIENT"].map(pat_ext).map(ext_payer),
            "source_org_id": proc["PATIENT"].map(pat_org),
            "record_type": "PROCEDURE",
            "record_date": dates.shift_series(proc["DATE"]),
            "recorded_datetime_utc": dates.shift_series(proc["DATE"]),
            "encounter_ref": proc["ENCOUNTER"].where(proc["ENCOUNTER"].ne(""), None),
            "source_code": proc["CODE"],
            "source_code_system": "SNOMED-CT",
            "source_code_desc": proc["DESCRIPTION"].str[:500],
            "icd10_code": None,
            "icd10_desc": None,
            "cpt_hcpcs_code": cpt_codes,
            "cpt_hcpcs_desc": cpt_descs,
            "crosswalk_method": cpt_methods,
            "clinical_status": None,
            "onset_date": None,
            "resolved_date": None,
            "is_chronic_flag": False,
            "result_value_numeric": None,
            "result_value_text": None,
            "result_unit": None,
            "reference_range_low": None,
            "reference_range_high": None,
            "abnormal_flag": None,
            "body_site": None,
            "body_region": None,
            "laterality": None,
            "severity": None,
            "performing_provider_npi": None,
        })
        frames.append(px)
        print(f"  procedures: {len(px)}")

    # --- Observations -> LAB_RESULT / VITAL_SIGN ---
    obs = _load("observations.csv", usecols=["DATE", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "VALUE", "UNITS"])
    obs = obs[obs["PATIENT"].isin(pat_ext)]
    if len(obs) > 0:
        seq = _add_counter_col(obs)
        is_vital = obs["CODE"].isin(VITAL_LOINCS)
        rec_type = np.where(is_vital, "VITAL_SIGN", "LAB_RESULT")
        val_num = pd.to_numeric(obs["VALUE"], errors="coerce")

        ob = pd.DataFrame({
            "clinical_record_id": _stable_ids("CR-", pd.Series("OB", index=obs.index), obs["PATIENT"], seq.astype(str)),
            "ext_patient_id": obs["PATIENT"].map(pat_ext),
            "payer_member_id": obs["PATIENT"].map(pat_ext).map(ext_payer),
            "source_org_id": obs["PATIENT"].map(pat_org),
            "record_type": rec_type,
            "record_date": dates.shift_series(obs["DATE"]),
            "recorded_datetime_utc": dates.shift_series(obs["DATE"]),
            "encounter_ref": obs["ENCOUNTER"].where(obs["ENCOUNTER"].ne(""), None),
            "source_code": obs["CODE"],
            "source_code_system": "LOINC",
            "source_code_desc": obs["DESCRIPTION"].str[:500],
            "icd10_code": None,
            "icd10_desc": None,
            "cpt_hcpcs_code": None,
            "cpt_hcpcs_desc": None,
            "crosswalk_method": None,
            "clinical_status": None,
            "onset_date": None,
            "resolved_date": None,
            "is_chronic_flag": False,
            "result_value_numeric": val_num,
            "result_value_text": obs["VALUE"].where(obs["VALUE"].ne(""), None).str[:500],
            "result_unit": obs["UNITS"].where(obs["UNITS"].ne(""), None).str[:40],
            "reference_range_low": None,
            "reference_range_high": None,
            "abnormal_flag": None,
            "body_site": None,
            "body_region": None,
            "laterality": None,
            "severity": None,
            "performing_provider_npi": None,
        })
        frames.append(ob)
        print(f"  observations: {len(ob)}")

    # --- Imaging -> IMAGING_RESULT ---
    img = _load("imaging_studies.csv")
    img = img[img["PATIENT"].isin(pat_ext)]
    if len(img) > 0:
        seq = _add_counter_col(img)
        body_site = img.get("BODYSITE_DESCRIPTION", pd.Series("", index=img.index)).fillna("")
        modality_desc = img.get("MODALITY_DESCRIPTION", pd.Series("", index=img.index)).fillna("")
        sop_code = img.get("SOP_CODE", pd.Series("", index=img.index)).fillna("")
        sop_desc = img.get("SOP_DESCRIPTION", pd.Series("", index=img.index)).fillna("")

        im = pd.DataFrame({
            "clinical_record_id": _stable_ids("CR-", pd.Series("IM", index=img.index), img["PATIENT"], seq.astype(str)),
            "ext_patient_id": img["PATIENT"].map(pat_ext),
            "payer_member_id": img["PATIENT"].map(pat_ext).map(ext_payer),
            "source_org_id": img["PATIENT"].map(pat_org),
            "record_type": "IMAGING_RESULT",
            "record_date": dates.shift_series(img["DATE"]),
            "recorded_datetime_utc": dates.shift_series(img["DATE"]),
            "encounter_ref": img["ENCOUNTER"].where(img["ENCOUNTER"].ne(""), None),
            "source_code": sop_code.where(sop_code.ne(""), None).str[:30],
            "source_code_system": "SNOMED-CT",
            "source_code_desc": modality_desc.where(modality_desc.ne(""), None).str[:500],
            "icd10_code": None,
            "icd10_desc": None,
            "cpt_hcpcs_code": None,
            "cpt_hcpcs_desc": None,
            "crosswalk_method": None,
            "clinical_status": None,
            "onset_date": None,
            "resolved_date": None,
            "is_chronic_flag": False,
            "result_value_numeric": None,
            "result_value_text": sop_desc.where(sop_desc.ne(""), None).str[:500],
            "result_unit": None,
            "reference_range_low": None,
            "reference_range_high": None,
            "abnormal_flag": None,
            "body_site": body_site.where(body_site.ne(""), None).str[:120],
            "body_region": _body_region_vec(body_site),
            "laterality": None,
            "severity": None,
            "performing_provider_npi": None,
        })
        frames.append(im)
        print(f"  imaging: {len(im)}")

    df = pd.concat(frames, ignore_index=True)
    print(f"  total: {len(df)} clinical records")
    return df


# ---------------------------------------------------------------------------
# ext_patientEhrEvents
# ---------------------------------------------------------------------------

def build_ext_patientEhrEvents(org_map: pd.DataFrame) -> pd.DataFrame:
    print("\n--- ext_patientEhrEvents ---")
    rng = det.rng("ext_patientEhrEvents")
    pat_ext = dict(zip(org_map["patient_id"], org_map["ext_patient_id"]))
    pat_org = dict(zip(org_map["patient_id"], org_map["source_org_id"]))
    pat_vendor = dict(zip(org_map["patient_id"], org_map["source_ehr_vendor"]))
    ext_payer = dict(zip(org_map["ext_patient_id"], org_map["payer_member_id"]))

    enc = _load("encounters.csv")
    enc = enc[enc["PATIENT"].isin(pat_ext)]
    n = len(enc)

    enc_class = enc["ENCOUNTERCLASS"].str.lower()
    is_inpatient = enc_class.eq("inpatient")

    event_type_map = {
        "inpatient": "ADMISSION", "outpatient": "ENCOUNTER_START",
        "emergency": "ED_ARRIVAL", "ambulatory": "ENCOUNTER_START",
        "wellness": "ENCOUNTER_START", "urgentcare": "ENCOUNTER_START",
        "virtual": "ENCOUNTER_START",
    }
    adt_map_local = {
        "inpatient": "A01", "outpatient": "A04", "emergency": "A04",
        "ambulatory": "A04", "wellness": "A04", "urgentcare": "A04",
        "virtual": None,
    }

    event_types = enc_class.map(event_type_map).fillna("ENCOUNTER_START")
    adt_msgs = enc_class.map(adt_map_local)

    lag_hours = np.round(rng.exponential(4.0, n), 2)
    event_dt = dates.shift_series(enc["START"])
    received_dt = event_dt + pd.to_timedelta(lag_hours, unit="h")

    start_dt = dates.shift_series(enc["START"])
    stop_dt = dates.shift_series(enc["STOP"].where(enc["STOP"].ne(""), None))

    los = np.where(
        is_inpatient & start_dt.notna() & stop_dt.notna(),
        ((stop_dt - start_dt).dt.total_seconds() / 86400).round(2),
        None,
    )

    valid_classes = set(S.ENCOUNTER_CLASSES)
    safe_class = enc_class.where(enc_class.isin(valid_classes), "ambulatory")

    seq = pd.Series(range(n), index=enc.index)

    admit_events = pd.DataFrame({
        "ehr_event_id": _stable_ids("EHR-", enc["PATIENT"], seq.astype(str)),
        "ext_patient_id": enc["PATIENT"].map(pat_ext),
        "payer_member_id": enc["PATIENT"].map(pat_ext).map(ext_payer),
        "source_org_id": enc["PATIENT"].map(pat_org),
        "source_ehr_vendor": enc["PATIENT"].map(pat_vendor),
        "event_datetime_utc": event_dt,
        "received_datetime_utc": received_dt,
        "ingestion_latency_hours": lag_hours,
        "event_type": event_types,
        "adt_message_type": adt_msgs.where(adt_msgs.notna(), None),
        "encounter_id": enc["Id"].where(enc["Id"].ne(""), None),
        "encounter_class": safe_class,
        "encounter_reason_code": enc["REASONCODE"].where(enc["REASONCODE"].ne(""), None),
        "encounter_reason_desc": enc["REASONDESCRIPTION"].where(enc["REASONDESCRIPTION"].ne(""), None).str[:500],
        "department": None,
        "location": None,
        "attending_provider_npi": None,
        "admission_datetime_utc": start_dt.where(is_inpatient, None),
        "discharge_datetime_utc": stop_dt.where(is_inpatient, None),
        "length_of_stay_days": los,
        "discharge_disposition": np.where(is_inpatient, "Home", None),
        "order_type": None,
        "order_code": None,
        "order_desc": None,
        "order_status": None,
        "referral_specialty": None,
        "referral_reason": None,
    })

    # Discharge events for inpatient encounters
    inp = enc[is_inpatient & stop_dt.notna()].copy()
    if len(inp) > 0:
        inp_idx = inp.index
        d_lag = np.round(rng.exponential(2.0, len(inp)), 2)
        d_seq = pd.Series(range(n, n + len(inp)), index=inp_idx)

        discharge_events = pd.DataFrame({
            "ehr_event_id": _stable_ids("EHR-", inp["PATIENT"], d_seq.astype(str)),
            "ext_patient_id": inp["PATIENT"].map(pat_ext),
            "payer_member_id": inp["PATIENT"].map(pat_ext).map(ext_payer),
            "source_org_id": inp["PATIENT"].map(pat_org),
            "source_ehr_vendor": inp["PATIENT"].map(pat_vendor),
            "event_datetime_utc": stop_dt.loc[inp_idx],
            "received_datetime_utc": stop_dt.loc[inp_idx] + pd.to_timedelta(d_lag, unit="h"),
            "ingestion_latency_hours": d_lag,
            "event_type": "DISCHARGE",
            "adt_message_type": "A03",
            "encounter_id": inp["Id"],
            "encounter_class": "inpatient",
            "encounter_reason_code": inp["REASONCODE"].where(inp["REASONCODE"].ne(""), None),
            "encounter_reason_desc": inp["REASONDESCRIPTION"].where(inp["REASONDESCRIPTION"].ne(""), None).str[:500],
            "department": None,
            "location": None,
            "attending_provider_npi": None,
            "admission_datetime_utc": start_dt.loc[inp_idx],
            "discharge_datetime_utc": stop_dt.loc[inp_idx],
            "length_of_stay_days": los[inp_idx] if isinstance(los, pd.Series) else [los[i] for i in range(len(inp))],
            "discharge_disposition": "Home",
            "order_type": None,
            "order_code": None,
            "order_desc": None,
            "order_status": None,
            "referral_specialty": None,
            "referral_reason": None,
        })
        df = pd.concat([admit_events, discharge_events], ignore_index=True)
    else:
        df = admit_events

    print(f"  {len(df)} EHR events")
    return df


# ---------------------------------------------------------------------------
# ext_patientMedicalHistory
# ---------------------------------------------------------------------------

def build_ext_patientMedicalHistory(org_map: pd.DataFrame) -> pd.DataFrame:
    print("\n--- ext_patientMedicalHistory ---")
    rng = det.rng("ext_patientMedicalHistory")
    pat_ext = dict(zip(org_map["patient_id"], org_map["ext_patient_id"]))
    pat_org = dict(zip(org_map["patient_id"], org_map["source_org_id"]))
    ext_payer = dict(zip(org_map["ext_patient_id"], org_map["payer_member_id"]))

    # Real ICD-10 code set, so condition crosswalks here are validated the same
    # way they are in ext_patientClinicalRecords.
    try:
        from common.frames import read_table as _read_tbl
        _mh_icd10_set = set(
            _read_tbl("bronze", "ref_icd10cm")["icd10_code"].dropna().astype(str)
        )
    except Exception:
        _mh_icd10_set = set()

    # Template of nullable columns
    NULL_COLS = [
        "condition_snomed_code", "condition_icd10_code", "condition_desc", "condition_category",
        "medication_rxnorm_code", "medication_ndc_code", "medication_name", "medication_generic_name",
        "therapeutic_class", "dose_amount", "dose_unit", "route", "frequency", "sig_text",
        "prescriber_npi", "prescribed_date", "discontinued_date", "discontinue_reason",
        "dispense_count", "days_supply_total", "therapy_duration_days", "adherence_pdc_pct",
        "is_specialty_drug", "requires_pa_flag", "formulary_tier", "is_first_line_therapy",
        "allergy_substance", "allergy_reaction", "allergy_severity", "allergy_criticality",
        "immunization_cvx_code", "immunization_desc",
        "smoking_status", "bmi_value", "bmi_recorded_date", "height_cm", "weight_kg", "notes_text",
    ]

    frames: list[pd.DataFrame] = []
    counter = [0]

    def _make_base(src: pd.DataFrame, hist_type: str) -> pd.DataFrame:
        n = len(src)
        seq = pd.Series(range(counter[0], counter[0] + n), index=src.index)
        counter[0] += n
        base = pd.DataFrame({
            "medical_history_id": _stable_ids("MH-", src["PATIENT"], seq.astype(str)),
            "ext_patient_id": src["PATIENT"].map(pat_ext),
            "payer_member_id": src["PATIENT"].map(pat_ext).map(ext_payer),
            "source_org_id": src["PATIENT"].map(pat_org),
            "history_type": hist_type,
            "onset_date": None,
            "end_date": None,
            "is_active": True,
        })
        for c in NULL_COLS:
            base[c] = None
        return base

    # --- Conditions ---
    cond = _load("conditions.csv")
    cond = cond[cond["PATIENT"].isin(pat_ext)]
    if len(cond) > 0:
        base = _make_base(cond, "CHRONIC_CONDITION")
        is_chronic = cond["STOP"].eq("") | cond["STOP"].isna()
        base["onset_date"] = dates.shift_series(cond["START"])
        stop_vals = cond["STOP"].where(cond["STOP"].ne(""), None)
        base["end_date"] = dates.shift_series(stop_vals)
        base["is_active"] = is_chronic
        base["condition_snomed_code"] = cond["CODE"].values
        icd_codes = cond["CODE"].map(
            lambda c: curated_mod.resolve_icd10(c, _mh_icd10_set)[0]
        )
        base["condition_icd10_code"] = icd_codes.values
        base["condition_desc"] = cond["DESCRIPTION"].str[:500].values
        base["condition_category"] = _condition_category_vec(cond["DESCRIPTION"]).values
        frames.append(base)
        print(f"  conditions: {len(base)}")

    # --- Medications ---
    meds = _load("medications.csv")
    meds = meds[meds["PATIENT"].isin(pat_ext)]
    if len(meds) > 0:
        base = _make_base(meds, "MEDICATION")
        is_active = meds["STOP"].eq("") | meds["STOP"].isna()
        base["onset_date"] = dates.shift_series(meds["START"])
        stop_vals = meds["STOP"].where(meds["STOP"].ne(""), None)
        base["end_date"] = dates.shift_series(stop_vals)
        base["is_active"] = is_active
        base["medication_rxnorm_code"] = meds["CODE"].str[:30].values
        base["medication_name"] = meds["DESCRIPTION"].str[:300].values
        base["medication_generic_name"] = meds["DESCRIPTION"].str[:300].values
        base["therapeutic_class"] = _med_class_vec(meds["DESCRIPTION"]).values
        base["route"] = "ORAL"
        base["frequency"] = "DAILY"
        base["prescribed_date"] = dates.shift_series(meds["START"])

        n_m = len(meds)
        base["discontinued_date"] = dates.shift_series(stop_vals)
        disc_reasons = rng.choice(
            ["Course completed", "Inadequate response", "Side effects", "Patient preference", "Switched therapy"],
            n_m,
        )
        base["discontinue_reason"] = np.where(~is_active, disc_reasons, None)

        dispenses = pd.to_numeric(meds["DISPENSES"], errors="coerce").fillna(1).astype(int)
        base["dispense_count"] = dispenses.values
        base["days_supply_total"] = (dispenses * 30).values

        start_ts = dates.shift_series(meds["START"])
        stop_ts = dates.shift_series(stop_vals)
        dur = (stop_ts - start_ts).dt.days.clip(lower=0)
        base["therapy_duration_days"] = np.where(dur.notna(), dur, dispenses * 30)
        base["adherence_pdc_pct"] = rng.uniform(40, 100, n_m).round(2)

        # --- specialty / PA / step-therapy classification ---------------------
        # Classified by drug NAME, not by NDC. Synthea prescribes in RxNorm and
        # never emits an NDC, so joining to ref_ndc_product on a code yields
        # nothing at all - which is why these three flags were previously
        # hardcoded False and left SPECIALTY_DRUG prior authorization with no
        # evidence to stand on. is_first_line_therapy in particular has to be a
        # real property of the drug rather than a coin flip, because step-therapy
        # criteria are evaluated against a documented failed trial of a
        # first-line agent.
        med_class = meds["DESCRIPTION"].fillna("").map(curated_mod.classify_medication)
        base["therapeutic_class"] = [
            c["therapeutic_class"] for c in med_class
        ]
        base["is_specialty_drug"] = [bool(c["is_specialty_drug"]) for c in med_class]
        base["requires_pa_flag"] = [bool(c["requires_pa_flag"]) for c in med_class]
        base["is_first_line_therapy"] = [bool(c["is_first_line_therapy"]) for c in med_class]

        # Formulary tier follows the classification: specialty products sit on
        # tier 4, first-line generics on tier 1.
        tier = np.where(
            base["is_specialty_drug"], 4,
            np.where(base["is_first_line_therapy"], 1,
                     rng.choice([2, 3], n_m, p=[0.6, 0.4])),
        )
        base["formulary_tier"] = tier
        frames.append(base)
        n_spec = int(np.sum(base["is_specialty_drug"]))
        print(
            f"  medications: {len(base)} "
            f"({n_spec} specialty, "
            f"{int(np.sum(base['is_first_line_therapy']))} first-line)"
        )

    # --- Allergies ---
    allergy = _load("allergies.csv")
    allergy = allergy[allergy["PATIENT"].isin(pat_ext)]
    if len(allergy) > 0:
        base = _make_base(allergy, "ALLERGY")
        base["onset_date"] = dates.shift_series(allergy["START"])
        stop_vals = allergy["STOP"].where(allergy["STOP"].ne(""), None)
        base["end_date"] = dates.shift_series(stop_vals)
        base["is_active"] = allergy["STOP"].eq("") | allergy["STOP"].isna()
        na = len(allergy)
        base["allergy_substance"] = allergy["DESCRIPTION"].str[:300].values
        base["allergy_reaction"] = rng.choice(["Hives", "Anaphylaxis", "Rash", "Nausea", "Swelling"], na)
        base["allergy_severity"] = rng.choice(["MILD", "MODERATE", "SEVERE"], na, p=[0.4, 0.4, 0.2])
        base["allergy_criticality"] = rng.choice(["LOW", "HIGH", "UNABLE_TO_ASSESS"], na, p=[0.5, 0.35, 0.15])
        frames.append(base)
        print(f"  allergies: {len(base)}")

    # --- Immunizations ---
    imm = _load("immunizations.csv")
    imm = imm[imm["PATIENT"].isin(pat_ext)]
    if len(imm) > 0:
        base = _make_base(imm, "IMMUNIZATION")
        base["onset_date"] = dates.shift_series(imm["DATE"])
        base["is_active"] = False
        base["immunization_cvx_code"] = imm["CODE"].str[:10].values
        base["immunization_desc"] = imm["DESCRIPTION"].str[:300].values
        frames.append(base)
        print(f"  immunizations: {len(base)}")

    # --- BMI -> SOCIAL_HISTORY (one per patient, latest) ---
    obs = _load("observations.csv", usecols=["DATE", "PATIENT", "CODE", "VALUE"])
    obs = obs[obs["PATIENT"].isin(pat_ext)]
    bmi_obs = obs[obs["CODE"] == BMI_LOINC].copy()
    if len(bmi_obs) > 0:
        bmi_obs["_val"] = pd.to_numeric(bmi_obs["VALUE"], errors="coerce")
        bmi_obs = bmi_obs.dropna(subset=["_val"])
        # Latest per patient
        bmi_obs["_date"] = pd.to_datetime(bmi_obs["DATE"], errors="coerce")
        bmi_latest = bmi_obs.sort_values("_date").groupby("PATIENT").last().reset_index()

        # Also get latest height/weight
        h_obs = obs[obs["CODE"] == HEIGHT_LOINC].copy()
        h_obs["_val"] = pd.to_numeric(h_obs["VALUE"], errors="coerce")
        h_latest = h_obs.dropna(subset=["_val"]).groupby("PATIENT").last().reset_index() if len(h_obs) > 0 else pd.DataFrame()

        w_obs = obs[obs["CODE"] == WEIGHT_LOINC].copy()
        w_obs["_val"] = pd.to_numeric(w_obs["VALUE"], errors="coerce")
        w_latest = w_obs.dropna(subset=["_val"]).groupby("PATIENT").last().reset_index() if len(w_obs) > 0 else pd.DataFrame()

        base = _make_base(bmi_latest, "SOCIAL_HISTORY")
        base["onset_date"] = dates.shift_series(bmi_latest["DATE"])
        base["is_active"] = True
        base["bmi_value"] = bmi_latest["_val"].round(2).values
        base["bmi_recorded_date"] = dates.shift_series(bmi_latest["DATE"])

        if len(h_latest) > 0:
            h_map = dict(zip(h_latest["PATIENT"], h_latest["_val"]))
            base["height_cm"] = bmi_latest["PATIENT"].map(h_map).round(2)
        if len(w_latest) > 0:
            w_map = dict(zip(w_latest["PATIENT"], w_latest["_val"]))
            base["weight_kg"] = bmi_latest["PATIENT"].map(w_map).round(2)

        n_bmi = len(bmi_latest)
        base["smoking_status"] = rng.choice(
            ["Never smoker", "Former smoker", "Current smoker", "Unknown"],
            n_bmi, p=[0.55, 0.25, 0.15, 0.05],
        )
        frames.append(base)
        print(f"  social_history (BMI): {len(base)}")

    df = pd.concat(frames, ignore_index=True)
    print(f"  total: {len(df)} medical history records")
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("Building bronze external patient tables")
    print("=" * 78)

    patients = _load("patients.csv")
    print(f"  loaded {len(patients)} Synthea patients")

    try:
        ext_profile, org_map = build_ext_patientProfile(patients)
        write_table(ext_profile, "bronze", "ext_patientProfile", source_system="SYNTHEA")
    except Exception as exc:
        print(f"ERROR ext_patientProfile: {exc}")
        import traceback; traceback.print_exc()
        return 1

    try:
        ext_clinical = build_ext_patientClinicalRecords(org_map)
        write_table(ext_clinical, "bronze", "ext_patientClinicalRecords", source_system="SYNTHEA")
    except Exception as exc:
        print(f"ERROR ext_patientClinicalRecords: {exc}")
        import traceback; traceback.print_exc()
        return 1

    try:
        ext_events = build_ext_patientEhrEvents(org_map)
        write_table(ext_events, "bronze", "ext_patientEhrEvents", source_system="SYNTHEA")
    except Exception as exc:
        print(f"ERROR ext_patientEhrEvents: {exc}")
        import traceback; traceback.print_exc()
        return 1

    try:
        ext_history = build_ext_patientMedicalHistory(org_map)
        write_table(ext_history, "bronze", "ext_patientMedicalHistory", source_system="SYNTHEA")
    except Exception as exc:
        print(f"ERROR ext_patientMedicalHistory: {exc}")
        import traceback; traceback.print_exc()
        return 1

    print("\n" + "=" * 78)
    print("All 4 external patient tables built successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
