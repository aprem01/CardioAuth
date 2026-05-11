"""Patient longitudinal corpus + retrieval — the killer feature.

Peter's "treadmill 3 years ago" insight made the product strategy
clear: the pipeline doesn't just reason over the current encounter
note. It reads the patient's WHOLE chart and surfaces facts from
historical documents that strengthen the PA package.

This module models that:
  - CorpusDocument        one document in the patient's chart (current
                          note, prior encounter, stress test report,
                          ECG narrative, echo, imaging, lab summary,
                          outside records, etc.)
  - PatientCorpus         the full bundle for one patient + which
                          doc is the current encounter
  - retrieve_corpus       BM25 over the historical documents, scoped
                          to query terms drawn from the case's
                          applicable criteria

The retrieved snippets are folded into State 2's prompt so the LLM
can cite from any document — and the criterion evaluations carry
which document each quote came from. The clinician's narrative draft
then references those historical facts.

Design: simple by default. BM25 over sentence-level chunks, no
embeddings yet. The LLM does the heavy lifting once it has the
right snippets in context. Embeddings are a Stage 2 upgrade if
recall isn't sufficient.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from cardioauth.rag.retriever import _BM25Index, _tokenize


DocType = Literal[
    "current_note",       # the active encounter — never retrieved against itself
    "prior_encounter",    # past office visit notes
    "stress_test",        # exercise treadmill, SPECT, PET, dobutamine echo reports
    "ecg_report",         # baseline / event ECGs (narrative form, not strip)
    "echo_report",        # transthoracic / transesophageal echo
    "imaging_report",     # CT, MRI, nuclear, cath reports
    "cath_report",        # left/right heart catheterization specifically
    "lab_summary",        # batched labs / chem panels / cardiac markers
    "medication_list",    # current + historical med list
    "problem_list",       # active problem list dump
    "outside_records",    # PDFs from other institutions
    "other",
]


@dataclass(frozen=True)
class CorpusDocument:
    """One document in a patient's longitudinal chart."""

    doc_id: str                                    # stable identifier
    doc_type: DocType
    date: str                                      # ISO 8601 (YYYY-MM-DD) or "" if unknown
    title: str                                     # human-readable (e.g., "Exercise treadmill stress test")
    text: str                                      # full document text
    source: str = ""                               # facility / department, optional

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "doc_type": self.doc_type,
            "date": self.date, "title": self.title,
            "source": self.source,
            "text_length": len(self.text),
        }


@dataclass
class PatientCorpus:
    """The longitudinal bundle for one patient.

    Exactly one document SHOULD be tagged doc_type="current_note" —
    that's the encounter driving the PA. The rest are historical
    artifacts the reasoner can cite from.
    """

    patient_id: str
    documents: list[CorpusDocument] = field(default_factory=list)

    def current_note(self) -> CorpusDocument | None:
        for d in self.documents:
            if d.doc_type == "current_note":
                return d
        return None

    def historical(self) -> list[CorpusDocument]:
        return [d for d in self.documents if d.doc_type != "current_note"]

    def to_dict(self) -> dict:
        return {
            "patient_id": self.patient_id,
            "document_count": len(self.documents),
            "current_note_present": self.current_note() is not None,
            "historical_count": len(self.historical()),
            "doc_types": sorted({d.doc_type for d in self.historical()}),
            "earliest_date": _earliest_date(self.documents),
            "latest_date": _latest_date(self.documents),
        }


def _earliest_date(docs: list[CorpusDocument]) -> str:
    dates = sorted(d.date for d in docs if d.date)
    return dates[0] if dates else ""


def _latest_date(docs: list[CorpusDocument]) -> str:
    dates = sorted(d.date for d in docs if d.date)
    return dates[-1] if dates else ""


