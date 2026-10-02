"""
Build fct_memberTransactions from Synthea encounters, procedures and medications.

Maps Synthea claims to the bronze transaction schema: professional claims from
procedures, institutional claims from inpatient/emergency encounters, and
pharmacy claims from medications. Financial amounts are synthesised with
billed >= allowed >= plan_paid invariant enforced.
"""

from __future__ import annotations

import sys
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

SYNTHEA = Path(S.load_config()["_repo_root"]) / S.load_config()["paths"]["cache"] / "synthea"

TABLE = "fct_memberTransactions"
RNG_BASE = TABLE


def _load_synthea(name: str) -> pd.DataFrame:
    return pd.read_csv(SYNTHEA / f"{name}.csv", dtype=str)


def _member_id(patient_uuid: str) -> str:
    return identity.member_id(patient_uuid)


def _plan_id(patient_uuid: str, payer: str) -> str:
    return det.stable_id("PLN-", patient_uuid, payer, width=6)


# ---------------------------------------------------------------------------
# Financial synthesis helpers
# ---------------------------------------------------------------------------

PROCEDURE_COST_RANGES: dict[str, tuple[float, float]] = {
    "ADVANCED_IMAGING": (800, 5000),
    "SPECIALTY_DRUG": (2000, 15000),
    "ELECTIVE_SURGERY": (5000, 80000),
    "DME": (200, 8000),
    "BEHAVIORAL_HEALTH": (100, 500),
    "INPATIENT_ADMIT": (8000, 120000),
    "REHAB_THERAPY": (80, 400),
    "GENETIC_TESTING": (500, 5000),
    "HOME_HEALTH": (150, 1200),
    "SLEEP_STUDY": (1000, 4000),
    "PAIN_MANAGEMENT": (200, 3000),
    "RADIATION_ONCOLOGY": (3000, 25000),
    "TRANSPLANT": (50000, 500000),
    "DEFAULT": (50, 2000),
}


def _classify_service(desc: str | None, encounter_class: str | None) -> str:
    if not desc:
        if encounter_class and encounter_class.lower() in ("inpatient", "emergency"):
            return "INPATIENT_ADMIT"
        return "DEFAULT"
    up = desc.upper()
    keyword_map = {
        "MRI": "ADVANCED_IMAGING", "CT SCAN": "ADVANCED_IMAGING", "PET": "ADVANCED_IMAGING",
        "INJECTION": "SPECIALTY_DRUG", "INFUSION": "SPECIALTY_DRUG",
        "SURGERY": "ELECTIVE_SURGERY", "ARTHROPLASTY": "ELECTIVE_SURGERY",
        "DME": "DME", "WHEELCHAIR": "DME",
        "PSYCHIATRIC": "BEHAVIORAL_HEALTH", "MENTAL": "BEHAVIORAL_HEALTH",
        "GENETIC": "GENETIC_TESTING", "GENOMIC": "GENETIC_TESTING",
        "REHAB": "REHAB_THERAPY", "PHYSICAL THERAPY": "REHAB_THERAPY",
        "SLEEP": "SLEEP_STUDY", "PAIN": "PAIN_MANAGEMENT",
        "RADIATION": "RADIATION_ONCOLOGY", "CHEMOTHERAPY": "RADIATION_ONCOLOGY",
        "TRANSPLANT": "TRANSPLANT",
    }
    for kw, cat in keyword_map.items():
        if kw in up:
            return cat
    if encounter_class and encounter_class.lower() in ("inpatient", "emergency"):
        return "INPATIENT_ADMIT"
    return "DEFAULT"


