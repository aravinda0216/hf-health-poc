"""Small reference tables (synthetic subsets of CPT/HCPCS, revenue codes, NDC, ICD-10, CARC/RARC, taxonomy)."""

# code, description, kind, allowed rate (cents), PA required
CPT = [
 ("99213","Office visit, established patient, level 3","EM",9500,False),
 ("99214","Office visit, established patient, level 4","EM",14000,False),
 ("99203","Office visit, new patient, level 3","EM",13000,False),
 ("G0439","Annual wellness visit, subsequent","AWV",11000,False),
 ("93000","Electrocardiogram, complete","DX",3000,False),
 ("80053","Comprehensive metabolic panel","LAB",1400,False),
 ("36415","Venipuncture","LAB",800,False),
 ("71046","Chest X-ray, 2 views","IMG",5500,False),
 ("97110","Therapeutic exercise, 15 min","PT",4500,False),
 ("90837","Psychotherapy, 60 min","BH",16000,False),
 ("70553","MRI brain without and with contrast","MRI",92000,True),
 ("72148","MRI lumbar spine without contrast","MRI",85000,True),
 ("73721","MRI lower-extremity joint without contrast","MRI",80000,True),
 ("G0151","Home health physical therapy, 15 min","HH",6500,False),
 ("E0601","CPAP device","DME",85000,False),
]
# PA_CODES: codes that need prior authorization (the three MRI codes). EM_CODES: visits that satisfy a care gap. NONPA_PRO: codes for ordinary claims.
PA_CODES = [c[0] for c in CPT if c[4]]
EM_CODES = [c[0] for c in CPT if c[2] in ("EM", "AWV")]
NONPA_PRO = [c[0] for c in CPT if not c[4] and c[2] in ("EM","DX","LAB","IMG","PT")]
# The allowed rate in cents in these tables is the contract rate that rule F-02 recomputes allowed amounts from.
CPT_BY = {c[0]: c for c in CPT}

# revenue codes (facility): code, description, rate cents per unit
REV = [("0120","Room and board, semi-private",210000),("0190","Skilled nursing per diem",45000),("0450","Emergency room",95000),
       ("0300","Laboratory",12000),("0320","Radiology, diagnostic",30000),("0250","Pharmacy",8000)]
REV_BY = {r[0]: r for r in REV}
DRG = [("470","Major hip and knee joint replacement"),("291","Heart failure and shock"),("871","Septicemia, with MCC"),("194","Simple pneumonia"),("690","Kidney and urinary tract infection")]

# ndc, name, tier, price per 30 days (cents), PA required
# Drugs by formulary tier; tier 5 (specialty) and any flagged drug need prior authorization.
DRUGS = [
 ("99999-0001-01","Metformin 500 mg tablet",1,900,False),("99999-0002-02","Lisinopril 10 mg tablet",1,700,False),
 ("99999-0003-03","Atorvastatin 20 mg tablet",2,1500,False),("99999-0004-04","Amlodipine 5 mg tablet",1,800,False),
 ("99999-0005-05","Apixaban 5 mg tablet",3,48000,False),("99999-0006-06","Insulin glargine pen",3,32000,False),
 ("99999-0007-07","Semaglutide injection pen",4,95000,True),("99999-0008-08","Specialty biologic injectable",5,520000,True)]
DRUG_BY = {d[0]: d for d in DRUGS}
# Copay per 30-day supply by tier, in cents. Tier 5 uses 25% coinsurance instead.
TIER_COPAY = {1: 500, 2: 1200, 3: 4700, 4: 10000}   # tier 5 = 25% coinsurance

# icd10, description, sex restriction (None = any)
ICD = {
 "IMG":[("M54.50","Low back pain, unspecified",None),("G43.909","Migraine, unspecified",None),("M25.561","Pain in right knee",None)],
 "PT": [("M62.81","Muscle weakness (generalized)",None),("M25.511","Pain in right shoulder",None)],
 "INPATIENT":[("I50.9","Heart failure, unspecified",None),("J18.9","Pneumonia, unspecified",None),("M16.11","Unilateral primary osteoarthritis, right hip",None)],
 "SNF":[("I63.9","Cerebral infarction, unspecified",None),("S72.001D","Fracture of neck of right femur, subsequent encounter",None)],
 "HOME_HEALTH":[("I50.9","Heart failure, unspecified",None),("J44.9","COPD, unspecified",None)],
 "DME":[("G47.33","Obstructive sleep apnea",None)],
 "PHARMACY":[("E11.9","Type 2 diabetes mellitus without complications",None),("E66.01","Morbid obesity due to excess calories",None)],
 "BH":[("F32.9","Major depressive disorder, single episode, unspecified",None)],
 "SEXSPEC":[("N40.1","Benign prostatic hyperplasia with LUTS","M"),("N95.1","Menopausal and female climacteric states","F")],
}
ICD_ALL = {c: (d, s) for v in ICD.values() for c, d, s in v}

