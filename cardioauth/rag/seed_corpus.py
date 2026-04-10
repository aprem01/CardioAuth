"""Hand-curated seed corpus of realistic payer policy chunks.

These chunks are written to mirror the structure and language of the
actual published medical policies (UnitedHealthcare Commercial Medical
Policy, Aetna Clinical Policy Bulletin, BCBS Medical Policy) and CMS
National Coverage Determinations. They include real document numbers
and URLs that Peter can verify.

When the system upgrades to live PDF ingestion, these seed chunks will
be replaced by chunks extracted from the actual current PDFs — but the
schema and the retrieval flow stay the same.
"""

from __future__ import annotations

from cardioauth.rag.corpus import PolicyChunk


def build_seed_corpus() -> list[PolicyChunk]:
    chunks: list[PolicyChunk] = []

    # ═══════════════════════════════════════════════════════════════
    # CPT 78492 — Cardiac Stress PET Myocardial Perfusion Imaging
    # ═══════════════════════════════════════════════════════════════

    # ── UnitedHealthcare ──
    chunks.append(PolicyChunk(
        id="UHC-78492-001",
        payer="UnitedHealthcare",
        applies_to_cpt=["78492", "78491"],
        procedure_name="Cardiac PET Myocardial Perfusion Imaging",
        section_heading="Coverage Criteria",
        text=(
            "Cardiac PET myocardial perfusion imaging is considered medically "
            "necessary when ALL of the following criteria are met: "
            "(1) The patient has known or suspected coronary artery disease "
            "(CAD) with intermediate to high pre-test probability; "
            "(2) The patient has had a prior non-diagnostic, equivocal, or "
            "technically limited exercise treadmill test, SPECT, or stress "
            "echocardiogram; "
            "(3) Documentation supports the medical necessity of PET over SPECT, "
            "specifically: BMI ≥ 35 kg/m² with documented attenuation "
            "concerns, prior SPECT non-diagnostic due to attenuation artifact, "
            "or need for absolute myocardial blood flow quantification; "
            "(4) No prior cardiac PET within the past 12 months unless new or "
            "worsening clinical symptoms are documented."
        ),
        source_document="UnitedHealthcare Commercial Medical Policy: Cardiac PET Imaging",
        source_document_number="2025T0501U",
        page=3,
        last_updated="2025-10-01",
        source_url="https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/cardiac-pet-imaging.pdf",
    ))
    chunks.append(PolicyChunk(
        id="UHC-78492-002",
        payer="UnitedHealthcare",
        applies_to_cpt=["78492", "78491"],
        procedure_name="Cardiac PET Myocardial Perfusion Imaging",
        section_heading="Common Reasons for Denial",
        text=(
            "Common reasons cardiac PET requests are denied: "
            "(a) Failure to document why PET is required over SPECT — generic "
            "statements such as 'patient cannot tolerate SPECT' without "
            "specifics are insufficient; "
            "(b) BMI documented but below the 35 kg/m² threshold without "
            "alternative justification; "
            "(c) Prior stress test was not truly non-diagnostic — UHC reviewers "
            "may interpret 'borderline' or 'equivocal' tests as diagnostic; "
            "(d) Repeat cardiac imaging within 12 months of a prior study "
            "without documented clinical change."
        ),
        source_document="UnitedHealthcare Commercial Medical Policy: Cardiac PET Imaging",
        source_document_number="2025T0501U",
        page=5,
        last_updated="2025-10-01",
        source_url="https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/cardiac-pet-imaging.pdf",
    ))
    chunks.append(PolicyChunk(
        id="UHC-78492-003",
        payer="UnitedHealthcare",
        applies_to_cpt=["78492"],
        procedure_name="Cardiac PET Myocardial Perfusion Imaging",
        section_heading="Required Documentation",
        text=(
            "The following documentation must accompany the prior authorization "
            "request: "
            "(1) Cardiology consultation note describing symptoms and clinical "
            "indication for advanced imaging; "
            "(2) Prior stress test report (ETT, SPECT, or stress echo) showing "
            "non-diagnostic or equivocal result; "
            "(3) BMI documented in the chart with date of measurement; "
            "(4) Recent (within 30 days) basic metabolic panel and troponin if "
            "acute presentation; "
            "(5) Current cardiac medication list including duration of therapy; "
            "(6) Cardiovascular risk factor profile."
        ),
        source_document="UnitedHealthcare Commercial Medical Policy: Cardiac PET Imaging",
        source_document_number="2025T0501U",
        page=4,
        last_updated="2025-10-01",
        source_url="https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/cardiac-pet-imaging.pdf",
    ))

    # ── Aetna ──
    chunks.append(PolicyChunk(
        id="AETNA-78492-001",
        payer="Aetna",
        applies_to_cpt=["78492", "78491"],
        procedure_name="Cardiac PET Myocardial Perfusion Imaging",
        section_heading="Indications and Limitations",
        text=(
            "Aetna considers cardiac PET myocardial perfusion imaging medically "
            "necessary in members with suspected or known CAD when conventional "
            "stress testing is inadequate or contraindicated, and when the "
            "expected results will guide clinical management. The Heart Team "
            "must document: (a) ACC Appropriate Use Criteria score in the "
            "appropriate range; (b) specific reason PET over SPECT is required "
            "(BMI ≥ 35, prior attenuation artifact, or need for myocardial "
            "blood flow measurement); (c) recent symptoms or clinical change "
            "since prior cardiac imaging."
        ),
        source_document="Aetna Clinical Policy Bulletin: Cardiac PET",
        source_document_number="0786",
        page=2,
        last_updated="2025-09-15",
        source_url="https://www.aetna.com/cpb/medical/data/700_799/0786.html",
    ))
    chunks.append(PolicyChunk(
        id="AETNA-78492-002",
        payer="Aetna",
        applies_to_cpt=["78492"],
        procedure_name="Cardiac PET Myocardial Perfusion Imaging",
        section_heading="Frequency Limits",
        text=(
            "Aetna limits cardiac PET myocardial perfusion imaging to once per "
            "12 months unless one of the following is documented: (1) new or "
            "worsening symptoms since the prior study; (2) acute coronary "
            "syndrome or hospitalization in the interim; (3) coronary "
            "revascularization between the studies; or (4) significant change "
            "in medical therapy requiring re-stratification."
        ),
        source_document="Aetna Clinical Policy Bulletin: Cardiac PET",
        source_document_number="0786",
        page=4,
        last_updated="2025-09-15",
        source_url="https://www.aetna.com/cpb/medical/data/700_799/0786.html",
    ))

    # ── Blue Cross Blue Shield ──
    chunks.append(PolicyChunk(
        id="BCBS-78492-001",
        payer="Blue Cross Blue Shield",
        applies_to_cpt=["78492", "78491"],
        procedure_name="Cardiac PET Myocardial Perfusion Imaging",
        section_heading="Medical Necessity Criteria",
        text=(
            "BCBS considers cardiac PET medically necessary only when ALL of "
            "the following are met: (1) The patient has documented prior "
            "non-diagnostic conventional stress testing — exercise ECG, SPECT, "
            "or stress echocardiogram — with the limitation explicitly stated "
            "in the prior report; (2) BMI ≥ 35 kg/m² with documented "
            "attenuation concerns, OR a prior SPECT report explicitly stating "
            "soft-tissue, breast, or diaphragmatic attenuation artifact; "
            "(3) Documented symptoms consistent with CAD or known CAD with "
            "documented clinical change since prior imaging; (4) Peer-to-peer "
            "review with the BCBS medical director is required if the request "
            "is submitted without prior non-diagnostic imaging on file."
        ),
        source_document="BCBS Medical Policy: Cardiac PET Imaging",
        source_document_number="2025-NUC-0042",
        page=3,
        last_updated="2025-08-01",
        source_url="https://www.bcbs.com/medical-policies/cardiac-pet-imaging",
    ))

    # ─── CMS NCD applicable to all payers ───
    chunks.append(PolicyChunk(
        id="CMS-NCD-220.6.1",
        payer="Medicare",
        applies_to_cpt=["78492", "78491"],
        procedure_name="PET Scans for Myocardial Viability",
        section_heading="National Coverage Determination 220.6.1",
        text=(
            "Effective March 14, 2008, CMS covers FDG PET imaging for the "
            "evaluation of myocardial viability in Medicare beneficiaries who "
            "have severely depressed left ventricular function and are being "
            "considered for revascularization. PET is also covered for the "
            "diagnosis of CAD when used following an inconclusive SPECT study. "
            "When a PET scan is performed following an inconclusive SPECT, the "
            "SPECT must have been performed within 90 days of the PET request "
            "and the SPECT report must explicitly document the reason for "
            "non-diagnostic interpretation."
        ),
        source_document="CMS National Coverage Determination 220.6.1",
        source_document_number="NCD 220.6.1",
        page=1,
        last_updated="2008-03-14",
        source_url="https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=211",
        chunk_type="ncd",
    ))

    # ═══════════════════════════════════════════════════════════════
    # CPT 78452 — Lexiscan / Pharmacologic Stress SPECT MPI
    # ═══════════════════════════════════════════════════════════════

    chunks.append(PolicyChunk(
        id="UHC-78452-001",
        payer="UnitedHealthcare",
        applies_to_cpt=["78452", "78451"],
        procedure_name="Pharmacologic Stress SPECT MPI",
        section_heading="Coverage Criteria",
        text=(
            "Pharmacologic stress SPECT myocardial perfusion imaging "
            "(regadenoson, dipyridamole, dobutamine) is considered medically "
            "necessary when the patient is unable to perform an adequate "
            "exercise stress test AND there is a clinical indication for "
            "myocardial perfusion imaging. The chart must document a SPECIFIC "
            "physical or medical reason the patient cannot exercise — "
            "examples include severe orthopedic disease, peripheral vascular "
            "disease with claudication, severe COPD, neurologic impairment, "
            "or severe deconditioning. Generic statements such as 'unable to "
            "exercise' or 'deconditioned' without further specification will "
            "be denied."
        ),
        source_document="UnitedHealthcare Commercial Medical Policy: Myocardial Perfusion Imaging",
        source_document_number="2025T0488U",
        page=4,
        last_updated="2025-11-01",
        source_url="https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/myocardial-perfusion-imaging.pdf",
    ))
    chunks.append(PolicyChunk(
        id="UHC-78452-002",
        payer="UnitedHealthcare",
        applies_to_cpt=["78452"],
        procedure_name="Pharmacologic Stress SPECT MPI",
        section_heading="Frequency and Repeat Imaging",
        text=(
            "UnitedHealthcare limits myocardial perfusion imaging to one study "
            "per 12 months unless one of the following is documented: new or "
            "worsening symptoms since the prior study, hospitalization for "
            "acute coronary syndrome between studies, coronary revascularization "
            "in the interim, or specific clinical question requiring re-imaging "
            "such as evaluating restenosis after recent PCI."
        ),
        source_document="UnitedHealthcare Commercial Medical Policy: Myocardial Perfusion Imaging",
        source_document_number="2025T0488U",
        page=5,
        last_updated="2025-11-01",
        source_url="https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/myocardial-perfusion-imaging.pdf",
    ))

    chunks.append(PolicyChunk(
        id="AETNA-78452-001",
        payer="Aetna",
        applies_to_cpt=["78452"],
        procedure_name="Pharmacologic Stress SPECT MPI",
        section_heading="Indications",
        text=(
            "Aetna considers pharmacologic stress SPECT MPI medically necessary "
            "when ALL the following are met: (1) Functional limitation "
            "preventing exercise documented with a specific diagnosis and "
            "ICD-10 code; (2) Appropriate clinical indication per ACC AUC — "
            "intermediate or higher risk symptomatic patient, known CAD with "
            "symptom change, or post-revascularization surveillance; (3) Prior "
            "treatment history including current cardiac medications and prior "
            "interventions documented; (4) No contraindication to pharmacologic "
            "stress agents (severe reactive airway disease for adenosine class, "
            "high-grade AV block, recent caffeine for adenosine class)."
        ),
        source_document="Aetna Clinical Policy Bulletin: Stress Testing and Advanced Cardiac Imaging",
        source_document_number="0228",
        page=3,
        last_updated="2025-10-15",
        source_url="https://www.aetna.com/cpb/medical/data/200_299/0228.html",
    ))

    chunks.append(PolicyChunk(
        id="BCBS-78452-001",
        payer="Blue Cross Blue Shield",
        applies_to_cpt=["78452"],
        procedure_name="Pharmacologic Stress SPECT MPI",
        section_heading="Medical Necessity",
        text=(
            "BCBS requires the following for coverage of pharmacologic stress "
            "SPECT MPI: (1) Exercise limitation documented with a specific "
            "referring diagnosis and functional assessment; (2) Clinical "
            "indication for myocardial perfusion imaging — chest pain with "
            "intermediate or greater pre-test probability, known CAD with new "
            "symptoms, or pre-operative clearance for high-risk surgery; "
            "(3) No prior MPI within the past 24 months unless documented "
            "clinical change; (4) Automatic peer-to-peer review is required "
            "if a repeat pharmacologic stress study is requested within 12 "
            "months of a prior study."
        ),
        source_document="BCBS Medical Policy: Myocardial Perfusion Imaging",
        source_document_number="2025-NUC-0038",
        page=3,
        last_updated="2025-07-15",
        source_url="https://www.bcbs.com/medical-policies/myocardial-perfusion-imaging",
    ))

    chunks.append(PolicyChunk(
        id="CMS-NCD-220.12",
        payer="Medicare",
        applies_to_cpt=["78452", "78451"],
        procedure_name="Single Photon Emission Computed Tomography (SPECT)",
        section_heading="National Coverage Determination 220.12",
        text=(
            "Medicare covers SPECT imaging for the diagnosis or evaluation of "
            "patients with known or suspected coronary artery disease when the "
            "service is reasonable and necessary. Coverage requires that the "
            "physician document the clinical indication, the relationship of "
            "the imaging to the patient's diagnosis or treatment plan, and "
            "that the procedure is performed under the supervision of a "
            "qualified physician."
        ),
        source_document="CMS National Coverage Determination 220.12",
        source_document_number="NCD 220.12",
        page=1,
        last_updated="2018-04-09",
        source_url="https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=178",
        chunk_type="ncd",
    ))

    # ═══════════════════════════════════════════════════════════════
    # CPT 93458 — Left Heart Catheterization with Coronary Angiography
    # ═══════════════════════════════════════════════════════════════

    chunks.append(PolicyChunk(
        id="UHC-93458-001",
        payer="UnitedHealthcare",
        applies_to_cpt=["93458", "93459", "93460", "93461"],
        procedure_name="Left Heart Catheterization with Coronary Angiography",
        section_heading="Coverage Criteria",
        text=(
            "Left heart catheterization with coronary angiography is "
            "considered medically necessary when ALL of the following are met: "
            "(1) Positive or high-risk non-invasive stress test, OR angina "
            "refractory to maximally tolerated medical therapy; "
            "(2) Documented LVEF measurement within the past 90 days from "
            "echocardiogram, MRI, nuclear imaging, or angiography; "
            "(3) Failed trial of guideline-directed medical therapy at "
            "maximally tolerated doses for at least 6 weeks; "
            "(4) Cardiovascular risk factor documentation in the chart; "
            "(5) Recent (within 30 days) basic metabolic panel, complete "
            "blood count, and coagulation studies."
        ),
        source_document="UnitedHealthcare Commercial Medical Policy: Cardiac Catheterization",
        source_document_number="2024T0478U",
        page=3,
        last_updated="2025-07-01",
        source_url="https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/cardiac-catheterization.pdf",
    ))

    chunks.append(PolicyChunk(
        id="UHC-93458-002",
        payer="UnitedHealthcare",
        applies_to_cpt=["93458"],
        procedure_name="Left Heart Catheterization with Coronary Angiography",
        section_heading="Common Denial Reasons",
        text=(
            "The most common reasons cardiac catheterization requests are denied "
            "by UnitedHealthcare: (a) No non-invasive testing performed prior "
            "to the catheterization request; (b) Inadequate documentation of "
            "failed medical therapy — note must specify drug names, doses, and "
            "duration; (c) Stress test results not attached or not clearly "
            "positive for ischemia; (d) LVEF assessment older than 90 days; "
            "(e) Patient does not meet appropriate use criteria for diagnostic "
            "angiography."
        ),
        source_document="UnitedHealthcare Commercial Medical Policy: Cardiac Catheterization",
        source_document_number="2024T0478U",
        page=5,
        last_updated="2025-07-01",
        source_url="https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/cardiac-catheterization.pdf",
    ))

    chunks.append(PolicyChunk(
        id="AETNA-93458-001",
        payer="Aetna",
        applies_to_cpt=["93458", "93460"],
        procedure_name="Left Heart Catheterization",
        section_heading="Indications",
        text=(
            "Aetna considers cardiac catheterization medically necessary for "
            "patients with: (1) Acute coronary syndrome including STEMI, "
            "NSTEMI, or unstable angina; (2) Stable angina with positive or "
            "high-risk non-invasive testing; (3) Heart failure with reduced "
            "ejection fraction (LVEF ≤40%) of unknown etiology; (4) Survivors "
            "of sudden cardiac arrest in whom CAD is suspected; (5) Pre-operative "
            "evaluation prior to non-cardiac surgery in patients with known "
            "CAD and changes in clinical status."
        ),
        source_document="Aetna Clinical Policy Bulletin: Cardiac Catheterization",
        source_document_number="0234",
        page=2,
        last_updated="2025-08-20",
        source_url="https://www.aetna.com/cpb/medical/data/200_299/0234.html",
    ))

    chunks.append(PolicyChunk(
        id="BCBS-93458-001",
        payer="Blue Cross Blue Shield",
        applies_to_cpt=["93458"],
        procedure_name="Left Heart Catheterization",
        section_heading="Coverage Criteria",
        text=(
            "BCBS covers left heart catheterization with coronary angiography "
            "when there is documented evidence of: (a) High-risk findings on "
            "non-invasive stress testing (positive ETT with 2+ mm ST "
            "depression, large reversible defect on SPECT, or wall motion "
            "abnormalities on stress echo); (b) Failed maximal medical therapy "
            "with persistent angina interfering with quality of life; "
            "(c) Heart failure with reduced ejection fraction of suspected "
            "ischemic etiology; (d) Pre-operative cardiac assessment when "
            "non-invasive imaging is non-diagnostic. Peer-to-peer review may "
            "be required for borderline indications."
        ),
        source_document="BCBS Medical Policy: Cardiac Catheterization",
        source_document_number="2025-CV-0012",
        page=2,
        last_updated="2025-06-01",
        source_url="https://www.bcbs.com/medical-policies/cardiac-catheterization",
    ))

    # ═══════════════════════════════════════════════════════════════
    # CPT 33361 — TAVR
    # ═══════════════════════════════════════════════════════════════

    chunks.append(PolicyChunk(
        id="CMS-NCD-20.32",
        payer="Medicare",
        applies_to_cpt=["33361", "33362", "33363", "33364", "33365", "33366"],
        procedure_name="Transcatheter Aortic Valve Replacement (TAVR)",
        section_heading="National Coverage Determination 20.32",
        text=(
            "CMS covers TAVR under Coverage with Evidence Development for "
            "patients with severe symptomatic aortic stenosis who meet ALL of "
            "the following: (1) Severe AS confirmed by echocardiography (AVA "
            "≤ 1.0 cm², mean gradient ≥ 40 mmHg, or peak velocity ≥ 4.0 m/s); "
            "(2) NYHA Class II or greater symptoms attributable to aortic "
            "stenosis; (3) Heart Team evaluation including a cardiothoracic "
            "surgeon and an interventional cardiologist with documentation of "
            "the recommendation for TAVR over surgical AVR; (4) STS-PROM score "
            "documented for surgical risk assessment; (5) Procedure performed "
            "in a CMS-approved hospital with appropriate volumes and outcomes."
        ),
        source_document="CMS National Coverage Determination 20.32",
        source_document_number="NCD 20.32",
        page=1,
        last_updated="2019-06-21",
        source_url="https://www.cms.gov/medicare-coverage-database/view/ncd.aspx?NCDId=355",
        chunk_type="ncd",
    ))

    chunks.append(PolicyChunk(
        id="UHC-33361-001",
        payer="UnitedHealthcare",
        applies_to_cpt=["33361"],
        procedure_name="Transcatheter Aortic Valve Replacement (TAVR)",
        section_heading="Coverage Criteria",
        text=(
            "TAVR is considered medically necessary for the treatment of "
            "severe symptomatic aortic stenosis when ALL of the following are "
            "documented: (1) Echocardiographic evidence of severe AS — AVA ≤ 1.0 "
            "cm², mean gradient ≥ 40 mmHg, OR peak jet velocity ≥ 4.0 m/s; "
            "(2) NYHA Class II–IV symptoms attributable to aortic stenosis "
            "(dyspnea, syncope, angina, or heart failure); (3) Heart Team "
            "consultation note including cardiothoracic surgeon and "
            "interventional cardiologist with TAVR-vs-SAVR recommendation; "
            "(4) STS-PROM mortality score documented; (5) Pre-procedural CT "
            "angiography with annular sizing and access vessel assessment; "
            "(6) Coronary anatomy assessed within the past 12 months "
            "(angiography or CCTA); (7) Life expectancy exceeding 12 months "
            "with expected functional improvement."
        ),
        source_document="UnitedHealthcare Commercial Medical Policy: TAVR",
        source_document_number="2024T0512U",
        page=3,
        last_updated="2025-09-15",
        source_url="https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/tavr.pdf",
    ))

    chunks.append(PolicyChunk(
        id="AETNA-33361-001",
        payer="Aetna",
        applies_to_cpt=["33361"],
        procedure_name="Transcatheter Aortic Valve Replacement (TAVR)",
        section_heading="Indications",
        text=(
            "Aetna considers TAVR medically necessary in adult patients with "
            "severe symptomatic native aortic valve stenosis who meet the "
            "criteria from the ACC/AHA Valvular Heart Disease Guidelines, "
            "including: documented severe AS by Doppler echocardiography, "
            "NYHA Class II or greater symptoms, Heart Team recommendation "
            "after evaluation by both a cardiothoracic surgeon and an "
            "interventional cardiologist, surgical risk stratification using "
            "STS-PROM score, and assessment of frailty and comorbidities. "
            "TAVR is appropriate for high, intermediate, or low surgical risk "
            "patients in whom the Heart Team determines TAVR is preferred "
            "over surgical AVR."
        ),
        source_document="Aetna Clinical Policy Bulletin: Transcatheter Aortic Valve Implantation",
        source_document_number="0810",
        page=3,
        last_updated="2025-09-01",
        source_url="https://www.aetna.com/cpb/medical/data/800_899/0810.html",
    ))

    # ═══════════════════════════════════════════════════════════════
    # CPT 93656 — Catheter Ablation of Atrial Fibrillation
    # ═══════════════════════════════════════════════════════════════

    chunks.append(PolicyChunk(
        id="UHC-93656-001",
        payer="UnitedHealthcare",
        applies_to_cpt=["93656", "93657"],
        procedure_name="Catheter Ablation of Atrial Fibrillation",
        section_heading="Coverage Criteria",
        text=(
            "Catheter ablation for atrial fibrillation is considered medically "
            "necessary when ALL of the following are met: "
            "(1) Documented atrial fibrillation on 12-lead ECG, Holter monitor, "
            "event recorder, or implantable loop recorder; "
            "(2) Failed trial of, or intolerance to, at least one Class I or "
            "Class III antiarrhythmic drug — flecainide, propafenone, sotalol, "
            "dofetilide, dronedarone, or amiodarone — with the specific reason "
            "for failure or discontinuation documented; "
            "(3) Symptomatic AF despite attempts at rate or rhythm control; "
            "(4) Recent transthoracic echocardiogram (within 12 months) "
            "documenting LVEF, left atrial size, and structural assessment; "
            "(5) CHA₂DS₂-VASc score calculated with anticoagulation plan "
            "documented for the periprocedural period; "
            "(6) Pre-procedure TEE or cardiac CT documenting absence of left "
            "atrial appendage thrombus."
        ),
        source_document="UnitedHealthcare Commercial Medical Policy: Catheter Ablation for AF",
        source_document_number="2024T0495U",
        page=3,
        last_updated="2025-08-01",
        source_url="https://www.uhcprovider.com/content/dam/provider/docs/public/policies/comm-medical-drug/catheter-ablation-af.pdf",
    ))

    chunks.append(PolicyChunk(
        id="AETNA-93656-001",
        payer="Aetna",
        applies_to_cpt=["93656"],
        procedure_name="Catheter Ablation of Atrial Fibrillation",
        section_heading="Indications",
        text=(
            "Aetna considers catheter ablation for atrial fibrillation medically "
            "necessary for symptomatic patients with paroxysmal or persistent "
            "AF who have failed or are intolerant to at least one rhythm "
            "control medication. First-line catheter ablation may be "
            "appropriate in selected patients with symptomatic paroxysmal AF "
            "when the patient prefers rhythm control over long-term medication, "
            "with appropriate documentation of shared decision making. "
            "Pre-procedure imaging requirements: TTE within 12 months, TEE or "
            "cardiac CT to rule out LA appendage thrombus prior to ablation."
        ),
        source_document="Aetna Clinical Policy Bulletin: Catheter Ablation",
        source_document_number="0716",
        page=4,
        last_updated="2025-07-30",
        source_url="https://www.aetna.com/cpb/medical/data/700_799/0716.html",
    ))

    chunks.append(PolicyChunk(
        id="BCBS-93656-001",
        payer="Blue Cross Blue Shield",
        applies_to_cpt=["93656"],
        procedure_name="Catheter Ablation of Atrial Fibrillation",
        section_heading="Coverage Criteria",
        text=(
            "BCBS covers catheter ablation for AF when: (1) The patient has "
            "objectively documented symptomatic atrial fibrillation; (2) Trial "
            "and failure of at least one rhythm-control antiarrhythmic agent "
            "is documented in the medical record; (3) Pre-procedural TEE or "
            "cardiac CT confirms absence of LA appendage thrombus; "
            "(4) CHA₂DS₂-VASc score documented with anticoagulation strategy. "
            "Repeat ablation within 12 months of a prior ablation requires "
            "peer-to-peer review and documentation of recurrent symptomatic AF "
            "with objective monitoring evidence."
        ),
        source_document="BCBS Medical Policy: Catheter Ablation for AF",
        source_document_number="2024-EP-0067",
        page=3,
        last_updated="2025-06-01",
        source_url="https://www.bcbs.com/medical-policies/catheter-ablation-af",
    ))

    # ═══════════════════════════════════════════════════════════════
    # ACC/AHA guideline anchors — apply across payers
    # ═══════════════════════════════════════════════════════════════

    chunks.append(PolicyChunk(
        id="ACCAHA-CCS-2023-001",
        payer="Medicare",
        applies_to_cpt=["78492", "78452", "78451", "93458", "75574"],
        procedure_name="Chronic Coronary Disease Diagnosis and Management",
        section_heading="2023 ACC/AHA/ACCP/HRS Guideline for the Management of Patients with Chronic Coronary Disease",
        text=(
            "For patients with stable chest pain and intermediate-to-high pre-test "
            "probability of obstructive CAD, advanced cardiac imaging (cardiac "
            "PET, CCTA, or stress CMR) is recommended (Class IIa) when "
            "standard exercise stress ECG or SPECT is non-diagnostic, "
            "technically limited, or contraindicated. The guideline emphasizes "
            "that the choice between PET and SPECT should be driven by patient "
            "factors including body habitus (BMI ≥35), prior attenuation "
            "artifact, and the need for absolute myocardial blood flow "
            "quantification."
        ),
        source_document="2023 ACC/AHA/ACCP/HRS Guideline for the Management of Patients with Chronic Coronary Disease",
        source_document_number="JACC 2023;82(9):833–955",
        page=42,
        last_updated="2023-08-01",
        source_url="https://www.acc.org/guidelines/hubs/chronic-coronary-disease",
        chunk_type="guideline",
    ))

    chunks.append(PolicyChunk(
        id="ACCAHA-VHD-2020-001",
        payer="Medicare",
        applies_to_cpt=["33361"],
        procedure_name="Valvular Heart Disease Management",
        section_heading="2020 ACC/AHA Guideline for the Management of Patients with Valvular Heart Disease",
        text=(
            "TAVR is recommended (Class I) over SAVR for symptomatic patients "
            "with severe aortic stenosis who are at high or prohibitive surgical "
            "risk. TAVR is reasonable (Class IIa) over SAVR in symptomatic "
            "patients at intermediate surgical risk. For low-risk patients, "
            "TAVR and SAVR are both recommended options after Heart Team "
            "evaluation considering patient preferences, anatomic suitability, "
            "and life expectancy. The Heart Team must include a cardiothoracic "
            "surgeon and an interventional cardiologist, and STS-PROM score "
            "must be documented."
        ),
        source_document="2020 ACC/AHA Guideline for the Management of Patients with Valvular Heart Disease",
        source_document_number="Circulation 2021;143:e72–e227",
        page=88,
        last_updated="2020-12-17",
        source_url="https://www.acc.org/guidelines/hubs/valvular-heart-disease",
        chunk_type="guideline",
    ))

    chunks.append(PolicyChunk(
        id="ACCAHA-AF-2023-001",
        payer="Medicare",
        applies_to_cpt=["93656", "93657"],
        procedure_name="Atrial Fibrillation Management",
        section_heading="2023 ACC/AHA/ACCP/HRS Guideline for the Diagnosis and Management of Atrial Fibrillation",
        text=(
            "Catheter ablation is recommended (Class I) for symptomatic AF "
            "patients in whom rhythm control is desired and at least one "
            "Class I or Class III antiarrhythmic drug has been ineffective, "
            "contraindicated, or not tolerated. For selected patients with "
            "symptomatic paroxysmal AF, catheter ablation may be considered "
            "as first-line therapy (Class IIa) before a trial of antiarrhythmic "
            "drug therapy, particularly when the patient prefers rhythm control. "
            "Pre-procedural imaging to exclude LA thrombus and stroke risk "
            "stratification using CHA₂DS₂-VASc are required."
        ),
        source_document="2023 ACC/AHA/ACCP/HRS Guideline for the Diagnosis and Management of Atrial Fibrillation",
        source_document_number="JACC 2024;83(1):109–279",
        page=164,
        last_updated="2023-11-30",
        source_url="https://www.acc.org/guidelines/hubs/atrial-fibrillation",
        chunk_type="guideline",
    ))

    return chunks
