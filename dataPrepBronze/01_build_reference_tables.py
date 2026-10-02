"""
Build the six bronze reference tables from fetched open datasets.

    ref_icd10cm          <- CMS ICD-10-CM code descriptions
    ref_hcpcs            <- CMS HCPCS Level II + curated PA-relevant codes
    ref_ndc_product      <- FDA NDC directory
    ref_provider_npi     <- NPPES API (best-effort) + synthetic fallback
    ref_place_of_service <- static CMS place-of-service codes
    ref_synpuf_benchmark <- CMS DE-SynPUF beneficiary summary (calibration)

Each builder reads raw files from `.cache/<dataset>/`, transforms them to
match the frozen schema, and writes via `common.frames.write_table` which
enforces the column contract and populates the audit trailer.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common import determinism as det  # noqa: E402
from common import fetch as fetch_mod  # noqa: E402
from common.frames import write_table  # noqa: E402
from config import schemas as S  # noqa: E402

# Curated CPT codes, HCPCS PA range rules and the SNOMED crosswalks. Kept in a
# separate module because the ext_* builders consume the same crosswalk maps.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import reference_curated as curated_mod  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PA_SERVICE_CATEGORY_MAP: dict[str, str] = {
    "MRI": "ADVANCED_IMAGING",
    "CT": "ADVANCED_IMAGING",
    "PET": "ADVANCED_IMAGING",
    "INJECTION": "SPECIALTY_DRUG",
    "INFUSION": "SPECIALTY_DRUG",
    "CHEMOTHERAPY": "RADIATION_ONCOLOGY",
    "RADIATION": "RADIATION_ONCOLOGY",
    "SURGERY": "ELECTIVE_SURGERY",
    "ORTHOPEDIC": "ELECTIVE_SURGERY",
    "DME": "DME",
    "WHEELCHAIR": "DME",
    "PROSTHE": "DME",
    "ORTHO": "DME",
    "HOME HEALTH": "HOME_HEALTH",
    "PSYCHIATRIC": "BEHAVIORAL_HEALTH",
    "MENTAL": "BEHAVIORAL_HEALTH",
    "SUBSTANCE": "BEHAVIORAL_HEALTH",
    "GENETIC": "GENETIC_TESTING",
    "GENOMIC": "GENETIC_TESTING",
    "REHAB": "REHAB_THERAPY",
    "PHYSICAL THERAPY": "REHAB_THERAPY",
    "OCCUPATIONAL THERAPY": "REHAB_THERAPY",
    "SPEECH": "REHAB_THERAPY",
    "SLEEP": "SLEEP_STUDY",
    "POLYSOMNOGRAPHY": "SLEEP_STUDY",
    "PAIN": "PAIN_MANAGEMENT",
    "NERVE BLOCK": "PAIN_MANAGEMENT",
    "TRANSPLANT": "TRANSPLANT",
    "INPATIENT": "INPATIENT_ADMIT",
}


def _classify_pa_category(desc: str | None) -> str | None:
    if not desc:
        return None
    up = desc.upper()
    for keyword, cat in PA_SERVICE_CATEGORY_MAP.items():
        if keyword in up:
            return cat
    return None


def _find_file(directory: Path, *patterns: str) -> Path | None:
    for pat in patterns:
        matches = list(directory.glob(pat))
        if matches:
            return matches[0]
    # Case-insensitive fallback
    for f in directory.iterdir():
        low = f.name.lower()
        for pat in patterns:
            if pat.lower().replace("*", "") in low:
                return f
    return None


# ---------------------------------------------------------------------------
# ref_icd10cm
# ---------------------------------------------------------------------------

def build_ref_icd10cm() -> pd.DataFrame:
    print("\n--- ref_icd10cm ---")
    icd_dir = fetch_mod.cache_dir() / "icd10cm"

    # CMS distributes ICD-10-CM in a fixed-width or tab-delimited format.
    # Try to find the order file first.
    txt_file = _find_file(icd_dir, "*order*", "*icd10cm*", "*.txt")

    if txt_file is None:
        # Fallback: look for CSV (GitHub mirror)
        csv_file = _find_file(icd_dir, "*.csv")
        if csv_file is None:
            raise FileNotFoundError(f"No ICD-10-CM data file found in {icd_dir}")
        return _build_icd10_from_csv(csv_file)

    return _build_icd10_from_cms_txt(txt_file)


def _build_icd10_from_cms_txt(path: Path) -> pd.DataFrame:
    """Parse the CMS fixed-width order file (icd10cm_order_YYYY.txt)."""
    rows = []
    text = path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if len(line) < 20:
            continue
        # CMS order format: positions 6-13 = code, 14 = billable flag, 16+ = description
        # Some editions use slightly different positions; be tolerant.
        parts = line.split()
        if len(parts) < 3:
            continue
        # Find the code (alphanumeric, 3-7 chars starting with a letter)
        code = None
        billable = None
        desc_parts = []
        for i, p in enumerate(parts):
            if code is None and re.match(r"^[A-Z]\d{2}", p) and len(p) <= 8:
                code = p
            elif code is not None and billable is None and p in ("0", "1"):
                billable = p == "1"
            elif code is not None:
                desc_parts.append(p)
        if code is None:
            continue

        dotted = code[:3] + "." + code[3:] if len(code) > 3 else code
        description = " ".join(desc_parts).strip()

        rows.append({
            "icd10_code": code,
            "icd10_code_dotted": dotted,
            "short_description": description[:120] if description else None,
            "long_description": description[:500] if description else None,
            "chapter_code": None,
            "chapter_description": None,
            "category_code": code[:3],
            "is_billable": billable if billable is not None else (len(code) > 3),
            "code_edition": "2026",
        })

    df = pd.DataFrame(rows)
    # Enrich chapter info from the code prefix
    df = _enrich_icd10_chapters(df)
    # Deduplicate on code
    df = df.drop_duplicates(subset=["icd10_code"], keep="first")
    print(f"  parsed {len(df)} ICD-10-CM codes from {path.name}")
    return df


def _build_icd10_from_csv(path: Path) -> pd.DataFrame:
    """Parse a CSV-format ICD-10 file (e.g. GitHub mirror)."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Find the code column
    code_col = None
    for candidate in ("code", "icd10_code", "diagnosis_code", "icd_code"):
        if candidate in df.columns:
            code_col = candidate
            break
    if code_col is None:
        code_col = df.columns[0]

    desc_col = None
    for candidate in ("long_description", "description", "short_description", "name"):
        if candidate in df.columns:
            desc_col = candidate
            break
    if desc_col is None and len(df.columns) > 1:
        desc_col = df.columns[1]

    out = pd.DataFrame()
    out["icd10_code"] = df[code_col].str.replace(".", "", regex=False).str.strip()
    out["icd10_code_dotted"] = out["icd10_code"].apply(
        lambda c: c[:3] + "." + c[3:] if len(str(c)) > 3 else c
    )
    out["short_description"] = df[desc_col].str[:120] if desc_col else None
    out["long_description"] = df[desc_col].str[:500] if desc_col else None
    out["chapter_code"] = None
    out["chapter_description"] = None
    out["category_code"] = out["icd10_code"].str[:3]
    out["is_billable"] = out["icd10_code"].str.len() > 3
    out["code_edition"] = "2026"

    out = _enrich_icd10_chapters(out)
    out = out.drop_duplicates(subset=["icd10_code"], keep="first")
    print(f"  parsed {len(out)} ICD-10-CM codes from {path.name}")
    return out


