# Email — Peter — 2026-05-15 — feeder green light, starting build

**To:** Peter
**Subject:** Re: Possible next steps — building it

---

Peter,

Got it — markdown, Whitford as the template, PDF-style documents for stress tests / echoes / cath reports / outside records. Starting the build today.

The PDF point is important and surfaces a real gap I want to fix while I'm in there: our pipeline today doesn't actually parse PDF content out of Epic Binary attachments — it only reads HTML/text bodies and skips PDFs. Real Epic charts have plenty of PDFs (especially in the older / cardiology / outside-records sections), so the synthetic feeder is the right reason to wire actual PDF text extraction into the corpus retrieval. That way:

- Synthetic PDFs in the test fixtures behave like real Epic PDFs
- When we eventually hit a real Epic install with PDF-heavy charts, the pipeline reads them too
- Each synthetic case can include a downloadable PDF artifact alongside the indexed text, so the UI feels like a real chart

I'll ship the loader, the markdown spec, the Whitford template, the PDF-parsing path, and a synthetic-cases page in the app this week. Once the first case is live and you can see the round-trip — markdown → synthetic Epic chart → pipeline → packet — I'll send you the template so you can start authoring real-world borderline cases from your workflow.

Thanks,
Prem

<!-- STATUS: drafted 2026-05-15, awaiting send -->