def _synth_financials(
    rng: np.random.Generator, n: int, service_cats: list[str]
) -> dict[str, np.ndarray]:
    billed = np.zeros(n)
    for i, cat in enumerate(service_cats):
        lo, hi = PROCEDURE_COST_RANGES.get(cat, PROCEDURE_COST_RANGES["DEFAULT"])
        billed[i] = rng.uniform(lo, hi)
    billed = np.round(billed, 2)

    discount = rng.uniform(0.30, 0.70, n)
    allowed = np.round(billed * discount, 2)

    plan_share = rng.uniform(0.60, 0.95, n)
    plan_paid = np.round(allowed * plan_share, 2)
    plan_paid = np.minimum(plan_paid, allowed)

    member_resp = np.round(allowed - plan_paid, 2)
    deductible_share = rng.uniform(0.0, 0.4, n)
    member_deductible = np.round(member_resp * deductible_share, 2)
    member_copay = np.round(member_resp * rng.uniform(0.0, 0.3, n), 2)
    member_coinsurance = np.round(member_resp - member_deductible - member_copay, 2)
    member_coinsurance = np.maximum(member_coinsurance, 0)

    return {
        "billed_amt": billed,
        "allowed_amt": allowed,
        "plan_paid_amt": plan_paid,
        "member_deductible_amt": member_deductible,
        "member_coinsurance_amt": member_coinsurance,
        "member_copay_amt": member_copay,
        "member_responsibility_amt": member_resp,
        "cob_paid_amt": np.zeros(n),
        "adjustment_amt": np.zeros(n),
        "outstanding_amt": np.zeros(n),
    }


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_professional_claims() -> pd.DataFrame:
    """Professional claims from Synthea procedures."""
    rng = det.rng(RNG_BASE, "professional")
    procedures = _load_synthea("procedures")
    encounters = _load_synthea("encounters")

    enc_map = encounters.set_index("Id")[["ENCOUNTERCLASS", "ORGANIZATION"]].to_dict("index")

    n = len(procedures)
    service_cats = []
    encounter_classes = []
    for _, row in procedures.iterrows():
        enc_info = enc_map.get(row["ENCOUNTER"], {})
        ec = enc_info.get("ENCOUNTERCLASS", "outpatient")
        encounter_classes.append(ec)
        service_cats.append(_classify_service(row.get("DESCRIPTION"), ec))

    fin = _synth_financials(rng, n, service_cats)

    claim_ids = [det.stable_id("CLM", row["PATIENT"], row["ENCOUNTER"], row["CODE"], str(idx), width=10)
                 for idx, (_, row) in enumerate(procedures.iterrows())]
    tx_ids = [det.stable_id("TXN", row["PATIENT"], row["ENCOUNTER"], row["CODE"], "PROF", str(idx), width=12)
              for idx, (_, row) in enumerate(procedures.iterrows())]

    statuses = rng.choice(["PAID", "DENIED", "PENDED", "REVERSED"], n, p=[0.82, 0.08, 0.07, 0.03])

    npi_pool = [det.synthetic_npi("provider", i) for i in range(50)]
    billing_npis = [rng.choice(npi_pool) for _ in range(n)]
    rendering_npis = [rng.choice(npi_pool) for _ in range(n)]
    facility_npis = [rng.choice(npi_pool) for _ in range(n)]

    network_choices = list(S.NETWORK_STATUSES)
    network = rng.choice(network_choices, n, p=[0.85, 0.12, 0.03])

    ref_icd10 = pd.read_parquet(
        Path(S.load_config()["_repo_root"]) / S.load_config()["paths"]["bronze_out"] / "ref_icd10cm.parquet"
    )
    icd10_codes = ref_icd10["icd10_code"].dropna().unique().tolist()[:500]

    ref_hcpcs = pd.read_parquet(
        Path(S.load_config()["_repo_root"]) / S.load_config()["paths"]["bronze_out"] / "ref_hcpcs.parquet"
    )
    hcpcs_codes = ref_hcpcs["procedure_code"].dropna().unique().tolist()[:500]

    rows = []
    for i, (_, proc) in enumerate(procedures.iterrows()):
        enc_info = enc_map.get(proc["ENCOUNTER"], {})

        primary_dx = rng.choice(icd10_codes) if icd10_codes else None
        dx_list = [primary_dx] if primary_dx else []
        if rng.random() > 0.5 and len(icd10_codes) > 1:
            dx_list.append(rng.choice(icd10_codes))

        proc_code = rng.choice(hcpcs_codes) if hcpcs_codes else proc.get("CODE")

        service_date = dates.shift_scalar(proc["DATE"])
        received_offset = int(rng.integers(1, 30))
        received_date = service_date + pd.Timedelta(days=received_offset) if service_date else None
        adjudicated_offset = int(rng.integers(5, 45))
        adjudicated_date = received_date + pd.Timedelta(days=adjudicated_offset) if received_date else None
        paid_offset = int(rng.integers(1, 15))
        paid_date = adjudicated_date + pd.Timedelta(days=paid_offset) if adjudicated_date else None

        rows.append({
            "transaction_id": tx_ids[i],
            "claim_id": claim_ids[i],
            "claim_line_number": 1,
            "member_id": _member_id(proc["PATIENT"]),
            "plan_id": _plan_id(proc["PATIENT"], proc.get("PAYER", "DEFAULT")),
            "transaction_type": "CLAIM_PROFESSIONAL",
            "claim_status": statuses[i],
            "service_start_date": service_date,
            "service_end_date": service_date,
            "received_date": received_date,
            "adjudicated_date": adjudicated_date,
            "paid_date": paid_date if statuses[i] == "PAID" else None,
            "billing_provider_npi": billing_npis[i],
            "rendering_provider_npi": rendering_npis[i],
            "facility_npi": facility_npis[i],
            "provider_network_status": network[i],
            "place_of_service_code": "11" if encounter_classes[i] == "outpatient" else "21",
            "procedure_code": proc_code,
            "procedure_code_system": "HCPCS",
            "procedure_desc": proc.get("DESCRIPTION", ""),
            "modifier_1": None,
            "modifier_2": None,
            "revenue_code": None,
            "drg_code": None,
            "units_of_service": 1.0,
            "primary_dx_icd10": primary_dx,
            "dx_icd10_list": dx_list,
            "ndc_code": None,
            "drug_name": None,
            "quantity_dispensed": None,
            "days_supply": None,
            "fill_number": None,
            "is_generic": None,
            "formulary_tier": None,
            **{k: float(v[i]) for k, v in fin.items()},
            "related_auth_number": None,
            "pa_required_flag": rng.random() < 0.15,
            "pa_on_file_flag": False,
            "denial_code": f"CO-{rng.integers(1,30)}" if statuses[i] == "DENIED" else None,
            "denial_desc": "Claim denied" if statuses[i] == "DENIED" else None,
        })

    return pd.DataFrame(rows)


