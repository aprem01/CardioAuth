"""Parse a markdown case file into a FHIR R4 Bundle.

File shape:

    ---
    patient_id: DEMO-WHITFORD
    patient_name: Eleanor R. Whitford
    dob: 1957-04-12
    sex: female
    member_id: UHC-66432198
    payer: UnitedHealthcare
    ordering_physician: Yuki R. Tanaka, MD
    ordering_npi: 1029384765
    encounter_date: 2026-05-06
    procedure_code: 78452
    procedure_name: Cardiac SPECT MPI
    diagnoses:
      - code: I25.10
        text: Atherosclerotic heart disease
    ---

    # Current Encounter Note
    type: progress_note
    date: 2026-05-06
    author: Yuki R. Tanaka, MD

    Patient: Eleanor R. Whitford
    DOB: 04/12/1957
    [...body...]

    # Exercise Treadmill Stress Test
    type: stress_test
    date: 2023-06-18
    author: David L. Carrasco, MD, FACC
    format: pdf

    Cardiology Department Stress Test Report
    [...body...]

YAML frontmatter → Patient + Coverage + Encounter + ServiceRequest +
Practitioner. Each H1 section → one DocumentReference. Section-level
properties (type, date, author, format) live on lines immediately
under the H1 in `key: value` form; the body starts after a blank line.

When format=pdf, the renderer wraps the body into a PDF and attaches
both the PDF (downloadable) and the plaintext body (indexed by the
corpus). For non-PDF sections the body is HTML-wrapped to match what
Epic actually serves.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


_CASES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "synthetic_cases"


# LOINC code lookup for each DocType — matches what Epic emits so the
# downstream corpus mapper categorises identically to real Epic data.
_DOCTYPE_LOINC = {
    "progress_note":     ("11506-3", "Progress note"),
    "stress_test":       ("18746-1", "Cardiac stress study report"),
    "ecg_report":        ("11524-6", "EKG study"),
    "echo_report":       ("29274-8", "Echocardiography study"),
    "cath_report":       ("18748-7", "Cardiac catheterization study"),
    "imaging_report":    ("18747-9", "Diagnostic imaging study"),
    "lab_summary":       ("11502-2", "Laboratory report"),
    "prior_encounter":   ("11506-3", "Progress note"),
    "outside_records":   ("34108-1", "Outpatient note"),
    "h_and_p":           ("34117-2", "History and physical note"),
    "discharge_summary": ("18842-5", "Discharge summary"),
    "letter":            ("51852-2", "Letter"),
    "consult":           ("11488-4", "Consult note"),
    "patient_instructions": ("80631-8", "Patient instruction"),
    "other":             ("34109-9", "Note"),
}


@dataclass
class CaseSection:
    title: str
    doc_type: str
    date: str          # ISO 8601 date
    author: str
    body: str
    format: str        # "html" (default) | "pdf"


@dataclass
class SyntheticCase:
    patient_id: str
    patient_name: str
    dob: str
    sex: str
    member_id: str
    payer: str
    ordering_physician: str
    ordering_npi: str
    encounter_date: str
    procedure_code: str
    procedure_name: str
    diagnoses: list[dict] = field(default_factory=list)
    sections: list[CaseSection] = field(default_factory=list)
    source_path: str = ""


# ── Parsing ─────────────────────────────────────────────────────────────


_SECTION_PROP_KEYS = {"type", "date", "author", "format"}


def load_case_markdown(text: str, *, source_path: str = "") -> SyntheticCase:
    """Parse a markdown case file into a SyntheticCase. Raises ValueError
    on malformed frontmatter or missing required fields."""
    frontmatter, body = _split_frontmatter(text)
    if not frontmatter:
        raise ValueError("Case file missing YAML frontmatter (---) at top")

    try:
        meta = yaml.safe_load(frontmatter) or {}
    except yaml.YAMLError as e:
        raise ValueError(f"Bad YAML frontmatter: {e}")

    required = ["patient_id", "patient_name", "dob", "payer", "procedure_code"]
    missing = [k for k in required if not meta.get(k)]
    if missing:
        raise ValueError(f"Frontmatter missing required fields: {missing}")

    sections = _parse_sections(body)

    return SyntheticCase(
        patient_id=str(meta["patient_id"]),
        patient_name=str(meta["patient_name"]),
        dob=str(meta["dob"]),
        sex=str(meta.get("sex", "")),
        member_id=str(meta.get("member_id", "")),
        payer=str(meta["payer"]),
        ordering_physician=str(meta.get("ordering_physician", "")),
        ordering_npi=str(meta.get("ordering_npi", "")),
        encounter_date=str(meta.get("encounter_date", "")),
        procedure_code=str(meta["procedure_code"]),
        procedure_name=str(meta.get("procedure_name", "")),
        diagnoses=list(meta.get("diagnoses") or []),
        sections=sections,
        source_path=source_path,
    )


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_yaml, rest_of_body). Frontmatter is the block
    between two `---` lines at the start of the file."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return "", text
    end = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end = i
            break
    if end is None:
        return "", text
    return "\n".join(lines[1:end]), "\n".join(lines[end + 1:])


def _parse_sections(body: str) -> list[CaseSection]:
    """Walk the body, splitting on H1 headings. Each section's first lines
    after the heading are interpreted as `key: value` props until a blank
    line, after which everything else is the section body."""
    sections: list[CaseSection] = []
    cur_title: str | None = None
    cur_props: dict[str, str] = {}
    cur_body_lines: list[str] = []
    in_props = False

    def flush() -> None:
        if cur_title is None:
            return
        sections.append(CaseSection(
            title=cur_title.strip(),
            doc_type=(cur_props.get("type") or "other").strip(),
            date=(cur_props.get("date") or "").strip(),
            author=(cur_props.get("author") or "").strip(),
            body="\n".join(cur_body_lines).strip(),
            format=(cur_props.get("format") or "html").strip().lower(),
        ))

    for line in body.splitlines():
        if line.startswith("# "):
            flush()
            cur_title = line[2:]
            cur_props = {}
            cur_body_lines = []
            in_props = True
            continue
        if cur_title is None:
            continue
        if in_props:
            stripped = line.strip()
            if not stripped:
                in_props = False
                continue
            m = re.match(r"^([a-zA-Z_]+)\s*:\s*(.+)$", stripped)
            if m and m.group(1) in _SECTION_PROP_KEYS:
                cur_props[m.group(1)] = m.group(2)
                continue
            # Not a recognised prop and not blank — assume the body starts here
            in_props = False
            cur_body_lines.append(line)
        else:
            cur_body_lines.append(line)
    flush()
    return sections


# ── Case discovery ──────────────────────────────────────────────────────


def list_available_cases(cases_dir: Path | str | None = None) -> list[dict]:
    """List all available cases — both built-in (shipped on disk) and
    custom (uploaded by users, stored in SQLite). Custom cases get
    a `source: 'custom'` tag so the UI can show delete buttons for
    those without exposing built-in templates to deletion.
    """
    base = Path(cases_dir) if cases_dir else _CASES_DIR
    out: list[dict] = []
    seen_ids: set[str] = set()

    # Built-in templates from disk
    if base.exists():
        for path in sorted(base.glob("*.md")):
            try:
                case = load_case_markdown(path.read_text(), source_path=str(path))
                out.append({
                    "id": path.stem,
                    "patient_id": case.patient_id,
                    "patient_name": case.patient_name,
                    "procedure_code": case.procedure_code,
                    "procedure_name": case.procedure_name,
                    "payer": case.payer,
                    "section_count": len(case.sections),
                    "pdf_section_count": sum(1 for s in case.sections if s.format == "pdf"),
                    "source": "builtin",
                })
                seen_ids.add(path.stem)
            except Exception as e:
                logger.warning("Skipping unparseable case %s: %s", path, e)

    # User-authored custom cases from the DB. Lazy-import to avoid a
    # circular dep — persistence.py doesn't depend on synthetic, and we
    # don't want synthetic to require persistence at module import time.
    try:
        from cardioauth.persistence import get_store
        for row in get_store().list_synthetic_cases():
            if row["case_id"] in seen_ids:
                continue  # Custom case shadowed by a built-in of the same id
            out.append({
                "id": row["case_id"],
                "patient_id": row.get("patient_name", ""),
                "patient_name": row.get("patient_name", ""),
                "procedure_code": row.get("procedure_code", ""),
                "procedure_name": "",
                "payer": row.get("payer", ""),
                "section_count": int(row.get("section_count") or 0),
                "pdf_section_count": int(row.get("pdf_section_count") or 0),
                "source": "custom",
            })
    except Exception as e:
        logger.warning("Couldn't list custom synthetic cases: %s", e)

    return out


def load_case_by_id(case_id: str, cases_dir: Path | str | None = None) -> SyntheticCase:
    """Load a case by ID. Tries the built-in templates on disk first;
    falls back to the user-authored DB store. Built-ins win on conflict
    so users can't shadow a template (which is the desired safety —
    Peter's template should always be reproducible)."""
    base = Path(cases_dir) if cases_dir else _CASES_DIR
    path = base / f"{case_id}.md"
    if path.exists():
        return load_case_markdown(path.read_text(), source_path=str(path))

    # Try user-authored DB store
    try:
        from cardioauth.persistence import get_store
        row = get_store().get_synthetic_case(case_id)
        if row and row.get("markdown"):
            return load_case_markdown(row["markdown"], source_path=f"db:{case_id}")
    except Exception as e:
        logger.warning("DB lookup for case %s failed: %s", case_id, e)

    raise FileNotFoundError(f"No synthetic case with id '{case_id}'")