# ─────────────────────────────────────────────────────────────────────
# Retrieval
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CorpusSnippet:
    """One retrieved sentence-level snippet from the historical corpus."""

    doc_id: str
    doc_type: DocType
    doc_date: str
    doc_title: str
    snippet: str
    score: float
    rank: int

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id, "doc_type": self.doc_type,
            "doc_date": self.doc_date, "doc_title": self.doc_title,
            "snippet": self.snippet,
            "score": round(self.score, 4), "rank": self.rank,
        }

    def citation(self) -> str:
        """Human-readable inline citation for prompts / narrative."""
        date_str = f" {self.doc_date}" if self.doc_date else ""
        return f"[{self.doc_title}{date_str}]"


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_HEADING_LINE = re.compile(r"^[A-Z][A-Z\s/:.-]{2,}$", re.MULTILINE)


def _split_into_snippets(text: str, max_chunk_chars: int = 400) -> list[str]:
    """Split a document into searchable snippets. Sentences first; if
    a sentence is huge (lab dump, long paragraph), fall back to fixed
    windows. Skips empty + heading-only lines.
    """
    if not text:
        return []
    # First, split on paragraph breaks; within each, split on sentences
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    snippets: list[str] = []
    for para in paragraphs:
        # Skip heading-only paragraphs
        if _HEADING_LINE.match(para) and len(para) < 50:
            continue
        # Split on sentence boundaries
        sentences = _SENTENCE_SPLIT.split(para)
        for s in sentences:
            s = s.strip()
            if not s or len(s) < 10:
                continue
            if len(s) <= max_chunk_chars:
                snippets.append(s)
            else:
                # Long sentence — chunk by char window with overlap
                for i in range(0, len(s), max_chunk_chars - 50):
                    snippets.append(s[i : i + max_chunk_chars])
    return snippets


def retrieve_corpus(
    corpus: PatientCorpus,
    *,
    query_terms: list[str],
    top_k: int = 6,
    exclude_current: bool = True,
) -> list[CorpusSnippet]:
    """BM25 retrieval over the patient's historical corpus.

    `query_terms` is a list of clinical keywords distilled from the
    case's applicable criteria + CPT (e.g. ["LBBB", "treadmill",
    "exercise", "stress", "BMI", "ejection fraction"]). The caller
    is responsible for building it from the taxonomy slice.

    Returns up to `top_k` CorpusSnippet objects ranked by relevance,
    each carrying its source document metadata for citation.
    """
    if not corpus.documents or not query_terms:
        return []

    # Build (document, snippet) pairs across historical documents
    doc_snippet_pairs: list[tuple[CorpusDocument, str]] = []
    for doc in corpus.documents:
        if exclude_current and doc.doc_type == "current_note":
            continue
        for snip in _split_into_snippets(doc.text):
            doc_snippet_pairs.append((doc, snip))

    if not doc_snippet_pairs:
        return []

    # BM25 index over snippet tokens
    snippet_tokens = [_tokenize(snip) for _, snip in doc_snippet_pairs]
    index = _BM25Index.build(snippet_tokens)
    query_tokens = [t.lower() for t in query_terms if t]
    scores = index.score(query_tokens)

    # Rank by score; keep only non-zero
    ranked = sorted(
        enumerate(scores), key=lambda x: x[1], reverse=True,
    )
    out: list[CorpusSnippet] = []
    for rank, (idx, score) in enumerate(ranked[:top_k], start=1):
        if score <= 0:
            break
        doc, snip = doc_snippet_pairs[idx]
        out.append(CorpusSnippet(
            doc_id=doc.doc_id, doc_type=doc.doc_type,
            doc_date=doc.date, doc_title=doc.title,
            snippet=snip, score=score, rank=rank,
        ))
    return out


# ─────────────────────────────────────────────────────────────────────
# Query-term construction from criteria
# ─────────────────────────────────────────────────────────────────────