ICD10_CHAPTERS = [
    ("A00", "B99", "A00-B99", "Certain infectious and parasitic diseases"),
    ("C00", "D49", "C00-D49", "Neoplasms"),
    ("D50", "D89", "D50-D89", "Diseases of the blood"),
    ("E00", "E89", "E00-E89", "Endocrine, nutritional and metabolic diseases"),
    ("F01", "F99", "F01-F99", "Mental, behavioral and neurodevelopmental disorders"),
    ("G00", "G99", "G00-G99", "Diseases of the nervous system"),
    ("H00", "H59", "H00-H59", "Diseases of the eye and adnexa"),
    ("H60", "H95", "H60-H95", "Diseases of the ear and mastoid process"),
    ("I00", "I99", "I00-I99", "Diseases of the circulatory system"),
    ("J00", "J99", "J00-J99", "Diseases of the respiratory system"),
    ("K00", "K95", "K00-K95", "Diseases of the digestive system"),
    ("L00", "L99", "L00-L99", "Diseases of the skin and subcutaneous tissue"),
    ("M00", "M99", "M00-M99", "Diseases of the musculoskeletal system"),
    ("N00", "N99", "N00-N99", "Diseases of the genitourinary system"),
    ("O00", "O9A", "O00-O9A", "Pregnancy, childbirth and the puerperium"),
    ("P00", "P96", "P00-P96", "Certain conditions originating in the perinatal period"),
    ("Q00", "Q99", "Q00-Q99", "Congenital malformations"),
    ("R00", "R99", "R00-R99", "Symptoms, signs and abnormal findings"),
    ("S00", "T88", "S00-T88", "Injury, poisoning and external causes"),
    ("V00", "Y99", "V00-Y99", "External causes of morbidity"),
    ("Z00", "Z99", "Z00-Z99", "Factors influencing health status"),
]


def _enrich_icd10_chapters(df: pd.DataFrame) -> pd.DataFrame:
    cat = df["category_code"].fillna("")
    for start, end, code, desc in ICD10_CHAPTERS:
        mask = (cat >= start) & (cat <= end)
        df.loc[mask, "chapter_code"] = code
        df.loc[mask, "chapter_description"] = desc
    return df


