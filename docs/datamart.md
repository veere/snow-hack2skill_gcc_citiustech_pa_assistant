# Member360 Prior Authorization Datamart

A medallion-architecture datamart in Snowflake `M360MART`, built to support an
**exploratory, non-decision-making** conversational assistant that helps payer
prior-authorization analysts work faster.

---

## 1. What this is for

A PA analyst answers the same questions on every case: is the member eligible, does
the plan cover this, has the clinical evidence been submitted, what did we decide
last time, is the provider in network, how long until the regulatory deadline. Those
answers normally live in six different systems.

This mart consolidates them so one question gets one row.

**The assistant surfaces facts; it never decides.** This is enforced, not just
intended — see §6.

---

## 2. Architecture

```
M360MART
├── BRONZE    17 tables    987,484 rows   landed source + reference data
├── SILVER    19 tables    132,501 rows   conformed, derived, criteria-evaluated
├── GOLD      13 tables    123,688 rows   denormalised, one row per question
└── SERVING   14 views                    the ONLY surface the app reads
```

Data flows one way. Each layer reads only the layer above it.

| Layer | Purpose | Grain philosophy |
|---|---|---|
| **BRONZE** | Faithful landing. Real open data plus grounded synthesis, with lineage columns retained. | Source grain |
| **SILVER** | Conform, resolve identity, derive clinical evidence, evaluate medical-necessity criteria. | Business entity grain |
| **GOLD** | Answer an analyst's question without joins. Wide and denormalised. | Question grain |
| **SERVING** | Governance boundary. Drops direct identifiers, adds friendly names and column comments. | Same as GOLD |

### Naming

Table identifiers are written unquoted exactly as specified (`dim_memberProfile`).
Snowflake resolves unquoted identifiers to uppercase, so the physical name is
`DIM_MEMBERPROFILE` and every unquoted reference still resolves. **No quoted
identifiers anywhere**, so nothing downstream has to carry quoting through the app.

---

## 3. BRONZE — 17 tables

### Reference code sets (real open data)

| Table | Rows | Source |
|---|---|---|
| `ref_icd10cm` | 97,572 | CMS ICD-10-CM 2026 |
| `ref_hcpcs` | 8,930 | 8,769 real CMS HCPCS Level II + 161 curated real CPT |
| `ref_ndc_product` | 114,740 | FDA National Drug Code directory |
| `ref_provider_npi` | 1,181 | **Real NPPES NPIs** (synthetic fallback flagged) |
| `ref_place_of_service` | 50 | CMS place-of-service codes |
| `ref_synpuf_benchmark` | 6 | CMS DE-SynPUF cost-sharing percentiles (calibration only) |

### Member dimensions (payer side)

| Table | Rows | Notes |
|---|---|---|
| `dim_memberProfile` | 1,171 | Demographics. 36 columns. |
| `dim_memberPlan` | 3,589 | Coverage spans, LOB, carve-outs, **and the regulatory TAT pair that drives every SLA calculation**. 93.1% of members currently active. |
| `dim_memberFinance` | 2,633 | Cost-sharing design + accumulators, calibrated to real Medicare distributions. |
| `dim_memberHistory` | 7,963 | Enrolment change log with coverage gaps and retroactive terminations. |

### Facts

| Table | Rows | Notes |
|---|---|---|
| `fct_memberPA` | 6,099 | **The centrepiece.** 72 columns. 1,206 open with live SLA clocks. |
| `fct_memberTransactions` | 163,249 | Claim lines, payments, adjustments. 40,002 linked to an authorization. |
| `fct_memberEvents` | 111,463 | Member contact history. 31,627 linked to a PA case. |

### Provider-shared (VBC partner side)

| Table | Rows | Notes |
|---|---|---|
| `ext_patientProfile` | 1,171 | Own MRN identity space; ~8% missing/conflicting member id **by design**. |
| `ext_patientClinicalRecords` | 343,909 | Diagnoses, procedures, labs, vitals, imaging — with ICD-10/CPT crosswalks. |
| `ext_patientEhrEvents` | 55,184 | ADT-style event stream with realistic ingestion lag. |
| `ext_patientMedicalHistory` | 68,574 | Conditions, medications (with PDC adherence), allergies, immunisations, BMI. |

