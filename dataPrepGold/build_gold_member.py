"""Build gold member/provider/KPI tables from silver data."""

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

RNG_BASE = "gold_member"


def build_eligibility_snapshot() -> pd.DataFrame:
    master = read_table("silver", "slv_member_master")
    coverage = read_table("silver", "slv_member_coverage_span")
    current_cov = coverage[coverage["is_current"] == True].drop_duplicates(subset=["member_id"])
    cov_map = current_cov.set_index("member_id")
    any_gap = coverage[coverage["has_gap_before"] == True].groupby("member_id").size()

    rows = []
    for _, m in master.iterrows():
        mid = m["member_id"]
        c = cov_map.loc[mid] if mid in cov_map.index else pd.Series()
        rows.append({
            "member_id": mid,
            "member_name": f"{m.get('first_name','')} {m.get('last_name','')}",
            "date_of_birth": m.get("date_of_birth"),
            "age_years": m.get("age_years"),
            "gender": m.get("gender"),
            "enrollment_status": m.get("enrollment_status"),
            "line_of_business": m.get("line_of_business"),
            "plan_id": c.get("plan_id") if not c.empty else None,
            "coverage_start_date": c.get("coverage_start_date") if not c.empty else None,
            "is_eligible_today": m.get("enrollment_status") == "ACTIVE",
            "has_coverage_gap": mid in any_gap,
        })
    return pd.DataFrame(rows)


def build_clinical_summary() -> pd.DataFrame:
    master = read_table("silver", "slv_member_master")
    conditions = read_table("silver", "slv_member_condition_profile")
    meds = read_table("silver", "slv_member_medication_history")
    labs = read_table("silver", "slv_member_lab_vitals_latest")
    conservative = read_table("silver", "slv_member_conservative_care")
    imaging = read_table("silver", "slv_member_imaging_history")

    active_cond = conditions[conditions["is_active"] == True].groupby("member_id").size()
    chronic_cond = conditions[conditions["is_chronic"] == True].groupby("member_id").size()
    active_meds = meds.groupby("member_id").size()
    labs_map = labs.set_index("member_id")
    cons_agg = conservative.groupby("member_id")["total_conservative_visits"].sum()
    img_agg = imaging.groupby("member_id")["study_count"].sum()

    rows = []
    for _, m in master.iterrows():
        mid = m["member_id"]
        rows.append({
            "member_id": mid,
            "active_condition_count": int(active_cond.get(mid, 0)),
            "chronic_condition_count": int(chronic_cond.get(mid, 0)),
            "active_medication_count": int(active_meds.get(mid, 0)),
            "bmi_value": labs_map.loc[mid]["bmi_value"] if mid in labs_map.index else None,
            "hba1c_value": labs_map.loc[mid]["hba1c_value"] if mid in labs_map.index else None,
            "total_conservative_visits": int(cons_agg.get(mid, 0)),
            "imaging_study_count": int(img_agg.get(mid, 0)),
        })
    return pd.DataFrame(rows)


def build_financial_position_gold() -> pd.DataFrame:
    fin = read_table("silver", "slv_member_financial_position")
    claims = read_table("silver", "slv_claims_summary")
    current = fin[fin["is_current"] == True].drop_duplicates(subset=["member_id"])
    claims_map = claims.set_index("member_id")

    rows = []
    for _, f in current.iterrows():
        mid = f["member_id"]
        tc = int(claims_map.loc[mid]["total_claims"]) if mid in claims_map.index else 0
        rows.append({
            "member_id": mid,
            "deductible_remaining_amt": f.get("deductible_remaining_amt"),
            "oop_remaining_amt": f.get("oop_remaining_amt"),
            "ytd_plan_paid_amt": f.get("ytd_plan_paid_amt"),
            "ytd_member_paid_amt": f.get("ytd_member_paid_amt"),
            "total_claims_count": tc,
        })
    return pd.DataFrame(rows)


def build_timeline() -> pd.DataFrame:
    pa = read_table("bronze", "fct_memberPA")
    txn = read_table("bronze", "fct_memberTransactions")
    evt = read_table("bronze", "fct_memberEvents")

    rows = []
    idx = 0

    # PA events
    for _, r in pa.iterrows():
        sk = det.stable_id("TL", r["pa_id"], "PA", str(idx), width=10)
        idx += 1
        received = pd.to_datetime(r.get("received_datetime_utc"), errors="coerce")
        rows.append({
            "timeline_sk": sk, "member_id": r["member_id"],
            "event_date": received.date() if pd.notna(received) else dates.reference_date(),
            "event_type": "PA_RECEIVED",
            "event_desc": f"PA {r['pa_id']}: {r.get('service_category','')} - {r.get('pa_status','')}",
            "related_pa_id": r["pa_id"], "related_claim_id": None,
            "amount": r.get("estimated_allowed_amt"),
        })

    # Claims (sample to keep size manageable)
    claims = txn[txn["transaction_type"].str.startswith("CLAIM", na=False)].head(10000)
    for _, r in claims.iterrows():
        sk = det.stable_id("TL", r["transaction_id"], "CLM", str(idx), width=10)
        idx += 1
        sd = pd.to_datetime(r.get("service_start_date"), errors="coerce")
        rows.append({
            "timeline_sk": sk, "member_id": r["member_id"],
            "event_date": sd.date() if pd.notna(sd) else dates.reference_date(),
            "event_type": "CLAIM",
            "event_desc": f"{r.get('transaction_type','')}: {r.get('procedure_desc','')}",
            "related_pa_id": None, "related_claim_id": r.get("claim_id"),
            "amount": r.get("allowed_amt"),
        })

    # Member events (sample)
    for _, r in evt.head(10000).iterrows():
        sk = det.stable_id("TL", r["event_id"], "EVT", str(idx), width=10)
        idx += 1
        ed = pd.to_datetime(r.get("event_datetime_utc"), errors="coerce")
        rows.append({
            "timeline_sk": sk, "member_id": r["member_id"],
            "event_date": ed.date() if pd.notna(ed) else dates.reference_date(),
            "event_type": r.get("event_category", "CONTACT"),
            "event_desc": r.get("topic", "Member interaction"),
            "related_pa_id": r.get("related_pa_id"), "related_claim_id": None,
            "amount": None,
        })

    return pd.DataFrame(rows)