# ---------------------------------------------------------------------------
# ref_hcpcs
# ---------------------------------------------------------------------------

def build_ref_hcpcs() -> pd.DataFrame:
    """Real CMS HCPCS Level II codes, plus the curated CPT Level I set.

    Two sources are combined because neither alone is sufficient:

      * CMS ANWEB gives ~8,700 real Level II alphanumeric codes (A-V) and dental
        D codes. It contains NO numeric CPT codes - CPT is AMA-copyrighted and is
        not distributed in any free CMS file.
      * Prior authorization is transacted on CPT. An analyst reviewing an MRI
        request works with 72148. So reference_curated.CURATED_CPT supplies a
        hand-verified set of real CPT codes across the thirteen PA service
        categories, recorded with source_system CURATED so its provenance is
        explicit.
    """
    print("\n--- ref_hcpcs ---")
    hcpcs_dir = fetch_mod.cache_dir() / "hcpcs"

    anweb = _find_file(hcpcs_dir, "*ANWEB*.txt")
    if anweb is None:
        anweb = _find_file(hcpcs_dir, "*.txt")

    level2 = _parse_hcpcs_anweb(anweb) if anweb else pd.DataFrame()
    curated = _build_curated_cpt()

    df = pd.concat([level2, curated], ignore_index=True)
    df = df.drop_duplicates(subset=["procedure_code"], keep="first")

    n_pa = int(df["typically_requires_pa"].sum())
    print(
        f"  ref_hcpcs total {len(df)} codes "
        f"({len(level2)} real HCPCS Level II + {len(curated)} curated CPT), "
        f"{n_pa} flagged PA-required"
    )
    return df


def _parse_hcpcs_anweb(path: Path) -> pd.DataFrame:
    """Parse the CMS HCPCS ANWEB fixed-width file.

    Column positions come from the CMS record layout shipped alongside the data
    (HCPC<year>_recordlayout.txt), not from inspection:

        1-5     HCPCS code            (positions 1-3 blank => MODIFIER row)
        6-10    sequence number
        11      record identification code
        12-91   long description      (80 chars)
        92-119  short description     (28 chars)
        120-121 pricing indicator
        230     coverage code
        257-259 Berenson-Eggers type of service
        285-292 termination date      (YYYYMMDD)

    Two details cause silent corruption if missed:

      * The file interleaves MODIFIER rows with code rows. A modifier row is
        identified by positions 1-3 being blank, with the 2-character modifier in
        positions 4-5. Treating those as codes yields junk identifiers.
      * Terminated codes are retained in the file. They are kept here but flagged
        via the termination date, because a claim or PA from an earlier period may
        legitimately reference a code that is no longer active.
    """
    text = path.read_text(encoding="latin-1", errors="replace")

    rows: list[dict] = []
    n_modifiers = 0
    n_terminated = 0

    for line in text.splitlines():
        if len(line) < 120:
            continue

        head = line[0:5]
        if head[0:3].strip() == "":
            # Modifier row, not a procedure code.
            n_modifiers += 1
            continue

        code = head.strip()
        if not re.fullmatch(r"[A-Z0-9]\d{3,4}", code):
            continue

        long_desc = line[11:91].strip() or None
        short_desc = line[91:119].strip() or None
        pricing_ind = line[119:121].strip() or None
        coverage = line[229:230].strip() if len(line) > 229 else ""
        betos = line[256:259].strip() if len(line) > 258 else ""
        term_raw = line[284:292].strip() if len(line) > 291 else ""

        if term_raw and term_raw.isdigit() and term_raw != "00000000":
            n_terminated += 1

        pa_cat = curated_mod.hcpcs_pa_category(code)

        rows.append({
            "procedure_code": code,
            "code_system": "HCPCS",
            "short_description": (short_desc or long_desc or "")[:120] or None,
            "long_description": (long_desc or short_desc or "")[:500] or None,
            "category": curated_mod.hcpcs_category(code),
            "pa_service_category": pa_cat,
            "typically_requires_pa": pa_cat is not None,
            "coverage_code": coverage or None,
            "pricing_indicator": pricing_ind,
            "betos_code": betos or None,
            "typical_allowed_amt": None,
            "code_edition": "HCPCS-2026-OCT",
        })

    df = pd.DataFrame(rows)
    print(
        f"  parsed {len(df)} HCPCS Level II codes from {path.name} "
        f"(skipped {n_modifiers} modifier rows, {n_terminated} codes are terminated)"
    )
    return df


def _build_curated_cpt() -> pd.DataFrame:
    """The curated CPT Level I set from reference_curated.CURATED_CPT."""
    rows = []
    for code, short_desc, category, pa_cat, allowed in curated_mod.CURATED_CPT:
        rows.append({
            "procedure_code": code,
            "code_system": "CPT",
            "short_description": short_desc[:120],
            "long_description": short_desc[:500],
            "category": category,
            "pa_service_category": pa_cat,
            "typically_requires_pa": pa_cat is not None,
            "coverage_code": None,
            "pricing_indicator": None,
            "betos_code": None,
            "typical_allowed_amt": allowed,
            "code_edition": "CPT-CURATED-2026",
        })
    df = pd.DataFrame(rows)
    print(f"  curated {len(df)} CPT Level I codes (PA-relevant services)")
    return df


