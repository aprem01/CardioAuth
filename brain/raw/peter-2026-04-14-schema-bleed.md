# Peter — 2026-04-14 — schema/categorization feedback

I spent some time running several cases through the system, and overall it's
looking very promising. The extraction is working mostly correctly.

As I went through multiple cases, I started noticing a few recurring patterns
where certain data elements land in slightly different fields than expected:

- ECG findings sometimes appear under Imaging Results → could be cleaner if
  ECGs stay only in the ECG section
- Imaging/test results occasionally get grouped under Prior Procedures → may
  help to separate Prior Testing vs Prior Procedures more clearly
- Clinical events (e.g., MI) sometimes show up under procedures → might benefit
  from a distinct Past Medical History or Comorbidities category
- Family history can get included under Comorbidities → could be split into
  its own field
- Symptoms and exam findings (e.g., dyspnea, edema) sometimes appear under
  comorbidities → clearer separation between Symptoms, Exam Findings, and
  Comorbidities may help
- Broader clinical narrative sometimes gets pulled into structured fields →
  could tighten what qualifies as structured vs background content

Since these patterns were fairly consistent across cases, it feels more like
a schema/categorization refinement opportunity rather than an extraction
issue itself.

One thought that might help: before expanding testing further, it could be
useful to align on a "core" master schema that captures the common fields
across all cardiac prior auth use cases (testing and procedures) we intend
to cover. [...]

Also, I ran into an "AI extraction unavailable — spend limit reached" message
while testing, just wanted to flag that in case it's something on the config
side.

<!-- INGESTED: 2026-04-14. Pages updated: people/peter.md, decisions/2026-04-14-chartdata-v2.md -->