def build_institutional_claims() -> pd.DataFrame:
    """Institutional claims from inpatient/emergency encounters."""
    rng = det.rng(RNG_BASE, "institutional")
    encounters = _load_synthea("encounters")

    inst = encounters[encounters["ENCOUNTERCLASS"].isin(["inpatient", "emergency"])].copy()
    n = len(inst)
    if n == 0:
        return pd.DataFrame(columns=S.get("bronze", TABLE).col_names())

    service_cats = ["INPATIENT_ADMIT"] * n
    fin = _synth_financials(rng, n, service_cats)

    statuses = rng.choice(["PAID", "DENIED", "PENDED", "REVERSED"], n, p=[0.85, 0.06, 0.06, 0.03])

    npi_pool = [det.synthetic_npi("facility", i) for i in range(30)]

    ref_icd10 = pd.read_parquet(
        Path(S.load_config()["_repo_root"]) / S.load_config()["paths"]["bronze_out"] / "ref_icd10cm.parquet"
    )
    icd10_codes = ref_icd10["icd10_code"].dropna().unique().tolist()[:500]

    rows = []
    for i, (_, enc) in enumerate(inst.iterrows()):
        claim_id = det.stable_id("CLM", enc["PATIENT"], enc["Id"], "INST", width=10)
        tx_id = det.stable_id("TXN", enc["PATIENT"], enc["Id"], "INST", width=12)

        start = dates.shift_scalar(enc["START"])
        stop = dates.shift_scalar(enc.get("STOP", enc["START"]))

        received_date = stop + pd.Timedelta(days=int(rng.integers(1, 14))) if stop else None
        adj_date = received_date + pd.Timedelta(days=int(rng.integers(10, 60))) if received_date else None
        paid_date = adj_date + pd.Timedelta(days=int(rng.integers(1, 15))) if adj_date else None

        primary_dx = rng.choice(icd10_codes) if icd10_codes else None
        dx_list = [primary_dx] if primary_dx else []

        drg = f"{rng.integers(1, 999):03d}" if rng.random() < 0.7 else None

        fac_npi = rng.choice(npi_pool)

        rows.append({
            "transaction_id": tx_id,
            "claim_id": claim_id,
            "claim_line_number": 1,
            "member_id": _member_id(enc["PATIENT"]),
            "plan_id": _plan_id(enc["PATIENT"], enc.get("PAYER", "DEFAULT")),
            "transaction_type": "CLAIM_INSTITUTIONAL",
            "claim_status": statuses[i],
            "service_start_date": start,
            "service_end_date": stop,
            "received_date": received_date,
            "adjudicated_date": adj_date,
            "paid_date": paid_date if statuses[i] == "PAID" else None,
            "billing_provider_npi": fac_npi,
            "rendering_provider_npi": fac_npi,
            "facility_npi": fac_npi,
            "provider_network_status": rng.choice(list(S.NETWORK_STATUSES), p=[0.88, 0.10, 0.02]),
            "place_of_service_code": "21",
            "procedure_code": None,
            "procedure_code_system": None,
            "procedure_desc": enc.get("DESCRIPTION", ""),
            "modifier_1": None,
            "modifier_2": None,
            "revenue_code": f"{rng.integers(100, 999)}",
            "drg_code": drg,
            "units_of_service": 1.0,
            "primary_dx_icd10": primary_dx,
            "dx_icd10_list": dx_list,
            "ndc_code": None,
            "drug_name": None,
            "quantity_dispensed": None,
            "days_supply": None,
            "fill_number": None,
            "is_generic": None,
            "formulary_tier": None,
            **{k: float(v[i]) for k, v in fin.items()},
            "related_auth_number": None,
            "pa_required_flag": rng.random() < 0.25,
            "pa_on_file_flag": False,
            "denial_code": f"CO-{rng.integers(1,30)}" if statuses[i] == "DENIED" else None,
            "denial_desc": "Claim denied" if statuses[i] == "DENIED" else None,
        })

    return pd.DataFrame(rows)


