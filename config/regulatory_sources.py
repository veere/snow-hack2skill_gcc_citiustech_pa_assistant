"""
Registry of regulatory source documents for the PA copilot.

Every URL here was fetched and confirmed to return HTTP 200 with real content on
2026-09-01 from this machine. Notes record the traps found while verifying, so a
future maintainer does not rediscover them:

* eCFR: the `current` snapshot 404s on the /full/ endpoint. A DATED snapshot works.
  Section-level retrieval also needs the full hierarchy (chapter/subchapter/part/
  subpart), not just `part` + `section`.
* HHS OIG: the bare host `oig.hhs.gov` refuses the connection. `www.oig.hhs.gov`
  serves the PDF.
* All sources are US federal or state government works, hence public domain. No
  proprietary criteria sets (InterQual, MCG) are included - they are licensed and
  cannot be redistributed.
"""

from __future__ import annotations

from config.app_config import RegDoc

# eCFR REST base. The date is pinned so the corpus is reproducible: re-running the
# fetch months later retrieves the same text rather than silently drifting.
ECFR_SNAPSHOT_DATE = "2026-01-01"
ECFR_BASE = "https://www.ecfr.gov/api/versioner/v1/full"


def _ecfr_url(title: int) -> str:
    return f"{ECFR_BASE}/{ECFR_SNAPSHOT_DATE}/title-{title}.xml"