# ── Bundle assembly ─────────────────────────────────────────────────────


def case_to_bundle(case: SyntheticCase) -> dict[str, Any]:
    """Render a SyntheticCase as a FHIR Bundle shape-identical to what
    FHIRClient.get_patient_bundle returns from real Epic.

    PDF-format sections get rendered to actual PDF bytes and embedded
    as base64 inline data on the DocumentReference attachment, so the
    Binary-resolution path the corpus mapper already exercises picks
    up the text exactly as it would from real Epic.
    """
    from cardioauth.synthetic.pdf_renderer import render_section_pdf

    # Build each resource type's searchset
    patient = _patient_resource(case)
    coverage = _coverage_resource(case)
    encounter = _encounter_resource(case)
    practitioner = _practitioner_resource(case)
    service_request = _service_request_resource(case)
    conditions = _condition_resources(case)
    documents: list[dict[str, Any]] = []

    for i, sec in enumerate(case.sections):
        loinc_code, loinc_display = _DOCTYPE_LOINC.get(sec.doc_type, _DOCTYPE_LOINC["other"])
        body_bytes = sec.body.encode("utf-8")
        content_type = "text/plain"
        attachment_data = body_bytes
        if sec.format == "pdf":
            try:
                attachment_data = render_section_pdf(case, sec)
                content_type = "application/pdf"
            except Exception as e:
                logger.warning("PDF render failed for section %s: %s", sec.title, e)
                content_type = "text/plain"
        elif sec.format == "html":
            html = f"<html><body><h2>{sec.title}</h2><pre>{sec.body}</pre></body></html>"
            attachment_data = html.encode("utf-8")
            content_type = "text/html"

        doc = {
            "resourceType": "DocumentReference",
            "id": f"syn-{case.patient_id}-{i+1:02d}",
            "status": "current",
            "date": f"{sec.date}T00:00:00Z" if sec.date else "",
            "type": {
                "coding": [{
                    "system": "http://loinc.org",
                    "code": loinc_code,
                    "display": loinc_display,
                }],
                "text": sec.title,
            },
            "subject": {"reference": f"Patient/{case.patient_id}"},
            "author": [{"display": sec.author}] if sec.author else [],
            "content": [{
                "attachment": {
                    "contentType": content_type,
                    "data": base64.b64encode(attachment_data).decode("ascii"),
                },
            }],
        }
        documents.append(doc)

    return {
        "patient_id": case.patient_id,
        "resources": {
            "Patient":           _searchset([patient]),
            "Coverage":          _searchset([coverage]),
            "Encounter":         _searchset([encounter]),
            "Procedure":         _searchset([service_request]),  # use ServiceRequest for the order
            "Condition":         _searchset(conditions),
            "DocumentReference": _searchset(documents),
            # Other resource types come back empty for synthetic cases —
            # the lean pipeline tolerates missing types gracefully.
            "Observation":       _searchset([]),
            "MedicationRequest": _searchset([]),
            "DiagnosticReport":  _searchset([]),
        },
    }