def build_pharmacy_claims() -> pd.DataFrame:
    """Pharmacy claims from Synthea medications."""
    rng = det.rng(RNG_BASE, "pharmacy")
    medications = _load_synthea("medications")

    n = len(medications)
    service_cats = ["SPECIALTY_DRUG" if rng.random() < 0.15 else "DEFAULT" for _ in range(n)]
    fin = _synth_financials(rng, n, service_cats)

    statuses = rng.choice(["PAID", "DENIED", "PENDED", "REVERSED"], n, p=[0.90, 0.04, 0.04, 0.02])

    ref_ndc = pd.read_parquet(
        Path(S.load_config()["_repo_root"]) / S.load_config()["paths"]["bronze_out"] / "ref_ndc_product.parquet"
    )
    ndc_codes = ref_ndc["product_ndc"].dropna().unique().tolist()[:1000]

    npi_pool = [det.synthetic_npi("pharmacy", i) for i in range(20)]

    rows = []
    for i, (_, med) in enumerate(medications.iterrows()):
        tx_id = det.stable_id("TXN", med["PATIENT"], med.get("ENCOUNTER", ""), med["CODE"], "RX", str(i), width=12)
        claim_id = det.stable_id("CLM", med["PATIENT"], med.get("ENCOUNTER", ""), med["CODE"], "RX", str(i), width=10)

        start = dates.shift_scalar(med["START"])
        stop = dates.shift_scalar(med.get("STOP")) if pd.notna(med.get("STOP")) else start

        received_date = start + pd.Timedelta(days=int(rng.integers(0, 3))) if start else None
        adj_date = received_date + pd.Timedelta(days=int(rng.integers(0, 5))) if received_date else None
        paid_date = adj_date + pd.Timedelta(days=int(rng.integers(1, 10))) if adj_date else None

        ndc = rng.choice(ndc_codes) if ndc_codes else None
        dispenses = int(med.get("DISPENSES", 1) or 1)
        days_supply = int(rng.integers(7, 90))
        is_generic = rng.random() < 0.65
        tier = int(rng.integers(1, 5))
        is_specialty = service_cats[i] == "SPECIALTY_DRUG"

        rows.append({
            "transaction_id": tx_id,
            "claim_id": claim_id,
            "claim_line_number": 1,
            "member_id": _member_id(med["PATIENT"]),
            "plan_id": _plan_id(med["PATIENT"], med.get("PAYER", "DEFAULT")),
            "transaction_type": "CLAIM_PHARMACY",
            "claim_status": statuses[i],
            "service_start_date": start,
            "service_end_date": stop,
            "received_date": received_date,
            "adjudicated_date": adj_date,
            "paid_date": paid_date if statuses[i] == "PAID" else None,
            "billing_provider_npi": rng.choice(npi_pool),
            "rendering_provider_npi": None,
            "facility_npi": None,
            "provider_network_status": rng.choice(list(S.NETWORK_STATUSES), p=[0.92, 0.06, 0.02]),
            "place_of_service_code": "01",
            "procedure_code": None,
            "procedure_code_system": None,
            "procedure_desc": None,
            "modifier_1": None,
            "modifier_2": None,
            "revenue_code": None,
            "drg_code": None,
            "units_of_service": float(dispenses),
            "primary_dx_icd10": None,
            "dx_icd10_list": None,
            "ndc_code": ndc,
            "drug_name": med.get("DESCRIPTION", ""),
            "quantity_dispensed": float(dispenses) * float(rng.integers(10, 120)),
            "days_supply": days_supply,
            "fill_number": int(rng.integers(0, dispenses)),
            "is_generic": is_generic,
            "formulary_tier": tier,
            **{k: float(v[i]) for k, v in fin.items()},
            "related_auth_number": None,
            "pa_required_flag": is_specialty,
            "pa_on_file_flag": False,
            "denial_code": f"RX-{rng.integers(1,15)}" if statuses[i] == "DENIED" else None,
            "denial_desc": "Pharmacy claim denied" if statuses[i] == "DENIED" else None,
        })

    return pd.DataFrame(rows)