**Why `ext_*` is separate from `dim_*`.** These are the same humans arriving from a
different system with different identifiers. Keeping them apart makes identity
resolution real work in silver — which mirrors how payer/provider data sharing
actually behaves, and gives the mart something honest to say about match confidence.

---

## 4. SILVER — 19 tables

**Member-centric (8):** `slv_member_master` (identity resolution) ·
`slv_member_coverage_span` (non-overlapping, gap-flagged) ·
`slv_member_financial_position` · `slv_member_condition_profile` ·
`slv_member_medication_history` (PDC adherence → step-therapy evidence) ·
`slv_member_conservative_care` (PT/OT/chiro/injection counts by body system) ·
`slv_member_imaging_history` (by modality and region) ·
`slv_member_lab_vitals_latest` (BMI, HbA1c, CRP, ESR, eGFR)

**PA-centric (7):** `slv_pa_case` (business-day TAT clock with pend pauses) ·
`slv_pa_decision_history` · `slv_pa_precedent_stats` (approval rates by
procedure × diagnosis × LOB) · `slv_pa_criteria_ruleset` (**63 versioned rules,
scoped by service category**) · `slv_pa_criteria_evaluation` (**32,501 evidence-based
evaluations**) · `slv_duplicate_pa_candidates` · `slv_benefit_pa_requirement`

**Provider and other (4):** `slv_provider_profile` (network, credentialing,
computed gold-card eligibility) · `slv_claims_summary` · `slv_utilization_flags` ·
`slv_member_event_summary`

### The criteria engine

This is the layer that does the real work. Each PA is matched to **only the criteria
applicable to its service category**, and each criterion is evaluated against actual
clinical evidence from seven silver tables:

| Criterion | Evaluated against |
|---|---|
| `BMI_THRESHOLD` | `slv_member_lab_vitals_latest.bmi_value` vs `threshold_value` |
| `LAB_THRESHOLD` | HbA1c / CRP / ESR / eGFR values |
| `CONSERVATIVE_CARE_DURATION` | PT visit counts and duration, matched on body system |
| `IMAGING_PREREQUISITE` | Prior imaging by modality and body region |
| `STEP_THERAPY` / `FAILED_FIRST_LINE` | First-line trials, therapy duration, PDC adherence, discontinuation reason |
| `DIAGNOSIS_SUPPORT` | PA diagnosis vs the member's recorded condition profile |
| `NO_DUPLICATE` | Overlapping open authorizations |

Results are **three-valued**: `MET`, `UNMET`, `INDETERMINATE`. `INDETERMINATE` means
the evidence is genuinely **absent** — which is itself what an analyst needs to know,
and is never collapsed into a pass or a fail.

Scoping is clinically correct: `BMI_THRESHOLD` applies only to elective surgery
(bariatric), sleep study (OSA) and transplant. `IMAGING_PREREQUISITE` only to
advanced imaging. `STEP_THERAPY` only to specialty drugs.

Resulting distribution — note that it varies by criterion, as real criteria do:

| Criterion | Evaluated | % MET |
|---|---|---|
| `BMI_THRESHOLD` | 388 | 2.8% |
| `IMAGING_PREREQUISITE` | 1,377 | 37.0% |
| `CONSERVATIVE_CARE_DURATION` | 2,128 | 74.4% |
| `STEP_THERAPY` | 2,571 | 78.6% |
| `DIAGNOSIS_SUPPORT` | 6,099 | 87.6% |
| `NO_DUPLICATE` | 6,099 | 88.9% |
| `LAB_THRESHOLD` | 2,722 | 91.1% |
| `FAILED_FIRST_LINE` | 2,827 | 91.9% |

`evidence_text` quotes the actual observed value, e.g.:

- `BMI 36.5 recorded 2026-05-15 below threshold 40`
- `PT 1 visits, 16 total over 32 days (WRIST); does not meet 6-week/6-visit minimum`
- `No prior X-ray on file; prior imaging limited to PET`
- `Diagnosis J0190 (Viral sinusitis) confirmed in member condition profile (resolved)`