# Curated mapping from criterion-code prefix to clinical keywords
# the historical corpus is likely to contain. Keeps query construction
# deterministic — no LLM call needed to figure out what to search for.
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "ECG":  ["LBBB", "left bundle", "RBBB", "right bundle", "paced", "pacemaker",
             "WPW", "Wolff-Parkinson-White", "pre-excitation", "LVH", "ECG", "EKG"],
    "EX":   ["exercise", "treadmill", "Bruce", "METs", "MPHR", "target heart rate",
             "submaximal", "tolerance", "unable to exercise", "knee", "arthrit",
             "deconditioned", "wheelchair", "non-ambulatory"],
    "NDX":  ["nondiagnostic", "non-diagnostic", "equivocal", "submaximal",
             "inconclusive", "attenuation", "false-positive", "suboptimal",
             "technically limited"],
    "BMI":  ["BMI", "body mass", "obesity", "obese", "morbid obesity",
             "attenuation", "breast attenuation", "diaphragmatic"],
    "SX":   ["chest pain", "chest pressure", "angina", "dyspnea", "syncope",
             "palpitations", "CCS", "NYHA"],
    "MED":  ["aspirin", "statin", "beta-blocker", "metoprolol", "ACE",
             "ARB", "nitrate", "isosorbide"],
    "RISK": ["diabetes", "hypertension", "hyperlipidemia", "family history",
             "smoker", "tobacco", "CAD", "MI", "CABG", "PCI", "stent"],
    "LVEF": ["LVEF", "ejection fraction", "EF", "systolic function"],
    "ANTI": ["warfarin", "Coumadin", "apixaban", "Eliquis", "rivaroxaban",
             "Xarelto", "dabigatran", "Pradaxa", "CHADS", "HAS-BLED",
             "atrial fibrillation", "AF"],
    "IMG":  ["echo", "MRI", "CT", "angiography", "imaging"],
    "FREQ": ["last year", "prior stress", "previous", "since"],
    "GUI":  ["AUC", "appropriate use", "appropriateness"],
    "DOC":  [],  # documentation completeness — no keywords needed
    "DEM":  ["age", "sex", "male", "female", "geriatric"],
    "HT":   ["heart team", "Heart Team", "multidisciplinary", "STS-PROM"],
}


def build_query_terms(applicable_criteria: list[dict], request_cpt: str) -> list[str]:
    """Distill query terms from the case's applicable taxonomy slice.

    Walks each criterion's code prefix to look up clinical keywords;
    deduplicates; returns a flat list. Caller passes to retrieve_corpus.
    """
    terms: list[str] = []
    seen: set[str] = set()
    for entry in applicable_criteria:
        code = entry.get("code", "")
        prefix = code.split("-")[0] if "-" in code else ""
        kws = _CATEGORY_KEYWORDS.get(prefix, [])
        for kw in kws:
            lower = kw.lower()
            if lower not in seen:
                seen.add(lower)
                terms.append(kw)
    # Always include the procedure name keywords
    cpt_keywords = {
        "78492": ["PET", "positron emission", "myocardial perfusion"],
        "78452": ["SPECT", "single photon", "myocardial perfusion", "Lexiscan",
                  "regadenoson", "dipyridamole", "adenosine"],
        "75574": ["CT angiography", "CTA", "coronary CT"],
        "33361": ["TAVR", "transcatheter aortic valve", "aortic stenosis"],
        "92928": ["PCI", "stent", "angioplasty", "LAD", "RCA", "LCx"],
        "93312": ["TEE", "transesophageal"],
        "93619": ["EP study", "electrophysiology", "ablation"],
        "93880": ["carotid", "duplex"],
        "75561": ["cardiac MRI", "CMR", "gadolinium", "LGE"],
        "93458": ["catheterization", "LHC", "coronary angiography"],
    }
    for kw in cpt_keywords.get(request_cpt, []):
        if kw.lower() not in seen:
            seen.add(kw.lower())
            terms.append(kw)
    return terms