def build_provider_network_summary() -> pd.DataFrame:
    prov = read_table("silver", "slv_provider_profile")
    return prov[["provider_npi", "provider_name", "provider_specialty",
                  "network_status", "gold_card_eligible",
                  "approval_rate_6mo_pct", "total_pa_requests_6mo"]].copy()


def build_kpi_daily() -> pd.DataFrame:
    pa = read_table("bronze", "fct_memberPA")
    pa_case = read_table("silver", "slv_pa_case")

    pa["received_date"] = pd.to_datetime(pa["received_datetime_utc"], errors="coerce").dt.date
    pa["decision_date"] = pd.to_datetime(pa.get("decision_datetime_utc"), errors="coerce").dt.date

    # Vectorized aggregation by date
    recv_counts = pa.groupby("received_date").size().reset_index(name="total_received")
    recv_counts.columns = ["kpi_date", "total_received"]

    decided = pa.dropna(subset=["decision_date"])
    dec_counts = decided.groupby("decision_date").agg(
        total_decided=("pa_id", "count"),
        total_approved=("pa_status", lambda x: x.isin(["APPROVED", "PARTIALLY_APPROVED"]).sum()),
        total_denied=("pa_status", lambda x: (x == "DENIED").sum()),
        auto_adj_count=("auto_adjudicated_flag", "sum"),
    ).reset_index()
    dec_counts.columns = ["kpi_date"] + list(dec_counts.columns[1:])

    # TAT by decision date
    tat_merge = decided.merge(pa_case[["pa_id", "tat_business_days"]], on="pa_id", how="left")
    tat_avg = tat_merge.groupby("decision_date")["tat_business_days"].mean().reset_index()
    tat_avg.columns = ["kpi_date", "avg_tat_days"]

    # Merge all
    all_dates = pd.DataFrame({"kpi_date": pd.date_range(
        start=pa["received_date"].dropna().min(),
        end=dates.reference_date(), freq="D"
    ).date})

    result = all_dates.merge(recv_counts, on="kpi_date", how="left")
    result = result.merge(dec_counts, on="kpi_date", how="left")
    result = result.merge(tat_avg, on="kpi_date", how="left")

    result["total_received"] = result["total_received"].fillna(0).astype(int)
    result["total_decided"] = result["total_decided"].fillna(0).astype(int)
    result["total_approved"] = result["total_approved"].fillna(0).astype(int)
    result["total_denied"] = result["total_denied"].fillna(0).astype(int)
    auto_adj_count = result.get("auto_adj_count", pd.Series(0, index=result.index)).fillna(0)
    result["auto_adj_pct"] = (auto_adj_count / result["total_decided"].replace(0, np.nan) * 100).round(2)
    result["avg_tat_days"] = result["avg_tat_days"].round(2)

    # Open and breached as cumulative
    result["open_cases"] = 0
    result["breached_cases"] = 0
    breached_set = set(pa_case[pa_case["sla_state"] == "BREACHED"]["pa_id"])

    # Simple: count open cases as total received - total decided cumulative
    result["cum_received"] = result["total_received"].cumsum()
    result["cum_decided"] = result["total_decided"].cumsum()
    result["open_cases"] = (result["cum_received"] - result["cum_decided"]).clip(lower=0)
    result["breached_cases"] = len(breached_set)  # simplified

    result = result.drop(columns=["cum_received", "cum_decided", "auto_adj_count"], errors="ignore")
    return result


def main() -> int:
    print("=" * 78)
    print("Building Gold Member/Provider/KPI Tables")
    print("=" * 78)

    builders = [
        ("gold_member_eligibility_snapshot", build_eligibility_snapshot),
        ("gold_member_clinical_summary", build_clinical_summary),
        ("gold_member_financial_position", build_financial_position_gold),
        ("gold_member_timeline", build_timeline),
        ("gold_provider_network_summary", build_provider_network_summary),
        ("gold_pa_kpi_daily", build_kpi_daily),
    ]

    errors = []
    for name, builder in builders:
        print(f"\n  Building {name} ...")
        try:
            df = builder()
            write_table(df, "gold", name, source_system="DERIVED")
        except Exception as exc:
            import traceback
            traceback.print_exc()
            errors.append((name, exc))

    print("\n" + "=" * 78)
    if errors:
        print(f"FAILED: {len(errors)}:")
        for n, e in errors:
            print(f"  {n}: {e}")
        return 1
    print("All 6 gold member/provider/KPI tables built.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