---

## 5. GOLD — 13 tables · SERVING — 14 views

| Gold table | Rows | Serving view | Answers |
|---|---|---|---|
| `gold_pa_worklist` | 1,206 | `VW_PA_WORKLIST` | What should I work on next? |
| `gold_pa_request_360` | 6,099 | `VW_PA_REQUEST_360` | Everything about this case |
| `gold_pa_criteria_evidence` | 32,501 | `VW_PA_CRITERIA_EVIDENCE` | Which criteria are met, and on what evidence |
| `gold_pa_sla_tracking` | 6,099 | `VW_PA_SLA_TRACKING` | How long until the deadline |
| `gold_pa_decision_audit` | 5,204 | `VW_PA_DECISION_AUDIT` | Decision and appeal trail |
| `gold_pa_precedent` | 4,539 | `VW_PA_PRECEDENT` | How have we decided this before |
| `gold_pa_duplicate_safety_flags` | 6,099 | `VW_PA_DUPLICATE_SAFETY` | Duplicates, interactions, utilisation flags |
| `gold_member_eligibility_snapshot` | 1,171 | `VW_MEMBER_ELIGIBILITY` | Is the member eligible on the service date |
| `gold_member_clinical_summary` | 1,171 | `VW_MEMBER_CLINICAL_SUMMARY` | Conditions, meds, labs, BMI at a glance |
| `gold_member_financial_position` | 860 | `VW_MEMBER_FINANCIAL` | Deductible and OOP position |
| `gold_member_timeline` | 26,099 | `VW_MEMBER_TIMELINE` | Chronological member narrative |
| `gold_provider_network_summary` | 80 | `VW_PROVIDER_NETWORK` | In network, credentialed, gold-carded |
| `gold_pa_kpi_daily` | 34,635 | `VW_PA_KPI_DAILY` | Volume, approval rate, TAT, breach trends |
| — | — | `VW_ASSISTANT_DICTIONARY` (123 rows) | Self-describing catalogue for agent grounding |

`VW_ASSISTANT_DICTIONARY` is worth calling out: it exposes the view and column
inventory with descriptions, so a conversational agent can ground itself on what the
mart actually contains instead of guessing table names.

### SLA tracking

`sla_state` is derived from the real regulatory clock, with pend-period pauses
excluded. Current open worklist: **595 ON_TRACK, 602 AT_RISK**, spread realistically
across `EMERGENT` (24 h), `EXPEDITED` (72 h) and `STANDARD` (7 or 15 days by LOB).

---

## 6. Governance

### The assistant cannot recommend a decision

Enforced in three places:

1. **Contract level** — `config/mart_config.json → governance.banned_column_substrings`
   lists 11 forbidden patterns (`recommended_decision`, `approval_probability`,
   `should_approve`, `decision_score`, …).
2. **Build level** — `schemas.assert_no_banned_columns()` fails the build if any
   registered column matches.
3. **Deployed level** — verified against `INFORMATION_SCHEMA`: **0 matches** across
   all 14 SERVING views.

`gold_pa_criteria_evidence` reports per-criterion results, an evidence string and a
met/total count. It stops there. The analyst decides.

### Identifier minimisation

SERVING views exclude `ssn_last4`, `email`, `phone`, `address_line1`, `latitude`,
`longitude` and the Synthea lineage id. Verified: **0 leaked identifiers**.

### RBAC

| Role | Grants |
|---|---|
| `M360_APP_ROLE` | USAGE on database + `SERVING` schema, USAGE on warehouse, SELECT on the 14 views. **Nothing on BRONZE / SILVER / GOLD.** |
| `M360_ENGINEER_ROLE` | Full access for pipeline operation |

The Streamlit app runs as `M360_APP_ROLE` and is structurally incapable of reading
base tables.

---

## 7. Reproducing the mart

