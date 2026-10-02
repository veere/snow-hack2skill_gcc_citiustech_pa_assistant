"""Build gold PA-centric tables from silver data."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import dates, determinism as det  # noqa: E402
from common.frames import read_table, write_table  # noqa: E402
from config import schemas as S  # noqa: E402

RNG_BASE = "gold_pa"


def build_pa_worklist() -> pd.DataFrame:
    pa_case = read_table("silver", "slv_pa_case")
    master = read_table("silver", "slv_member_master")
    pa_bronze = read_table("bronze", "fct_memberPA")
    crit_eval = read_table("silver", "slv_pa_criteria_evaluation")

    open_pa = pa_case[pa_case["is_open"] == True].copy()
    if open_pa.empty:
        open_pa = pa_case.head(100)

    name_map = master.set_index("member_id").apply(lambda r: f"{r.get('first_name','')} {r.get('last_name','')}", axis=1).to_dict()
    pa_info = pa_bronze.set_index("pa_id")

    crit_agg = crit_eval.groupby("pa_id").agg(
        criteria_met_count=("criteria_met_count", "max"),
        criteria_total_count=("criteria_total_count", "max"),
    ).to_dict("index")

    rows = []
    for _, r in open_pa.iterrows():
        pid = r["pa_id"]
        ca = crit_agg.get(pid, {})
        met = ca.get("criteria_met_count", 0) or 0
        total = ca.get("criteria_total_count", 1) or 1
        completeness = round(met / total * 100, 2)
        days_left = r.get("days_until_deadline")
        hours_left = days_left * 24 if pd.notna(days_left) else None
        priority = 100 - (days_left if pd.notna(days_left) else 50)
        if r.get("urgency_flag") == "EMERGENT":
            priority += 50
        elif r.get("urgency_flag") == "EXPEDITED":
            priority += 25
        priority = max(0, priority)

        pa_row = pa_info.loc[pid] if pid in pa_info.index else {}

        rows.append({
            "pa_id": pid, "member_id": r["member_id"],
            "member_name": name_map.get(r["member_id"], ""),
            "line_of_business": r["line_of_business"], "service_category": r["service_category"],
            "urgency_flag": r["urgency_flag"], "pa_status": r["pa_status"],
            "sla_state": r["sla_state"], "days_until_deadline": days_left,
            "hours_until_deadline": hours_left,
            "criteria_met_count": met, "criteria_total_count": total,
            "completeness_pct": completeness, "priority_score": round(priority, 2),
            "ordering_provider_name": pa_row.get("ordering_provider_name") if isinstance(pa_row, pd.Series) else None,
            "requested_procedure_desc": pa_row.get("requested_procedure_desc") if isinstance(pa_row, pd.Series) else None,
        })
    return pd.DataFrame(rows)


def build_pa_request_360() -> pd.DataFrame:
    pa = read_table("bronze", "fct_memberPA")
    master = read_table("silver", "slv_member_master")
    pa_case = read_table("silver", "slv_pa_case")
    crit_eval = read_table("silver", "slv_pa_criteria_evaluation")
    fin = read_table("silver", "slv_member_financial_position")

    m_info = master.set_index("member_id")
    sla_map = pa_case.set_index("pa_id")["sla_state"].to_dict()
    crit_agg = crit_eval.groupby("pa_id").agg(
        criteria_met_count=("criteria_met_count", "max"),
        criteria_total_count=("criteria_total_count", "max"),
    ).to_dict("index")
    fin_current = fin[fin["is_current"] == True].set_index("member_id")

    rows = []
    for _, r in pa.iterrows():
        mid = r["member_id"]
        m = m_info.loc[mid] if mid in m_info.index else pd.Series()
        ca = crit_agg.get(r["pa_id"], {})
        deduct = fin_current.loc[mid]["deductible_remaining_amt"] if mid in fin_current.index else None

        rows.append({
            "pa_id": r["pa_id"], "member_id": mid,
            "member_name": f"{m.get('first_name','')} {m.get('last_name','')}" if not m.empty else "",
            "member_age": m.get("age_years"), "member_gender": m.get("gender"),
            "line_of_business": r["line_of_business"], "plan_id": r["plan_id"],
            "service_category": r["service_category"], "pa_status": r["pa_status"],
            "urgency_flag": r["urgency_flag"],
            "requested_procedure_desc": r.get("requested_procedure_desc"),
            "primary_dx_desc": r.get("primary_dx_desc"),
            "ordering_provider_name": r.get("ordering_provider_name"),
            "ordering_provider_specialty": r.get("ordering_provider_specialty"),
            "network_status": r.get("network_status_at_submission"),
            "estimated_allowed_amt": r.get("estimated_allowed_amt"),
            "sla_state": sla_map.get(r["pa_id"]),
            "criteria_met_count": ca.get("criteria_met_count"),
            "criteria_total_count": ca.get("criteria_total_count"),
            "deductible_remaining_amt": deduct,
        })
    return pd.DataFrame(rows)


def build_criteria_evidence() -> pd.DataFrame:
    crit_eval = read_table("silver", "slv_pa_criteria_evaluation")
    ruleset = read_table("silver", "slv_pa_criteria_ruleset")
    pa_case = read_table("silver", "slv_pa_case")

    pa_svc = pa_case.set_index("pa_id")["service_category"].to_dict()
    desc_map: dict[tuple[str, str], str] = {}
    for _, rs in ruleset.iterrows():
        desc_map[(rs["service_category"], rs["criterion_type"])] = rs["criterion_desc"]

    rows = []
    for i, (_, r) in enumerate(crit_eval.iterrows()):
        sk = det.stable_id("GCE", r["pa_id"], r["criterion_type"], str(i), width=14)
        svc = pa_svc.get(r["pa_id"], "")
        desc = desc_map.get((svc, r["criterion_type"]), r["criterion_type"])
        rows.append({
            "criteria_evidence_sk": sk,
            "pa_id": r["pa_id"], "member_id": r["member_id"],
            "criterion_type": r["criterion_type"], "criterion_result": r["criterion_result"],
            "evidence_text": r.get("evidence_summary"),
            "criterion_desc": desc,
        })
    return pd.DataFrame(rows)


def build_sla_tracking() -> pd.DataFrame:
    return read_table("silver", "slv_pa_case")[[
        "pa_id", "line_of_business", "urgency_flag", "sla_state",
        "tat_business_days", "regulatory_deadline_datetime_utc",
        "days_until_deadline", "clock_paused_days", "is_open",
    ]].copy()


def build_decision_audit() -> pd.DataFrame:
    dh = read_table("silver", "slv_pa_decision_history")
    pa = read_table("bronze", "fct_memberPA")
    auto_map = pa.set_index("pa_id")["auto_adjudicated_flag"].to_dict()
    dh["auto_adjudicated_flag"] = dh["pa_id"].map(auto_map)
    dh = dh.rename(columns={"decision_history_sk": "decision_audit_sk"})
    return dh[["decision_audit_sk", "pa_id", "decision_type", "pa_status",
               "decision_by_role", "denial_reason_desc", "appeal_outcome",
               "auto_adjudicated_flag"]].copy()


def build_precedent() -> pd.DataFrame:
    return read_table("silver", "slv_pa_precedent_stats")[[
        "precedent_sk", "service_code", "service_code_system",
        "requested_procedure_code", "requested_ndc", "primary_dx_icd10",
        "line_of_business", "total_requests", "decided_count",
        "approved_count", "denied_count", "approval_rate_pct",
    ]].copy()


def build_duplicate_safety() -> pd.DataFrame:
    pa = read_table("bronze", "fct_memberPA")
    dupes = read_table("silver", "slv_duplicate_pa_candidates")

    best_dupe = dupes.sort_values("similarity_score", ascending=False).drop_duplicates(subset=["pa_id_1"])
    dupe_map = best_dupe.set_index("pa_id_1")[["pa_id_2", "similarity_score"]].to_dict("index")
    dupe_ids = set(dupes["pa_id_1"].unique())

    rows = []
    for _, r in pa.iterrows():
        pid = r["pa_id"]
        has_dupe = pid in dupe_ids
        d_info = dupe_map.get(pid, {})
        rows.append({
            "pa_id": pid,
            "has_potential_duplicate": has_dupe,
            "duplicate_pa_id": d_info.get("pa_id_2"),
            "duplicate_similarity": d_info.get("similarity_score"),
            "high_cost_flag": bool(r.get("high_cost_review_flag")),
            "network_concern_flag": r.get("network_status_at_submission") == "OUT_OF_NETWORK",
        })
    return pd.DataFrame(rows)


def main() -> int:
    print("=" * 78)
    print("Building Gold PA-Centric Tables")
    print("=" * 78)

    builders = [
        ("gold_pa_worklist", build_pa_worklist),
        ("gold_pa_request_360", build_pa_request_360),
        ("gold_pa_criteria_evidence", build_criteria_evidence),
        ("gold_pa_sla_tracking", build_sla_tracking),
        ("gold_pa_decision_audit", build_decision_audit),
        ("gold_pa_precedent", build_precedent),
        ("gold_pa_duplicate_safety_flags", build_duplicate_safety),
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

    S.assert_no_banned_columns()
    print("Governance guardrail passed.")
    print("All 7 gold PA-centric tables built.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
