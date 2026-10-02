# Datasets — Member360 PA Datamart

How the bronze layer was sourced, synthesised and validated. Written for human
review: every claim below is checkable against the artefacts in this repo.

---

## 1. Principle

The mart mixes **real open data** with **grounded synthesis**, and the boundary is
always explicit. Nothing synthetic is presented as real, and nothing real is
altered except for a single documented date shift.

| Kind | What it covers | Where it comes from |
|---|---|---|
| **Real reference data** | ICD-10-CM, HCPCS Level II, NDC drug directory, NPI registry, place-of-service codes | Direct CMS / FDA / NPPES downloads |
| **Real synthetic population** | Patients, encounters, conditions, procedures, observations, medications, payers | Synthea (MITRE) — synthetic by construction, statistically calibrated to real US population health |
| **Curated by hand** | CPT Level I codes, SNOMED→ICD-10/CPT crosswalk, specialty-drug classification, PA criteria rulesets | Authored here; provenance recorded as `CURATED` |
| **Synthesised** | Prior authorization cases, member plans/finances, member communications, EHR event stream | Generated, but **grounded** in a real Synthea clinical record |

There is no real patient data anywhere in this mart, and no PHI. Synthea output is
synthetic at source.

---

## 2. Open datasets used

All five were fetched from their **primary** URL — no fallback was needed on this
run. Checksums are recorded automatically to `.cache/fetch_manifest.json` at fetch
time, so the documentation describes the bytes that were actually used rather than
what was expected.

| Dataset | Size | SHA-256 (first 24) | Role |
|---|---|---|---|
| **Synthea** sample CSV (Apr 2020) | 8.98 MB | `4194b18c11eaedcf0d5d5dd4` | **PRIMARY** population — 1,171 patients across 16 referentially-intact CSVs |
| **FDA NDC Directory** | 10.81 MB | `983f41fcbc2a944eebc9f358` | Real drug codes, ingredients, pharmacologic classes, DEA schedules |
| **CMS ICD-10-CM 2026** | 2.20 MB | `55a9124a27ca78a4a5c41e11` | Real diagnosis codes — the crosswalk target |
| **CMS HCPCS Level II** (Oct 2026) | 2.50 MB | `36dc098353acd706a1081188` | Real procedure/supply/drug codes |
| **CMS DE-SynPUF** beneficiary summary | 3.12 MB | `2b0c9cbfb07a6eb46d5f9647` | **Calibration only** — real Medicare cost-sharing distributions |
| **NPPES NPI Registry API** | — | (API, not archived) | 1,181 **real** provider NPIs across 6 Massachusetts cities |

Total download ≈ 27 MB, cached under `.cache/` and reused on rerun.

### Why these

- **Synthea over SynPUF as the population.** SynPUF has more claims volume, but its
  records are not referentially linked in a way that supports a member-360 view.
  Synthea gives one coherent person with conditions, encounters, medications, labs
  and payer history that all join — which is what a PA analyst actually needs.
- **SynPUF retained for calibration.** Rather than inventing deductible and
  coinsurance distributions, `ref_synpuf_benchmark` holds percentile shapes derived
  from real Medicare beneficiary cost-sharing, and synthetic accumulators are drawn
  against those shapes.
- **NPPES via API, not the bulk file.** The monthly full file is ~1.1 GB for data we
  need a few thousand rows of. The API requires `city` + `state` (state alone is
  rejected), so six MA cities are queried. Providers that could not be sourced are
  flagged `is_synthetic_npi = TRUE` and given Luhn-valid NPIs so they pass the same
  format validation a real system would apply.

### Fallback strategy

CMS reorganises file paths and renames quarterly releases, so every dataset
declares fallback URLs (`config/mart_config.json → open_datasets`). ICD-10 falls
back through prior years then a GitHub mirror; HCPCS walks back through quarterly
releases. Required datasets raise on total failure; optional ones are skipped with
a warning so the pipeline stays runnable.

---

## 3. A finding that shaped the design: CPT is not in any free CMS file

The CMS HCPCS "ANWEB" release contains **8,769 Level II alphanumeric codes (A–V)
and dental D codes, and zero numeric CPT Level I codes.** CPT is copyrighted by the
AMA and is not distributed in any free CMS download.

That matters because **prior authorization is transacted on CPT**. An analyst
reviewing an imaging request works with `72148`, not a SNOMED concept id. A mart
built only on the CMS file would have had no codes for the highest-volume PA
category.