```bash
pip install -r requirements.txt
python validate/check_contract.py          # verify the schema contract

# bronze (order matters - 00 sets the date anchor, 01 builds code sets)
python dataPrepBronze/00_fetch_open_datasets.py
python dataPrepBronze/01_build_reference_tables.py
python dataPrepBronze/02_build_dim_member.py
python dataPrepBronze/03_build_ext_patient.py
python dataPrepBronze/04_build_fct_transactions.py
python dataPrepBronze/05_build_fct_events.py
python dataPrepBronze/06_build_fct_pa.py

# silver, then gold
python dataPrepSilver/build_silver_member.py
python dataPrepSilver/build_silver_pa.py
python dataPrepSilver/build_silver_provider.py
python dataPrepGold/build_gold_pa.py
python dataPrepGold/build_gold_member.py

# load and deploy
python load/run_load.py --layer all
python load/deploy_sql.py                  # SERVING views + RBAC
```

All Python runs **locally**. Nothing executes on Snowflake compute except DDL,
`COPY INTO`, and the views themselves.

### Repository layout

```
config/          mart_config.json, schemas.py (FROZEN contract), schemas_{bronze,silver,gold}.py
common/          determinism, identity, dates, fetch, frames, checks
dataPrepBronze/  00-06 generators + reference_curated.py (CPT set + crosswalks)
dataPrepSilver/  3 builders
dataPrepGold/    2 builders
dataFiles{Bronze,Silver,Gold}/   parquet output
sql/             00 schemas · 10/20/30 DDL · 40 views · 50 RBAC
load/            snowflake_loader.py, run_load.py, deploy_sql.py
validate/        check_contract.py, smoke_test_loader.py
docs/            PA_analyst_research.md (37 analyst questions + regulatory citations)
```

### Authentication

**Non-interactive only.** Interactive browser OAuth is deliberately unsupported —
its token expires mid-run, which leaves layers half-loaded, and it cannot work
unattended at all.

Set one of `SNOWFLAKE_PAT` (preferred), `SNOWFLAKE_PRIVATE_KEY_PATH`, or
`SNOWFLAKE_PASSWORD`. Account, user and role are read from
`~/.snowflake/connections.toml` — those are not secrets.

Store the credential once, then inject it per run so it never lands on disk or in
shell history:

```bash
cortex secret store snowflake_pat --prompt          # masked input, stored in OS keychain

cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python load/run_load.py --layer all
cortex secret run --map snowflake_pat=SNOWFLAKE_PAT -- python load/deploy_sql.py
```

`connect()` raises immediately with these instructions if no credential is present,
rather than blocking on a browser prompt. **No credential is stored in this
repository.**

---

## 8. Quality guarantees

Enforced by code, not convention:

| Guarantee | Where enforced |
|---|---|
| Column set, order and types match the frozen contract | `frames.write_table` |
| No nulls in `NOT NULL` columns | `frames.write_table` |
| **Primary keys unique** — Snowflake does *not* enforce these | `frames.write_table` |
| Zero-row loads raise — `COPY INTO` reports success on no-file-match | `snowflake_loader.load_table` |
| Parquet ↔ Snowflake row-count reconciliation | `snowflake_loader.print_recon` |
| No decision-recommending columns | `schemas.assert_no_banned_columns` |
| FK targets registered before dependants (load order) | `validate/check_contract.py` |
| Deterministic rerun — identical `record_hash` | `common/determinism.py` |

Current state: **1,243,673 rows across 49 tables, 0 duplicate primary keys, 0 orphan
foreign keys, 0 NOT NULL violations, 100% payer↔provider identity linkage.**

### Determinism

`master_seed = 20260901`. Every random draw derives from it via `det.rng(...)`.
`ingested_at_utc` is the only column that varies between runs and is excluded from
`record_hash`.

### Known limitations

- `AGE_GENDER_GATE`, `DURATION_QUANTITY_LIMIT` and `PLACE_OF_SERVICE` evaluate 100%
  `MET` — near-always satisfied in practice, but they carry little discriminating
  information as implemented.
- 249 of 1,171 members have no payer transitions in the Synthea source, so they have
  no plan or financial rows.
- `slv_provider_profile` covers 80 providers (the PA-participating subset), not all
  1,181 in `ref_provider_npi`.
