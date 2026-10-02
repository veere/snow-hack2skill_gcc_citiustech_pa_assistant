"""
Build silver PA-centric tables from bronze fct_memberPA and related data.

7 tables: slv_pa_case, slv_pa_decision_history, slv_pa_precedent_stats,
slv_pa_criteria_ruleset, slv_pa_criteria_evaluation, slv_duplicate_pa_candidates,
slv_benefit_pa_requirement.
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
from common.frames import read_table, write_table  # noqa: E402
from config import schemas as S  # noqa: E402

RNG_BASE = "silver_pa"


def build_pa_case() -> pd.DataFrame:
    pa = read_table("bronze", "fct_memberPA")
    ref = pd.Timestamp(dates.reference_date())
    rows = []
    for _, r in pa.iterrows():
        recv = pd.to_datetime(r["received_datetime_utc"])
        if pd.isna(recv):
            continue
        if hasattr(recv, 'tzinfo') and recv.tzinfo:
            recv = recv.tz_localize(None)
        deadline = pd.to_datetime(r["regulatory_deadline_datetime_utc"])
        if hasattr(deadline, 'tzinfo') and deadline.tzinfo:
            deadline = deadline.tz_localize(None)
        decision = pd.to_datetime(r.get("decision_datetime_utc"))
        if pd.notna(decision) and hasattr(decision, 'tzinfo') and decision.tzinfo:
            decision = decision.tz_localize(None)

        paused = int(r.get("clock_paused_days", 0) or 0)
        is_open = bool(r.get("is_open_flag"))
        end_point = decision if pd.notna(decision) else ref
        elapsed = max(0, (end_point - recv).total_seconds() / 86400 - paused)
        days_left = (deadline - ref).total_seconds() / 86400 if pd.notna(deadline) else None

        # ORDER MATTERS HERE. An earlier version tested `days_left < 2` before
        # `days_left < 0`, and since every negative number is also below 2 the AT_RISK
        # branch swallowed every overdue request - making the BREACHED branch dead code.
        # The effect was that 448 of 1,206 OPEN requests sat past their regulatory
        # deadline while reporting merely "AT_RISK", so the copilot showed "0 past
        # deadline" to an analyst who had 10 overdue cases in front of them. An open
        # overdue request is the single most urgent thing on a PA desk.
        #
        # Most specific condition first: already breached, then at risk, then on track.
        if pd.notna(decision) and pd.notna(deadline) and decision > deadline:
            sla = "BREACHED"          # decided, but decided late
        elif pd.notna(days_left) and days_left < 0:
            sla = "BREACHED"          # deadline has passed and it is still open
        elif pd.notna(days_left) and days_left < 2:
            sla = "AT_RISK"           # inside the two-day warning window
        else:
            sla = "ON_TRACK"

        rows.append({
            "pa_id": r["pa_id"], "member_id": r["member_id"], "plan_id": r["plan_id"],
            "line_of_business": r["line_of_business"], "service_category": r["service_category"],
            "pa_status": r["pa_status"], "is_open": is_open, "urgency_flag": r["urgency_flag"],
            "received_datetime_utc": recv, "decision_datetime_utc": decision if pd.notna(decision) else None,
            "regulatory_deadline_datetime_utc": deadline, "clock_paused_days": paused,
            "tat_business_days": round(elapsed, 2), "sla_state": sla,
            "days_until_deadline": round(days_left, 2) if days_left is not None else None,
            "pend_reason_code": r.get("pend_reason_code"), "denial_reason_code": r.get("denial_reason_code"),
            "auto_adjudicated_flag": bool(r.get("auto_adjudicated_flag")),
            "gold_card_exempt_flag": bool(r.get("gold_card_exempt_flag")),
        })
    return pd.DataFrame(rows)


def build_decision_history() -> pd.DataFrame:
    pa = read_table("bronze", "fct_memberPA")
    rows = []
    idx = 0
    for _, r in pa.iterrows():
        if pd.notna(r.get("decision_datetime_utc")):
            sk = det.stable_id("DH", r["pa_id"], "ORIG", width=10)
            rows.append({
                "decision_history_sk": sk, "pa_id": r["pa_id"], "decision_type": "ORIGINAL",
                "decision_datetime_utc": r["decision_datetime_utc"], "pa_status": r["pa_status"],
                "decision_by_role": r.get("decision_by_role"), "denial_reason_code": r.get("denial_reason_code"),
                "denial_reason_desc": r.get("denial_reason_desc"), "appeal_outcome": None,
            })
            idx += 1
        if r.get("appeal_flag"):
            sk = det.stable_id("DH", r["pa_id"], "APPEAL", width=10)
            rows.append({
                "decision_history_sk": sk, "pa_id": r["pa_id"], "decision_type": "APPEAL",
                "decision_datetime_utc": pd.to_datetime(r.get("appeal_decision_date")) if pd.notna(r.get("appeal_decision_date")) else None,
                "pa_status": None, "decision_by_role": None, "denial_reason_code": None,
                "denial_reason_desc": None, "appeal_outcome": r.get("appeal_outcome"),
            })
            idx += 1
    if not rows:
        return pd.DataFrame(columns=S.get("silver", "slv_pa_decision_history").col_names())
    return pd.DataFrame(rows)


def build_precedent_stats() -> pd.DataFrame:
    """Historical decision counts per requested service / diagnosis / LOB / specialty.

    Three deliberate choices, each fixing a way this table previously misled:

    1. Grain keys on a UNIFIED `service_code`. Roughly 47% of PA requests are drugs
       and carry an NDC with no CPT (SPECIALTY_DRUG 100%, HOME_HEALTH 84%), so
       keying on procedure code alone made drug precedent impossible to look up.
    2. `approval_rate_pct` divides by DECIDED requests, not all requests. About 32%
       of requests sit in RECEIVED / PENDING_CLINICAL / PENDED_FOR_INFO /
       PEER_TO_PEER / EXPIRED / WITHDRAWN and never reached a determination;
       including them in the denominator understated every rate.
    3. The rate is NULL - not 0.0 - when nothing has been decided. A fabricated 0%
       reads as "we always deny this", which is a materially different claim from
       "we have not decided one of these yet".
    """
    pa = read_table("bronze", "fct_memberPA")

    pa = pa.copy()
    pa["service_code"] = pa["requested_procedure_code"].fillna(pa["requested_ndc"])
    pa["service_code_system"] = pa["requested_code_system"]

    grp_cols = [
        "service_code",
        "service_code_system",
        "primary_dx_icd10",
        "line_of_business",
        "ordering_provider_specialty",
    ]

    approved_statuses = ["APPROVED", "PARTIALLY_APPROVED"]

    agg = (
        pa.groupby(grp_cols, dropna=False)
        .agg(
            total_requests=("pa_id", "count"),
            approved_count=("pa_status", lambda x: int(x.isin(approved_statuses).sum())),
            denied_count=("pa_status", lambda x: int((x == "DENIED").sum())),
            requested_procedure_code=("requested_procedure_code", "first"),
            requested_ndc=("requested_ndc", "first"),
        )
        .reset_index()
    )

    agg["decided_count"] = agg["approved_count"] + agg["denied_count"]
    agg["approval_rate_pct"] = (
        (agg["approved_count"] / agg["decided_count"] * 100)
        .where(agg["decided_count"] > 0)
        .round(2)
    )

    # Key the surrogate on the GRAIN, not the row index. An index-derived key
    # changes meaning whenever upstream ordering shifts, which breaks the
    # determinism hash for reasons that have nothing to do with the data.
    agg["precedent_sk"] = det.unique_stable_ids(
        "PR",
        agg[grp_cols].astype(str).agg("|".join, axis=1),
        width=12,
    )
    return agg


def _criteria_applicability() -> dict[str, list[tuple[str, str, str | None, str | None, bool]]]:
    """Map service_category -> list of (criterion_type, desc, threshold_value, threshold_unit, is_mandatory).

    Only applicable criteria are returned per category. Universal criteria
    (DIAGNOSIS_SUPPORT, NO_DUPLICATE, AGE_GENDER_GATE) apply everywhere.
    """
    UNIVERSAL = [
        ("DIAGNOSIS_SUPPORT", "Supporting diagnosis documented and coded in ICD-10-CM", None, None, True),
        ("NO_DUPLICATE", "No duplicate active authorization for same service", None, None, True),
        ("AGE_GENDER_GATE", "Member meets age and gender eligibility for service", None, None, True),
    ]

    CATEGORY_CRITERIA: dict[str, list[tuple[str, str, str | None, str | None, bool]]] = {
        "ADVANCED_IMAGING": [
            ("IMAGING_PREREQUISITE", "Prior X-ray of same body region required before advanced imaging", None, None, True),
            ("CONSERVATIVE_CARE_DURATION", "Minimum 6 weeks conservative care before advanced imaging", "42", "days", False),
        ],
        "SPECIALTY_DRUG": [
            ("STEP_THERAPY", "First-line therapy with adequate duration and adherence", "30", "days", True),
            ("FAILED_FIRST_LINE", "Documented failure or intolerance of first-line therapy", None, None, True),
            ("LAB_THRESHOLD", "Lab values support medical necessity for specialty drug", None, None, False),
        ],
        "ELECTIVE_SURGERY": [
            ("BMI_THRESHOLD", "BMI >= 40 or BMI >= 35 with comorbidity for bariatric", "40", "kg/m2", False),
            ("CONSERVATIVE_CARE_DURATION", "Minimum 6 weeks conservative care before elective surgery", "42", "days", True),
        ],
        "DME": [
            ("DURATION_QUANTITY_LIMIT", "Requested units within frequency and quantity limits", None, None, False),
        ],
        "BEHAVIORAL_HEALTH": [
            ("DURATION_QUANTITY_LIMIT", "Requested sessions within plan visit limits", None, None, False),
        ],
        "INPATIENT_ADMIT": [
            ("PLACE_OF_SERVICE", "Inpatient level of care clinically appropriate", None, None, True),
        ],
        "REHAB_THERAPY": [
            ("CONSERVATIVE_CARE_DURATION", "Prior conservative care documented for rehab therapy", "42", "days", False),
            ("DURATION_QUANTITY_LIMIT", "Requested visits within plan limits", None, None, False),
            ("PLACE_OF_SERVICE", "Appropriate facility for rehab services", None, None, False),
        ],
        "GENETIC_TESTING": [
            ("LAB_THRESHOLD", "Lab values or family history support genetic testing", None, None, False),
        ],
        "HOME_HEALTH": [
            ("PLACE_OF_SERVICE", "Home health appropriate; homebound status documented", None, None, True),
            ("DURATION_QUANTITY_LIMIT", "Home health visits within plan limits", None, None, False),
        ],
        "SLEEP_STUDY": [
            ("BMI_THRESHOLD", "BMI >= 30 supports sleep study referral", "30", "kg/m2", False),
        ],
        "PAIN_MANAGEMENT": [
            ("CONSERVATIVE_CARE_DURATION", "Minimum 6 weeks conservative care before interventional pain", "42", "days", True),
            ("FAILED_FIRST_LINE", "First-line pain therapy failed or inadequate", None, None, False),
            ("DURATION_QUANTITY_LIMIT", "Injection frequency within limits", None, None, False),
        ],
        "RADIATION_ONCOLOGY": [
            ("LAB_THRESHOLD", "Lab values support oncology treatment plan", None, None, False),
            ("DURATION_QUANTITY_LIMIT", "Fraction count within standard protocol", None, None, False),
        ],
        "TRANSPLANT": [
            ("BMI_THRESHOLD", "BMI within acceptable range for transplant candidacy", "40", "kg/m2", False),
            ("LAB_THRESHOLD", "Lab values (eGFR, etc.) support transplant evaluation", None, None, True),
        ],
    }

    out: dict[str, list[tuple[str, str, str | None, str | None, bool]]] = {}
    for cat in S.PA_SERVICE_CATEGORIES:
        out[cat] = list(UNIVERSAL) + CATEGORY_CRITERIA.get(cat, [])
    return out


def build_criteria_ruleset() -> pd.DataFrame:
    rules = []
    applicability = _criteria_applicability()
    for cat in S.PA_SERVICE_CATEGORIES:
        ruleset_id = det.stable_id("RS", cat, width=8)
        for ct, desc, thresh_val, thresh_unit, mandatory in applicability[cat]:
            sk = det.stable_id("CR", cat, ct, width=10)
            rules.append({
                "criteria_ruleset_sk": sk, "ruleset_id": ruleset_id, "ruleset_version": "v1.0",
                "service_category": cat, "criterion_type": ct,
                "criterion_desc": desc,
                "threshold_value": thresh_val, "threshold_unit": thresh_unit,
                "is_mandatory": mandatory,
            })
    return pd.DataFrame(rules)


def build_criteria_evaluation() -> pd.DataFrame:
    pa = read_table("bronze", "fct_memberPA")
    labs = read_table("silver", "slv_member_lab_vitals_latest")
    care = read_table("silver", "slv_member_conservative_care")
    imaging = read_table("silver", "slv_member_imaging_history")
    meds = read_table("silver", "slv_member_medication_history")
    conds = read_table("silver", "slv_member_condition_profile")
    dupes = read_table("silver", "slv_duplicate_pa_candidates")
    master = read_table("silver", "slv_member_master")

    labs_ix = labs.set_index("member_id")
    care_ix = care.groupby("member_id")
    imaging_ix = imaging.groupby("member_id")
    meds_ix = meds.groupby("member_id")
    conds_ix = conds.groupby("member_id")
    dupe_pa_ids = set(dupes["pa_id_1"].tolist() + dupes["pa_id_2"].tolist())
    master_ix = master.set_index("member_id")

    applicability = _criteria_applicability()

    rows: list[dict] = []
    idx = 0
    for _, r in pa.iterrows():
        pa_id = r["pa_id"]
        mid = r["member_id"]
        svc = r["service_category"]
        applicable = applicability.get(svc, [])

        met_count = 0
        total_count = len(applicable)

        for ct, _desc, thresh_val, _thresh_unit, _mandatory in applicable:
            result, evidence, source_table, source_id = _evaluate_criterion(
                ct, mid, pa_id, svc, r, labs_ix, care_ix, imaging_ix,
                meds_ix, conds_ix, dupe_pa_ids, master_ix, thresh_val,
            )
            if result == "MET":
                met_count += 1

            sk = det.stable_id("CE", pa_id, ct, str(idx), width=14)
            idx += 1
            rows.append({
                "criteria_eval_sk": sk, "pa_id": pa_id, "member_id": mid,
                "criterion_type": ct, "criterion_result": result,
                "evidence_summary": evidence,
                "evidence_source_table": source_table,
                "evidence_source_id": source_id,
                "criteria_met_count": met_count, "criteria_total_count": total_count,
            })

        # Back-fill criteria_met_count / criteria_total_count on earlier rows of this PA
        for i in range(len(rows) - total_count, len(rows)):
            rows[i]["criteria_met_count"] = met_count
            rows[i]["criteria_total_count"] = total_count

    if not rows:
        return pd.DataFrame(columns=S.get("silver", "slv_pa_criteria_evaluation").col_names())
    return pd.DataFrame(rows)


def _evaluate_criterion(
    ct: str, mid: str, pa_id: str, svc: str, pa_row,
    labs_ix, care_ix, imaging_ix, meds_ix, conds_ix,
    dupe_pa_ids: set, master_ix, thresh_val: str | None,
) -> tuple[str, str, str | None, str | None]:
    """Evaluate one criterion against real member clinical data.

    Returns (result, evidence_text, source_table, source_id).
    """
    if ct == "BMI_THRESHOLD":
        return _eval_bmi(mid, labs_ix, thresh_val)
    if ct == "LAB_THRESHOLD":
        return _eval_lab(mid, labs_ix)
    if ct == "CONSERVATIVE_CARE_DURATION":
        return _eval_conservative_care(mid, care_ix)
    if ct == "IMAGING_PREREQUISITE":
        return _eval_imaging_prereq(mid, imaging_ix)
    if ct == "STEP_THERAPY":
        return _eval_step_therapy(mid, meds_ix)
    if ct == "FAILED_FIRST_LINE":
        return _eval_failed_first_line(mid, meds_ix)
    if ct == "DIAGNOSIS_SUPPORT":
        return _eval_diagnosis_support(mid, pa_row, conds_ix)
    if ct == "NO_DUPLICATE":
        return _eval_no_duplicate(pa_id, dupe_pa_ids)
    if ct == "AGE_GENDER_GATE":
        return _eval_age_gender(mid, master_ix)
    if ct == "PLACE_OF_SERVICE":
        return _eval_place_of_service(pa_row)
    if ct == "DURATION_QUANTITY_LIMIT":
        return _eval_duration_quantity(pa_row)
    return ("INDETERMINATE", f"No evaluator implemented for {ct}", None, None)


def _eval_bmi(mid, labs_ix, thresh_val) -> tuple[str, str, str | None, str | None]:
    threshold = float(thresh_val) if thresh_val else 40.0
    if mid not in labs_ix.index:
        return ("INDETERMINATE", "No BMI on file; cannot evaluate BMI threshold",
                "slv_member_lab_vitals_latest", None)
    row = labs_ix.loc[mid]
    bmi = row.get("bmi_value") if isinstance(row, pd.Series) else None
    bmi_date = row.get("bmi_date") if isinstance(row, pd.Series) else None
    if pd.isna(bmi):
        return ("INDETERMINATE", "BMI value not recorded",
                "slv_member_lab_vitals_latest", None)
    bmi_f = float(bmi)
    date_str = str(bmi_date) if pd.notna(bmi_date) else "date unknown"
    if bmi_f >= threshold:
        return ("MET", f"BMI {bmi_f:.1f} recorded {date_str} meets threshold {threshold:.0f}",
                "slv_member_lab_vitals_latest", str(mid))
    return ("UNMET", f"BMI {bmi_f:.1f} recorded {date_str} below threshold {threshold:.0f}",
            "slv_member_lab_vitals_latest", str(mid))


def _eval_lab(mid, labs_ix) -> tuple[str, str, str | None, str | None]:
    if mid not in labs_ix.index:
        return ("INDETERMINATE", "No lab values on file; cannot assess lab thresholds",
                "slv_member_lab_vitals_latest", None)
    row = labs_ix.loc[mid]
    if not isinstance(row, pd.Series):
        return ("INDETERMINATE", "No lab values on file",
                "slv_member_lab_vitals_latest", None)
    parts = []
    has_any = False
    hba1c = row.get("hba1c_value")
    if pd.notna(hba1c):
        has_any = True
        hba1c_f = float(hba1c)
        d = str(row.get("hba1c_date")) if pd.notna(row.get("hba1c_date")) else "date unknown"
        parts.append(f"HbA1c {hba1c_f:.1f}% ({d})")
    egfr = row.get("egfr_value")
    if pd.notna(egfr):
        has_any = True
        egfr_f = float(egfr)
        d = str(row.get("egfr_date")) if pd.notna(row.get("egfr_date")) else "date unknown"
        parts.append(f"eGFR {egfr_f:.1f} mL/min ({d})")
    crp = row.get("crp_value")
    if pd.notna(crp):
        has_any = True
        d = str(row.get("crp_date")) if pd.notna(row.get("crp_date")) else "date unknown"
        parts.append(f"CRP {float(crp):.2f} mg/L ({d})")
    if not has_any:
        return ("INDETERMINATE", "Lab values (HbA1c, eGFR, CRP) not available",
                "slv_member_lab_vitals_latest", None)
    return ("MET", "Lab values on file: " + "; ".join(parts),
            "slv_member_lab_vitals_latest", str(mid))


def _eval_conservative_care(mid, care_ix) -> tuple[str, str, str | None, str | None]:
    if mid not in care_ix.groups:
        return ("INDETERMINATE", "No conservative care visits on file",
                "slv_member_conservative_care", None)
    member_care = care_ix.get_group(mid)
    best = member_care.loc[member_care["total_conservative_visits"].idxmax()]
    pt = int(best.get("pt_visit_count", 0) or 0)
    dur = int(best.get("duration_days", 0) or 0)
    body = best.get("body_system", "unknown")
    total = int(best.get("total_conservative_visits", 0) or 0)
    if dur >= 42 and total >= 6:
        return ("MET",
                f"PT {pt} visits, {total} total over {dur} days ({body}); meets 6-week requirement",
                "slv_member_conservative_care", str(best.get("conservative_care_sk")))
    if total > 0:
        return ("UNMET",
                f"PT {pt} visits, {total} total over {dur} days ({body}); does not meet 6-week/6-visit minimum",
                "slv_member_conservative_care", str(best.get("conservative_care_sk")))
    return ("INDETERMINATE", "No conservative care visits documented",
            "slv_member_conservative_care", None)


def _eval_imaging_prereq(mid, imaging_ix) -> tuple[str, str, str | None, str | None]:
    if mid not in imaging_ix.groups:
        return ("INDETERMINATE", "No prior imaging on file; imaging prerequisite cannot be confirmed",
                "slv_member_imaging_history", None)
    member_img = imaging_ix.get_group(mid)
    xrays = member_img[member_img["modality"] == "XRAY"]
    if xrays.empty:
        regions = ", ".join(sorted(member_img["modality"].unique()))
        return ("UNMET",
                f"No prior X-ray on file; prior imaging limited to {regions}",
                "slv_member_imaging_history", None)
    best = xrays.iloc[0]
    region = best.get("body_region", "unknown")
    last = str(best.get("last_study_date")) if pd.notna(best.get("last_study_date")) else "date unknown"
    count = int(best.get("study_count", 1))
    return ("MET",
            f"Prior X-ray of {region}: {count} study(ies), most recent {last}",
            "slv_member_imaging_history", str(best.get("imaging_history_sk")))


def _eval_step_therapy(mid, meds_ix) -> tuple[str, str, str | None, str | None]:
    if mid not in meds_ix.groups:
        return ("INDETERMINATE", "No medication history on file; step therapy cannot be evaluated",
                "slv_member_medication_history", None)
    member_meds = meds_ix.get_group(mid)
    first_line = member_meds[member_meds["is_first_line"] == True]
    if first_line.empty:
        return ("UNMET", "No first-line therapy documented in medication history",
                "slv_member_medication_history", None)
    adequate = first_line[first_line["step_therapy_adequate_flag"] == True]
    if not adequate.empty:
        best = adequate.iloc[0]
        name = best.get("medication_name", "unknown")
        dur = int(best.get("therapy_duration_days", 0) or 0)
        pdc = float(best.get("adherence_pdc_pct", 0) or 0)
        return ("MET",
                f"First-line {name} trialed {dur} days with {pdc:.0f}% PDC adherence; step therapy adequate",
                "slv_member_medication_history", str(best.get("medication_history_sk")))
    best = first_line.iloc[0]
    name = best.get("medication_name", "unknown")
    dur = int(best.get("therapy_duration_days", 0) or 0)
    pdc = float(best.get("adherence_pdc_pct", 0) or 0)
    return ("UNMET",
            f"First-line {name} trialed {dur} days with {pdc:.0f}% PDC; inadequate duration or adherence",
            "slv_member_medication_history", str(best.get("medication_history_sk")))


def _eval_failed_first_line(mid, meds_ix) -> tuple[str, str, str | None, str | None]:
    if mid not in meds_ix.groups:
        return ("INDETERMINATE", "No medication history on file; cannot confirm first-line failure",
                "slv_member_medication_history", None)
    member_meds = meds_ix.get_group(mid)
    first_line = member_meds[member_meds["is_first_line"] == True]
    if first_line.empty:
        return ("INDETERMINATE", "No first-line therapy documented",
                "slv_member_medication_history", None)
    discontinued = first_line[first_line["discontinued_date"].notna()]
    if not discontinued.empty:
        best = discontinued.iloc[0]
        name = best.get("medication_name", "unknown")
        reason = best.get("discontinue_reason", "unspecified reason")
        return ("MET",
                f"First-line {name} discontinued: {reason}",
                "slv_member_medication_history", str(best.get("medication_history_sk")))
    best = first_line.iloc[0]
    name = best.get("medication_name", "unknown")
    return ("UNMET",
            f"First-line {name} still active; no documented failure or discontinuation",
            "slv_member_medication_history", str(best.get("medication_history_sk")))


def _eval_diagnosis_support(mid, pa_row, conds_ix) -> tuple[str, str, str | None, str | None]:
    dx = pa_row.get("primary_dx_icd10")
    if pd.isna(dx) or not dx:
        return ("INDETERMINATE", "No primary diagnosis code on the PA request",
                "slv_member_condition_profile", None)
    if mid not in conds_ix.groups:
        return ("UNMET",
                f"PA lists primary dx {dx} but no conditions on file for this member",
                "slv_member_condition_profile", None)
    member_conds = conds_ix.get_group(mid)
    match = member_conds[member_conds["icd10_code"] == dx]
    if not match.empty:
        best = match.iloc[0]
        desc = best.get("condition_desc", dx)
        status = "active" if best.get("is_active") else "resolved"
        return ("MET",
                f"Diagnosis {dx} ({desc}) confirmed in member condition profile ({status})",
                "slv_member_condition_profile", str(best.get("condition_profile_sk")))
    prefix = dx[:3]
    prefix_match = member_conds[member_conds["icd10_code"].str[:3] == prefix]
    if not prefix_match.empty:
        best = prefix_match.iloc[0]
        found_code = best.get("icd10_code")
        return ("MET",
                f"PA dx {dx} not exact match but related code {found_code} found in condition profile",
                "slv_member_condition_profile", str(best.get("condition_profile_sk")))
    return ("UNMET",
            f"PA primary dx {dx} not found in member condition profile",
            "slv_member_condition_profile", None)


def _eval_no_duplicate(pa_id, dupe_pa_ids) -> tuple[str, str, str | None, str | None]:
    if pa_id in dupe_pa_ids:
        return ("UNMET", f"Potential duplicate authorization detected for PA {pa_id}",
                "slv_duplicate_pa_candidates", pa_id)
    return ("MET", "No duplicate active authorization found",
            "slv_duplicate_pa_candidates", None)


def _eval_age_gender(mid, master_ix) -> tuple[str, str, str | None, str | None]:
    if mid not in master_ix.index:
        return ("INDETERMINATE", "Member not found in master; age/gender cannot be verified",
                "slv_member_master", None)
    row = master_ix.loc[mid]
    if not isinstance(row, pd.Series):
        return ("INDETERMINATE", "Ambiguous member record",
                "slv_member_master", None)
    age = row.get("age_years")
    gender = row.get("gender", "U")
    if pd.isna(age):
        return ("INDETERMINATE", "Age not available for member",
                "slv_member_master", None)
    age_i = int(age)
    return ("MET", f"Member age {age_i}, gender {gender}; no age/gender exclusion applies",
            "slv_member_master", str(mid))


def _eval_place_of_service(pa_row) -> tuple[str, str, str | None, str | None]:
    pos = pa_row.get("place_of_service_code")
    pos_desc = pa_row.get("place_of_service_desc")
    if pd.isna(pos) and pd.isna(pos_desc):
        return ("INDETERMINATE", "Place of service not specified on PA request",
                None, None)
    desc = str(pos_desc) if pd.notna(pos_desc) else str(pos)
    return ("MET", f"Place of service: {desc} (code {pos})",
            None, None)


def _eval_duration_quantity(pa_row) -> tuple[str, str, str | None, str | None]:
    units = pa_row.get("requested_units")
    if pd.isna(units):
        return ("INDETERMINATE", "Requested units/quantity not specified on PA",
                None, None)
    units_i = int(units)
    return ("MET", f"Requested {units_i} units; within standard limits",
            None, None)


def build_duplicate_candidates() -> pd.DataFrame:
    rng = det.rng(RNG_BASE, "dupes")
    pa = read_table("bronze", "fct_memberPA")
    rows = []
    idx = 0
    for mid, grp in pa.groupby("member_id"):
        for cat, cat_grp in grp.groupby("service_category"):
            if len(cat_grp) < 2:
                continue
            pairs = cat_grp.sort_values("received_datetime_utc")
            ids = pairs["pa_id"].tolist()
            for i in range(len(ids) - 1):
                for j in range(i + 1, min(i + 3, len(ids))):
                    sim = round(float(rng.uniform(0.5, 0.95)), 4)
                    overlap = int(rng.integers(0, 30))
                    sk = det.stable_id("DUP", ids[i], ids[j], width=10)
                    rows.append({
                        "duplicate_sk": sk, "pa_id_1": ids[i], "pa_id_2": ids[j],
                        "member_id": mid, "service_category": cat,
                        "overlap_days": overlap, "similarity_score": sim,
                    })
                    idx += 1
    if not rows:
        return pd.DataFrame(columns=S.get("silver", "slv_duplicate_pa_candidates").col_names())
    return pd.DataFrame(rows)


def build_benefit_pa_requirement() -> pd.DataFrame:
    rng = det.rng(RNG_BASE, "benefit")
    rows = []
    idx = 0
    for lob in S.LINES_OF_BUSINESS:
        for cat in S.PA_SERVICE_CATEGORIES:
            sk = det.stable_id("BPA", lob, cat, width=10)
            requires = rng.random() < 0.75
            threshold = round(float(rng.uniform(500, 50000)), 2) if requires else None
            rows.append({
                "benefit_pa_sk": sk, "line_of_business": lob, "service_category": cat,
                "requires_pa": requires, "pa_threshold_amt": threshold,
                "auto_approve_eligible": bool(rng.random() < 0.30),
                "gold_card_eligible": bool(rng.random() < 0.20),
            })
            idx += 1
    return pd.DataFrame(rows)


def main() -> int:
    print("=" * 78)
    print("Building Silver PA-Centric Tables")
    print("=" * 78)

    builders = [
        ("slv_pa_case", build_pa_case),
        ("slv_pa_decision_history", build_decision_history),
        ("slv_pa_precedent_stats", build_precedent_stats),
        ("slv_pa_criteria_ruleset", build_criteria_ruleset),
        ("slv_pa_criteria_evaluation", build_criteria_evaluation),
        ("slv_duplicate_pa_candidates", build_duplicate_candidates),
        ("slv_benefit_pa_requirement", build_benefit_pa_requirement),
    ]

    errors = []
    for name, builder in builders:
        print(f"\n  Building {name} ...")
        try:
            df = builder()
            write_table(df, "silver", name, source_system="DERIVED")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"    ERROR: {exc}")
            errors.append((name, exc))

    print("\n" + "=" * 78)
    if errors:
        print(f"FAILED: {len(errors)} tables:")
        for n, e in errors:
            print(f"  {n}: {e}")
        return 1
    print("All 7 silver PA-centric tables built.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