def _searchset(resources: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "resourceType": "Bundle",
        "type": "searchset",
        "total": len(resources),
        "entry": [{"resource": r} for r in resources],
    }


def _patient_resource(case: SyntheticCase) -> dict[str, Any]:
    given, family = _split_name(case.patient_name)
    return {
        "resourceType": "Patient",
        "id": case.patient_id,
        "name": [{"given": given, "family": family}],
        "birthDate": case.dob,
        "gender": case.sex or "unknown",
    }


def _coverage_resource(case: SyntheticCase) -> dict[str, Any]:
    return {
        "resourceType": "Coverage",
        "id": f"coverage-{case.patient_id}",
        "status": "active",
        "subscriberId": case.member_id,
        "payor": [{"display": case.payer}],
        "beneficiary": {"reference": f"Patient/{case.patient_id}"},
    }


def _encounter_resource(case: SyntheticCase) -> dict[str, Any]:
    return {
        "resourceType": "Encounter",
        "id": f"enc-{case.patient_id}",
        "status": "finished",
        "subject": {"reference": f"Patient/{case.patient_id}"},
        "period": {"start": case.encounter_date},
    }


def _practitioner_resource(case: SyntheticCase) -> dict[str, Any]:
    given, family = _split_name(case.ordering_physician)
    return {
        "resourceType": "Practitioner",
        "id": f"pract-{case.patient_id}",
        "name": [{"given": given, "family": family}],
        "identifier": [{
            "system": "http://hl7.org/fhir/sid/us-npi",
            "value": case.ordering_npi,
        }],
    }


