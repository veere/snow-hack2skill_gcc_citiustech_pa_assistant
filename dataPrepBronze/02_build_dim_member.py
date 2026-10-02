"""
Build the four member dimension tables from Synthea patients + payer data.

    dim_memberProfile   <- patients.csv  (one row per member)
    dim_memberPlan      <- payer_transitions.csv + payers.csv  (coverage spans)
    dim_memberFinance   <- derived from plan spans, calibrated to SynPUF benchmarks
    dim_memberHistory   <- enrollment change log with coverage gaps + retro terms

All dates are shifted by the deterministic date anchor so SLA clocks are live.
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

from common import determinism as det
from common import dates as anchor
from common import identity
from common.frames import write_table, read_table
from config import schemas as S

CACHE = REPO_ROOT / ".cache" / "synthea"

# ── Payer → LOB mapping ──────────────────────────────────────────────────────

PAYER_LOB_MAP: dict[str, str] = {
    "Medicare": "MEDICARE_ADVANTAGE",
    "Dual Eligible": "MEDICARE_ADVANTAGE",
    "Medicaid": "MEDICAID",
    "Humana": "COMMERCIAL",
    "Blue Cross Blue Shield": "COMMERCIAL",
    "UnitedHealthcare": "COMMERCIAL",
    "Aetna": "COMMERCIAL",
    "Cigna Health": "COMMERCIAL",
    "Anthem": "MARKETPLACE",
}

PAYER_PRODUCT_MAP: dict[str, str] = {
    "Medicare": "HMO",
    "Dual Eligible": "HMO",
    "Medicaid": "HMO",
    "Humana": "PPO",
    "Blue Cross Blue Shield": "PPO",
    "UnitedHealthcare": "HMO",
    "Aetna": "EPO",
    "Cigna Health": "POS",
    "Anthem": "PPO",
}

US_STATE_ABBREV: dict[str, str] = {
    "Massachusetts": "MA", "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ",
    "Arkansas": "AR", "California": "CA", "Colorado": "CO", "Connecticut": "CT",
    "Delaware": "DE", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND",
    "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY", "District of Columbia": "DC",
}

METAL_TIERS = ("BRONZE", "SILVER", "GOLD", "PLATINUM")
LANGUAGES = ("English", "Spanish", "Portuguese", "Mandarin", "Vietnamese",
             "Arabic", "French", "Haitian Creole", "Russian", "Korean")


# ═════════════════════════════════════════════════════════════════════════════
# 1) dim_memberProfile
# ═════════════════════════════════════════════════════════════════════════════

def build_dim_memberProfile() -> pd.DataFrame:
    rng = det.rng("dim_memberProfile")
    patients = pd.read_csv(CACHE / "patients.csv", dtype=str)
    ref_date = anchor.reference_date()

    rows = []
    for idx, p in patients.iterrows():
        pat_id = p["Id"]
        member_id = identity.member_id(pat_id)

        dob_shifted = anchor.shift_scalar(p["BIRTHDATE"])
        dob_date = dob_shifted.date() if dob_shifted else None
        age = (ref_date - dob_date).days // 365 if dob_date else None

        dod_shifted = anchor.shift_scalar(p.get("DEATHDATE"))
        dod_date = dod_shifted.date() if dod_shifted and not pd.isna(dod_shifted) else None

        state_full = str(p.get("STATE", "")) if pd.notna(p.get("STATE")) else ""
        state_abbrev = US_STATE_ABBREV.get(state_full, state_full[:2].upper() if state_full else None)

        gender_raw = str(p.get("GENDER", "U")).upper()
        gender = gender_raw if gender_raw in ("M", "F") else "U"

        ssn_raw = str(p.get("SSN", "")) if pd.notna(p.get("SSN")) else ""
        ssn_last4 = ssn_raw.replace("-", "")[-4:] if len(ssn_raw) >= 4 else None

        lang = rng.choice(LANGUAGES, p=[0.75, 0.10, 0.04, 0.02, 0.02,
                                         0.02, 0.02, 0.01, 0.01, 0.01])
        zip_code = str(p.get("ZIP", "")).zfill(5) if pd.notna(p.get("ZIP")) else None

        income = float(p["HEALTHCARE_EXPENSES"]) * rng.uniform(0.8, 3.5) if pd.notna(p.get("HEALTHCARE_EXPENSES")) else None

        rows.append({
            "member_id": member_id,
            "synthea_patient_id": pat_id,
            "subscriber_id": det.stable_id("SUB-", pat_id, width=6),
            "relationship_to_subscriber": "SELF",
            "mbi": None,
            "ssn_last4": ssn_last4,
            "first_name": p.get("FIRST", "Unknown"),
            "middle_name": None,
            "last_name": p.get("LAST", "Unknown"),
            "name_suffix": p.get("SUFFIX") if pd.notna(p.get("SUFFIX")) else None,
            "date_of_birth": dob_date,
            "age_years": max(0, min(120, age)) if age is not None else 0,
            "date_of_death": dod_date,
            "deceased_flag": dod_date is not None,
            "gender": gender,
            "race": p.get("RACE") if pd.notna(p.get("RACE")) else None,
            "ethnicity": p.get("ETHNICITY") if pd.notna(p.get("ETHNICITY")) else None,
            "marital_status": p.get("MARITAL") if pd.notna(p.get("MARITAL")) else None,
            "preferred_language": lang,
            "interpreter_needed_flag": lang != "English",
            "address_line1": p.get("ADDRESS") if pd.notna(p.get("ADDRESS")) else None,
            "city": p.get("CITY") if pd.notna(p.get("CITY")) else None,
            "county": p.get("COUNTY") if pd.notna(p.get("COUNTY")) else None,
            "state": state_abbrev if state_abbrev else None,
            "zip_code": zip_code,
            "latitude": float(p["LAT"]) if pd.notna(p.get("LAT")) else None,
            "longitude": float(p["LON"]) if pd.notna(p.get("LON")) else None,
            "phone": f"({rng.integers(200,999)}) {rng.integers(200,999)}-{rng.integers(1000,9999):04d}",
            "email": f"{p.get('FIRST','x').lower().replace(' ','')}.{p.get('LAST','x').lower().replace(' ','')}@example.com",
            "annual_income_amt": round(income, 2) if income else None,
            "healthcare_expenses_lifetime_amt": float(p["HEALTHCARE_EXPENSES"]) if pd.notna(p.get("HEALTHCARE_EXPENSES")) else None,
            "healthcare_coverage_lifetime_amt": float(p["HEALTHCARE_COVERAGE"]) if pd.notna(p.get("HEALTHCARE_COVERAGE")) else None,
            "is_current": True,
        })

    df = pd.DataFrame(rows)
    print(f"  dim_memberProfile: {len(df)} rows from {len(patients)} Synthea patients")
    return df


# ═════════════════════════════════════════════════════════════════════════════
# 2) dim_memberPlan
# ═════════════════════════════════════════════════════════════════════════════

def build_dim_memberPlan(profile_df: pd.DataFrame) -> pd.DataFrame:
    rng = det.rng("dim_memberPlan")
    transitions = pd.read_csv(CACHE / "payer_transitions.csv", dtype=str)
    payers = pd.read_csv(CACHE / "payers.csv", dtype=str)

    payer_map = dict(zip(payers["Id"], payers["NAME"]))
    member_lookup = dict(zip(profile_df["synthea_patient_id"], profile_df["member_id"]))
    state_lookup = dict(zip(profile_df["member_id"], profile_df["state"]))

    ref_date = anchor.reference_date()
    offset = anchor.offset_days()
    rows = []

    for _, t in transitions.iterrows():
        pat_id = t["PATIENT"]
        member_id = member_lookup.get(pat_id)
        if not member_id:
            continue

        payer_name = payer_map.get(t["PAYER"], "Unknown")
        if payer_name == "NO_INSURANCE":
            continue

        lob = PAYER_LOB_MAP.get(payer_name, "COMMERCIAL")
        product = PAYER_PRODUCT_MAP.get(payer_name, "PPO")
        tat = S.TAT_RULES[lob]

        start_year = int(t["START_YEAR"])
        end_year = int(t["END_YEAR"]) if pd.notna(t.get("END_YEAR")) and t["END_YEAR"] != "" else None

        start_date_raw = date(start_year, 1, 1)
        start_date_shifted = start_date_raw + timedelta(days=offset)
        if end_year and end_year > 0:
            end_date_raw = date(end_year, 12, 31)
            end_date_shifted = end_date_raw + timedelta(days=offset)
        else:
            end_date_shifted = None

        is_current = end_date_shifted is None or end_date_shifted >= ref_date
        status = "ACTIVE" if is_current else "TERMED"

        state = state_lookup.get(member_id, "MA")
        plan_id = det.stable_id("PLN-", payer_name, lob, width=6)
        payer_id = det.stable_id("PYR-", payer_name, width=6)

        sk = det.stable_id("MPS-", member_id, plan_id, str(start_date_shifted), width=12)

        pharm_carveout = bool(rng.random() < 0.15) if lob == "COMMERCIAL" else False
        bh_carveout = bool(rng.random() < 0.10) if lob == "COMMERCIAL" else False

        metal = None
        if lob == "MARKETPLACE":
            metal = str(rng.choice(METAL_TIERS))

        pcp_npi = None
        pcp_name = None
        if product in ("HMO", "POS"):
            pcp_npi = det.synthetic_npi("pcp", member_id, str(start_date_shifted))
            pcp_name = f"Dr. PCP-{pcp_npi[-4:]}"

        rows.append({
            "member_plan_sk": sk,
            "member_id": member_id,
            "plan_id": plan_id,
            "plan_name": f"{payer_name} {lob.replace('_',' ').title()} {product}",
            "payer_id": payer_id,
            "payer_name": payer_name,
            "line_of_business": lob,
            "product_type": product,
            "metal_tier": metal,
            "group_number": det.stable_id("GRP-", payer_name, width=6) if lob == "COMMERCIAL" else None,
            "group_name": f"{payer_name} Employer Group" if lob == "COMMERCIAL" else None,
            "coverage_start_date": start_date_shifted,
            "coverage_end_date": end_date_shifted,
            "is_current": is_current,
            "enrollment_status": status,
            "pcp_npi": pcp_npi,
            "pcp_name": pcp_name,
            "pharmacy_carveout_flag": pharm_carveout,
            "pharmacy_pbm_name": "Express Scripts" if pharm_carveout else None,
            "behavioral_health_carveout_flag": bh_carveout,
            "behavioral_health_vendor_name": "Optum BH" if bh_carveout else None,
            "state_of_issue": state if state else "MA",
            "regulatory_tat_standard_days": tat["standard_days"],
            "regulatory_tat_urgent_hours": tat["expedited_hours"],
            "tat_authority": tat["authority"],
            "pa_ruleset_id": f"RS-{lob[:4]}-2026-01",
        })

    df = pd.DataFrame(rows)
    df = _make_book_of_business_current(df, ref_date, rng)
    print(f"  dim_memberPlan: {len(df)} rows from {len(transitions)} payer transitions")
    return df


def _make_book_of_business_current(
    df: pd.DataFrame, ref_date: date, rng
) -> pd.DataFrame:
    """Extend each living member's most recent coverage span to remain open.

    WHY: Synthea payer transitions end whenever a patient's record ends, which for
    most patients is years before the newest encounter in the population. After the
    date-anchor shift only 28 of 1,171 members still had coverage spanning the
    reference date - so 97.6% of the book looked terminated.

    That is fatal for a prior-authorization mart. "Is this member eligible on the
    date of service?" is the single most common question an analyst asks, and PA
    requests are raised for active members. A book of business that is almost
    entirely termed makes the eligibility questions unanswerable and every PA case
    look invalid.

    A real payer's book is mostly active. Each living member's latest span is
    therefore left open-ended, except for a deliberate ~7% who stay genuinely
    terminated so that eligibility denials and coverage-gap scenarios remain
    present and findable.
    """
    if df.empty:
        return df

    TERMED_SHARE = 0.07

    df = df.sort_values(["member_id", "coverage_start_date"]).reset_index(drop=True)
    latest_idx = df.groupby("member_id", sort=False).tail(1).index

    n_extended = 0
    n_kept_termed = 0

    for idx in latest_idx:
        end = df.at[idx, "coverage_end_date"]
        if end is not None and not pd.isna(end) and end < ref_date:
            member = df.at[idx, "member_id"]
            # Deterministic per-member draw, so the same members stay termed on
            # every rerun.
            if rng.random() < TERMED_SHARE:
                n_kept_termed += 1
                continue
            df.at[idx, "coverage_end_date"] = None
            df.at[idx, "is_current"] = True
            df.at[idx, "enrollment_status"] = "ACTIVE"
            n_extended += 1

    total_members = df["member_id"].nunique()
    n_current = df[df["is_current"].astype(bool)]["member_id"].nunique()
    print(
        f"    book of business: extended {n_extended} members to open coverage, "
        f"kept {n_kept_termed} terminated -> {n_current}/{total_members} active "
        f"({100 * n_current / total_members:.1f}%)"
    )
    return df


# ═════════════════════════════════════════════════════════════════════════════
# 3) dim_memberFinance
# ═════════════════════════════════════════════════════════════════════════════

def _load_synpuf_benchmarks() -> dict[str, dict]:
    bench_df = read_table("bronze", "ref_synpuf_benchmark")
    out: dict[str, dict] = {}
    for _, r in bench_df.iterrows():
        metric = r["metric_name"]
        out[metric] = {
            "p25": float(r["p25"]) if pd.notna(r["p25"]) else 0,
            "p50": float(r["p50"]) if pd.notna(r["p50"]) else 0,
            "p75": float(r["p75"]) if pd.notna(r["p75"]) else 0,
            "mean": float(r["mean_value"]) if pd.notna(r["mean_value"]) else 0,
        }
    return out


def build_dim_memberFinance(plan_df: pd.DataFrame) -> pd.DataFrame:
    rng = det.rng("dim_memberFinance")
    benchmarks = _load_synpuf_benchmarks()
    ref_date = anchor.reference_date()
    current_year = ref_date.year

    lob_deductible_ranges: dict[str, tuple[float, float]] = {
        "MEDICARE_ADVANTAGE": (200, 500),
        "MEDICAID": (0, 100),
        "COMMERCIAL": (500, 5000),
        "MARKETPLACE": (1000, 8000),
    }
    lob_oop_ranges: dict[str, tuple[float, float]] = {
        "MEDICARE_ADVANTAGE": (3000, 7550),
        "MEDICAID": (0, 2000),
        "COMMERCIAL": (3000, 9100),
        "MARKETPLACE": (4000, 9100),
    }

    ip_bench = benchmarks.get("medreimb_ip", {"p50": 0, "p75": 0, "mean": 2200})
    op_bench = benchmarks.get("medreimb_op", {"p50": 20, "p75": 550, "mean": 622})
    car_bench = benchmarks.get("medreimb_car", {"p50": 610, "p75": 1650, "mean": 1162})

    rows = []
    # The declared grain is one row per member per plan per benefit year, but this
    # loop walks coverage SPANS. A member with two spans on the same plan covering
    # the same year would emit that year twice, duplicating the primary key and
    # double-counting the member's accumulators. Tracking the natural key keeps the
    # output at its declared grain.
    seen_finance_keys: set[tuple[str, str, int]] = set()
    for _, p in plan_df.iterrows():
        lob = p["line_of_business"]
        member_id = p["member_id"]
        plan_id = p["plan_id"]

        start = p["coverage_start_date"]
        end = p["coverage_end_date"]
        if isinstance(start, str):
            start = date.fromisoformat(start)
        if isinstance(end, str) and end:
            end = date.fromisoformat(end)
        elif pd.isna(end):
            end = None

        start_year = start.year if start else current_year
        end_year = end.year if end else current_year

        for yr in range(max(start_year, current_year - 2), min(end_year, current_year) + 1):
            key = (member_id, plan_id, yr)
            if key in seen_finance_keys:
                continue
            seen_finance_keys.add(key)
            sk = det.stable_id("MFS-", member_id, plan_id, str(yr), width=12)
            is_current_yr = (yr == current_year)

            ded_lo, ded_hi = lob_deductible_ranges[lob]
            deductible = round(rng.integers(int(ded_lo / 50), int(ded_hi / 50) + 1) * 50, 2)

            oop_lo, oop_hi = lob_oop_ranges[lob]
            oop_max = round(rng.integers(int(oop_lo / 100), int(oop_hi / 100) + 1) * 100, 2)

            utilization_factor = float(rng.beta(2, 5))

            mean_total = ip_bench["mean"] + op_bench["mean"] + car_bench["mean"]
            ytd_allowed = round(mean_total * utilization_factor * rng.uniform(0.3, 2.5), 2)
            coinsurance_pct = round(float(rng.choice([10, 15, 20, 25, 30])), 2)
            ded_met = round(min(deductible, ytd_allowed * 0.4), 2)
            ded_remaining = round(deductible - ded_met, 2)
            member_paid = round(ded_met + (ytd_allowed - ded_met) * coinsurance_pct / 100, 2)
            plan_paid = round(ytd_allowed - member_paid, 2)
            oop_met = round(min(oop_max, member_paid), 2)
            oop_remaining = round(oop_max - oop_met, 2)

            premium = round(rng.uniform(100, 800) if lob != "MEDICAID" else 0, 2)
            months_in = min(12, max(1, ref_date.month)) if is_current_yr else 12
            paid_through = date(yr, min(12, months_in), 28) if is_current_yr else date(yr, 12, 31)
            delinquent = bool(rng.random() < 0.03) if premium > 0 else False

            rows.append({
                "member_finance_sk": sk,
                "member_id": member_id,
                "plan_id": plan_id,
                "benefit_year": yr,
                "deductible_individual_amt": deductible,
                "deductible_met_amt": ded_met,
                "deductible_remaining_amt": ded_remaining,
                "deductible_met_flag": ded_met >= deductible,
                "oop_max_individual_amt": oop_max,
                "oop_met_amt": oop_met,
                "oop_remaining_amt": oop_remaining,
                "oop_max_met_flag": oop_met >= oop_max,
                "coinsurance_pct": coinsurance_pct,
                "pcp_copay_amt": round(float(rng.choice([15, 20, 25, 30, 40])), 2),
                "specialist_copay_amt": round(float(rng.choice([30, 40, 50, 60, 75])), 2),
                "er_copay_amt": round(float(rng.choice([100, 150, 200, 250, 300])), 2),
                "urgent_care_copay_amt": round(float(rng.choice([25, 35, 50, 75])), 2),
                "inpatient_copay_per_day_amt": round(float(rng.choice([0, 100, 200, 350, 500])), 2),
                "inpatient_copay_max_days": int(rng.choice([0, 3, 5, 7, 10])),
                "rx_deductible_amt": round(float(rng.choice([0, 0, 50, 100, 150, 250])), 2),
                "rx_tier1_copay_amt": round(float(rng.choice([0, 5, 10, 15])), 2),
                "rx_tier2_copay_amt": round(float(rng.choice([20, 30, 40, 50])), 2),
                "rx_tier3_copay_amt": round(float(rng.choice([50, 60, 75, 100])), 2),
                "rx_specialty_coinsurance_pct": round(float(rng.choice([20, 25, 30, 33])), 2),
                "premium_monthly_amt": premium,
                "premium_paid_through_date": paid_through,
                "premium_delinquent_flag": delinquent,
                "grace_period_end_date": (paid_through + timedelta(days=90)) if delinquent else None,
                "hsa_fsa_balance_amt": round(float(rng.uniform(0, 3000)), 2) if lob in ("COMMERCIAL", "MARKETPLACE") else None,
                "ytd_allowed_amt": ytd_allowed,
                "ytd_plan_paid_amt": max(0, plan_paid),
                "ytd_member_paid_amt": member_paid,
                "is_current": is_current_yr,
            })

    df = pd.DataFrame(rows)
    print(f"  dim_memberFinance: {len(df)} rows across {df['benefit_year'].nunique()} benefit years")
    return df


# ═════════════════════════════════════════════════════════════════════════════
# 4) dim_memberHistory
# ═════════════════════════════════════════════════════════════════════════════

def build_dim_memberHistory(plan_df: pd.DataFrame) -> pd.DataFrame:
    rng = det.rng("dim_memberHistory")
    offset = anchor.offset_days()

    CHANGE_REASONS = {
        "ENROLL": ("CR01", "New enrollment"),
        "TERM": ("CR02", "Voluntary termination"),
        "REINSTATE": ("CR03", "Coverage reinstated"),
        "PLAN_CHANGE": ("CR04", "Plan transfer"),
        "ADDRESS_CHANGE": ("CR05", "Address update"),
        "PCP_CHANGE": ("CR06", "PCP reassignment"),
        "RETRO_TERM": ("CR07", "Retroactive termination"),
        "DEATH": ("CR08", "Death of member"),
    }

    sorted_plan = plan_df.sort_values(["member_id", "coverage_start_date"]).reset_index(drop=True)
    rows = []

    for member_id, group in sorted_plan.groupby("member_id"):
        seq = 0
        prev_end: date | None = None

        for i, (_, p) in enumerate(group.iterrows()):
            start = p["coverage_start_date"]
            end = p["coverage_end_date"]
            if isinstance(start, str):
                start = date.fromisoformat(start)
            if isinstance(end, str) and end:
                end = date.fromisoformat(end)
            elif pd.isna(end):
                end = None

            # coverage gap detection
            gap_days = 0
            creates_gap = False
            if prev_end is not None and start > prev_end + timedelta(days=1):
                gap_days = (start - prev_end).days - 1
                creates_gap = True

            # ENROLL event
            seq += 1
            retro = bool(rng.random() < 0.05)
            processed = start + timedelta(days=int(rng.integers(0, 15))) if retro else start
            rows.append({
                "member_history_sk": det.stable_id("MHS-", member_id, str(seq), width=12),
                "member_id": member_id,
                "sequence_number": seq,
                "change_type": "ENROLL",
                "effective_start_date": start,
                "effective_end_date": end,
                "processed_date": processed,
                "enrollment_status": "ACTIVE",
                "changed_attribute": "coverage",
                "prior_value": "NO_COVERAGE" if i == 0 else "TERMED",
                "new_value": p["plan_id"],
                "change_reason_code": CHANGE_REASONS["ENROLL"][0],
                "change_reason_desc": CHANGE_REASONS["ENROLL"][1],
                "plan_id": p["plan_id"],
                "retroactive_flag": retro,
                "retroactive_days": (processed - start).days if retro and processed > start else 0,
                "coverage_gap_days": gap_days,
                "creates_coverage_gap_flag": creates_gap,
            })

            # mid-span events
            if end and (end - start).days > 365:
                n_changes = int(rng.integers(0, 3))
                for c in range(n_changes):
                    change_type = str(rng.choice(["ADDRESS_CHANGE", "PCP_CHANGE", "PLAN_CHANGE"]))
                    days_in = int(rng.integers(90, max(91, (end - start).days - 30)))
                    event_date = start + timedelta(days=days_in)
                    seq += 1
                    rows.append({
                        "member_history_sk": det.stable_id("MHS-", member_id, str(seq), width=12),
                        "member_id": member_id,
                        "sequence_number": seq,
                        "change_type": change_type,
                        "effective_start_date": event_date,
                        "effective_end_date": None,
                        "processed_date": event_date,
                        "enrollment_status": "ACTIVE",
                        "changed_attribute": change_type.lower().replace("_change", ""),
                        "prior_value": "previous_value",
                        "new_value": "updated_value",
                        "change_reason_code": CHANGE_REASONS.get(change_type, ("CR99", "Other"))[0],
                        "change_reason_desc": CHANGE_REASONS.get(change_type, ("CR99", "Other"))[1],
                        "plan_id": p["plan_id"],
                        "retroactive_flag": False,
                        "retroactive_days": 0,
                        "coverage_gap_days": 0,
                        "creates_coverage_gap_flag": False,
                    })

            # TERM event
            if end:
                seq += 1
                is_retro_term = bool(rng.random() < 0.08)
                if is_retro_term:
                    change_type = "RETRO_TERM"
                    retro_lag = int(rng.integers(5, 60))
                    proc_date = end + timedelta(days=retro_lag)
                else:
                    change_type = "TERM"
                    retro_lag = 0
                    proc_date = end

                rows.append({
                    "member_history_sk": det.stable_id("MHS-", member_id, str(seq), width=12),
                    "member_id": member_id,
                    "sequence_number": seq,
                    "change_type": change_type,
                    "effective_start_date": end,
                    "effective_end_date": None,
                    "processed_date": proc_date,
                    "enrollment_status": "TERMED",
                    "changed_attribute": "coverage",
                    "prior_value": p["plan_id"],
                    "new_value": "NO_COVERAGE",
                    "change_reason_code": CHANGE_REASONS.get(change_type, ("CR02", "Termination"))[0],
                    "change_reason_desc": CHANGE_REASONS.get(change_type, ("CR02", "Termination"))[1],
                    "plan_id": p["plan_id"],
                    "retroactive_flag": is_retro_term,
                    "retroactive_days": retro_lag,
                    "coverage_gap_days": 0,
                    "creates_coverage_gap_flag": False,
                })

            prev_end = end

    df = pd.DataFrame(rows)
    print(f"  dim_memberHistory: {len(df)} rows for {df['member_id'].nunique()} members")
    return df


# ═════════════════════════════════════════════════════════════════════════════
# Main
# ═════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("=" * 78)
    print("02_build_dim_member.py  — member dimension tables")
    print("=" * 78)
    print(f"  {anchor.describe()}")
    print()

    errors = []
    builders = [
        ("dim_memberProfile", build_dim_memberProfile, "SYNTHEA"),
    ]

    profile_df = None
    plan_df = None

    # 1) Profile
    try:
        profile_df = build_dim_memberProfile()
        write_table(profile_df, "bronze", "dim_memberProfile", source_system="SYNTHEA")
    except Exception as exc:
        print(f"\n  ERROR building dim_memberProfile: {exc}")
        errors.append(("dim_memberProfile", exc))
        return 1

    # 2) Plan (depends on profile)
    try:
        plan_df = build_dim_memberPlan(profile_df)
        write_table(plan_df, "bronze", "dim_memberPlan", source_system="SYNTHEA")
    except Exception as exc:
        print(f"\n  ERROR building dim_memberPlan: {exc}")
        errors.append(("dim_memberPlan", exc))
        return 1

    # 3) Finance (depends on plan + SynPUF benchmarks)
    try:
        finance_df = build_dim_memberFinance(plan_df)
        write_table(finance_df, "bronze", "dim_memberFinance", source_system="SYNTHETIC")
    except Exception as exc:
        print(f"\n  ERROR building dim_memberFinance: {exc}")
        errors.append(("dim_memberFinance", exc))
        return 1

    # 4) History (depends on plan)
    try:
        history_df = build_dim_memberHistory(plan_df)
        write_table(history_df, "bronze", "dim_memberHistory", source_system="SYNTHETIC")
    except Exception as exc:
        print(f"\n  ERROR building dim_memberHistory: {exc}")
        errors.append(("dim_memberHistory", exc))
        return 1

    print("\n" + "=" * 78)
    if errors:
        print(f"FAILED: {len(errors)} tables could not be built:")
        for name, exc in errors:
            print(f"  {name}: {exc}")
        return 1

    print("All 4 member dimension tables built successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