def build_payments_adjustments(claims_df: pd.DataFrame) -> pd.DataFrame:
    """Payments and adjustments derived from paid/adjusted claims."""
    rng = det.rng(RNG_BASE, "payments")

    paid = claims_df[claims_df["claim_status"] == "PAID"].copy()
    if paid.empty:
        return pd.DataFrame(columns=S.get("bronze", TABLE).col_names())

    sample_size = min(len(paid), int(len(paid) * 0.15))
    adj_idx = rng.choice(paid.index, size=sample_size, replace=False)

    payment_rows = []
    for _, row in paid.iterrows():
        tx_id = det.stable_id("TXN", row["claim_id"], "PAY", width=12)
        payment_rows.append({
            **{c: None for c in S.get("bronze", TABLE).col_names()
               if c not in ("source_system", "ingested_at_utc", "record_hash")},
            "transaction_id": tx_id,
            "claim_id": row["claim_id"],
            "claim_line_number": None,
            "member_id": row["member_id"],
            "plan_id": row["plan_id"],
            "transaction_type": "PAYMENT",
            "claim_status": None,
            "service_start_date": row["service_start_date"],
            "service_end_date": row["service_end_date"],
            "paid_date": row["paid_date"],
            "plan_paid_amt": row["plan_paid_amt"],
            "billed_amt": None,
            "allowed_amt": None,
        })

    for idx in adj_idx:
        row = paid.loc[idx]
        tx_id = det.stable_id("TXN", row["claim_id"], "ADJ", width=12)
        adj_amt = round(float(rng.uniform(-200, 200)), 2)
        payment_rows.append({
            **{c: None for c in S.get("bronze", TABLE).col_names()
               if c not in ("source_system", "ingested_at_utc", "record_hash")},
            "transaction_id": tx_id,
            "claim_id": row["claim_id"],
            "claim_line_number": None,
            "member_id": row["member_id"],
            "plan_id": row["plan_id"],
            "transaction_type": "ADJUSTMENT",
            "claim_status": None,
            "service_start_date": row["service_start_date"],
            "service_end_date": row["service_end_date"],
            "adjustment_amt": adj_amt,
        })

    return pd.DataFrame(payment_rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("Building fct_memberTransactions")
    print("=" * 78)
    print(f"  date anchor: {dates.describe()}")

    print("\n  [1/4] Professional claims from procedures ...")
    prof = build_professional_claims()
    print(f"         {len(prof):,d} rows")

    print("  [2/4] Institutional claims from encounters ...")
    inst = build_institutional_claims()
    print(f"         {len(inst):,d} rows")

    print("  [3/4] Pharmacy claims from medications ...")
    rx = build_pharmacy_claims()
    print(f"         {len(rx):,d} rows")

    claims = pd.concat([prof, inst, rx], ignore_index=True)

    print("  [4/4] Payment and adjustment transactions ...")
    pay_adj = build_payments_adjustments(claims)
    print(f"         {len(pay_adj):,d} rows")

    all_txn = pd.concat([claims, pay_adj], ignore_index=True)
    print(f"\n  Total transactions: {len(all_txn):,d}")

    write_table(all_txn, "bronze", TABLE, source_system="SYNTHEA")

    print("\nfct_memberTransactions complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