- Drug–drug interaction flags in `gold_pa_duplicate_safety_flags` are synthesised
  from therapeutic-class overlap, not a real interaction database.

See `datasets.md` §7 for the 15 defects found and fixed during the build.

---

## 9. Application layer (SERVING)

The PA Insight Copilot sits on top of SERVING. Full detail in **`app.md`**.

### Objects added

| Object | Type | Contents |
|---|---|---|
| `SERVING.SV_PA_COPILOT` | Semantic view | 12 SERVING views, 118 columns, 12 validated verified queries |
| `SERVING.SVC_REGULATORY` | Cortex Search service | 626 citation-anchored chunks from 10 public-domain documents |
| `SERVING.REGULATORY_CHUNKS` | Table | Chunk text + full citation metadata |
| `SERVING.REGULATORY_SOURCES` | Table | One provenance row per source document |
| `SERVING.RADIOLOGY_REFERENCE` | Table | 24 licence-gated teaching images |
| `SERVING.STG_REGULATORY` | Stage | The 10 source documents themselves |
| `SERVING.STG_RADIOLOGY` | Stage | The reference images |
| `SERVING.STG_APP` | Stage | Streamlit source for the SiS deployment |
| `SERVING.PA_INSIGHT_COPILOT` | Streamlit | The standalone analyst surface |

`M360_APP_ROLE` holds 17 grants in SERVING and **zero** in BRONZE, SILVER or GOLD.
That negative guarantee is asserted, not assumed.

### Non-decision guarantee

Enforced in three independent places, and tested in both directions: it must block
the copilot **asserting** a determination, and must not block it **reporting** a
historical one. `validate/validate_app.py` covers 8 decision-seeking questions, 6
must-block phrasings and 9 must-allow phrasings.

### Defects found while building the app

Building the copilot exposed four upstream defects that the datamart's own validation
had not caught, because each produced plausible-looking values rather than errors.

| # | Defect | Root cause | Fix |
|---|---|---|---|
| 16 | `enrollment_status` and `line_of_business` NULL for all 1,171 members; `is_eligible_today` False for every one | `build_member_master` read both from `dim_memberProfile`, which does not carry them. `Series.get()` returns `None` for a missing label instead of raising | Derive from the member's current coverage span via a shared `_is_current_span()` rule. Now 858 ACTIVE / 313 TERMED, matching the span data exactly |
| 17 | `approval_rate_pct` systematically understated | Denominator was `total_requests`, including the 32% of requests that never reached a determination (RECEIVED, PENDING_CLINICAL, PENDED_FOR_INFO, PEER_TO_PEER, EXPIRED, WITHDRAWN) | Divide by `decided_count`. Rate is **NULL** — not 0.0 — for the 1,668 groups with nothing decided, because a fabricated 0% reads as "we always deny this" |
| 18 | Drug precedent unfindable | Precedent keyed on `requested_procedure_code`, which is NULL for the 47% of requests that are drugs (SPECIALTY_DRUG 100%, HOME_HEALTH 84%) | Added unified `service_code` = CPT/HCPCS else NDC. Zero NULLs |
| 19 | SQL files silently mis-split on deploy | `execute_script` split on `;` before stripping comments, so a semicolon inside a comment *or* inside a `COMMENT = '...'` literal created a false statement boundary. Latent because DDL is normally applied from the spec, not the `.sql` artefact | Quote- and comment-aware `split_sql_statements()`. Statement counts corrected: bronze 21→17, silver 22→19, gold 14→13 |

Two "defects" turned out to be **correct behaviour**, and the checks were wrong:

- `VW_PA_DECISION_AUDIT` has 5,204 rows for 4,893 PA ids. That is **event grain** — 311
  appealed cases carry both an ORIGINAL and an APPEAL row. The composite
  `(pa_id, decision_type)` is the true key; the fan-out was a bug in my verified query.
- `VW_MEMBER_FINANCIAL` covers 73% of members. Correct: a terminated member has no
  current-year accumulator. Against the right denominator it is **100% of active
  members**. The app renders absence as "not on record", never `$0`.

The grain checker was rewritten to assert these as expectations. A check that fires on
correct data trains you to ignore it.