Resolution: `dataPrepBronze/reference_curated.py` carries **161 hand-verified real
CPT codes** spanning all thirteen PA service categories with realistic allowed
amounts, loaded alongside the 8,769 real HCPCS codes and marked
`code_edition = 'CPT-CURATED-2026'` so the provenance is never ambiguous.

**This also produced the single worst defect found in the build.** The original
HCPCS parser read the code from character positions 3–8 instead of 1–5 (per the CMS
record layout shipped with the data), and did not skip **modifier rows** — which the
file interleaves with code rows, identified by positions 1–3 being blank. The result
was 1,676 junk identifiers like `00001` and `21001`, with coverage-indicator letters
(`O`, `N`, `B`) landing in `pa_service_category` despite that column having an
enumerated domain. It is now parsed from the documented layout and yields 8,930
valid codes.

---

## 4. Data synthesis

### 4.1 The date anchor

Synthea's sample population was generated in 2020, so its newest encounter is years
in the past. A PA mart in that state is useless: every SLA clock is expired, no case
is genuinely open, and "days until the regulatory deadline" is negative everywhere.

A single integer offset is therefore computed once and applied to **every date in
every table**:

```
offset = (reference_date − 3 days) − max_source_date
       = (2026-09-01 − 3) − 2020-04-28
       = +2,314 days
```

Because one offset is applied uniformly, **all intervals are preserved exactly** — a
6-week course of physical therapy is still 6 weeks, and an admission still precedes
its discharge. Only absolute position moves. The offset is cached to
`.cache/date_anchor.json` so every script and every rerun shares one timeline.

Set `date_anchor.reference_date` in `config/mart_config.json` to pin the pipeline for
a reproducible demo.

### 4.2 Grounded prior authorization

No public dataset contains real PA records, so `fct_memberPA` is synthesised. The
important property is that it is **grounded**: every request is seeded from an actual
Synthea condition, procedure or medication belonging to that member, and
`grounded_on_source` / `grounded_on_source_id` record which one. That is what makes
the clinical evidence in `ext_*` genuinely corroborate or contradict a request,
rather than being decorative.

Concretely:

- **Service category** is keyword-matched from the seeding record, with a weighted
  fallback reflecting real PA volume mix (advanced imaging and specialty drugs
  dominate; transplants are rare). The fallback is **restricted by seed type**, so a
  prescription can only become a drug-shaped request.
- **Diagnosis** is crosswalked from the seeding SNOMED concept, never drawn at
  random.
- **Procedure code** prefers the crosswalk of the seeding procedure, then a code
  carrying the requested PA category.
- **Filing dates** are compressed into a 730-day operational window, because a
  payer's UM system holds roughly two years of cases, not a member's lifetime.
- **~20% of cases are left open** with live SLA clocks spread across the regulatory
  window, including a slice deliberately past deadline so real breaches exist.

### 4.3 Regulatory turnaround times

Not invented. Sourced and cited in `config/schemas.py → TAT_RULES`:

| Line of business | Standard | Expedited | Authority |
|---|---|---|---|
| Medicare Advantage | 7 days | 72 h | CMS-0057-F (supersedes the 14-day standard from 2026-01-01) |
| Medicaid | 7 days | 72 h | CMS-0057-F |
| Marketplace | 7 days | 72 h | CMS-0057-F (QHP issuers on FFEs) |
| Commercial | 15 days | 72 h | 29 CFR 2560.503-1 (ERISA pre-service) |

Gold-carding uses the Texas HB 3459 threshold: 90% approval rate over a 6-month
lookback.

### 4.4 Deliberate imperfection

Realistic data is not clean data. The following are intentional:

- **~8% of provider-shared records have a missing or conflicting
  `payer_member_id`**, so identity resolution in silver is real work rather than a
  trivial join.
- **~7% of members are genuinely terminated**, so eligibility denials and
  coverage-gap scenarios exist to be found.
- **EHR events carry ingestion latency**, because those feeds lag in reality.
- **Some crosswalks are `CATEGORY_DEFAULT` rather than `CURATED_MAP`**, and say so.

### 4.5 Determinism

Every stochastic decision derives from `master_seed = 20260901` via
`common/determinism.py`. There are no unseeded RNG calls and no wall-clock values in
business data — `ingested_at_utc` is the only column that varies between runs, and it
is excluded from `record_hash`. A rerun with the same seed reproduces identical row
hashes.

---

## 5. Crosswalk methodology

