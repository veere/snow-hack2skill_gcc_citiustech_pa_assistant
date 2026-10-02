# Prior Authorization (PA) Analyst Research
## What a UM Nurse / PA Review Analyst Needs — Driving a "Gold" Serving Layer

---

## 1. Prior Authorization Workflow Steps (Payer Side)

A typical end-to-end PA workflow at a health insurer:

| Step | What happens | Who |
|------|-------------|-----|
| **1. Intake / Receipt** | PA request arrives (portal, fax, phone, X12 278, FHIR PAS API). Assigned a tracking number. | Intake clerk / auto-system |
| **2. Eligibility Verification** | Confirm member is active on the date of service, plan is in force, coverage segment identified. | Auto-system / intake |
| **3. Benefit / Coverage Check** | Does the member's plan cover the requested service? Is the service on the PA-required list? Check exclusions, carve-outs (e.g., behavioral health carved to vendor). | Benefit config system |
| **4. Duplicate / Existing Auth Check** | Is there already an open or recently approved auth for the same service/member? | Auto-system |
| **5. Administrative Review / Triage** | Is the request complete? Right CPT/HCPCS, valid ICD-10 diagnosis, clinical notes attached? Route to appropriate queue (medical, behavioral, pharmacy, DME). | PA coordinator |
| **6. Clinical / Medical Necessity Review** | UM nurse or clinical reviewer applies evidence-based criteria (InterQual, MCG, or internal policy) to determine if the service is medically necessary. | UM nurse / clinical reviewer |
| **7. Peer-to-Peer (if needed)** | If criteria not met at nurse level, case escalated to Medical Director for peer-to-peer with requesting physician. | Medical Director + ordering MD |
| **8. Decision** | Approve, deny, partially approve, modify (approve alternative), or pend for additional info. | UM nurse (approve) or MD (deny) |
| **9. Notification** | Communicate decision to provider and member within regulatory timelines. Include specific denial reason and appeal rights if denied. | Auto-system / letter generation |
| **10. Appeals (if denied)** | Provider or member may appeal. First-level internal appeal, then external/IRO review. | Appeals team |

**Key point:** Under NCQA UM standards and most state laws, only a licensed physician can issue a medical necessity denial. Nurses can approve but cannot deny.

---

## 2. Medical Necessity Criteria Frameworks

### InterQual (Change Healthcare / Optum)
- Level-of-care criteria (inpatient admission, observation, subacute, SNF, rehab, home health)
- Procedure-focused criteria for surgeries and interventions
- Structured as decision trees with clinical "checkpoints"
- Subset-based: you walk through branching yes/no clinical questions
- Updated annually; versioned (e.g., InterQual 2024.1)

### MCG (Milliman Care Guidelines)
- Indications for care — does the clinical picture support the requested service?
- Organized by care setting: ambulatory, inpatient, recovery facility, home care, general recovery
- Includes "Appropriate Use Criteria" for imaging
- Each guideline has numbered citations to peer-reviewed literature

### Common Criteria Patterns (across both frameworks)

| Pattern | Example |
|---------|---------|
| **Step therapy / fail-first** | Must try metformin before GLP-1 agonist; must try PT for 6 weeks before spinal surgery |
| **Conservative treatment duration** | 6-12 weeks of conservative management documented before approving knee replacement |
| **Failed first-line therapy** | Must document failure of ≥2 NSAIDs before COX-2 inhibitor approval |
| **Imaging prerequisites** | MRI required before approving surgery; X-ray required before approving MRI |
| **BMI thresholds** | Bariatric surgery typically requires BMI ≥40, or ≥35 with comorbidities |
| **Lab value thresholds** | Biologic drugs may require documented lab values (CRP, ESR for RA) |
| **Diagnosis-specific** | Genetic testing requires specific family history criteria |
| **Duration/quantity limits** | Home health limited to X visits per authorization period |
| **Age/gender criteria** | Certain screenings/procedures gated by age and risk factors |
| **Place of service** | Outpatient preferred over inpatient for specific procedures (e.g., arthroscopy) |

---

## 3. Regulatory Turnaround Time (TAT) Requirements