# category -> (carc, rarc, plain-language reason, liable party)
# Denial reasons. CO = provider liability (write-off), PR = member liability (see rule F-05). The NETWORK entry (CO-242) backs the PON-04 scenario.
DENIALS = {
 "AUTH":("CO-197","N54","Authorization was not on file for this service.","PROVIDER"),
 "MED_NECESSITY":("CO-50","N115","The service was not considered medically necessary based on the information submitted.","PROVIDER"),
 "ELIGIBILITY":("CO-27",None,"Coverage was not in effect on the date of service.","PROVIDER"),
 "CODING":("CO-4",None,"The procedure code is inconsistent with the modifier used, or a required modifier is missing.","PROVIDER"),
 "DUPLICATE":("CO-18",None,"This claim duplicates a previously processed claim.","PROVIDER"),
 "TIMELY_FILING":("CO-29",None,"The time limit for filing this claim has expired.","PROVIDER"),
 "COB":("CO-22",None,"Another payer may be responsible. Coordination-of-benefits information is needed.","PROVIDER"),
 "NON_COVERED":("PR-96",None,"This service is not a covered benefit under the member's plan.","MEMBER"),
 "NETWORK":("CO-242",None,"The billing provider was not recognized as in-network on the date of service.","PROVIDER"),
 "MISSING_INFO":("CO-16","N290","The claim is missing information needed to process it.","PROVIDER"),
}
# Adjustment reasons for paid claims: contractual write-off (CO-45) and member cost share (PR-1/2/3).
CARC_ADJ = {"CO-45":"Charge exceeds the contracted fee schedule amount","PR-1":"Deductible amount","PR-2":"Coinsurance amount","PR-3":"Co-payment amount"}
PEND_REASONS = ["RECORDS_REQUEST","COB_INFO","AUTH_REVIEW","CLINICAL_REVIEW","PRICING","ELIGIBILITY"]

TAXONOMY = {
 "PCP":[("207Q00000X","Family Medicine"),("207R00000X","Internal Medicine")],
 "SPECIALIST":[("207RC0000X","Cardiovascular Disease"),("207X00000X","Orthopedic Surgery"),("2084N0400X","Neurology"),("207RG0100X","Gastroenterology"),("2085R0202X","Diagnostic Radiology")],
 "HOSPITAL":[("282N00000X","General Acute Care Hospital")],"SNF":[("314000000X","Skilled Nursing Facility")],
 "HOME_HEALTH":[("251E00000X","Home Health Agency")],"URGENT_CARE":[("261QU0200X","Urgent Care Clinic")],
 "LAB":[("291U00000X","Clinical Medical Laboratory")],"IMAGING":[("261QR0200X","Radiology Clinic/Center")],
 "PHARMACY":[("3336C0003X","Community/Retail Pharmacy")]}

# Name, city and street lists are generic. Phone numbers (555-01xx) and emails (@example.test) are reserved formats, so no value can be a real contact.
FIRST_M = ["James","Robert","John","Michael","William","David","Richard","Joseph","Thomas","Charles","Daniel","Paul","Mark","George","Kenneth","Steven","Edward","Brian","Ronald","Anthony"]
FIRST_F = ["Mary","Patricia","Jennifer","Linda","Elizabeth","Barbara","Susan","Jessica","Sarah","Karen","Nancy","Lisa","Betty","Margaret","Sandra","Ashley","Dorothy","Kimberly","Emily","Donna"]
LAST = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson","Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores"]
CITIES = [("Dallas","TX","752",32.78,-96.80),("Houston","TX","770",29.76,-95.37),("Miami","FL","331",25.76,-80.19),("Columbus","OH","432",39.96,-83.00),
          ("Philadelphia","PA","191",39.95,-75.17),("Atlanta","GA","303",33.75,-84.39),("Phoenix","AZ","850",33.45,-112.07),("Denver","CO","802",39.74,-104.99)]
STREETS = ["Main St","Oak Ave","Maple Dr","Cedar Ln","Elm St","Park Blvd","Lake Rd","Hill St"]
ORG_WORDS = {"HOSPITAL":["Regional Medical Center","Memorial Hospital","General Hospital"],"SNF":["Care Center","Rehabilitation and Nursing","Skilled Nursing"],
 "HOME_HEALTH":["Home Health Services","Visiting Nurses"],"URGENT_CARE":["Urgent Care"],"LAB":["Diagnostic Laboratory"],"IMAGING":["Imaging Center"],"PHARMACY":["Pharmacy"]}