def _service_request_resource(case: SyntheticCase) -> dict[str, Any]:
    return {
        "resourceType": "Procedure",
        "id": f"order-{case.patient_id}",
        "status": "not-done",
        "code": {
            "coding": [{
                "system": "http://www.ama-assn.org/go/cpt",
                "code": case.procedure_code,
                "display": case.procedure_name,
            }],
            "text": case.procedure_name,
        },
        "subject": {"reference": f"Patient/{case.patient_id}"},
    }


def _condition_resources(case: SyntheticCase) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, dx in enumerate(case.diagnoses):
        code = dx.get("code", "") if isinstance(dx, dict) else ""
        text = dx.get("text", "") if isinstance(dx, dict) else str(dx)
        out.append({
            "resourceType": "Condition",
            "id": f"dx-{case.patient_id}-{i+1}",
            "code": {
                "coding": [{
                    "system": "http://hl7.org/fhir/sid/icd-10-cm",
                    "code": code,
                    "display": text,
                }] if code else [],
                "text": text,
            },
            "subject": {"reference": f"Patient/{case.patient_id}"},
        })
    return out


def _split_name(full: str) -> tuple[list[str], str]:
    """Split 'Eleanor R. Whitford' → (['Eleanor', 'R.'], 'Whitford')."""
    parts = full.strip().split()
    if not parts:
        return [], ""
    if len(parts) == 1:
        return [parts[0]], ""
    return parts[:-1], parts[-1]