# ---------------------------------------------------------------------------
# ref_ndc_product
# ---------------------------------------------------------------------------

SPECIALTY_DRUG_CLASSES = {
    "ANTINEOPLASTIC", "IMMUNOSUPPRESSANT", "BIOLOGIC", "MONOCLONAL",
    "ENZYME REPLACEMENT", "GENE THERAPY", "CAR-T", "INTERFERON",
    "GROWTH HORMONE", "ERYTHROPOIESIS", "COLONY-STIMULATING",
    "TNF BLOCKING", "INTERLEUKIN", "KINASE INHIBITOR",
}

PA_DRUG_CLASSES = SPECIALTY_DRUG_CLASSES | {
    "OPIOID", "BENZODIAZEPINE", "STIMULANT", "BARBITURATE",
    "TESTOSTERONE", "ESTROGEN", "FERTILITY",
}


def build_ref_ndc_product() -> pd.DataFrame:
    print("\n--- ref_ndc_product ---")
    ndc_dir = fetch_mod.cache_dir() / "fda_ndc"

    product_file = _find_file(ndc_dir, "product*", "Product*", "PRODUCT*")
    if product_file is None:
        raise FileNotFoundError(f"No product file found in {ndc_dir}")

    df = pd.read_csv(product_file, sep="\t", dtype=str, keep_default_na=False,
                      on_bad_lines="skip", encoding="utf-8", encoding_errors="replace")
    df.columns = [c.strip().upper() for c in df.columns]

    def _get(col_candidates: list[str], default: str = "") -> pd.Series:
        for c in col_candidates:
            if c in df.columns:
                return df[c].fillna("").str.strip()
        return pd.Series(default, index=df.index)

    product_ndc = _get(["PRODUCTNDC", "PRODUCT_NDC", "NDC"])
    if product_ndc.eq("").all():
        product_ndc = df.iloc[:, 1] if df.shape[1] > 1 else product_ndc

    proprietary = _get(["PROPRIETARYNAME", "PROPRIETARY_NAME", "BRANDNAME"])
    nonproprietary = _get(["NONPROPRIETARYNAME", "NONPROPRIETARY_NAME", "GENERIC_NAME"])
    product_type = _get(["PRODUCTTYPENAME", "PRODUCT_TYPE_NAME", "PRODUCTTYPE"])
    dosage_form = _get(["DOSAGEFORMNAME", "DOSAGE_FORM_NAME", "DOSAGEFORM"])
    route = _get(["ROUTENAME", "ROUTE_NAME", "ROUTE"])
    labeler = _get(["LABELERNAME", "LABELER_NAME"])
    substance = _get(["SUBSTANCENAME", "SUBSTANCE_NAME", "ACTIVE_SUBSTANCE"])
    strength = _get(["ACTIVE_NUMERATOR_STRENGTH", "STRENGTH"])
    unit = _get(["ACTIVE_INGRED_UNIT", "ACTIVE_INGREDIENT_UNIT"])
    pharm = _get(["PHARM_CLASSES", "PHARMACOLOGIC_CLASS"])
    dea = _get(["DEASCHEDULE", "DEA_SCHEDULE"])
    start_date = _get(["STARTMARKETINGDATE", "MARKETING_START_DATE"])
    end_date = _get(["ENDMARKETINGDATE", "MARKETING_END_DATE"])

    def _classify_therapeutic(pharm_val: str, name: str) -> str:
        combined = (pharm_val + " " + name).upper()
        if any(k in combined for k in ("ANTIBIOTIC", "ANTI-INFECT")):
            return "ANTI-INFECTIVE"
        if any(k in combined for k in ("ANTINEOPLASTIC", "ONCOLOGY")):
            return "ONCOLOGY"
        if any(k in combined for k in ("CARDIOVASCUL", "ANTIHYPERTENS", "STATIN")):
            return "CARDIOVASCULAR"
        if any(k in combined for k in ("ANTIDIABETIC", "INSULIN", "METFORMIN")):
            return "ENDOCRINE"
        if any(k in combined for k in ("ANALGESIC", "OPIOID", "NSAID", "PAIN")):
            return "PAIN_MANAGEMENT"
        if any(k in combined for k in ("PSYCHO", "ANTIDEPRESSANT", "ANTIPSYCHOTIC", "ANXIOLYTIC")):
            return "CNS"
        if any(k in combined for k in ("IMMUNOSUP", "BIOLOGIC", "MONOCLONAL")):
            return "IMMUNOLOGY"
        if any(k in combined for k in ("PULMONARY", "BRONCHOD", "INHALER")):
            return "PULMONARY"
        return "OTHER"

    def _is_specialty(pharm_val: str, name: str) -> bool:
        combined = (pharm_val + " " + name).upper()
        return any(k in combined for k in SPECIALTY_DRUG_CLASSES)

    def _is_pa_required(pharm_val: str, name: str, dea_val: str) -> bool:
        combined = (pharm_val + " " + name).upper()
        if any(k in combined for k in PA_DRUG_CLASSES):
            return True
        if dea_val and dea_val.strip().upper() in ("CII", "CIII"):
            return True
        return False

    def _parse_date(s: str) -> str | None:
        if not s or len(s) < 8:
            return None
        try:
            # CMS format: YYYYMMDD
            return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
        except (ValueError, IndexError):
            return None

    out = pd.DataFrame({
        "product_ndc": product_ndc,
        "product_type": product_type.where(product_type != "", None),
        "proprietary_name": proprietary.where(proprietary != "", None),
        "nonproprietary_name": nonproprietary.where(nonproprietary != "", None),
        "dosage_form": dosage_form.where(dosage_form != "", None),
        "route": route.where(route != "", None),
        "labeler_name": labeler.where(labeler != "", None),
        "substance_name": substance.where(substance != "", None),
        "active_numerator_strength": strength.where(strength != "", None),
        "active_ingredient_unit": unit.where(unit != "", None),
        "pharm_classes": pharm.where(pharm != "", None),
        "dea_schedule": dea.where(dea != "", None),
    })

    out["therapeutic_class"] = [
        _classify_therapeutic(str(p), str(n))
        for p, n in zip(pharm, nonproprietary)
    ]
    out["is_specialty_drug"] = [
        _is_specialty(str(p), str(n))
        for p, n in zip(pharm, nonproprietary)
    ]
    out["typically_requires_pa"] = [
        _is_pa_required(str(p), str(n), str(d))
        for p, n, d in zip(pharm, nonproprietary, dea)
    ]
    out["is_controlled_substance"] = dea.str.strip().ne("")
    out["marketing_start_date"] = start_date.map(_parse_date)
    out["marketing_end_date"] = end_date.map(_parse_date)

    # Filter out empty NDCs
    out = out[out["product_ndc"].str.strip().ne("")]
    out = out.drop_duplicates(subset=["product_ndc"], keep="first")
    print(f"  parsed {len(out)} NDC products from {product_file.name}")
    return out