### CMS Interoperability and Prior Authorization Final Rule (CMS-0057-F)
Source: [CMS Fact Sheet, Jan 17, 2024](https://www.cms.gov/newsroom/fact-sheets/cms-interoperability-prior-authorization-final-rule-cms-0057-f)

Applies to: Medicare Advantage (MA), Medicaid FFS, Medicaid managed care, CHIP managed care, QHP issuers on FFEs.

| Request Type | Maximum Decision Time | Effective |
|-------------|----------------------|-----------|
| **Standard (non-urgent)** | **7 calendar days** | Jan 1, 2026 |
| **Expedited / Urgent** | **72 hours** | Jan 1, 2026 |

Additional CMS-0057-F requirements:
- Must provide **specific reason** for denial (not just "does not meet criteria") — effective Jan 1, 2026
- Must publicly report PA metrics annually (first report by March 31, 2026)
- Prior Authorization FHIR API required by Jan 1, 2027
- Excludes drug prior authorizations from the API and some timing requirements

### Medicare Advantage (pre-existing, 42 CFR §422.568)

| Request Type | TAT |
|-------------|-----|
| Standard pre-service | 14 calendar days (extendable by 14 more if justified) |
| Expedited pre-service | 72 hours |
| Part D standard drug | 72 hours |
| Part D expedited drug | 24 hours |

**Note:** CMS-0057-F tightens the MA standard pre-service from 14 → 7 days starting 2026.

### Medicaid Managed Care (42 CFR §438.210)

| Request Type | TAT |
|-------------|-----|
| Standard | 14 calendar days (extendable by 14 more) |
| Expedited | 72 hours |

### ERISA / Commercial (DOL jurisdiction)
- Federal ERISA plans: "urgent" pre-service = 72 hours; non-urgent pre-service = 15 calendar days (29 CFR §2560.503-1)
- Many state laws impose tighter requirements on fully-insured plans

### Selected State Laws (examples)

| State | Standard | Urgent | Notes |
|-------|----------|--------|-------|
| Texas (HB 3459, 2021) | 3 business days (prescription drug), 15 calendar days (medical) | 24 hours (life-threatening) | Gold-card law enacted |
| California | 5 business days | 72 hours | Knox-Keene Act |
| New York | 3 business days (non-urgent medical) | 24-72 hours | Varies by urgency |
| Florida | 7 calendar days | 72 hours | Mirrors CMS framework |
| Illinois | 5 business days | 24 hours | Shorter urgent window |

### NCQA UM Accreditation Standards (UM 5-7)
- Decisions in "clinically appropriate timeframe"
- Urgent: within 24-72 hours
- Non-urgent: within 14 calendar days
- Notification to practitioner within 24 hours of decision
- Written notification to member within 2 business days of decision

---

## 4. Common Reasons for PA Denial

### Top Denial Reasons
1. **Does not meet medical necessity criteria** — clinical documentation doesn't support the level/type of service per InterQual/MCG
2. **Incomplete/insufficient documentation** — missing clinical notes, operative reports, lab results, imaging results
3. **Step therapy not completed** — patient hasn't tried required first-line treatments
4. **Conservative treatment not exhausted** — e.g., PT not attempted before surgical intervention
5. **Service not covered under plan** — exclusion, benefit not purchased, cosmetic classification
6. **Out-of-network provider without authorization** — non-participating provider, no single-case agreement
7. **Duplicate request** — auth already exists for same date/service
8. **Expired referral or authorization** — request submitted after valid window
9. **Member not eligible** — coverage terminated, COBRA lapse, coordination of benefits issue
10. **Incorrect coding** — wrong CPT/HCPCS, diagnosis doesn't support procedure, place-of-service mismatch
11. **Experimental/investigational** — service classified as not yet proven
12. **Frequency/quantity exceeded** — exceeds plan-allowed visits or units

### Common Reasons for "Pend for Additional Information"
1. Missing clinical notes or progress notes
2. Missing imaging/lab results referenced in request
3. Missing prior treatment history (documentation of failed conservative care)
4. Incomplete provider information (NPI, TIN)
5. Missing pathology or biopsy results
6. Operative report not submitted (for post-service review)
7. Missing medication trial history (for step-therapy drugs)
8. Height/weight/BMI not documented (for bariatric, weight-based drugs)
9. Functional assessment not included (for rehab, DME)
10. Missing letter of medical necessity from ordering physician

---

## 5. Key Data Elements on a Standard PA Request

### X12 278 (Health Care Services Review — Request/Response)

The HIPAA X12 278 transaction is the EDI standard for PA. Key segments/loops and their data:

| Loop/Segment | Data Elements |
|-------------|---------------|
| **2000A — Utilization Mgmt Org** | UM org identifier |
| **2000B — Requester (Provider)** | Provider name, NPI, TIN/EIN, taxonomy code, contact info |
| **2000C — Subscriber** | Member ID, subscriber name, DOB, gender, relationship to subscriber |
| **2000D — Dependent** | Patient (if different from subscriber) — name, DOB, gender |
| **2000E — Patient Event** | Place of service, admission date, discharge date, diagnosis codes (ICD-10-CM), procedure codes (CPT/HCPCS), quantity/units, certification type (initial, renewal, extension), service type |
| **2000F — Service** | Specific service line: CPT/HCPCS code, ICD-10-PCS (inpatient), revenue code, modifier, requested quantity/units, from/to dates, facility info |
| **TRN** | Tracking/reference numbers |
| **UM** | Request category (admission, service, extension), certification type, level of service (elective/urgent/emergency), health care service location |
| **HI** | Diagnosis codes (principal + additional) |
| **DTP** | Dates — requested service date, admission date, certification effective/expiry |
| **REF** | Reference IDs — prior auth number, case number, provider control number |

### HL7 FHIR Da Vinci PAS (Prior Authorization Support) IG — STU 2.0.1

The FHIR PAS IG maps PA to a Claim resource (PAS Claim Profile). Key resources and data elements:

| FHIR Resource | Key Elements |
|--------------|--------------|
| **Claim** (PAS Request Profile) | claim.type (institutional/professional), claim.use = "preauthorization", claim.priority (normal/stat), claim.diagnosis (ICD-10), claim.procedure (CPT/ICD-10-PCS), claim.item (service lines with codes, quantities, dates, place of service) |
| **ClaimResponse** (PAS Response Profile) | disposition, item-level adjudication (approved/denied/pended), review action code, denial reason |
| **Patient** | Name, DOB, gender, member ID, address |
| **Practitioner / PractitionerRole** | NPI, name, specialty, taxonomy code, organizational affiliation |
| **Organization** | Payer org, provider org, TIN, address |
| **Coverage** | Plan ID, member ID, subscriber relationship, benefit period, group number |
| **Encounter** | Admission date, type (inpatient/outpatient/emergency), facility |
| **ServiceRequest** | Requested service (coded), quantity, frequency, occurrence period, reason reference (condition) |
| **Condition** | ICD-10-CM, clinical status, onset, evidence |
| **DocumentReference / Binary** | Attached clinical documents (notes, images, reports) supporting medical necessity |
| **Bundle** | Wraps all above into a single submission bundle |

**Key difference:** FHIR PAS supports **structured attachments** (questionnaire responses from DTR — Documentation Templates and Rules) and **unstructured attachments** (PDFs, images), whereas X12 278 is limited and attachments typically go via X12 275 or out-of-band (fax).

### Combined: The Essential Data Elements for Any PA Request
1. Member ID / subscriber ID
2. Patient demographics (name, DOB, gender, address)
3. Provider NPI (ordering + rendering + facility)
4. Provider taxonomy / specialty
5. Diagnosis codes (ICD-10-CM — primary + secondary)
6. Procedure codes (CPT / HCPCS / ICD-10-PCS)
7. Place of service code
8. Requested service dates (from/to)
9. Requested quantity / units / visits
10. Urgency indicator (routine / urgent / emergent)
11. Clinical justification / letter of medical necessity
12. Supporting documentation (clinical notes, labs, imaging)
13. Prior treatment history
14. Referring provider info
15. Admission type and date (if inpatient)
16. Plan/group number
17. Certification type (initial / extension / renewal)

---

## 6. Services Most Commonly Requiring Prior Authorization

Based on OIG, KFF, and AMA survey data:

### High-Volume PA Categories

| Category | Examples | Why PA Required |
|----------|----------|----------------|
| **Advanced Imaging** | MRI, CT, PET scans | High cost, potential overutilization |
| **Specialty Drugs / Biologics** | TNF inhibitors (Humira, Enbrel), oncology drugs, gene therapies, MS drugs | Extremely high cost ($10K-$100K+/year), step therapy |
| **Elective / Non-Emergent Surgery** | Joint replacement, spinal fusion, bariatric, cosmetic-adjacent procedures | Verify medical necessity vs. elective |
| **Durable Medical Equipment (DME)** | Power wheelchairs, CPAP, prosthetics, home oxygen | High cost, fraud risk |
| **Behavioral Health** | Inpatient psych, residential treatment, intensive outpatient (IOP), ABA therapy | Level-of-care appropriateness, duration management |
| **Inpatient Admissions** | All elective, many urgent (concurrent review) | Level-of-care, length of stay |
| **Rehabilitation** | Physical therapy >X visits, occupational therapy, speech therapy | Quantity/frequency limits, functional improvement required |
| **Genetic / Molecular Testing** | Whole-exome sequencing, pharmacogenomic panels, hereditary cancer panels | Appropriateness criteria, evolving coverage |
| **Home Health Services** | Skilled nursing visits, home infusion | Duration, visit limits, homebound status |
| **Outpatient Procedures (site-of-service)** | Procedures that can move to ambulatory surgery center vs. hospital outpatient | Cost steering, clinical appropriateness |
| **Transplants** | Solid organ, bone marrow/stem cell | Center of Excellence requirements, clinical readiness |
| **Sleep Studies** | Polysomnography, home sleep tests | Clinical appropriateness criteria |
| **Pain Management** | Spinal cord stimulators, epidurals beyond initial series, intrathecal pumps | Step therapy, conservative care first |
| **Radiation Therapy** | Proton beam, SBRT, SRS | Cost vs. conventional radiation, tumor type criteria |

**Scale:** The OIG found that MA plans received >35 million PA requests in 2021. The KFF reported that 99% of Marketplace enrollees are in plans requiring PA for at least some services.

---

## 7. Gold-Carding / PA Exemption Programs

### Definition
"Gold-carding" exempts high-performing providers from PA requirements for specific services based on their historical approval rate.

### Texas HB 3459 (effective June 2021; updated Sept 2023)
- First state to mandate gold-carding
- Providers with ≥90% approval rate over the prior 6 months for a given service are exempt from PA for that service
- Payer must re-evaluate every 6 months
- Payer may remove exemption if approval rate falls below 90%
- Source: Texas Insurance Code Chapter 4201, Subchapter N

### Other State Gold-Card Laws
- **West Virginia** (HB 2351, 2023): Similar 90% threshold
- **Louisiana** (SB 187, 2022): Gold-card provisions
- **Michigan**, **Montana**, and several others have introduced or passed similar legislation (2023-2025 sessions)

### Payer Voluntary Gold-Card Programs
- **UnitedHealthcare:** "Gold Card" program — exempts providers with 90%+ approval rates from PA for select services (commercial and MA products)
- **Aetna / CVS Health:** Provider auto-approval programs for high-approval-rate providers
- **Blue Cross Blue Shield plans:** Several BCBS affiliates have voluntary exemption programs

### Auto-Approval / Auto-Adjudication Logic
Many payers implement rules-engine-driven auto-adjudication:

| Auto-Approval Trigger | Example |
|-----------------------|---------|
| Diagnosis + procedure match on approved pathway | MRI lumbar spine + ICD-10 M54.5 (low back pain) + 6 weeks conservative care documented |
| Provider gold-card eligible | Provider NPI in exemption list for service type |
| Drug on preferred formulary tier + no step therapy required | Generic statin — auto-approve |
| Renewal with documented continued need | Home health recertification with documented improvement |
| Procedure below cost threshold | Outpatient procedure <$X, no review needed |
| Age/gender matching screening guideline | Colonoscopy for patient ≥45 with average risk |

### Auto-Denial Triggers (system-level, pre-clinical review)
| Trigger | Example |
|---------|---------|
| Service is a plan exclusion | Cosmetic surgery on standard plan |
| Member not eligible | Coverage terminated before service date |
| Provider not credentialed | Not contracted for the service type |
| Duplicate active auth | Same service already authorized for same period |

---

## 8. Synthesized Questions: What a PA Analyst Would Ask a Conversational Assistant

### Theme 1: ELIGIBILITY & ENROLLMENT (5 questions)
*Data needed: Member enrollment tables, coverage segments, COB (coordination of benefits), eligibility spans, plan assignments*

1. **"Is member [ID] active and eligible on [date of service]?"**
2. **"What plan and benefit package does this member have, and is there a behavioral health or pharmacy carve-out?"**
3. **"Does this member have other coverage? If so, which payer is primary vs. secondary?"**
4. **"Has this member's coverage had any gaps or retroactive terminations in the past 12 months?"**
5. **"Is this member in a Medicaid, Medicare Advantage, or commercial plan — and which regulatory TAT rules apply?"**

### Theme 2: BENEFITS & COVERAGE POLICY (5 questions)
*Data needed: Benefit configuration, PA-required service lists, medical policy library, exclusion lists, plan documents*

6. **"Does this member's plan require prior authorization for [CPT/HCPCS code]?"**
7. **"Is [service/procedure] excluded or subject to any plan limitations (visit caps, dollar limits, age limits)?"**
8. **"What are the medical policy criteria for [procedure] — which InterQual or MCG guideline applies, and what version?"**
9. **"Is this service subject to step therapy, and if so, what prior treatments must be tried first?"**
10. **"Are there site-of-service requirements — must this be done outpatient, at an ASC, or is inpatient allowed?"**

### Theme 3: CLINICAL EVIDENCE & MEDICAL NECESSITY (7 questions)
*Data needed: Claims history, clinical notes/attachments, lab results, imaging reports, medication fill history (pharmacy claims), prior auth history*

11. **"What clinical documentation has been submitted with this request — are there notes, labs, or imaging attached?"**
12. **"What is the member's diagnosis history — have they had [ICD-10 code] documented in claims or prior auths?"**
13. **"Has this member completed the required conservative treatment (e.g., 6+ weeks of PT) before this surgical request?"**
14. **"What medications has this member tried in the past 12 months — is step therapy satisfied based on pharmacy claims?"**
15. **"Are there recent lab results (e.g., BMI, HbA1c, CRP/ESR) in the clinical record that meet the threshold for approval?"**
16. **"Does the clinical documentation support the requested level of care per [InterQual/MCG guideline name and number]?"**
17. **"Has the ordering physician provided a letter of medical necessity, and does it address the specific criteria gaps?"**

### Theme 4: PRIOR AUTHORIZATION HISTORY & PRECEDENT (4 questions)
*Data needed: PA case history, decision log, auth status table, appeal outcomes*

18. **"Has this member had any prior authorizations for the same or related service — what were the decisions?"**
19. **"Is there an existing open authorization for this member/service that this request might duplicate?"**
20. **"Was a previous denial for this service overturned on appeal, and if so, what additional documentation was provided?"**
21. **"How have we historically decided requests for [CPT code] with [ICD-10 diagnosis] — what's the approval rate?"**

### Theme 5: PROVIDER & NETWORK (4 questions)
*Data needed: Provider directory, network/contract tables, credentialing status, gold-card eligibility, NPI registry*

22. **"Is the requesting provider [NPI] in-network for this member's plan, and are they credentialed for this service?"**
23. **"Is this provider gold-carded or exempt from PA for this service category?"**
24. **"If the provider is out-of-network, is there a single-case agreement (SCA) or gap exception in place?"**
25. **"Is the rendering facility in-network and appropriate for the place of service on this request?"**

### Theme 6: FINANCIAL & COST (3 questions)
*Data needed: Fee schedules, allowed amounts, member cost-sharing (copay/coinsurance/deductible), accumulator data*

26. **"What is the estimated allowed amount for this service, and does it exceed any cost threshold that triggers additional review?"**
27. **"What is the member's current deductible and out-of-pocket accumulation — how close are they to their max?"**
28. **"Is there a lower-cost clinically equivalent alternative (e.g., biosimilar, preferred drug, outpatient vs. inpatient)?"**

### Theme 7: SLA / TURNAROUND & COMPLIANCE (5 questions)
*Data needed: PA case timestamps, SLA configuration, regulatory rules engine, case aging reports*

29. **"When was this PA request received, and what is the regulatory deadline for a decision (standard vs. urgent)?"**
30. **"How many calendar days remain before this request exceeds the TAT requirement?"**
31. **"Is this request flagged as urgent/expedited, and if so, does the clinical situation justify expedited review?"**
32. **"Are there any pending requests for this member that are approaching their SLA deadline?"**
33. **"Has this request been pended for additional information — when was the pend letter sent, and has the response clock paused?"**

### Theme 8: DUPLICATES, SAFETY & QUALITY FLAGS (4 questions)
*Data needed: Drug interaction databases, utilization flags, fraud/waste/abuse alerts, concurrent review data*

34. **"Are there any drug-drug interaction or therapeutic duplication alerts if this medication is approved?"**
35. **"Does this member have any utilization flags (e.g., high ER utilizer, opioid management program, case management open)?"**
36. **"Is there a concurrent inpatient review open for this member that relates to the requested outpatient service?"**
37. **"Has this request been flagged by the auto-adjudication engine, and if so, why was it routed to manual review?"**

---

## Summary of Data Domains for the Gold Serving Layer

| Data Domain | Source Systems | Key Tables/Entities |
|-------------|---------------|-------------------|
| **Member Enrollment & Eligibility** | Enrollment/eligibility system, 834 transactions | Member, enrollment spans, plan assignment, COB |
| **Benefit Configuration** | Plan/product config system | Benefit packages, PA-required lists, exclusions, limits |
| **Medical Policy** | Policy management system, InterQual/MCG | Clinical criteria, step therapy rules, guidelines |
| **Claims History** | Claims adjudication system (medical + pharmacy) | Claims, claim lines, diagnosis, procedure, fills |
| **PA Case Management** | UM platform (e.g., Jiva, TruCare, QNXT UM) | Auth cases, decisions, statuses, clinical notes, pend reasons |
| **Provider Directory & Network** | Provider data management, credentialing | Provider demographics, network participation, contracts, gold-card status |
| **Clinical Attachments** | Document management, EHR integrations | Clinical notes, lab results, imaging reports, LMNs |
| **Regulatory / SLA Rules** | Configuration / rules engine | TAT rules by LOB, urgency type, state |
| **Financial** | Fee schedule, accumulator systems | Allowed amounts, member cost-sharing, accumulators |
| **Drug Formulary & Pharmacy** | PBM / formulary management | Formulary tiers, step therapy, quantity limits, fill history |
| **Alerts & Flags** | Care management, FWA systems | Utilization flags, drug interactions, concurrent review status |

---

## Key Sources

1. **CMS Interoperability and Prior Authorization Final Rule (CMS-0057-F)** — [cms.gov fact sheet](https://www.cms.gov/newsroom/fact-sheets/cms-interoperability-prior-authorization-final-rule-cms-0057-f) — Jan 17, 2024. Defines 72-hour urgent / 7-day standard TAT (effective Jan 2026), specific denial reason requirement, FHIR API mandate (Jan 2027).
2. **42 CFR §422.568** — Medicare Advantage organization determination timeframes (14 days standard, 72 hours expedited, 24 hours Part D expedited).
3. **42 CFR §438.210** — Medicaid managed care service authorization timeframes (14 days standard, 72 hours expedited).
4. **29 CFR §2560.503-1** — DOL claims procedure regulation for ERISA plans (15 days pre-service non-urgent, 72 hours urgent).
5. **Texas Insurance Code Chapter 4201 Subchapter N (HB 3459)** — Gold-card mandate (90% threshold, 6-month lookback).
6. **X12 278 Health Care Services Review** — HIPAA-mandated EDI transaction for PA request/response. ASC X12N/005010X217.
7. **HL7 FHIR Da Vinci PAS IG (STU 2.0.1)** — [build.fhir.org/ig/HL7/davinci-pas/](https://build.fhir.org/ig/HL7/davinci-pas/) — FHIR-based PA request/response using Claim resource profiles.
8. **HL7 FHIR Da Vinci CRD IG** — Coverage Requirements Discovery, helps providers determine PA requirements at point of order.
9. **HL7 FHIR Da Vinci DTR IG** — Documentation Templates and Rules, enables structured questionnaire-based documentation collection.
10. **HHS OIG Report OEI-09-18-00260** (2022) — Found that 13% of MA PA denials were for services that met Medicare coverage rules; MA plans received >35 million PA requests in 2021.
11. **NCQA UM Accreditation Standards (UM 5-7)** — Industry-standard UM timeliness and notification requirements.
12. **InterQual (Change Healthcare/Optum)** — Level-of-care and procedure-based clinical criteria used by ~70% of US health plans.
13. **MCG (Milliman Care Guidelines)** — Indications for care guidelines used by ~3,200 hospitals and health plans.
14. **AMA Prior Authorization Physician Survey (2023)** — 94% of physicians report care delays due to PA; 33% report PA led to serious adverse event.