Synthea codes clinical data in SNOMED-CT, LOINC and RxNorm. None of those are what a
payer adjudicates on. The complete Synthea vocabulary — **129 distinct diagnosis
concepts and 144 procedure concepts** — is crosswalked to real ICD-10-CM and
CPT/HCPCS.

Each diagnosis maps to a preferred ICD-10 code plus a category root. Every target is
**validated against the 97,572 real codes** in `ref_icd10cm`, and the outcome is
recorded honestly:

| `crosswalk_method` | Meaning |
|---|---|
| `CURATED_MAP` | The specific curated code exists in the CMS release |
| `CATEGORY_DEFAULT` | The specific code was absent, so the most specific real code under its category root was used |
| `UNMAPPED` | No crosswalk exists for this concept |

So every emitted code is a **real** code, and the analyst can see how much precision
the mapping carries rather than having to assume.

Achieved coverage: **100% of diagnoses** and **86.3% of procedures**
(`CURATED_MAP` 38,436 / `CATEGORY_DEFAULT` 121 / `UNMAPPED` 4,800; the remainder are
labs and vitals, which correctly need no procedure crosswalk).

### Specialty drugs

Synthea prescribes in RxNorm and **never emits an NDC**, so joining medications to
`ref_ndc_product` on a code returns nothing — which is why the specialty flags were
initially empty for all 42,989 medication rows. Classification is therefore by
**ingredient name** against a curated table of real high-cost biologics and specialty
agents, grouped by therapeutic class so step-therapy rules have a coherent set to
evaluate within. Result: 4,667 specialty, 22,102 first-line.

---

## 6. Validation

Three layers of checking, all runnable:

```bash
python validate/check_contract.py      # schema contract self-check
python validate/smoke_test_loader.py   # spec -> parquet -> Snowflake round-trip
python load/run_load.py --layer all    # load with row-count reconciliation
```

### Enforced at write time (`common/frames.write_table`)

Every table must pass before a parquet file is produced:

- all spec columns present, in declared physical order; unexpected columns rejected
- declared `NOT NULL` columns contain no nulls
- **primary key uniqueness** — see below
- dtypes coerced from the Snowflake type, so `DATE` really is a date

### Enforced at load time

- explicit typed projection per column, so a type problem surfaces at load rather
  than as a silent coercion
- **zero-row loads raise.** `COPY INTO` reports *success* when it matches no file, so
  without this check a staging-path error looks like a clean run
- parquet vs table row-count reconciliation for every table

### Why primary key checking is not optional

**Snowflake accepts `PRIMARY KEY` constraints but does not enforce them.** A duplicate
surrogate key loads without complaint and then silently fans out every downstream
join — a member with a duplicated key has their claims counted twice.

This is not theoretical. Surrogate keys were originally 6-digit truncated hashes,
which by the birthday bound collide with ~50% probability across just 1,200 keys —
and `member_id` did collide, giving two different patients the same identifier. IDs
are now generated by `common/identity.py` and `det.unique_stable_ids`, which resolve
collisions deterministically, and `write_table` fails the build on any duplicate.

### Final integrity state

| Layer | Tables | Rows | Duplicate PKs | Orphan FKs | NOT NULL violations |
|---|---|---|---|---|---|
| BRONZE | 17 | 987,484 | 0 | 0 | 0 |
| SILVER | 19 | 129,683 | 0 | 0 | 0 |
| GOLD | 13 | 125,241 | 0 | 0 | 0 |

Payer↔provider identity linkage: **100%** of asserted `payer_member_id` values
resolve to a real member.

---

## 7. Defects found and fixed

Recorded because several were **silent** — they produced plausible-looking data that
was wrong, and would have surfaced only in the app.