# ---------------------------------------------------------------------------
# ref_provider_npi
# ---------------------------------------------------------------------------

def build_ref_provider_npi() -> pd.DataFrame:
    print("\n--- ref_provider_npi ---")
    cfg = S.load_config()
    nppes_cfg = cfg["open_datasets"]["nppes"]
    rng = det.rng("ref_provider_npi")

    all_rows: list[dict] = []

    # Try NPPES API
    if nppes_cfg.get("enabled", True):
        api_base = nppes_cfg["api_base"]
        version = nppes_cfg.get("api_version", "2.1")
        cities = nppes_cfg.get("query_cities", [])
        per_city = nppes_cfg.get("per_city_limit", 200)

        import requests
        for city, state in cities:
            try:
                resp = requests.get(
                    api_base,
                    params={
                        "version": version,
                        "city": city,
                        "state": state,
                        "limit": per_city,
                    },
                    timeout=30,
                    headers={"User-Agent": fetch_mod.USER_AGENT},
                )
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                for r in results:
                    basic = r.get("basic", {})
                    taxonomies = r.get("taxonomies", [])
                    addresses = r.get("addresses", [])
                    practice_addr = next(
                        (a for a in addresses if a.get("address_purpose") == "LOCATION"),
                        addresses[0] if addresses else {},
                    )
                    entity_type = str(r.get("enumeration_type", "NPI-1"))
                    is_org = "NPI-2" in entity_type

                    primary_tax = taxonomies[0] if taxonomies else {}

                    all_rows.append({
                        "npi": str(r.get("number", "")),
                        "entity_type": "2" if is_org else "1",
                        "provider_name": (
                            basic.get("organization_name", "")
                            if is_org
                            else f"{basic.get('first_name', '')} {basic.get('last_name', '')}".strip()
                        ),
                        "first_name": None if is_org else basic.get("first_name"),
                        "last_name": None if is_org else basic.get("last_name"),
                        "credential": basic.get("credential"),
                        "gender": basic.get("gender"),
                        "primary_taxonomy_code": primary_tax.get("code"),
                        "primary_specialty": primary_tax.get("desc"),
                        "organization_name": basic.get("organization_name") if is_org else None,
                        "practice_city": practice_addr.get("city"),
                        "practice_state": curated_mod.state_code(practice_addr.get("state")),
                        "practice_zip": practice_addr.get("postal_code", "")[:10],
                        "practice_phone": practice_addr.get("telephone_number"),
                        "enumeration_date": basic.get("enumeration_date"),
                        "is_sole_proprietor": basic.get("sole_proprietor") == "YES",
                        "is_synthetic_npi": False,
                    })
                print(f"  NPPES API: {city}, {state} -> {len(results)} providers")
            except Exception as exc:
                print(f"  NPPES API: {city}, {state} -> FAILED ({exc})")

    # Deduplicate API results
    if all_rows:
        df_api = pd.DataFrame(all_rows).drop_duplicates(subset=["npi"], keep="first")
        print(f"  NPPES API total: {len(df_api)} unique providers")
    else:
        df_api = pd.DataFrame()

    # Generate synthetic fallback providers to ensure minimum useful count
    min_providers = 200
    n_synthetic = max(0, min_providers - len(df_api))
    if n_synthetic > 0:
        print(f"  generating {n_synthetic} synthetic providers")
        specialties = [
            ("207R00000X", "Internal Medicine", "MD"),
            ("207Q00000X", "Family Medicine", "MD"),
            ("2084P0800X", "Psychiatry", "MD"),
            ("207X00000X", "Orthopedic Surgery", "MD"),
            ("2085R0202X", "Diagnostic Radiology", "MD"),
            ("208600000X", "Surgery", "MD"),
            ("207Y00000X", "Ophthalmology", "MD"),
            ("208000000X", "Pediatrics", "MD"),
            ("207V00000X", "Obstetrics & Gynecology", "MD"),
            ("363L00000X", "Nurse Practitioner", "NP"),
            ("363A00000X", "Physician Assistant", "PA-C"),
            ("208100000X", "Physical Medicine & Rehabilitation", "DO"),
        ]
        synth_rows = []
        first_names = ["James", "Mary", "Robert", "Linda", "Michael", "Patricia",
                       "William", "Elizabeth", "David", "Jennifer", "Richard", "Maria",
                       "Joseph", "Susan", "Thomas", "Karen", "Charles", "Nancy"]
        last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
                      "Miller", "Davis", "Rodriguez", "Martinez", "Anderson", "Taylor",
                      "Thomas", "Jackson", "White", "Harris", "Clark", "Lewis"]
        cities_states = [("Boston", "MA"), ("Worcester", "MA"), ("Springfield", "MA"),
                         ("Cambridge", "MA"), ("New York", "NY"), ("Chicago", "IL")]

        for i in range(n_synthetic):
            npi = det.synthetic_npi("ref_provider_npi", i)
            spec_idx = i % len(specialties)
            tax_code, spec_name, cred = specialties[spec_idx]
            fn = first_names[rng.integers(len(first_names))]
            ln = last_names[rng.integers(len(last_names))]
            city, state = cities_states[rng.integers(len(cities_states))]
            gender = rng.choice(["M", "F"])

            synth_rows.append({
                "npi": npi,
                "entity_type": "1",
                "provider_name": f"{fn} {ln}",
                "first_name": fn,
                "last_name": ln,
                "credential": cred,
                "gender": gender,
                "primary_taxonomy_code": tax_code,
                "primary_specialty": spec_name,
                "organization_name": None,
                "practice_city": city,
                "practice_state": curated_mod.state_code(state),
                "practice_zip": f"{rng.integers(10000, 99999):05d}",
                "practice_phone": None,
                "enumeration_date": None,
                "is_sole_proprietor": None,
                "is_synthetic_npi": True,
            })

        df_synth = pd.DataFrame(synth_rows)
        df_api = pd.concat([df_api, df_synth], ignore_index=True) if len(df_api) > 0 else df_synth

    df_api = df_api.drop_duplicates(subset=["npi"], keep="first")
    print(f"  ref_provider_npi total: {len(df_api)} providers")
    return df_api


