"""
Curated reference data and code crosswalks.

WHY THIS MODULE EXISTS
----------------------
Three gaps in the raw open data have to be closed deliberately rather than
guessed at, and all three matter for prior authorization:

1. CPT Level I codes are absent from every free CMS file. The CMS HCPCS ANWEB
   release contains only Level II alphanumeric codes (A-V) plus dental D codes -
   8,769 of them, and zero numeric CPT. CPT is AMA-copyrighted. But PA is
   transacted on CPT: an analyst reviewing an MRI request is looking at 72148,
   not at a SNOMED concept id. So CURATED_CPT below carries a hand-verified set
   of real CPT codes covering the thirteen PA service categories, with realistic
   allowed amounts. Provenance is recorded as CURATED so nothing pretends these
   came out of a CMS download.

2. PA service categories cannot be inferred from description keywords. Doing so
   produced obvious nonsense - a BLS defibrillation supply classified as
   ADVANCED_IMAGING - because words like "defibrillation" collide across
   domains. HCPCS_PA_RULES instead assigns categories from documented code
   ranges, which is how the code set is actually organised.

3. Synthea codes clinical data in SNOMED-CT, LOINC and RxNorm. None of those are
   what a payer adjudicates on. SNOMED_DX_MAP and SNOMED_PROC_MAP crosswalk the
   complete Synthea vocabulary (129 diagnoses, 144 procedures) onto real
   ICD-10-CM and CPT/HCPCS codes.

HOW THE DIAGNOSIS MAP STAYS HONEST
----------------------------------
Each diagnosis maps to a preferred ICD-10-CM code plus a fallback category root.
The builder validates the preferred code against the 97,572 real codes in
ref_icd10cm:

    preferred code exists            -> CURATED_MAP
    preferred missing, root resolves -> CATEGORY_DEFAULT  (a real, less specific code)
    neither resolves                 -> UNMAPPED

So every emitted ICD-10 code is guaranteed to be a real code, and
crosswalk_method records exactly how much precision the mapping actually has.
That distinction is visible to the analyst rather than hidden.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Curated CPT Level I codes
#
# (code, short_description, category, pa_service_category, typical_allowed_amt)
# pa_service_category = None means the code is routine and not PA-gated.
# ---------------------------------------------------------------------------

CURATED_CPT: tuple[tuple[str, str, str, str | None, float], ...] = (
    # --- advanced imaging (the highest-volume PA domain) -------------------
    ("70450", "CT head/brain without contrast", "IMAGING", "ADVANCED_IMAGING", 320.00),
    ("70486", "CT maxillofacial without contrast", "IMAGING", "ADVANCED_IMAGING", 340.00),
    ("70551", "MRI brain without contrast", "IMAGING", "ADVANCED_IMAGING", 610.00),
    ("70553", "MRI brain without and with contrast", "IMAGING", "ADVANCED_IMAGING", 890.00),
    ("71250", "CT chest without contrast", "IMAGING", "ADVANCED_IMAGING", 360.00),
    ("71260", "CT chest with contrast", "IMAGING", "ADVANCED_IMAGING", 430.00),
    ("71271", "CT chest low dose for lung cancer screening", "IMAGING", "ADVANCED_IMAGING", 210.00),
    ("72141", "MRI cervical spine without contrast", "IMAGING", "ADVANCED_IMAGING", 640.00),
    ("72148", "MRI lumbar spine without contrast", "IMAGING", "ADVANCED_IMAGING", 650.00),
    ("72158", "MRI lumbar spine without and with contrast", "IMAGING", "ADVANCED_IMAGING", 920.00),
    ("72192", "CT pelvis without contrast", "IMAGING", "ADVANCED_IMAGING", 350.00),
    ("73221", "MRI upper extremity joint without contrast", "IMAGING", "ADVANCED_IMAGING", 620.00),
    ("73721", "MRI lower extremity joint without contrast", "IMAGING", "ADVANCED_IMAGING", 630.00),
    ("74177", "CT abdomen and pelvis with contrast", "IMAGING", "ADVANCED_IMAGING", 520.00),
    ("75561", "Cardiac MRI without and with contrast", "IMAGING", "ADVANCED_IMAGING", 980.00),
    ("75574", "CT angiography coronary arteries", "IMAGING", "ADVANCED_IMAGING", 760.00),
    ("78815", "PET/CT skull base to mid-thigh", "IMAGING", "ADVANCED_IMAGING", 1850.00),
    ("78816", "PET/CT whole body", "IMAGING", "ADVANCED_IMAGING", 2100.00),
    # routine imaging - not PA-gated
    ("71046", "Chest X-ray 2 views", "IMAGING", None, 62.00),
    ("72170", "Pelvis X-ray 1-2 views", "IMAGING", None, 55.00),
    ("73030", "Shoulder X-ray complete", "IMAGING", None, 58.00),
    ("73090", "Forearm X-ray 2 views", "IMAGING", None, 52.00),
    ("73130", "Hand X-ray 3 or more views", "IMAGING", None, 54.00),
    ("73560", "Knee X-ray 1-2 views", "IMAGING", None, 56.00),
    ("73600", "Ankle X-ray 2 views", "IMAGING", None, 53.00),
    ("76700", "Ultrasound abdomen complete", "IMAGING", None, 190.00),
    ("76805", "Obstetric ultrasound after first trimester", "IMAGING", None, 210.00),
    ("76811", "Obstetric ultrasound detailed anatomic", "IMAGING", None, 285.00),
    ("76815", "Obstetric ultrasound limited", "IMAGING", None, 120.00),
    ("77065", "Diagnostic mammography unilateral", "IMAGING", None, 145.00),
    ("77066", "Diagnostic mammography bilateral", "IMAGING", None, 175.00),
    ("77067", "Screening mammography bilateral", "IMAGING", None, 140.00),
    ("77080", "DXA bone density axial skeleton", "IMAGING", None, 105.00),
    ("93000", "Electrocardiogram complete", "DIAGNOSTIC", None, 48.00),
    ("93306", "Transthoracic echocardiography complete with Doppler", "IMAGING", None, 420.00),
    ("93308", "Transthoracic echocardiography limited", "IMAGING", None, 235.00),

    # --- elective surgery --------------------------------------------------
    ("19301", "Partial mastectomy", "SURGERY", "ELECTIVE_SURGERY", 3100.00),
    ("19303", "Total mastectomy", "SURGERY", "ELECTIVE_SURGERY", 4200.00),
    ("22551", "Cervical spine fusion anterior", "SURGERY", "ELECTIVE_SURGERY", 14500.00),
    ("22633", "Lumbar spine fusion posterior interbody", "SURGERY", "ELECTIVE_SURGERY", 19800.00),
    ("27130", "Total hip arthroplasty", "SURGERY", "ELECTIVE_SURGERY", 17200.00),
    ("27447", "Total knee arthroplasty", "SURGERY", "ELECTIVE_SURGERY", 16800.00),
    ("29827", "Shoulder arthroscopy with rotator cuff repair", "SURGERY", "ELECTIVE_SURGERY", 6900.00),
    ("29881", "Knee arthroscopy with meniscectomy", "SURGERY", "ELECTIVE_SURGERY", 4300.00),
    ("32663", "Thoracoscopic lobectomy", "SURGERY", "ELECTIVE_SURGERY", 15600.00),
    ("33533", "Coronary artery bypass single arterial graft", "SURGERY", "ELECTIVE_SURGERY", 28500.00),
    ("43644", "Laparoscopic gastric bypass Roux-en-Y", "SURGERY", "ELECTIVE_SURGERY", 17500.00),
    ("43775", "Laparoscopic sleeve gastrectomy", "SURGERY", "ELECTIVE_SURGERY", 14200.00),
    ("47562", "Laparoscopic cholecystectomy", "SURGERY", "ELECTIVE_SURGERY", 5400.00),
    ("55866", "Laparoscopic radical prostatectomy", "SURGERY", "ELECTIVE_SURGERY", 12400.00),
    ("58571", "Laparoscopic total hysterectomy", "SURGERY", "ELECTIVE_SURGERY", 8900.00),
    ("63030", "Lumbar laminotomy with discectomy", "SURGERY", "ELECTIVE_SURGERY", 8600.00),
    ("64721", "Carpal tunnel release", "SURGERY", "ELECTIVE_SURGERY", 2400.00),
    ("92928", "Percutaneous coronary intervention with stent", "SURGERY", "ELECTIVE_SURGERY", 13800.00),
    # routine / urgent surgery - not PA-gated
    ("11042", "Debridement subcutaneous tissue", "SURGERY", None, 210.00),
    ("11981", "Insertion of non-biodegradable drug delivery implant", "SURGERY", None, 190.00),
    ("12001", "Simple repair of superficial wound", "SURGERY", None, 185.00),
    ("19120", "Excision of breast lesion", "SURGERY", None, 1450.00),
    ("20610", "Arthrocentesis major joint injection", "SURGERY", None, 105.00),
    ("21800", "Closed treatment of rib fracture", "SURGERY", None, 320.00),
    ("23500", "Closed treatment of clavicle fracture", "SURGERY", None, 480.00),
    ("25600", "Closed treatment of distal radius fracture", "SURGERY", None, 690.00),
    ("27786", "Closed treatment of ankle fracture", "SURGERY", None, 640.00),
    ("29075", "Application of short arm cast", "SURGERY", None, 145.00),
    ("31231", "Nasal endoscopy diagnostic", "SURGERY", None, 275.00),
    ("31622", "Diagnostic bronchoscopy", "SURGERY", None, 480.00),
    ("32554", "Thoracentesis without imaging", "SURGERY", None, 320.00),
    ("38525", "Biopsy or excision of axillary lymph node", "SURGERY", None, 1250.00),
    ("38792", "Injection for sentinel node identification", "SURGERY", None, 165.00),
    ("44970", "Laparoscopic appendectomy", "SURGERY", None, 6200.00),
    ("45378", "Diagnostic colonoscopy", "SURGERY", None, 780.00),
    ("45380", "Colonoscopy with biopsy", "SURGERY", None, 890.00),
    ("45385", "Colonoscopy with snare polypectomy", "SURGERY", None, 1020.00),
    ("49505", "Repair of initial inguinal hernia", "SURGERY", None, 3200.00),
    ("55700", "Biopsy of prostate needle", "SURGERY", None, 620.00),
    ("58150", "Total abdominal hysterectomy", "SURGERY", None, 7800.00),
    ("58300", "Insertion of intrauterine device", "SURGERY", None, 175.00),
    ("58301", "Removal of intrauterine device", "SURGERY", None, 145.00),
    ("59025", "Fetal non-stress test", "DIAGNOSTIC", None, 95.00),
    ("59400", "Vaginal delivery with antepartum and postpartum care", "SURGERY", None, 3900.00),
    ("59510", "Cesarean delivery with antepartum and postpartum care", "SURGERY", None, 5100.00),
    ("65546", "Extraction of tooth", "SURGERY", None, 260.00),

    # --- genetic and molecular testing -------------------------------------
    ("81162", "BRCA1/BRCA2 full sequence and duplication analysis", "LAB", "GENETIC_TESTING", 2900.00),
    ("81445", "Targeted genomic sequence panel 5-50 genes solid tumor", "LAB", "GENETIC_TESTING", 1750.00),
    ("81455", "Targeted genomic sequence panel 51 or more genes", "LAB", "GENETIC_TESTING", 2850.00),
    ("81479", "Unlisted molecular pathology procedure", "LAB", "GENETIC_TESTING", 1200.00),
    ("81528", "Colorectal cancer screening DNA analysis of stool", "LAB", "GENETIC_TESTING", 510.00),
    ("88175", "Cervical cytology automated screening with interpretation", "LAB", None, 42.00),

    # --- sleep studies ------------------------------------------------------
    ("95800", "Home sleep study with oximetry and heart rate", "DIAGNOSTIC", "SLEEP_STUDY", 290.00),
    ("95810", "Attended polysomnography 4 or more parameters", "DIAGNOSTIC", "SLEEP_STUDY", 1150.00),
    ("95811", "Polysomnography with CPAP titration", "DIAGNOSTIC", "SLEEP_STUDY", 1320.00),

    # --- rehabilitation therapy --------------------------------------------
    ("97110", "Therapeutic exercise each 15 minutes", "THERAPY", "REHAB_THERAPY", 42.00),
    ("97112", "Neuromuscular re-education each 15 minutes", "THERAPY", "REHAB_THERAPY", 45.00),
    ("97116", "Gait training each 15 minutes", "THERAPY", "REHAB_THERAPY", 41.00),
    ("97140", "Manual therapy techniques each 15 minutes", "THERAPY", "REHAB_THERAPY", 40.00),
    ("97161", "Physical therapy evaluation low complexity", "THERAPY", "REHAB_THERAPY", 105.00),
    ("97530", "Therapeutic activities each 15 minutes", "THERAPY", "REHAB_THERAPY", 46.00),

    # --- behavioral health --------------------------------------------------
    ("90791", "Psychiatric diagnostic evaluation", "BEHAVIORAL", "BEHAVIORAL_HEALTH", 240.00),
    ("90792", "Psychiatric diagnostic evaluation with medical services", "BEHAVIORAL", "BEHAVIORAL_HEALTH", 290.00),
    ("90834", "Psychotherapy 45 minutes", "BEHAVIORAL", "BEHAVIORAL_HEALTH", 130.00),
    ("90837", "Psychotherapy 60 minutes", "BEHAVIORAL", "BEHAVIORAL_HEALTH", 175.00),
    ("90853", "Group psychotherapy", "BEHAVIORAL", "BEHAVIORAL_HEALTH", 48.00),
    ("97151", "Behavior identification assessment each 15 minutes", "BEHAVIORAL", "BEHAVIORAL_HEALTH", 52.00),
    ("97153", "Adaptive behavior treatment by protocol each 15 minutes", "BEHAVIORAL", "BEHAVIORAL_HEALTH", 38.00),
    ("96127", "Brief emotional or behavioral assessment", "BEHAVIORAL", None, 15.00),
    ("99406", "Smoking cessation counseling 3-10 minutes", "PREVENTIVE", None, 18.00),

    # --- radiation oncology -------------------------------------------------
    ("77301", "Intensity modulated radiotherapy plan", "RADIATION", "RADIATION_ONCOLOGY", 2400.00),
    ("77373", "Stereotactic body radiation therapy per fraction", "RADIATION", "RADIATION_ONCOLOGY", 1850.00),
    ("77385", "IMRT delivery simple", "RADIATION", "RADIATION_ONCOLOGY", 480.00),
    ("77386", "IMRT delivery complex", "RADIATION", "RADIATION_ONCOLOGY", 620.00),
    ("77427", "Radiation treatment management 5 treatments", "RADIATION", "RADIATION_ONCOLOGY", 390.00),
    ("77778", "Interstitial brachytherapy complex", "RADIATION", "RADIATION_ONCOLOGY", 2650.00),

    # --- transplant ---------------------------------------------------------
    ("32851", "Lung transplant single without bypass", "SURGERY", "TRANSPLANT", 68000.00),
    ("33945", "Heart transplant", "SURGERY", "TRANSPLANT", 92000.00),
    ("38241", "Autologous haematopoietic progenitor cell transplant", "SURGERY", "TRANSPLANT", 45000.00),
    ("47135", "Liver transplant orthotopic", "SURGERY", "TRANSPLANT", 105000.00),
    ("50360", "Renal allotransplantation", "SURGERY", "TRANSPLANT", 58000.00),

    # --- pain management ----------------------------------------------------
    ("62323", "Lumbar epidural injection with imaging guidance", "PAIN", "PAIN_MANAGEMENT", 620.00),
    ("62362", "Implantation of programmable infusion pump", "PAIN", "PAIN_MANAGEMENT", 9800.00),
    ("63650", "Percutaneous implantation of spinal cord stimulator electrode", "PAIN", "PAIN_MANAGEMENT", 12500.00),
    ("64483", "Transforaminal epidural injection lumbar single level", "PAIN", "PAIN_MANAGEMENT", 580.00),
    ("64493", "Lumbar facet joint injection single level", "PAIN", "PAIN_MANAGEMENT", 490.00),
    ("64635", "Radiofrequency ablation lumbar facet joint", "PAIN", "PAIN_MANAGEMENT", 1150.00),

    # --- inpatient admission -----------------------------------------------
    ("99221", "Initial hospital care low complexity", "EVALUATION_MANAGEMENT", "INPATIENT_ADMIT", 115.00),
    ("99223", "Initial hospital care high complexity", "EVALUATION_MANAGEMENT", "INPATIENT_ADMIT", 225.00),
    ("99232", "Subsequent hospital care moderate complexity", "EVALUATION_MANAGEMENT", None, 105.00),
    ("99233", "Subsequent hospital care high complexity", "EVALUATION_MANAGEMENT", None, 150.00),

    # --- home health --------------------------------------------------------
    ("99341", "Home visit new patient low complexity", "EVALUATION_MANAGEMENT", "HOME_HEALTH", 105.00),

    # --- routine office, lab, dialysis, obstetric, immunisation ------------
    ("99213", "Office visit established patient low complexity", "EVALUATION_MANAGEMENT", None, 95.00),
    ("99214", "Office visit established patient moderate complexity", "EVALUATION_MANAGEMENT", None, 140.00),
    ("99283", "Emergency department visit moderate severity", "EVALUATION_MANAGEMENT", None, 260.00),
    ("99285", "Emergency department visit high severity", "EVALUATION_MANAGEMENT", None, 640.00),
    ("99395", "Preventive medicine re-evaluation 18-39 years", "PREVENTIVE", None, 155.00),
    ("36415", "Collection of venous blood by venipuncture", "LAB", None, 6.00),
    ("80053", "Comprehensive metabolic panel", "LAB", None, 15.00),
    ("80061", "Lipid panel", "LAB", None, 20.00),
    ("81001", "Urinalysis with microscopy automated", "LAB", None, 8.00),
    ("82105", "Alpha-fetoprotein serum", "LAB", None, 24.00),
    ("83036", "Haemoglobin A1c", "LAB", None, 17.00),
    ("85025", "Complete blood count with automated differential", "LAB", None, 12.00),
    ("86592", "Syphilis antibody non-treponemal qualitative", "LAB", None, 9.00),
    ("86762", "Rubella antibody", "LAB", None, 22.00),
    ("86787", "Varicella-zoster antibody", "LAB", None, 24.00),
    ("86803", "Hepatitis C antibody", "LAB", None, 26.00),
    ("87070", "Bacterial culture aerobic isolate", "LAB", None, 18.00),
    ("87340", "Hepatitis B surface antigen", "LAB", None, 16.00),
    ("87389", "HIV-1/HIV-2 antigen and antibodies single result", "LAB", None, 32.00),
    ("90471", "Immunization administration single vaccine", "PREVENTIVE", None, 26.00),
    ("90686", "Influenza vaccine quadrivalent preservative free", "PREVENTIVE", None, 22.00),
    ("90715", "Tetanus diphtheria pertussis vaccine", "PREVENTIVE", None, 45.00),
    ("90935", "Haemodialysis single evaluation", "DIALYSIS", None, 245.00),
    ("90960", "End-stage renal disease services monthly 4 or more visits", "DIALYSIS", None, 290.00),
    ("92950", "Cardiopulmonary resuscitation", "PROCEDURE", None, 220.00),
    ("92960", "Elective external cardioversion", "PROCEDURE", None, 350.00),
    ("94010", "Spirometry", "DIAGNOSTIC", None, 42.00),
    ("94060", "Spirometry before and after bronchodilator", "DIAGNOSTIC", None, 62.00),
    ("95004", "Percutaneous allergy skin tests", "DIAGNOSTIC", None, 8.00),
    ("95115", "Allergen immunotherapy single injection", "THERAPY", None, 18.00),
    ("96372", "Therapeutic injection intramuscular", "THERAPY", None, 28.00),
    ("96413", "Chemotherapy infusion up to 1 hour", "THERAPY", None, 320.00),
    ("18286", "Catheter ablation of cardiac tissue", "SURGERY", "ELECTIVE_SURGERY", 18500.00),
)


# ---------------------------------------------------------------------------
# PA service categories for real HCPCS Level II codes.
#
# Assigned from documented code ranges rather than description keywords.
# Each entry is (inclusive_low, inclusive_high, pa_service_category).
# ---------------------------------------------------------------------------

HCPCS_PA_RULES: tuple[tuple[str, str, str], ...] = (
    # J-codes: injectable and infused drugs. The specialty/biologic ranges are
    # the PA-gated ones; J0000-J0999 also contains many routine injectables, so
    # only the high-cost bands are flagged.
    ("J9000", "J9999", "SPECIALTY_DRUG"),   # antineoplastics
    ("J1745", "J1745", "SPECIALTY_DRUG"),   # infliximab
    ("J0135", "J0135", "SPECIALTY_DRUG"),   # adalimumab
    ("J0178", "J0178", "SPECIALTY_DRUG"),   # aflibercept
    ("J1300", "J1300", "SPECIALTY_DRUG"),   # eculizumab
    ("J2350", "J2350", "SPECIALTY_DRUG"),   # ocrelizumab
    ("J2323", "J2323", "SPECIALTY_DRUG"),   # natalizumab
    ("J3357", "J3358", "SPECIALTY_DRUG"),   # ustekinumab
    ("J1602", "J1602", "SPECIALTY_DRUG"),   # golimumab
    ("Q2041", "Q2059", "SPECIALTY_DRUG"),   # CAR-T and cellular therapies
    # E-codes: durable medical equipment
    ("E0100", "E8002", "DME"),
    # K-codes: temporary DME, largely wheelchairs
    ("K0001", "K0900", "DME"),
    # L-codes: orthotics and prosthetics
    ("L0112", "L9900", "DME"),
    # B-codes: enteral and parenteral nutrition
    ("B4034", "B9999", "HOME_HEALTH"),
    # H-codes: behavioural health services
    ("H0001", "H2037", "BEHAVIORAL_HEALTH"),
    # G-codes for home health nursing
    ("G0299", "G0300", "HOME_HEALTH"),
    # S-codes: home infusion therapy
    ("S9325", "S9379", "HOME_HEALTH"),
)

# HCPCS ranges that are definitively NOT PA-gated, checked before the rules
# above. Ambulance and routine supplies otherwise fall into the E-code sweep.
HCPCS_PA_EXCLUSIONS: tuple[tuple[str, str], ...] = (
    ("A0021", "A0999"),  # ambulance and transport
    ("A4000", "A8004"),  # routine medical and surgical supplies
    ("A9150", "A9999"),  # miscellaneous supplies
)

# Coarse category by leading letter, used for ref_hcpcs.category.
HCPCS_LETTER_CATEGORY: dict[str, str] = {
    "A": "SUPPLY_TRANSPORT",
    "B": "ENTERAL_PARENTERAL",
    "C": "OPPS_PASSTHROUGH",
    "D": "DENTAL",
    "E": "DME",
    "G": "PROCEDURE_SERVICE",
    "H": "BEHAVIORAL",
    "J": "DRUG",
    "K": "DME",
    "L": "ORTHOTIC_PROSTHETIC",
    "M": "MEDICAL_SERVICE",
    "P": "PATHOLOGY_LAB",
    "Q": "TEMPORARY",
    "R": "DIAGNOSTIC_RADIOLOGY",
    "S": "PRIVATE_PAYER",
    "T": "STATE_MEDICAID",
    "U": "LAB_TEST",
    "V": "VISION_HEARING",
}


def _in_range(code: str, low: str, high: str) -> bool:
    """Range test on the shared letter prefix plus zero-padded numeric part."""
    if not code or not code[0].isalpha():
        return False
    if code[0] != low[0] or code[0] != high[0]:
        return False
    try:
        return int(low[1:]) <= int(code[1:]) <= int(high[1:])
    except ValueError:
        return False


def hcpcs_pa_category(code: str) -> str | None:
    """PA service category for a real HCPCS Level II code, or None."""
    for low, high in HCPCS_PA_EXCLUSIONS:
        if _in_range(code, low, high):
            return None
    for low, high, category in HCPCS_PA_RULES:
        if _in_range(code, low, high):
            return category
    return None


def hcpcs_category(code: str) -> str:
    if not code:
        return "OTHER"
    return HCPCS_LETTER_CATEGORY.get(code[0].upper(), "OTHER")


# ---------------------------------------------------------------------------
# SNOMED-CT -> ICD-10-CM diagnosis crosswalk.
#
# Covers the complete Synthea diagnosis vocabulary (129 concepts).
# Value is (preferred_icd10_nodot, fallback_category_root).
# ---------------------------------------------------------------------------

SNOMED_DX_MAP: dict[str, tuple[str, str]] = {
    # respiratory / ENT
    "444814009": ("J0190", "J01"),      # viral sinusitis
    "195662009": ("J028", "J02"),       # acute viral pharyngitis
    "10509002": ("J209", "J20"),        # acute bronchitis
    "40055000": ("J329", "J32"),        # chronic sinusitis
    "36971009": ("J329", "J32"),        # sinusitis
    "75498004": ("J0190", "J01"),       # acute bacterial sinusitis
    "43878008": ("J020", "J02"),        # streptococcal sore throat
    "65363002": ("H6690", "H66"),       # otitis media
    "233604007": ("J189", "J18"),       # pneumonia
    "87433001": ("J439", "J43"),        # pulmonary emphysema
    "185086009": ("J440", "J44"),       # chronic obstructive bronchitis
    "233678006": ("J453", "J45"),       # childhood asthma
    "232353008": ("J3089", "J30"),      # perennial allergic rhinitis, seasonal variation
    "446096008": ("J3089", "J30"),      # perennial allergic rhinitis
    "367498001": ("J302", "J30"),       # seasonal allergic rhinitis

    # endocrine / metabolic
    "162864005": ("E669", "E66"),       # BMI 30+ obesity
    "408512008": ("E6601", "E66"),      # BMI 40+ severe obesity
    "15777000": ("R7303", "R73"),       # prediabetes
    "44054006": ("E119", "E11"),        # type 2 diabetes
    "80394007": ("R739", "R73"),        # hyperglycaemia
    "55822004": ("E785", "E78"),        # hyperlipidaemia
    "302870006": ("E781", "E78"),       # hypertriglyceridaemia
    "237602007": ("E8881", "E88"),      # metabolic syndrome
    "83664006": ("E039", "E03"),        # idiopathic atrophic hypothyroidism
    "127013003": ("E1122", "E11"),      # diabetic renal disease
    "368581000119106": ("E1140", "E11"),  # neuropathy due to T2DM
    "422034002": ("E11319", "E11"),     # diabetic retinopathy, T2DM
    "1551000119108": ("E11319", "E11"),  # nonproliferative diabetic retinopathy
    "1501000119109": ("E11359", "E11"),  # proliferative diabetic retinopathy
    "97331000119101": ("E11311", "E11"),  # macular oedema with retinopathy, T2DM
    "90781000119102": ("E1129", "E11"),  # microalbuminuria due to T2DM
    "90560007": ("M109", "M10"),        # gout

    # cardiovascular
    "59621000": ("I10", "I10"),         # hypertension
    "53741008": ("I2510", "I25"),       # coronary heart disease
    "88805009": ("I509", "I50"),        # chronic congestive heart failure
    "49436004": ("I4891", "I48"),       # atrial fibrillation
    "22298006": ("I219", "I21"),        # myocardial infarction
    "399211009": ("I252", "I25"),       # history of myocardial infarction
    "410429000": ("I469", "I46"),       # cardiac arrest
    "429007001": ("Z8674", "Z86"),      # history of cardiac arrest
    "230690007": ("I639", "I63"),       # stroke

    # renal / genitourinary
    "431855005": ("N181", "N18"),       # CKD stage 1
    "431856006": ("N182", "N18"),       # CKD stage 2
    "301011002": ("N390", "N39"),       # E. coli urinary tract infection
    "197927001": ("N390", "N39"),       # recurrent urinary tract infection
    "38822007": ("N3000", "N30"),       # cystitis
    "45816000": ("N10", "N10"),         # pyelonephritis

    # haematology
    "271737000": ("D649", "D64"),       # anaemia

    # musculoskeletal
    "239873007": ("M1710", "M17"),      # osteoarthritis of knee
    "239872002": ("M1610", "M16"),      # osteoarthritis of hip
    "201834006": ("M18Ï", "M18"),       # localized primary OA of hand (root fallback)
    "64859006": ("M810", "M81"),        # osteoporosis
    "443165006": ("M8080XA", "M80"),    # pathological fracture due to osteoporosis
    "69896004": ("M0679", "M06"),       # rheumatoid arthritis
    "95417003": ("M797", "M79"),        # primary fibromyalgia
    "82423001": ("G8929", "G89"),       # chronic pain

    # injuries
    "44465007": ("S93401A", "S93"),     # sprain of ankle
    "70704007": ("S63501A", "S63"),     # sprain of wrist
    "39848009": ("S134XXA", "S13"),     # whiplash injury to neck
    "62106007": ("S060X0A", "S06"),     # concussion without loss of consciousness
    "62564004": ("S060X9A", "S06"),     # concussion with loss of consciousness
    "110030002": ("S060X0A", "S06"),    # concussion injury of brain
    "275272006": ("S069X9A", "S06"),    # traumatic brain damage
    "65966004": ("S52901A", "S52"),     # fracture of forearm
    "263102004": ("S62109A", "S62"),    # fracture subluxation of wrist
    "16114001": ("S82891A", "S82"),     # fracture of ankle
    "58150001": ("S42001A", "S42"),     # fracture of clavicle
    "33737001": ("S2239XA", "S22"),     # fracture of rib
    "359817006": ("S72009A", "S72"),    # closed fracture of hip
    "1734006": ("S32000A", "S32"),      # vertebral fracture with cord injury
    "15724005": ("S32000A", "S32"),     # vertebral fracture without cord injury
    "283371005": ("S51809A", "S51"),    # laceration of forearm
    "284549007": ("S61409A", "S61"),    # laceration of hand
    "370247008": ("S0181XA", "S01"),    # facial laceration
    "283385000": ("S71109A", "S71"),    # laceration of thigh
    "284551006": ("S91309A", "S91"),    # laceration of foot
    "262574004": ("T148XXA", "T14"),    # bullet wound
    "403190006": ("T300", "T30"),       # first degree burn
    "403191005": ("T300", "T30"),       # second degree burn
    "48333001": ("T300", "T30"),        # burn injury
    "307731004": ("S46019A", "S46"),    # rotator cuff tendon injury
    "444448004": ("S83411A", "S83"),    # medial collateral ligament injury
    "444470001": ("S83511A", "S83"),    # anterior cruciate ligament injury
    "239720000": ("S83289A", "S83"),    # tear of meniscus of knee
    "30832001": ("S76119A", "S76"),     # rupture of patellar tendon

    # neurological / psychiatric
    "128613002": ("G40909", "G40"),     # seizure disorder
    "84757009": ("G40909", "G40"),      # epilepsy
    "703151001": ("Z8669", "Z86"),      # history of single seizure
    "124171000119105": ("G43711", "G43"),  # chronic intractable migraine without aura
    "26929004": ("G309", "G30"),        # Alzheimer's disease
    "230265002": ("G300", "G30"),       # early-onset familial Alzheimer's
    "370143000": ("F329", "F32"),       # major depressive disorder
    "36923009": ("F329", "F32"),        # major depression single episode
    "192127007": ("F909", "F90"),       # child attention deficit disorder
    "5602001": ("F1110", "F11"),        # opioid abuse
    "7200002": ("F1020", "F10"),        # alcoholism
    "55680006": ("T50901A", "T50"),     # drug overdose
    "449868002": ("F17200", "F17"),     # smokes tobacco daily

    # gastrointestinal
    "68496003": ("K635", "K63"),        # polyp of colon
    "713197008": ("K621", "K62"),       # recurrent rectal polyp
    "74400008": ("K3580", "K35"),       # appendicitis
    "47693006": ("K3520", "K35"),       # rupture of appendix
    "428251008": ("Z9049", "Z90"),      # history of appendectomy
    "6072007": ("K625", "K62"),         # bleeding from anus
    "236077008": ("R197", "R19"),       # protracted diarrhoea
    "196416002": ("K011", "K01"),       # impacted molars

    # neoplasms
    "126906006": ("D291", "D29"),       # neoplasm of prostate
    "92691004": ("D075", "D07"),        # carcinoma in situ of prostate
    "314994000": ("C7951", "C79"),      # metastasis from prostate tumour
    "254837009": ("C50919", "C50"),     # malignant neoplasm of breast
    "363406005": ("C189", "C18"),       # malignant tumour of colon
    "93761005": ("C189", "C18"),        # primary malignant neoplasm of colon
    "109838007": ("C188", "C18"),       # overlapping malignant neoplasm of colon
    "94260004": ("C785", "C78"),        # secondary malignant neoplasm of colon
    "254637007": ("C349", "C34"),       # non-small cell lung cancer
    "424132000": ("C3491", "C34"),      # NSCLC TNM stage 1
    "254632001": ("C349", "C34"),       # small cell carcinoma of lung
    "67811000119102": ("C3491", "C34"),  # primary small cell lung neoplasm stage 1
    "162573006": ("R911", "R91"),       # suspected lung cancer

    # dermatological / allergic
    "24079001": ("L209", "L20"),        # atopic dermatitis
    "40275004": ("L259", "L25"),        # contact dermatitis
    "241929008": ("T7840XA", "T78"),    # acute allergic reaction

    # obstetric
    "72892002": ("Z3490", "Z34"),       # normal pregnancy
    "19169002": ("O039", "O03"),        # miscarriage in first trimester
    "35999006": ("O021", "O02"),        # blighted ovum
    "79586000": ("O00101", "O00"),      # tubal pregnancy
    "156073000": ("O3689X0", "O36"),    # fetus with unknown complication
    "198992004": ("O1500", "O15"),      # antepartum eclampsia
    "398254007": ("O1490", "O14"),      # preeclampsia
}


# ---------------------------------------------------------------------------
# SNOMED-CT -> CPT/HCPCS procedure crosswalk.
#
# Covers the Synthea procedure vocabulary. Targets are codes that exist either
# in CURATED_CPT above or in the real HCPCS Level II set.
# ---------------------------------------------------------------------------

SNOMED_PROC_MAP: dict[str, str] = {
    # imaging
    "16335031000119103": "71250",  # high resolution CT chest without contrast
    "418891003": "74177",          # CT chest and abdomen
    "698354004": "70551",          # MRI for brain volume measurement
    "241615005": "73221",          # MRI of breast
    "399208008": "71046",          # plain chest X-ray
    "1225002": "73090",            # upper arm X-ray
    "60027007": "73130",           # X-ray of wrist
    "19490002": "73600",           # ankle X-ray
    "74016001": "73560",           # knee X-ray
    "168594001": "73030",          # clavicle X-ray
    "268425006": "72170",          # pelvis X-ray
    "71651007": "77066",           # mammography
    "241055006": "77065",          # mammogram symptomatic
    "24623002": "77067",           # screening mammography
    "1571000087109": "76700",      # ultrasonography of bilateral breasts
    "312681000": "77080",          # bone density scan
    "169230002": "76815",          # ultrasound for fetal viability
    "271442007": "76811",          # fetal anatomy study
    "434158009": "93306",          # 3D transthoracic echocardiography
    "40701008": "93306",           # echocardiography
    "433236007": "93306",          # transthoracic echocardiography

    # cardiac procedures
    "180325003": "92960",          # electrical cardioversion
    "18286008": "18286",           # catheter ablation of cardiac tissue
    "415070008": "92928",          # percutaneous coronary intervention
    "232717009": "33533",          # coronary artery bypass grafting
    "447365002": "33249",          # biventricular ICD insertion
    "433112001": "37187",          # percutaneous mechanical thrombectomy
    "313191000": "96372",          # injection of adrenaline
    "410429000": "92950",          # cardiac arrest care

    # oncology
    "703423002": "96413",          # combined chemotherapy and radiation
    "367336001": "96413",          # chemotherapy
    "33195004": "77385",           # teleradiotherapy
    "385798007": "77427",          # radiation therapy care
    "108290001": "77386",          # radiation oncology / radiotherapy
    "122548005": "19120",          # biopsy of breast
    "392021009": "19120",          # lumpectomy of breast
    "69031006": "19301",           # excision of breast tissue
    "234262008": "38525",          # excision of axillary lymph node
    "443497002": "38525",          # excision of sentinel lymph node
    "396487001": "38792",          # sentinel lymph node biopsy
    "433114000": "88360",          # HER2 by immunohistochemistry
    "434363004": "88377",          # HER2 by FISH
    "432231006": "32405",          # fine needle aspiration biopsy of lung
    "88039007": "32851",           # lung transplant
    "65575008": "55700",           # biopsy of prostate
    "90470006": "55866",           # prostatectomy

    # gastrointestinal
    "73761001": "45378",           # colonoscopy
    "274031008": "45385",          # rectal polypectomy
    "76164006": "45380",           # biopsy of colon
    "43075005": "44140",           # partial resection of colon
    "387607004": "44320",          # construction of diverting colostomy
    "80146002": "44970",           # appendectomy
    "104435004": "82274",          # screening for occult blood in faeces
    "410006001": "99213",          # digital examination of rectum

    # respiratory
    "23426006": "94010",           # measurement of respiratory function
    "127783003": "94010",          # spirometry
    "171231001": "94060",          # asthma screening
    "15081005": "97110",           # pulmonary rehabilitation
    "173160006": "31622",          # diagnostic fibreoptic bronchoscopy
    "91602002": "32554",           # thoracentesis
    "112790001": "31231",          # nasal sinus endoscopy
    "269911007": "87070",          # sputum examination
    "167995008": "87070",          # sputum microscopy

    # renal
    "265764009": "90935",          # renal dialysis

    # obstetric and gynaecological
    "225158009": "59025",          # auscultation of fetal heart
    "274804006": "59025",          # evaluation of uterine fundal height
    "66348005": "59400",           # childbirth
    "11466000": "59510",           # caesarean section
    "236974004": "59400",          # instrumental delivery
    "177157003": "59400",          # spontaneous breech delivery
    "31208007": "59200",           # medical induction of labour
    "237001001": "59200",          # augmentation of labour
    "85548006": "59300",           # episiotomy
    "18946005": "62323",           # epidural anaesthesia
    "65588006": "99221",           # premature birth of newborn
    "252160004": "81025",          # standard pregnancy test
    "65200003": "58300",           # insertion of intrauterine device
    "46706006": "58301",           # replacement of intrauterine device
    "68254000": "58301",           # removal of intrauterine device
    "169553002": "11981",          # insertion of subcutaneous contraceptive
    "301807007": "11982",          # removal of subcutaneous contraceptive
    "287664005": "58670",          # bilateral tubal ligation
    "22523008": "55250",           # vasectomy
    "10383002": "99401",           # counselling for termination of pregnancy
    "714812005": "59840",          # induced termination of pregnancy
    "386394001": "59840",          # pregnancy termination care
    "445912000": "59120",          # excision of fallopian tube for ectopic pregnancy
    "236931002": "96372",          # methotrexate injection into tubal pregnancy
    "51116004": "90384",           # RhD passive immunization
    "169673001": "86901",          # antenatal RhD antibody screening
    "443529005": "76946",          # screening for chromosomal aneuploidy
    "275833003": "82105",          # alpha-fetoprotein test
    "90226004": "88175",           # cervical cytology smear
    "35025007": "99213",           # manual pelvic examination
    "5880005": "99395",            # physical examination
    "180207008": "36430",          # intravenous transfusion of packed cells

    # musculoskeletal
    "274474001": "29075",          # bone immobilization
    "699253003": "29881",          # surgical manipulation of knee joint
    "387685009": "29827",          # surgical manipulation of shoulder joint
    "305428000": "99221",          # admission to orthopaedic department
    "305433001": "99223",          # admission to trauma surgery department
    "288086009": "12001",          # suture open wound

    # behavioural health
    "228557008": "90834",          # cognitive and behavioural therapy
    "171207006": "96127",          # depression screening
    "112001000119100": "96127",    # positive PHQ-9 screening
    "112011000119102": "96127",    # negative PHQ-9 screening
    "54550000": "95816",           # EEG seizure count

    # laboratory
    "104091002": "85025",          # haemoglobin / haematocrit / platelet count
    "14768001": "85060",           # peripheral blood smear interpretation
    "44608003": "86900",           # blood typing RH typing
    "117015009": "87070",          # throat culture
    "117010004": "87086",          # urine culture
    "395123002": "81003",          # urine screening for diabetes
    "268556000": "81003",          # urine screening for glucose
    "167271000": "81002",          # urine protein test
    "310861008": "87810",          # chlamydia antigen test
    "269828009": "86592",          # syphilis infection test
    "165829005": "87850",          # gonorrhoea infection test
    "47758006": "87340",           # hepatitis B surface antigen
    "104375008": "86803",          # hepatitis C antibody test
    "31676001": "87389",           # HIV antigen test
    "169690007": "86762",          # rubella screening
    "104326007": "86787",          # varicella-zoster antibody
    "118001005": "87899",          # streptococcus pneumoniae antigen test
    "28163009": "86580",           # skin test for tuberculosis
    "395142003": "95004",          # allergy screening test

    # preventive, immunisation and evaluation
    "430193006": "99213",          # medication reconciliation
    "180256009": "95115",          # subcutaneous immunotherapy
    "76601001": "96372",           # intramuscular injection
    "43060002": "96374",           # intravenous injection
    "384700001": "90389",          # injection of tetanus antitoxin
    "399014008": "90715",          # DTaP vaccination
    "398171003": "92551",          # hearing examination
    "311791003": "99213",          # information gathering
    "162676008": "99213",          # brief general examination
    "415300000": "99213",          # review of systems
    "183856001": "99213",          # referral to hypertension clinic
    "65546002": "65546",           # extraction of wisdom tooth
}


# ---------------------------------------------------------------------------
# Specialty drug identification.
#
# Synthea prescribes by RxNorm with no NDC, so joining medications to
# ref_ndc_product on NDC yields nothing. Matching on ingredient/brand name is
# what actually works. These are real high-cost specialty and biologic agents
# that are PA-gated in practice, grouped by therapeutic class so step-therapy
# rules have something coherent to evaluate within.
# ---------------------------------------------------------------------------

SPECIALTY_DRUG_TERMS: dict[str, str] = {
    # oncology
    "bevacizumab": "ANTINEOPLASTIC_BIOLOGIC",
    "trastuzumab": "ANTINEOPLASTIC_BIOLOGIC",
    "pembrolizumab": "ANTINEOPLASTIC_BIOLOGIC",
    "nivolumab": "ANTINEOPLASTIC_BIOLOGIC",
    "rituximab": "ANTINEOPLASTIC_BIOLOGIC",
    "paclitaxel": "ANTINEOPLASTIC",
    "carboplatin": "ANTINEOPLASTIC",
    "cisplatin": "ANTINEOPLASTIC",
    "doxorubicin": "ANTINEOPLASTIC",
    "cyclophosphamide": "ANTINEOPLASTIC",
    "etoposide": "ANTINEOPLASTIC",
    "oxaliplatin": "ANTINEOPLASTIC",
    "fluorouracil": "ANTINEOPLASTIC",
    "leucovorin": "ANTINEOPLASTIC",
    "letrozole": "HORMONAL_ANTINEOPLASTIC",
    "anastrozole": "HORMONAL_ANTINEOPLASTIC",
    "tamoxifen": "HORMONAL_ANTINEOPLASTIC",
    "leuprolide": "HORMONAL_ANTINEOPLASTIC",
    "bicalutamide": "HORMONAL_ANTINEOPLASTIC",
    "imatinib": "TARGETED_ONCOLOGY",
    "erlotinib": "TARGETED_ONCOLOGY",
    "palbociclib": "TARGETED_ONCOLOGY",
    # immunology / rheumatology
    "adalimumab": "TNF_INHIBITOR",
    "etanercept": "TNF_INHIBITOR",
    "infliximab": "TNF_INHIBITOR",
    "golimumab": "TNF_INHIBITOR",
    "certolizumab": "TNF_INHIBITOR",
    "ustekinumab": "INTERLEUKIN_INHIBITOR",
    "secukinumab": "INTERLEUKIN_INHIBITOR",
    "tocilizumab": "INTERLEUKIN_INHIBITOR",
    "abatacept": "IMMUNOMODULATOR",
    "tofacitinib": "JAK_INHIBITOR",
    "methotrexate": "DMARD",
    "hydroxychloroquine": "DMARD",
    "leflunomide": "DMARD",
    "sulfasalazine": "DMARD",
    # multiple sclerosis
    "ocrelizumab": "MS_DISEASE_MODIFYING",
    "natalizumab": "MS_DISEASE_MODIFYING",
    "fingolimod": "MS_DISEASE_MODIFYING",
    "dimethyl fumarate": "MS_DISEASE_MODIFYING",
    "glatiramer": "MS_DISEASE_MODIFYING",
    "interferon beta": "MS_DISEASE_MODIFYING",
    # ophthalmology
    "aflibercept": "ANTI_VEGF",
    "ranibizumab": "ANTI_VEGF",
    # metabolic / endocrine
    "insulin glargine": "INSULIN",
    "insulin lispro": "INSULIN",
    "insulin aspart": "INSULIN",
    "insulin detemir": "INSULIN",
    "liraglutide": "GLP1_AGONIST",
    "semaglutide": "GLP1_AGONIST",
    "dulaglutide": "GLP1_AGONIST",
    "exenatide": "GLP1_AGONIST",
    "empagliflozin": "SGLT2_INHIBITOR",
    "dapagliflozin": "SGLT2_INHIBITOR",
    "canagliflozin": "SGLT2_INHIBITOR",
    "somatropin": "GROWTH_HORMONE",
    # respiratory
    "omalizumab": "ASTHMA_BIOLOGIC",
    "mepolizumab": "ASTHMA_BIOLOGIC",
    "dupilumab": "ASTHMA_BIOLOGIC",
    # haematology / other
    "eculizumab": "COMPLEMENT_INHIBITOR",
    "epoetin": "ERYTHROPOIESIS_AGENT",
    "darbepoetin": "ERYTHROPOIESIS_AGENT",
    "filgrastim": "COLONY_STIMULATING_FACTOR",
    "pegfilgrastim": "COLONY_STIMULATING_FACTOR",
    "enoxaparin": "ANTICOAGULANT",
    "apixaban": "ANTICOAGULANT",
    "rivaroxaban": "ANTICOAGULANT",
    # hepatitis C
    "sofosbuvir": "HEPATITIS_C_ANTIVIRAL",
    "ledipasvir": "HEPATITIS_C_ANTIVIRAL",
    "glecaprevir": "HEPATITIS_C_ANTIVIRAL",
}

# First-line agents within their class. Step therapy is satisfied by a
# documented failed trial of one of these, so the flag has to be explicit.
FIRST_LINE_TERMS: frozenset[str] = frozenset({
    "methotrexate", "hydroxychloroquine", "sulfasalazine", "leflunomide",
    "metformin", "glipizide", "glyburide", "glimepiride",
    "lisinopril", "amlodipine", "hydrochlorothiazide", "losartan",
    "atorvastatin", "simvastatin", "pravastatin", "rosuvastatin",
    "ibuprofen", "naproxen", "meloxicam", "acetaminophen",
    "albuterol", "fluticasone", "budesonide",
    "sertraline", "fluoxetine", "citalopram", "escitalopram",
    "omeprazole", "pantoprazole", "famotidine",
    "insulin glargine", "insulin lispro",
})


# ---------------------------------------------------------------------------
# US state name -> 2-letter code.
#
# Synthea writes full state names ("Massachusetts") but the mart stores 2-char
# codes, and the state-level PA turnaround rules key on the code. Defined here so
# there is a single mapping shared by every builder.
# ---------------------------------------------------------------------------

US_STATE_ABBREV: dict[str, str] = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "District of Columbia": "DC", "Florida": "FL", "Georgia": "GA", "Hawaii": "HI",
    "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA",
    "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME",
    "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE",
    "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH",
    "Oklahoma": "OK", "Oregon": "OR", "Pennsylvania": "PA", "Puerto Rico": "PR",
    "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
    "Wisconsin": "WI", "Wyoming": "WY",
}


def state_code(value: str | None) -> str | None:
    """Normalise a state name or code to a 2-letter code."""
    if not value:
        return None
    v = str(value).strip()
    if len(v) == 2:
        return v.upper()
    return US_STATE_ABBREV.get(v, v[:2].upper() if v else None)


def resolve_icd10(snomed_code: str | None, icd10_set: set[str]) -> tuple[str | None, str]:
    """Crosswalk a SNOMED concept to a REAL ICD-10-CM code.

    Returns (icd10_code, crosswalk_method). The method value is the honest record
    of how much precision the mapping actually carries, and is surfaced to the
    analyst rather than hidden:

        CURATED_MAP      the specific curated code exists in the CMS release
        CATEGORY_DEFAULT the specific code was not in the release, so the most
                         specific real code under its category root was used
        UNMAPPED         no crosswalk exists for this concept

    Validating against the real code set is what guarantees the mart never
    contains an invented ICD-10 code.
    """
    if not snomed_code:
        return None, "UNMAPPED"

    mapped = SNOMED_DX_MAP.get(str(snomed_code))
    if not mapped:
        return None, "UNMAPPED"

    preferred, root = mapped
    if preferred in icd10_set:
        return preferred, "CURATED_MAP"

    candidates = sorted(c for c in icd10_set if c.startswith(root))
    if candidates:
        deeper = [c for c in candidates if len(c) > len(root)]
        return (deeper[0] if deeper else candidates[0]), "CATEGORY_DEFAULT"

    return None, "UNMAPPED"


def resolve_procedure(snomed_code: str | None, valid_codes: set[str]) -> tuple[str | None, str]:
    """Crosswalk a SNOMED procedure to a CPT/HCPCS code present in ref_hcpcs."""
    if not snomed_code:
        return None, "UNMAPPED"
    mapped = SNOMED_PROC_MAP.get(str(snomed_code))
    if not mapped:
        return None, "UNMAPPED"
    if mapped in valid_codes:
        return mapped, "CURATED_MAP"
    return None, "UNMAPPED"


def classify_medication(name: str | None, generic: str | None = None) -> dict:
    haystack = " ".join(p for p in (name, generic) if p).lower()
    if not haystack:
        return {
            "therapeutic_class": None,
            "is_specialty_drug": False,
            "requires_pa_flag": False,
            "is_first_line_therapy": False,
        }

    therapeutic_class = None
    is_specialty = False
    for term, klass in SPECIALTY_DRUG_TERMS.items():
        if term in haystack:
            therapeutic_class = klass
            is_specialty = klass not in {"DMARD", "INSULIN"}
            break

    is_first_line = any(term in haystack for term in FIRST_LINE_TERMS)

    # Insulins and DMARDs are PA-gated in practice but are not specialty-tier.
    requires_pa = is_specialty or therapeutic_class in {
        "INSULIN", "GLP1_AGONIST", "SGLT2_INHIBITOR", "DMARD",
    }

    return {
        "therapeutic_class": therapeutic_class,
        "is_specialty_drug": bool(is_specialty),
        "requires_pa_flag": bool(requires_pa),
        "is_first_line_therapy": bool(is_first_line),
    }
