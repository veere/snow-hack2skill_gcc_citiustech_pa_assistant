"""
Build silver provider/other tables.

4 tables: slv_provider_profile, slv_claims_summary, slv_utilization_flags,
slv_member_event_summary.
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

RNG_BASE = "silver_provider"


def build_provider_profile() -> pd.DataFrame:
    pa = read_table("bronze", "fct_memberPA")
    ref = dates.reference_date()
    cutoff = pd.Timestamp(ref) - pd.Timedelta(days=180)

    # Get unique ordering providers
    providers = pa[["ordering_provider_npi", "ordering_provider_name", "ordering_provider_specialty",
                     "network_status_at_submission"]].drop_duplicates(subset=["ordering_provider_npi"])

    rows = []
    for _, p in providers.iterrows():
        npi = p["ordering_provider_npi"]
        if pd.isna(npi):
            continue
        prov_pas = pa[pa["ordering_provider_npi"] == npi]
        recent = prov_pas[pd.to_datetime(prov_pas["received_datetime_utc"], errors="coerce") >= cutoff]
        total_6mo = len(recent)
        approved_6mo = len(recent[recent["pa_status"].isin(["APPROVED", "PARTIALLY_APPROVED"])])
        rate = round(approved_6mo / total_6mo * 100, 2) if total_6mo > 0 else None
        gc_eligible = (rate is not None and rate >= S.GOLD_CARD_APPROVAL_THRESHOLD_PCT
                       and total_6mo >= S.GOLD_CARD_MIN_VOLUME)
        vol_met = total_6mo >= S.GOLD_CARD_MIN_VOLUME

        rows.append({
            "provider_npi": npi,
            "provider_name": p.get("ordering_provider_name"),
            "provider_specialty": p.get("ordering_provider_specialty"),
            "network_status": p.get("network_status_at_submission"),
            "total_pa_requests_6mo": total_6mo,
            "approved_pa_6mo": approved_6mo,
            "approval_rate_6mo_pct": rate,
            "gold_card_eligible": gc_eligible,
            "gold_card_min_volume_met": vol_met,
        })
    return pd.DataFrame(rows)


def build_claims_summary() -> pd.DataFrame:
    txn = read_table("bronze", "fct_memberTransactions")
    claims = txn[txn["transaction_type"].str.startswith("CLAIM", na=False)]

    agg = claims.groupby("member_id").agg(
        total_claims=("transaction_id", "count"),
        professional_claims=("transaction_type", lambda x: (x == "CLAIM_PROFESSIONAL").sum()),
        institutional_claims=("transaction_type", lambda x: (x == "CLAIM_INSTITUTIONAL").sum()),
        pharmacy_claims=("transaction_type", lambda x: (x == "CLAIM_PHARMACY").sum()),
        total_billed_amt=("billed_amt", lambda x: x.fillna(0).sum()),
        total_allowed_amt=("allowed_amt", lambda x: x.fillna(0).sum()),
        total_plan_paid_amt=("plan_paid_amt", lambda x: x.fillna(0).sum()),
        total_member_paid_amt=("member_responsibility_amt", lambda x: x.fillna(0).sum()),
        denied_claims=("claim_status", lambda x: (x == "DENIED").sum()),
    ).reset_index()

    # ER and inpatient counts from encounters
    er = claims[claims["place_of_service_code"] == "23"]
    er_counts = er.groupby("member_id").size().reset_index(name="er_visit_count")
    inpt = claims[claims["transaction_type"] == "CLAIM_INSTITUTIONAL"]
    inpt_counts = inpt.groupby("member_id").size().reset_index(name="inpatient_admit_count")

    agg = agg.merge(er_counts, on="member_id", how="left")
    agg = agg.merge(inpt_counts, on="member_id", how="left")
    agg["er_visit_count"] = agg["er_visit_count"].fillna(0).astype(int)
    agg["inpatient_admit_count"] = agg["inpatient_admit_count"].fillna(0).astype(int)

    return agg


def build_utilization_flags() -> pd.DataFrame:
    rng = det.rng(RNG_BASE, "util")
    claims_sum = read_table("silver", "slv_claims_summary")

    p95 = claims_sum["total_allowed_amt"].quantile(0.95) if len(claims_sum) > 0 else 0

    rows = []
    for _, r in claims_sum.iterrows():
        er = int(r.get("er_visit_count", 0))
        inpt = int(r.get("inpatient_admit_count", 0))
        allowed = float(r.get("total_allowed_amt", 0))

        rows.append({
            "member_id": r["member_id"],
            "high_er_utilizer_flag": er >= 3,
            "opioid_mgmt_flag": bool(rng.random() < 0.08),
            "case_mgmt_open_flag": bool(rng.random() < 0.12),
            "readmission_risk_flag": inpt >= 2 or bool(rng.random() < 0.05),
            "high_cost_flag": allowed >= p95,
            "er_visits_12mo": er,
            "inpatient_admits_12mo": inpt,
        })
    return pd.DataFrame(rows)


def build_event_summary() -> pd.DataFrame:
    evt = read_table("bronze", "fct_memberEvents")

    agg = evt.groupby("member_id").agg(
        total_events=("event_id", "count"),
        call_count=("channel", lambda x: (x == "PHONE").sum()),
        portal_count=("channel", lambda x: (x == "PORTAL").sum()),
        mail_count=("channel", lambda x: (x == "MAIL").sum()),
        appeal_count=("event_category", lambda x: (x == "APPEAL_FILED").sum()),
        grievance_count=("event_category", lambda x: (x == "GRIEVANCE_FILED").sum()),
        pa_related_event_count=("related_pa_id", lambda x: x.notna().sum()),
        avg_sentiment_score=("sentiment_score", "mean"),
        escalated_count=("escalated_flag", "sum"),
    ).reset_index()

    agg["avg_sentiment_score"] = agg["avg_sentiment_score"].round(3)
    return agg


def main() -> int:
    print("=" * 78)
    print("Building Silver Provider/Other Tables")
    print("=" * 78)

    builders = [
        ("slv_provider_profile", build_provider_profile),
        ("slv_claims_summary", build_claims_summary),
        ("slv_utilization_flags", build_utilization_flags),
        ("slv_member_event_summary", build_event_summary),
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
    print("All 4 silver provider/other tables built.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