# ---------------------------------------------------------------------------
# ref_place_of_service
# ---------------------------------------------------------------------------

POS_CODES: list[tuple[str, str, str, str, bool]] = [
    ("01", "Pharmacy", "A facility or location where drugs are sold and dispensed.", "NON_FACILITY", False),
    ("02", "Telehealth", "Services via real-time interactive audio/video.", "NON_FACILITY", False),
    ("03", "School", "A facility providing educational services.", "NON_FACILITY", False),
    ("04", "Homeless Shelter", "A facility providing temporary housing.", "NON_FACILITY", False),
    ("05", "Indian Health Service Freestanding", "IHS freestanding facility.", "NON_FACILITY", False),
    ("06", "Indian Health Service Provider-Based", "IHS provider-based facility.", "FACILITY", False),
    ("07", "Tribal 638 Freestanding", "Tribal 638 freestanding facility.", "NON_FACILITY", False),
    ("08", "Tribal 638 Provider-Based", "Tribal 638 provider-based facility.", "FACILITY", False),
    ("09", "Prison/Correctional Facility", "A correctional facility.", "FACILITY", False),
    ("10", "Telehealth in Patient Home", "Telehealth provided to patient in home.", "NON_FACILITY", False),
    ("11", "Office", "A physician or other health care professional's office.", "NON_FACILITY", False),
    ("12", "Home", "Location, other than a hospital or other facility, where the patient resides.", "NON_FACILITY", False),
    ("13", "Assisted Living Facility", "An assisted living facility.", "NON_FACILITY", False),
    ("14", "Group Home", "A group home.", "NON_FACILITY", False),
    ("15", "Mobile Unit", "A mobile unit.", "NON_FACILITY", False),
    ("16", "Temporary Lodging", "Temporary lodging.", "NON_FACILITY", False),
    ("17", "Walk-in Retail Health Clinic", "A walk-in retail health clinic.", "NON_FACILITY", False),
    ("18", "Place of Employment/Worksite", "Location where the patient is employed.", "NON_FACILITY", False),
    ("19", "Off Campus Outpatient Hospital", "Off campus outpatient hospital.", "FACILITY", False),
    ("20", "Urgent Care Facility", "An urgent care facility.", "NON_FACILITY", False),
    ("21", "Inpatient Hospital", "A facility for inpatient hospital care.", "FACILITY", True),
    ("22", "On Campus Outpatient Hospital", "On campus outpatient hospital.", "FACILITY", False),
    ("23", "Emergency Room - Hospital", "A hospital emergency department.", "FACILITY", False),
    ("24", "Ambulatory Surgical Center", "An ambulatory surgical center.", "FACILITY", False),
    ("25", "Birthing Center", "A birthing center.", "NON_FACILITY", False),
    ("26", "Military Treatment Facility", "A military treatment facility.", "FACILITY", True),
    ("31", "Skilled Nursing Facility", "A skilled nursing facility.", "FACILITY", True),
    ("32", "Nursing Facility", "A nursing facility.", "FACILITY", True),
    ("33", "Custodial Care Facility", "A custodial care facility.", "FACILITY", True),
    ("34", "Hospice", "A hospice facility.", "FACILITY", True),
    ("41", "Ambulance - Land", "A land-based ambulance.", "NON_FACILITY", False),
    ("42", "Ambulance - Air or Water", "An air or water ambulance.", "NON_FACILITY", False),
    ("49", "Independent Clinic", "An independent clinic.", "NON_FACILITY", False),
    ("50", "Federally Qualified Health Center", "An FQHC.", "NON_FACILITY", False),
    ("51", "Inpatient Psychiatric Facility", "An inpatient psychiatric facility.", "FACILITY", True),
    ("52", "Psychiatric Facility - Partial Hospitalization", "Partial hospitalization psychiatric.", "FACILITY", False),
    ("53", "Community Mental Health Center", "A community mental health center.", "NON_FACILITY", False),
    ("54", "Intermediate Care Facility/Intellectually Disabled", "ICF/IID.", "FACILITY", True),
    ("55", "Residential Substance Abuse Treatment", "A residential substance abuse facility.", "FACILITY", True),
    ("56", "Psychiatric Residential Treatment Center", "A PRTC.", "FACILITY", True),
    ("57", "Non-residential Substance Abuse Treatment", "Non-residential substance abuse.", "NON_FACILITY", False),
    ("58", "Non-residential Opioid Treatment", "Non-residential opioid treatment.", "NON_FACILITY", False),
    ("60", "Mass Immunization Center", "A mass immunization center.", "NON_FACILITY", False),
    ("61", "Comprehensive Inpatient Rehab", "An inpatient rehab facility.", "FACILITY", True),
    ("62", "Comprehensive Outpatient Rehab", "An outpatient rehab facility.", "NON_FACILITY", False),
    ("65", "End-Stage Renal Disease Treatment", "An ESRD treatment facility.", "NON_FACILITY", False),
    ("71", "State or Local Public Health Clinic", "A public health clinic.", "NON_FACILITY", False),
    ("72", "Rural Health Clinic", "A rural health clinic.", "NON_FACILITY", False),
    ("81", "Independent Laboratory", "An independent laboratory.", "NON_FACILITY", False),
    ("99", "Other Place of Service", "An unlisted place of service.", "NON_FACILITY", False),
]