REG_DOCS: tuple[RegDoc, ...] = (
    # ---------------------------------------------------------------- CFR: SLA
    RegDoc(
        key="cfr_42_422_subpartM",
        title=(
            "42 CFR Part 422 Subpart M - Grievances, Organization Determinations "
            "and Appeals (Medicare Advantage)"
        ),
        citation_id="42 CFR 422 Subpart M",
        authority="CMS / HHS",
        url=_ecfr_url(42),
        fmt="ecfr_xml",
        themes=("SLA_COMPLIANCE", "PA_HISTORY_PRECEDENT", "ELIGIBILITY"),
        effective_date=ECFR_SNAPSHOT_DATE,
        ecfr_params={"chapter": "IV", "subchapter": "B", "part": "422", "subpart": "M"},
        notes=(
            "Pulled at subpart level (422.560-422.626) so cross-references between "
            "sections stay resolvable. Contains 422.568 standard timeframes and "
            "422.572 expedited timeframes - the two an analyst cites most."
        ),
    ),
    RegDoc(
        key="cfr_42_438_subpartF",
        title="42 CFR Part 438 Subpart F - Grievance and Appeal System (Medicaid Managed Care)",
        citation_id="42 CFR 438 Subpart F",
        authority="CMS / HHS",
        url=_ecfr_url(42),
        fmt="ecfr_xml",
        themes=("SLA_COMPLIANCE", "BENEFITS_COVERAGE", "ELIGIBILITY"),
        effective_date=ECFR_SNAPSHOT_DATE,
        ecfr_params={"chapter": "IV", "subchapter": "C", "part": "438", "subpart": "F"},
        notes="Medicaid managed care appeal/grievance timeframes.",
    ),
    RegDoc(
        key="cfr_42_438_210",
        title="42 CFR 438.210 - Coverage and Authorization of Services",
        citation_id="42 CFR 438.210",
        authority="CMS / HHS",
        url=_ecfr_url(42),
        fmt="ecfr_xml",
        themes=("BENEFITS_COVERAGE", "CLINICAL_NECESSITY", "SLA_COMPLIANCE"),
        effective_date=ECFR_SNAPSHOT_DATE,
        ecfr_params={
            "chapter": "IV",
            "subchapter": "C",
            "part": "438",
            "subpart": "D",
            "section": "438.210",
        },
        notes=(
            "The core Medicaid service-authorization rule: medical necessity "
            "standard, authorization timeframes, and the prohibition on arbitrarily "
            "denying or reducing an authorized service."
        ),
    ),
    RegDoc(
        key="cfr_29_2560_503_1",
        title="29 CFR 2560.503-1 - ERISA Claims Procedure",
        citation_id="29 CFR 2560.503-1",
        authority="Department of Labor (EBSA)",
        url=_ecfr_url(29),
        fmt="ecfr_xml",
        themes=("SLA_COMPLIANCE", "ELIGIBILITY", "PA_HISTORY_PRECEDENT"),
        effective_date=ECFR_SNAPSHOT_DATE,
        ecfr_params={
            "subtitle": "B",
            "chapter": "XXV",
            "subchapter": "L",
            "part": "2560",
            "section": "2560.503-1",
        },
        notes=(
            "Governs commercial/self-funded ERISA plans: 15 days pre-service "
            "non-urgent, 72 hours urgent, and the adverse-benefit-determination "
            "notice content requirements."
        ),
    ),
    # ------------------------------------------------------- CMS-0057-F final rule
    RegDoc(
        key="cms_0057_f_pdf",
        title=(
            "CMS Interoperability and Prior Authorization Final Rule (CMS-0057-F), "
            "89 FR 8758"
        ),
        citation_id="CMS-0057-F (89 FR 8758)",
        authority="CMS / HHS",
        url="https://www.govinfo.gov/content/pkg/FR-2024-02-08/pdf/2024-00895.pdf",
        fmt="pdf",
        themes=("SLA_COMPLIANCE", "PA_HISTORY_PRECEDENT", "PROVIDER_NETWORK"),
        effective_date="2024-02-08",
        notes=(
            "17.3 MB / ~600 pages. The source of the 7-calendar-day standard and "
            "72-hour expedited turnaround effective 2026-01-01, the specific-denial-"
            "reason requirement, and the 2027 FHIR PA API mandate. Parsed with "
            "AI_PARSE_DOCUMENT because it is far too large to chunk naively."
        ),
    ),
    RegDoc(
        key="cms_0057_f_meta",
        title="CMS-0057-F Federal Register metadata and structured full text",
        citation_id="FR Doc. 2024-00895",
        authority="Office of the Federal Register",
        url="https://www.federalregister.gov/api/v1/documents/2024-00895.json",
        fmt="fr_json",
        themes=("SLA_COMPLIANCE",),
        effective_date="2024-02-08",
        notes=(
            "JSON metadata carrying agencies, CFR references, effective dates and a "
            "full_text_xml_url. Used to attach authoritative dates to the PDF chunks."
        ),
    ),
    # ------------------------------------------------------------- CMS manuals
    RegDoc(
        key="cms_mcm_ch4",
        title="Medicare Managed Care Manual, Chapter 4 - Benefits and Beneficiary Protections",
        citation_id="CMS Pub. 100-16 Ch. 4",
        authority="CMS",
        url="https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/mc86c04.pdf",
        fmt="pdf",
        themes=("BENEFITS_COVERAGE", "CLINICAL_NECESSITY", "ELIGIBILITY"),
        notes="535 KB / ~111 pages. Benefit design, coverage rules, medical necessity basis.",
    ),
    RegDoc(
        key="cms_bpm_ch16",
        title="Medicare Benefit Policy Manual, Chapter 16 - General Exclusions from Coverage",
        citation_id="CMS Pub. 100-02 Ch. 16",
        authority="CMS",
        url="https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/bp102c16.pdf",
        fmt="pdf",
        themes=("BENEFITS_COVERAGE",),
        notes="Exclusions an analyst checks before any necessity review.",
    ),
    # ----------------------------------------------------------------- OIG
    RegDoc(
        key="oig_oei_09_18_00260",
        title=(
            "HHS OIG - Some Medicare Advantage Organization Denials of Prior "
            "Authorization Requests Raise Concerns About Beneficiary Access to "
            "Medically Necessary Care"
        ),
        citation_id="OIG OEI-09-18-00260",
        authority="HHS Office of Inspector General",
        url="https://www.oig.hhs.gov/oei/reports/OEI-09-18-00260.pdf",
        fmt="pdf",
        themes=("PA_HISTORY_PRECEDENT", "CLINICAL_NECESSITY", "SLA_COMPLIANCE"),
        effective_date="2022-04-01",
        notes=(
            "967 KB. NOTE the 'www.' prefix is required - the bare host refuses "
            "connections. Found 13% of sampled MA PA denials met Medicare coverage "
            "rules, which is why an analyst treats absent evidence as INDETERMINATE "
            "rather than as grounds to deny."
        ),
    ),
    # ------------------------------------------------------- State: gold-carding
    RegDoc(
        key="tx_ins_4201",
        title=(
            "Texas HB 3459 (87R, enrolled) - Preauthorization Exemptions for "
            "Physicians and Health Care Providers, amending Insurance Code Ch. 4201"
        ),
        citation_id="Tex. HB 3459 (87R) / Tex. Ins. Code 4201.651-4201.659",
        authority="Texas Legislature",
        url="https://capitol.texas.gov/tlodocs/87R/billtext/pdf/HB03459F.pdf",
        fmt="pdf",
        themes=("PROVIDER_NETWORK", "PA_HISTORY_PRECEDENT"),
        licence="Public domain (Texas state government work)",
        effective_date="2022-01-01",
        fallback_urls=(
            "https://capitol.texas.gov/tlodocs/87R/billtext/html/HB03459F.htm",
        ),
        notes=(
            "The ENROLLED BILL, deliberately not statutes.capitol.texas.gov. That "
            "site is a JavaScript single-page app: fetching IN.4201.htm returns "
            "250 KB of markup containing only ~1.4 KB of navigation chrome and NONE "
            "of the statute text, which silently produced one useless chunk. The "
            "enrolled bill PDF is 12 pages of real text and verifiably contains "
            "4201.651-659, 'exempt', 'preauthorization' and the '90 percent' "
            "threshold. Codifies the gold-carding mandate that mirrors the "
            "gold_card_eligible flag in VW_PROVIDER_NETWORK."
        ),
    ),
)


def by_key(key: str) -> RegDoc:
    for doc in REG_DOCS:
        if doc.key == key:
            return doc
    raise KeyError(f"unknown regulatory doc key: {key!r}")


def keys() -> tuple[str, ...]:
    return tuple(d.key for d in REG_DOCS)
