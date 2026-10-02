"""
Build silver member-centric tables from bronze data.

8 tables: slv_member_master, slv_member_coverage_span, slv_member_financial_position,
slv_member_condition_profile, slv_member_medication_history, slv_member_conservative_care,
slv_member_imaging_history, slv_member_lab_vitals_latest.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import dates, determinism as det  # noqa: E402
from common.frames import read_table, write_table  # noqa: E402
from config import schemas as S  # noqa: E402

RNG_BASE = "silver_member"


# ---------------------------------------------------------------------------
# 1. slv_member_master - identity resolution
# ---------------------------------------------------------------------------

def _is_current_span(end) -> bool:
    """Single definition of 'this coverage span is in force'.

    An open-ended span (NULL end date) or one ending on/after the reference date
    is current. Both build_member_master and build_coverage_spans call this so the
    member-level status can never disagree with the span-level status.
    """
    if pd.isna(end):
        return True
    try:
        return pd.to_datetime(end).date() >= dates.reference_date()
    except Exception:  # noqa: BLE001
        return False


def _current_plan_attrs(plans: pd.DataFrame) -> dict[str, dict]:
    """Per-member enrollment status and line of business, derived from coverage.

    These are SPAN-level attributes, not person-level ones: a member is 'ACTIVE'
    because a coverage span is in force, not because of anything on their profile
    record. dim_memberProfile does not carry them at all - reading them from there
    is what silently produced 1,171 NULLs.

    Line of business is taken from the member's LATEST span even when that span has
    terminated, because LOB determines which regulatory turnaround rules an analyst
    must apply and must therefore never be NULL.
    """
    attrs: dict[str, dict] = {}
    for mid, grp in plans.groupby("member_id"):
        grp = grp.sort_values("coverage_start_date")
        current = [r for _, r in grp.iterrows() if _is_current_span(r.get("coverage_end_date"))]
        chosen = current[-1] if current else grp.iloc[-1]
        attrs[mid] = {
            "line_of_business": chosen.get("line_of_business") or "COMMERCIAL",
            "enrollment_status": "ACTIVE" if current else "TERMED",
        }
    return attrs


def build_member_master() -> pd.DataFrame:
    mbr = read_table("bronze", "dim_memberProfile")
    ext = read_table("bronze", "ext_patientProfile")
    plans = read_table("bronze", "dim_memberPlan")
    plan_attrs = _current_plan_attrs(plans)
    rng = det.rng(RNG_BASE, "master")
    ref = dates.reference_date()

    # Deterministic match: ext rows with valid payer_member_id
    ext_det = ext[ext["payer_member_id"].notna() & ext["payer_member_id_asserted_flag"].astype(bool)].copy()
    det_matched = ext_det.drop_duplicates(subset=["payer_member_id"]).set_index("payer_member_id")

    rows = []
    for _, m in mbr.iterrows():
        mid = m["member_id"]
        ext_match = det_matched.loc[mid] if mid in det_matched.index else None

        if ext_match is not None and not isinstance(ext_match, pd.DataFrame):
            method = "DETERMINISTIC"
            confidence = 1.0
            ext_pid = ext_match.get("ext_patient_id")
            risk = ext_match.get("risk_score_hcc")
        else:
            # Probabilistic: try name+DOB match
            matched = False
            for _, e in ext.iterrows():
                if (str(e.get("first_name", "")).lower() == str(m.get("first_name", "")).lower()
                    and str(e.get("last_name", "")).lower() == str(m.get("last_name", "")).lower()
                    and str(e.get("date_of_birth", "")) == str(m.get("date_of_birth", ""))):
                    method = "PROBABILISTIC"
                    confidence = round(float(rng.uniform(0.75, 0.95)), 4)
                    ext_pid = e.get("ext_patient_id")
                    risk = e.get("risk_score_hcc")
                    matched = True
                    break
            if not matched:
                method = "UNMATCHED"
                confidence = None
                ext_pid = None
                risk = None

        dob = m.get("date_of_birth")
        age = (ref - pd.to_datetime(dob).date()).days // 365 if pd.notna(dob) else 0

        rows.append({
            "member_id": mid,
            "ext_patient_id": ext_pid,
            "match_method": method,
            "match_confidence_score": confidence,
            "first_name": m.get("first_name", ""),
            "last_name": m.get("last_name", ""),
            "date_of_birth": dob,
            "gender": m.get("gender", "U"),
            "age_years": max(0, age),
            "city": m.get("city"),
            "state": m.get("state"),
            "zip_code": m.get("zip_code"),
            "line_of_business": plan_attrs.get(mid, {}).get("line_of_business", "COMMERCIAL"),
            "enrollment_status": plan_attrs.get(mid, {}).get("enrollment_status", "TERMED"),
            "pcp_npi": m.get("pcp_npi"),
            "pcp_name": m.get("pcp_name"),
            "risk_score_hcc": risk,
            "is_deceased": bool(pd.notna(m.get("death_date"))),
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. slv_member_coverage_span
# ---------------------------------------------------------------------------

def build_coverage_spans() -> pd.DataFrame:
    plans = read_table("bronze", "dim_memberPlan")
    rows = []

    for mid, grp in plans.groupby("member_id"):
        grp = grp.sort_values("coverage_start_date")
        prev_end = None

        for i, (_, row) in enumerate(grp.iterrows()):
            start = row.get("coverage_start_date")
            end = row.get("coverage_end_date")
            if pd.isna(start):
                continue

            gap_days = 0
            has_gap = False
            if prev_end is not None and pd.notna(prev_end):
                try:
                    gap_days = max(0, (pd.to_datetime(start) - pd.to_datetime(prev_end)).days)
                    has_gap = gap_days > 0
                except Exception:
                    pass

            is_current = _is_current_span(end)

            try:
                dur = (pd.to_datetime(end) - pd.to_datetime(start)).days if pd.notna(end) else None
            except Exception:
                dur = None

            sk = det.stable_id("CSK", str(mid), str(i), width=10)

            rows.append({
                "coverage_span_sk": sk,
                "member_id": mid,
                "plan_id": row.get("plan_id", ""),
                "line_of_business": row.get("line_of_business", "COMMERCIAL"),
                "coverage_start_date": start,
                "coverage_end_date": end if pd.notna(end) else None,
                "is_current": is_current,
                "gap_before_days": gap_days,
                "has_gap_before": has_gap,
                "enrollment_status": "ACTIVE" if is_current else "TERMED",
                "span_duration_days": dur,
            })
            prev_end = end

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. slv_member_financial_position (pass-through from dim_memberFinance)
# ---------------------------------------------------------------------------

def build_financial_position() -> pd.DataFrame:
    fin = read_table("bronze", "dim_memberFinance")
    cols = S.get("silver", "slv_member_financial_position").col_names()
    audit = {c.name for c in S.AUDIT_COLS}
    keep = [c for c in cols if c not in audit and c in fin.columns]
    return fin[keep].copy()


# ---------------------------------------------------------------------------
# 4. slv_member_condition_profile
# ---------------------------------------------------------------------------

def build_condition_profile() -> pd.DataFrame:
    # From ext_patientClinicalRecords (DIAGNOSIS type) + ext_patientMedicalHistory (CHRONIC_CONDITION)
    master = read_table("silver", "slv_member_master")
    mid_map = master.set_index("ext_patient_id")["member_id"].to_dict()

    rows = []
    idx = 0

    # Clinical records - diagnoses
    try:
        clin = read_table("bronze", "ext_patientClinicalRecords")
        dx = clin[clin["record_type"] == "DIAGNOSIS"].copy()
        for _, row in dx.iterrows():
            ext_pid = row.get("ext_patient_id")
            mid = mid_map.get(ext_pid, row.get("payer_member_id"))
            if not mid:
                continue
            sk = det.stable_id("CP", str(mid), str(idx), width=10)
            idx += 1
            rows.append({
                "condition_profile_sk": sk,
                "member_id": mid,
                "icd10_code": row.get("icd10_code"),
                "condition_desc": row.get("source_code_desc") or row.get("icd10_desc"),
                "onset_date": row.get("onset_date"),
                "resolved_date": row.get("resolved_date"),
                "is_active": bool(row.get("clinical_status") == "ACTIVE") if pd.notna(row.get("clinical_status")) else True,
                "is_chronic": bool(row.get("is_chronic_flag")),
                "condition_category": None,
                "source_type": "CLINICAL_RECORD",
            })
    except Exception:
        pass

    # Medical history - chronic conditions
    try:
        hist = read_table("bronze", "ext_patientMedicalHistory")
        conds = hist[hist["history_type"] == "CHRONIC_CONDITION"].copy()
        for _, row in conds.iterrows():
            ext_pid = row.get("ext_patient_id")
            mid = mid_map.get(ext_pid, row.get("payer_member_id"))
            if not mid:
                continue
            sk = det.stable_id("CP", str(mid), str(idx), width=10)
            idx += 1
            rows.append({
                "condition_profile_sk": sk,
                "member_id": mid,
                "icd10_code": row.get("condition_icd10_code"),
                "condition_desc": row.get("condition_desc"),
                "onset_date": row.get("onset_date"),
                "resolved_date": row.get("end_date"),
                "is_active": bool(row.get("is_active")),
                "is_chronic": True,
                "condition_category": row.get("condition_category"),
                "source_type": "MEDICAL_HISTORY",
            })
    except Exception:
        pass

    if not rows:
        return pd.DataFrame(columns=S.get("silver", "slv_member_condition_profile").col_names())
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 5. slv_member_medication_history
# ---------------------------------------------------------------------------

def build_medication_history() -> pd.DataFrame:
    master = read_table("silver", "slv_member_master")
    mid_map = master.set_index("ext_patient_id")["member_id"].to_dict()

    rows = []
    idx = 0

    try:
        hist = read_table("bronze", "ext_patientMedicalHistory")
        meds = hist[hist["history_type"] == "MEDICATION"].copy()
        for _, row in meds.iterrows():
            ext_pid = row.get("ext_patient_id")
            mid = mid_map.get(ext_pid, row.get("payer_member_id"))
            if not mid:
                continue
            sk = det.stable_id("MH", str(mid), str(idx), width=10)
            idx += 1

            duration = row.get("therapy_duration_days")
            pdc = row.get("adherence_pdc_pct")
            is_first_line = bool(row.get("is_first_line_therapy"))
            adequate = False
            if pd.notna(duration) and pd.notna(pdc):
                adequate = int(duration) >= 30 and float(pdc) >= 80.0

            rows.append({
                "medication_history_sk": sk,
                "member_id": mid,
                "medication_name": row.get("medication_name"),
                "generic_name": row.get("medication_generic_name"),
                "therapeutic_class": row.get("therapeutic_class"),
                "ndc_code": row.get("medication_ndc_code"),
                "rxnorm_code": row.get("medication_rxnorm_code"),
                "prescribed_date": row.get("prescribed_date"),
                "discontinued_date": row.get("discontinued_date"),
                "discontinue_reason": row.get("discontinue_reason"),
                "therapy_duration_days": duration,
                "adherence_pdc_pct": pdc,
                "is_first_line": is_first_line,
                "is_specialty": bool(row.get("is_specialty_drug")),
                "requires_pa": bool(row.get("requires_pa_flag")),
                "step_therapy_adequate_flag": adequate,
            })
    except Exception:
        pass

    if not rows:
        return pd.DataFrame(columns=S.get("silver", "slv_member_medication_history").col_names())
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 6. slv_member_conservative_care
# ---------------------------------------------------------------------------

CONSERVATIVE_KEYWORDS = {
    "PHYSICAL THERAPY": "pt",
    "OCCUPATIONAL THERAPY": "ot",
    "CHIROPRACTIC": "chiro",
    "INJECTION": "injection",
    "NERVE BLOCK": "injection",
    "JOINT INJECTION": "injection",
}

BODY_SYSTEMS = [
    "SPINE", "KNEE", "SHOULDER", "HIP", "ANKLE", "WRIST", "NECK",
    "LOWER_BACK", "UPPER_EXTREMITY", "LOWER_EXTREMITY", "OTHER",
]


def build_conservative_care() -> pd.DataFrame:
    rng = det.rng(RNG_BASE, "conservative")
    master = read_table("silver", "slv_member_master")
    member_ids = master["member_id"].unique().tolist()

    rows = []
    idx = 0
    for mid in member_ids:
        n_systems = int(rng.integers(0, 4))
        systems = rng.choice(BODY_SYSTEMS, min(n_systems, len(BODY_SYSTEMS)), replace=False).tolist()
        for sys_name in systems:
            sk = det.stable_id("CC", str(mid), sys_name, str(idx), width=10)
            idx += 1
            pt = int(rng.integers(0, 20))
            ot = int(rng.integers(0, 12))
            chiro = int(rng.integers(0, 15))
            inj = int(rng.integers(0, 6))
            total = pt + ot + chiro + inj
            if total == 0:
                continue
            dur = int(rng.integers(30, 365))
            ref = dates.reference_date()
            last = ref - timedelta(days=int(rng.integers(0, 180)))
            first = last - timedelta(days=dur)

            rows.append({
                "conservative_care_sk": sk,
                "member_id": mid,
                "body_system": sys_name,
                "pt_visit_count": pt,
                "ot_visit_count": ot,
                "chiro_visit_count": chiro,
                "injection_count": inj,
                "total_conservative_visits": total,
                "first_visit_date": first,
                "last_visit_date": last,
                "duration_days": dur,
            })

    if not rows:
        return pd.DataFrame(columns=S.get("silver", "slv_member_conservative_care").col_names())
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 7. slv_member_imaging_history
# ---------------------------------------------------------------------------

MODALITIES = ["XRAY", "CT", "MRI", "PET", "ULTRASOUND"]
IMAGING_REGIONS = ["HEAD", "CHEST", "ABDOMEN", "SPINE", "KNEE", "SHOULDER", "HIP", "PELVIS"]


def build_imaging_history() -> pd.DataFrame:
    rng = det.rng(RNG_BASE, "imaging")
    master = read_table("silver", "slv_member_master")
    member_ids = master["member_id"].unique().tolist()

    rows = []
    idx = 0
    for mid in member_ids:
        n_imaging = int(rng.integers(0, 5))
        for _ in range(n_imaging):
            modality = rng.choice(MODALITIES)
            region = rng.choice(IMAGING_REGIONS)
            sk = det.stable_id("IH", str(mid), modality, region, str(idx), width=10)
            idx += 1
            count = int(rng.integers(1, 6))
            ref = dates.reference_date()
            last = ref - timedelta(days=int(rng.integers(0, 365)))
            first = last - timedelta(days=int(rng.integers(0, 730)))

            rows.append({
                "imaging_history_sk": sk,
                "member_id": mid,
                "modality": modality,
                "body_region": region,
                "study_count": count,
                "first_study_date": first,
                "last_study_date": last,
            })

    if not rows:
        return pd.DataFrame(columns=S.get("silver", "slv_member_imaging_history").col_names())
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 8. slv_member_lab_vitals_latest
# ---------------------------------------------------------------------------

def build_lab_vitals_latest() -> pd.DataFrame:
    rng = det.rng(RNG_BASE, "labs")
    master = read_table("silver", "slv_member_master")

    rows = []
    ref = dates.reference_date()
    for _, m in master.iterrows():
        mid = m["member_id"]
        days_back = int(rng.integers(7, 365))
        lab_date = ref - timedelta(days=days_back)

        bmi = round(float(rng.normal(28, 6)), 2)
        bmi = max(15, min(65, bmi))

        rows.append({
            "member_id": mid,
            "bmi_value": bmi,
            "bmi_date": lab_date,
            "hba1c_value": round(float(rng.normal(6.0, 1.5)), 2) if rng.random() < 0.6 else None,
            "hba1c_date": lab_date if rng.random() < 0.6 else None,
            "crp_value": round(float(rng.exponential(5)), 3) if rng.random() < 0.4 else None,
            "crp_date": lab_date if rng.random() < 0.4 else None,
            "esr_value": round(float(rng.normal(20, 15)), 2) if rng.random() < 0.3 else None,
            "esr_date": lab_date if rng.random() < 0.3 else None,
            "egfr_value": round(float(rng.normal(85, 25)), 2) if rng.random() < 0.5 else None,
            "egfr_date": lab_date if rng.random() < 0.5 else None,
            "height_cm": round(float(rng.normal(170, 12)), 2),
            "weight_kg": round(float(rng.normal(80, 20)), 2),
        })

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _drop_unattributable(df, table_name: str):
    """Drop rows that could not be attributed to a known member.

    ext_patientProfile deliberately carries a ~8% identity gap (missing or
    conflicting payer_member_id), and slv_member_master resolves what it can.
    A residue remains that belongs to no known member.

    Those rows are dropped rather than carried with a NULL member_id. A NULL key
    in a member-centric table is worse than an absent row: it silently drops out
    of every join while still inflating row counts, so a "condition count per
    member" would quietly disagree with the table total. The count is reported so
    the loss is visible rather than hidden.
    """
    if "member_id" not in df.columns:
        return df
    n_before = len(df)
    out = df[df["member_id"].notna()].copy()
    dropped = n_before - len(out)
    if dropped:
        print(
            f"    dropped {dropped:,d} of {n_before:,d} rows with no resolvable "
            f"member_id (unmatched provider-shared records)"
        )
    return out


def main() -> int:
    print("=" * 78)
    print("Building Silver Member-Centric Tables")
    print("=" * 78)

    builders = [
        ("slv_member_master", build_member_master),
        ("slv_member_coverage_span", build_coverage_spans),
        ("slv_member_financial_position", build_financial_position),
        ("slv_member_condition_profile", build_condition_profile),
        ("slv_member_medication_history", build_medication_history),
        ("slv_member_conservative_care", build_conservative_care),
        ("slv_member_imaging_history", build_imaging_history),
        ("slv_member_lab_vitals_latest", build_lab_vitals_latest),
    ]

    errors = []
    for name, builder in builders:
        print(f"\n  Building {name} ...")
        try:
            df = builder()
            df = _drop_unattributable(df, name)
            write_table(df, "silver", name, source_system="DERIVED")
        except Exception as exc:
            print(f"    ERROR: {exc}")
            errors.append((name, exc))

    print("\n" + "=" * 78)
    if errors:
        print(f"FAILED: {len(errors)} tables:")
        for n, e in errors:
            print(f"  {n}: {e}")
        return 1
    print("All 8 silver member-centric tables built.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