def build_ref_place_of_service() -> pd.DataFrame:
    print("\n--- ref_place_of_service ---")
    rows = []
    for code, name, desc, ftype, inpatient in POS_CODES:
        rows.append({
            "pos_code": code,
            "pos_name": name,
            "pos_description": desc,
            "facility_type": ftype,
            "is_inpatient": inpatient,
        })
    df = pd.DataFrame(rows)
    print(f"  {len(df)} place-of-service codes")
    return df


# ---------------------------------------------------------------------------
# ref_synpuf_benchmark
# ---------------------------------------------------------------------------

def build_ref_synpuf_benchmark() -> pd.DataFrame:
    print("\n--- ref_synpuf_benchmark ---")
    synpuf_dir = fetch_mod.cache_dir() / "synpuf_beneficiary"

    if not synpuf_dir.exists() or not any(synpuf_dir.iterdir()):
        print("  SynPUF data not available — building synthetic benchmarks")
        return _build_synthetic_benchmarks()

    csv_file = _find_file(synpuf_dir, "*.csv", "DE1*")
    if csv_file is None:
        files = list(synpuf_dir.iterdir())
        if not files:
            return _build_synthetic_benchmarks()
        csv_file = files[0]

    return _build_benchmarks_from_synpuf(csv_file)


def _build_benchmarks_from_synpuf(path: Path) -> pd.DataFrame:
    """Extract percentile distributions from the SynPUF beneficiary file."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, on_bad_lines="skip")
    df.columns = [c.strip().upper() for c in df.columns]

    # SynPUF beneficiary columns with cost data
    cost_cols = {}
    for c in df.columns:
        if any(k in c for k in ("PPPYMT", "IP_ER", "OT_ER", "REIM", "PAYMENT", "COST", "AMT")):
            cost_cols[c] = c.lower().replace("_amt", "").replace("bene_", "")

    if not cost_cols:
        print(f"  no cost columns found in {path.name}, using synthetic benchmarks")
        return _build_synthetic_benchmarks()

    rows = []
    for col, metric in cost_cols.items():
        values = pd.to_numeric(df[col], errors="coerce").dropna()
        if len(values) < 10:
            continue
        rows.append({
            "benchmark_id": f"ALL_{metric}",
            "cohort": "ALL",
            "metric_name": metric,
            "p10": float(values.quantile(0.10)),
            "p25": float(values.quantile(0.25)),
            "p50": float(values.quantile(0.50)),
            "p75": float(values.quantile(0.75)),
            "p90": float(values.quantile(0.90)),
            "mean_value": float(values.mean()),
            "stddev_value": float(values.std()),
            "observation_count": len(values),
            "source_year": "2008",
        })

    if not rows:
        return _build_synthetic_benchmarks()

    out = pd.DataFrame(rows)
    print(f"  computed {len(out)} benchmark metrics from {path.name}")
    return out


def _build_synthetic_benchmarks() -> pd.DataFrame:
    """Fallback benchmarks when SynPUF data is unavailable."""
    rng = det.rng("ref_synpuf_benchmark")
    metrics = [
        ("inpatient_beneficiary_responsibility", 0, 500, 1200, 3500, 8000),
        ("outpatient_beneficiary_responsibility", 0, 100, 350, 800, 2000),
        ("prescription_beneficiary_responsibility", 0, 200, 600, 1500, 4000),
        ("total_reimbursement", 500, 2000, 5000, 15000, 45000),
        ("inpatient_reimbursement", 0, 0, 2000, 8000, 25000),
        ("outpatient_reimbursement", 100, 500, 1500, 4000, 12000),
    ]
    rows = []
    for metric, p10, p25, p50, p75, p90 in metrics:
        mean_val = (p10 + p25 + p50 + p75 + p90) / 5.0
        std_val = (p90 - p10) / 3.0
        rows.append({
            "benchmark_id": f"ALL_{metric}",
            "cohort": "ALL",
            "metric_name": metric,
            "p10": float(p10),
            "p25": float(p25),
            "p50": float(p50),
            "p75": float(p75),
            "p90": float(p90),
            "mean_value": round(mean_val, 2),
            "stddev_value": round(std_val, 2),
            "observation_count": 100000,
            "source_year": "2008",
        })
    print(f"  built {len(rows)} synthetic benchmark rows (SynPUF unavailable)")
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("Building bronze reference tables")
    print("=" * 78)

    builders: list[tuple[str, callable]] = [
        ("ref_icd10cm", build_ref_icd10cm),
        ("ref_hcpcs", build_ref_hcpcs),
        ("ref_ndc_product", build_ref_ndc_product),
        ("ref_provider_npi", build_ref_provider_npi),
        ("ref_place_of_service", build_ref_place_of_service),
        ("ref_synpuf_benchmark", build_ref_synpuf_benchmark),
    ]

    source_map = {
        "ref_icd10cm": "CMS_ICD10",
        "ref_hcpcs": "CMS_HCPCS",
        "ref_ndc_product": "FDA_NDC",
        "ref_provider_npi": "NPPES",
        "ref_place_of_service": "CMS_POS",
        "ref_synpuf_benchmark": "CMS_SYNPUF",
    }

    errors = []
    for name, builder in builders:
        try:
            df = builder()
            write_table(df, "bronze", name, source_system=source_map[name])
        except Exception as exc:
            print(f"\n  ERROR building {name}: {exc}")
            errors.append((name, exc))

    print("\n" + "=" * 78)
    if errors:
        print(f"FAILED: {len(errors)} reference tables could not be built:")
        for name, exc in errors:
            print(f"  {name}: {exc}")
        return 1

    print("All 6 reference tables built successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