| # | Defect | Impact | Fix |
|---|---|---|---|
| 1 | `ref_hcpcs` parsed from wrong character positions, modifier rows not skipped | 1,676 junk codes; enum-constrained column polluted with indicator letters | Parse from the documented CMS record layout → 8,930 valid codes |
| 2 | CPT Level I absent from all CMS files | No codes for the highest-volume PA category | 161 curated real CPT codes, provenance-tagged |
| 3 | PA service category drawn uniformly over all 13 domains, seeded by Python `hash()` | TRANSPLANT was the #1 category at 17%; `hash()` is salted per process, so **determinism was broken** | Weighted mix by real PA volume, seeded via `det.child_seed` |
| 4 | Open-case flag ANDed with "received in last 30 days" | Only **4** open cases — the analyst worklist was effectively empty | Decide openness first, then re-anchor receipt into the live SLA window → 1,210 open |
| 5 | `primary_dx` was a random ICD-10 from the first 500 alphabetical codes, with a placeholder description | The grounding premise was not actually implemented | Resolve from the seeding SNOMED concept → 100% real codes and descriptions |
| 6 | `is_specialty_drug` hardcoded `False`; `is_first_line_therapy` a coin flip | Pharmacy PA had no evidence to stand on | Name-based classification → 4,667 specialty |
| 7 | `payer_member_id` held raw Synthea UUIDs | **0%** match to `dim_memberProfile` — the entire payer↔provider linkage matched nothing | Canonical mapping in `common/identity.py` → 100% |
| 8 | `member_id` primary key collided | Two patients shared one ID; Snowflake did not catch it | `unique_stable_ids` + write-time PK guard |
| 9 | Loader lowercased the staged filename, but `PUT` preserves case | `COPY INTO` matched no file and **reported success**, loading 0 rows into 11 tables | Use the real filename; raise on zero-row load |
| 10 | `shift_series` returned tz-aware for `Z`-suffixed columns and naive for others | Mixing them made the column `object` dtype, and `to_datetime(errors='coerce')` turned 335,533 `record_date` values into `NaT` | Always return tz-naive |
| 11 | Only 28 of 1,171 members currently covered | 97.6% of the book looked terminated; the most common analyst question was unanswerable | Extend living members' latest span open-ended, retaining ~7% termed → 93.1% active |
| 12 | `dim_memberFinance` looped coverage spans, not member-plan-years | Violated its own declared grain; double-counted accumulators | Enforce the natural key |
| 13 | `ref_ndc_product` VARCHAR widths too narrow for real FDA data | `COPY INTO` rejected conjugate-vaccine antigen lists | Widths raised from measured maxima |
| 14 | PA filing dates spanned the member's lifetime | `PA-1956-000001` with a live SLA clock | Compress into a 730-day operational window |
| 15 | Medication seeds could become any service category | TRANSPLANT requesting Metformin; ADVANCED_IMAGING requesting an oral contraceptive | Restrict the category fallback by seed type |

---

## 8. Reproducing the bronze layer

```bash
pip install -r requirements.txt

python dataPrepBronze/00_fetch_open_datasets.py     # download + compute date anchor
python dataPrepBronze/01_build_reference_tables.py  # ref_* code sets
python dataPrepBronze/02_build_dim_member.py        # member dimensions
python dataPrepBronze/03_build_ext_patient.py       # provider-shared tables
python dataPrepBronze/04_build_fct_transactions.py  # claims
python dataPrepBronze/05_build_fct_events.py        # member communications
python dataPrepBronze/06_build_fct_pa.py            # grounded PA cases
```

Order matters: step 00 computes the date anchor and 01 produces the code sets that
02–06 crosswalk against. Downloads are cached, so reruns are offline and fast.
Outputs land in `dataFilesBronze/` as parquet.

Everything runs **locally**. Nothing executes on Snowflake compute except DDL,
`COPY INTO`, and the SERVING views.

---

## 9. Regulatory corpus (app layer)

10 documents, all **US federal or state government works** and therefore public
domain. Fetched by `dataPrepRegulatory/fetch_regulatory.py`, chunked by
`build_regulatory_chunks.py`, declared in `config/regulatory_sources.py`.

Proprietary criteria sets (InterQual, MCG) are **deliberately excluded** — they are
licensed and cannot be redistributed.

| doc_key | Citation | Chunks | Format |
|---|---|---|---|
| `cfr_42_422_subpartM` | 42 CFR 422 Subpart M | 44 | eCFR XML |
| `cfr_42_438_subpartF` | 42 CFR 438 Subpart F | 9 | eCFR XML |
| `cfr_42_438_210` | 42 CFR 438.210 | 2 | eCFR XML |
| `cfr_29_2560_503_1` | 29 CFR 2560.503-1 | 11 | eCFR XML |
| `cms_0057_f_pdf` | CMS-0057-F (89 FR 8758) | 418 | PDF, 17.3 MB |
| `cms_0057_f_meta` | FR Doc. 2024-00895 | 1 | FR JSON API |
| `cms_mcm_ch4` | CMS Pub. 100-16 Ch. 4 | 71 | PDF |
| `cms_bpm_ch16` | CMS Pub. 100-02 Ch. 16 | 24 | PDF |
| `oig_oei_09_18_00260` | OIG OEI-09-18-00260 | 41 | PDF |
| `tx_ins_4201` | Tex. HB 3459 (87R) | 5 | PDF |

