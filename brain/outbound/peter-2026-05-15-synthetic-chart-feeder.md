# Email — Peter — 2026-05-15 — yes to synthetic chart feeder

**To:** Peter
**Subject:** Re: Possible next steps — yes, synthetic chart feeder is the right call

---

Peter,

Right call, and it's the cleanest path forward. Epic's public sandbox patients aren't going to get richer (Epic doesn't curate them by specialty), and waiting on deidentified real cases stalls calibration. A synthetic chart that mirrors how your Epic chart is organized clinician-side lets us test rich cardiology cases this week.

**What I'll build**

A small synthetic-chart module + a new page in the app. Each case is a single file that contains both the structured FHIR resources (Patient demographics, Coverage / member ID, ordering Practitioner / NPI, Encounter, diagnoses, ordered procedure CPT) and the unstructured chart sections matching your Epic layout — Notes, Labs, Imaging, Procedures, Cardiology, Medications, Letters, Encounters, Plans of Care, Orders. Each section entry becomes a DocumentReference that flows through the existing pipeline.

The important architectural point: the synthetic case emits the **same Bundle shape** Epic returns. So the rest of the pipeline can't tell the difference — same mapper, same retrieval, same packet PDF, same coherence gate. The synthetic feeder is a drop-in replacement for the Epic FHIR fetch.

**What this enables**

A growing library of cardiology test cases that double as regression tests:

- Clear approval (all criteria met, well-documented) → HIGH packet, no retrieval needed
- Subtle approval (current note thin, key fact buried 3 years deep) → corpus retrieval surfaces it — the killer-feature path
- Edge case (LBBB + severe knee OA → treadmill contraindicated, should be pharmacologic) → pipeline flips the ordered test
- Clear denial (no indication, no supporting evidence) → blocks, no hallucination
- Adverse buried fact (contrast allergy 5 years prior in Allergies) → pipeline catches it

Each one runs in seconds. Every commit, the same cases produce the same verdicts — that's how we'll measure regression as we add features.

**Two questions before I start**

1. **Authoring format** — would you rather write each case as **plain markdown** with section headers (closer to how you write clinical notes), and I parse those into DocumentReferences, or **structured YAML** with one section per key? Markdown wins on speed-to-author; YAML wins on machine-readability. I'd default to markdown unless you prefer otherwise.

2. **First case** — do you want me to convert the Whitford SPECT case (currently in `/#corpus-demo`) into full chart form so it becomes the template, or would you rather draft a real-world borderline case you typically see — say a 70-year-old with prior nondiagnostic stress + new exertional symptoms — that we use as the first realistic test?

If you draft one case in whatever format feels natural, I'll build the loader and UI around it this week. After that, adding cases is a 10-minute copy-paste-edit per patient, and the library grows from there.

Thanks,
Prem

<!-- STATUS: drafted 2026-05-15, awaiting send -->