**626 chunks, 52 distinct citations.** Median chunk 4,786 chars.

### Sourcing traps, recorded so they are not rediscovered

- **eCFR** `/full/` returns 404 for the `current` snapshot. A **dated** snapshot works.
  Section retrieval also needs the whole hierarchy (chapter/subchapter/part/subpart),
  not just part+section. The date is pinned to `2026-01-01` so the corpus reproduces.
- **eCFR carries the citation in the source.** `<DIV8 N="438.210" TYPE="SECTION"
  hierarchy_metadata='{"citation":"42 CFR 438.210"}'>` — so the authoritative citation
  string is read, never reassembled. This is why chunking is section-level.
- **HHS OIG** requires the `www.` prefix; the bare host refuses the connection.
- **statutes.capitol.texas.gov is a JavaScript shell.** Fetching `IN.4201.htm` returns
  250 KB of markup containing ~1.4 KB of navigation chrome and **none** of the statute.
  It produced one useless chunk and passed every other check. Replaced with the
  enrolled **HB 3459 PDF**, which verifiably contains §4201.651-659, "exempt" and the
  "90 percent" threshold. A **text-yield guard** now fails any document that fetches
  large but parses tiny.
- A descriptive `User-Agent` is required or several .gov hosts reject the request.

---

## 10. Radiology reference library (app layer)

24 teaching images from **Wikimedia Commons**, for analyst orientation only. Never a
member's own studies, and never a basis for a determination.

### Licence gate — the most important part

`config/app_config.licence_allowed()` permits **CC0, Public domain and CC BY only**.
**CC BY-SA is deliberately excluded**: share-alike may extend obligations to a
proprietary payer application that displays the image. That is a legal judgement, so
the safe default is to omit it (`ALLOW_SHARE_ALIKE = False`).

Disqualified outright: **Radiopaedia** (CC BY-NC-SA — no commercial use), **CheXpert**,
**MIMIC-CXR**, **MURA** (data use agreements forbidding redistribution).

Loaded: CC0 ×12, Public domain ×5, CC BY 4.0 ×5, CC BY 3.0 ×1, CC BY 2.0 ×1.
**111 candidates rejected**, 79 of them on licence — the gate doing its job.

| Region | NORMAL | ABNORMAL |
|---|---|---|
| CHEST | 3 | 5 |
| BRAIN | 3 | 4 |
| KNEE | 3 | 1 |
| LUMBAR_SPINE | **0** | 5 |

**The lumbar gap is real and reported, not padded.** Normal lumbar studies on Commons
are almost entirely CC BY-SA. The UI states this explicitly rather than substituting an
image from another region.

### Label validation — five real mislabels caught

Search results cannot be trusted at face value. Each of these was **accepted** by an
earlier version of the validator, and each is now a regression test in
`validate/test_radiology_labels.py` (16 cases):

| Accepted as | Actually | Why it slipped through |
|---|---|---|
| LUMBAR NORMAL | *Hernie discale L4 L5* — French for disc herniation | pathology regex was English-only |
| KNEE NORMAL | a **foot** X-ray | body region was never verified |
| KNEE NORMAL | *child with polymelia* — paediatric limb malformation | no paediatric exclusion |
| CHEST NORMAL | *Stüve-Wiedemann* skeletal dysplasia | "normal" appeared only in the description |
| KNEE NORMAL | *Postoperative … knee prosthesis* | `prosthe\b` cannot match "prosthe**sis**" |

That last one exposed a systemic bug: the patterns were written as `\b(stem)\b`, and
the **trailing** `\b` silently disabled every stem — `herniat\b` cannot match
"herniation" either. Stems now anchor the start only.

The converse error also had to be fixed: checking the *description* for contradictions
rejected 25 CC0 files titled "CT of a **normal** brain" because their shared
description mentions the trauma workup the scan was acquired in. A normal study
acquired in a trauma protocol is still normal. So a NORMAL claim is vetoed on the
**title** only — a title is a deliberate label, a description is prose.

Also learned: `filetype:bitmap` in the Commons search removed 135 PDF and OGG
candidates per run, and **all 37** "download failed" rejections were 429 rate limits,
not bad images — image fetches now share the API throttle.
