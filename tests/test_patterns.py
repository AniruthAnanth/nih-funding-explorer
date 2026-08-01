"""Regression tests for the department-string pattern table.

The urology pattern originally read `urolog`, which matches "ne-UROLOG-y".
Three quarters of its hits were Department of Neurology strings. These cases
pin that class of substring trap.

    python3 tests/test_patterns.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rankmgb.mgb_surgery import (  # noqa: E402
    _patterns,
    classify_affiliation,
    classify_near_institution,
    is_composite,
)
from rankmgb.pubmed_evidence import INSTITUTION_PATTERNS  # noqa: E402

CASES: list[tuple[str, str | None]] = [
    # the substring traps
    ("Department of Neurology, Massachusetts General Hospital", None),
    ("Neurobiology Research Unit, Department of Neurology", None),
    ("Department of Urology, Massachusetts General Hospital", "UROLOGY"),
    ("Division of Urologic Oncology, Brigham and Women's Hospital", "UROLOGY"),
    # exclusions win over the surgical catch-all
    ("Department of Pathology, Division of Surgical Pathology, MGH", None),
    ("Department of Anesthesia, Critical Care and Pain Medicine, MGH", None),
    ("Department of Oral and Maxillofacial Surgery, MGH", None),
    # specific specialties
    ("Department of Neurosurgery, Brigham and Women's Hospital", "NEUROSURGERY"),
    ("Department of Neurological Surgery, MGH", "NEUROSURGERY"),
    ("Division of Cardiac Surgery, Brigham and Women's Hospital", "CARDIAC_CARDIOTHORACIC_SURGERY"),
    ("Division of Vascular and Endovascular Surgery, MGH", "VASCULAR_SURGERY"),
    ("Division of Transplant Surgery, MGH", "TRANSPLANT_SURGERY"),
    ("Division of Surgical Oncology, MGH", "SURGICAL_ONCOLOGY"),
    ("Division of Trauma, Emergency Surgery and Surgical Critical Care, MGH", "TRAUMA_ACUTE_CARE_SURGERY"),
    ("Department of Orthopaedic Surgery, MGH", "ORTHOPEDIC_SURGERY"),
    ("Division of Plastic and Reconstructive Surgery, BWH", "PLASTIC_SURGERY"),
    ("Department of Otolaryngology-Head and Neck Surgery, MEEI", "OTOLARYNGOLOGY_HNS"),
    ("Department of Pediatric Surgery, MGH", "PEDIATRIC_SURGERY"),
    # general and residual
    ("Department of Surgery, Massachusetts General Hospital", "GENERAL_AND_UNSPECIFIED_SURGERY"),
    ("Center for Surgery and Public Health, Brigham and Women's Hospital", "OTHER_EXPLICIT_SURGERY"),
    # broad-only categories still classify; the narrow/broad split is applied downstream
    ("Department of Ophthalmology, Massachusetts Eye and Ear", "OPHTHALMIC_SURGERY"),
    ("Department of Obstetrics and Gynecology, BWH", "OBGYN"),
    # nonsurgical departments must not match anything
    ("Department of Medicine, Brigham and Women's Hospital", None),
    ("Department of Radiology, Massachusetts General Hospital", None),
    ("Department of Dermatology, Massachusetts General Hospital", None),
    ("Broad Institute of MIT and Harvard", None),
]


COMPOSITE = (
    "From the Houston Methodist Neurological Institute (J.R.T., D.R.B., P.A.M.), Houston "
    "Methodist Hospital Research Institute, Stanley H. Appel Department of Neurology, and "
    "the Division of Pediatric Surgery, Texas Children's Hospital, Houston; the Sean M. "
    "Healey Center, Massachusetts General Hospital, Boston; and the Department of "
    "Neurology, Brigham and Women's Hospital, Boston."
)

# (affiliation, institution, expected specialty) — proximity and composite handling
PROXIMITY_CASES: list[tuple[str, str, str | None]] = [
    # the real failure: a pediatric-surgery unit at another institution must not
    # be credited to the MGH author on a combined affiliation block
    (COMPOSITE, "MGH", None),
    (COMPOSITE, "BWH", None),
    # department adjacent to the institution is credited
    ("Department of Surgery, Massachusetts General Hospital, Boston, MA.", "MGH",
     "GENERAL_AND_UNSPECIFIED_SURGERY"),
    # department adjacent to a *different* institution is not
    ("Department of Surgery, Duke University, Durham, NC; and the Department of "
     "Medicine, Massachusetts General Hospital, Boston, MA.", "MGH", None),
    ("Division of Neurosurgery, Brigham and Women's Hospital, Boston, MA.", "BWH",
     "NEUROSURGERY"),
    # institution first, department after, still adjacent
    ("Massachusetts General Hospital, Department of Urology, Boston, MA.", "MGH", "UROLOGY"),
]


def main() -> int:
    pats = _patterns()
    failures = []
    for text, expected in CASES:
        got, matched = classify_affiliation(text, pats)
        if got != expected:
            failures.append((text, expected, got, matched))
    for text, inst, expected in PROXIMITY_CASES:
        got, matched = classify_near_institution(text, INSTITUTION_PATTERNS[inst], pats)
        if got != expected:
            failures.append((f"[{inst}] {text[:70]}…", expected, got, matched))
    if not is_composite(COMPOSITE):
        failures.append(("composite block detection", "composite", "not composite", None))

    for text, expected, got, matched in failures:
        print(f"FAIL  {text!r}\n      expected {expected}, got {got} (pattern {matched!r})")
    total = len(CASES) + len(PROXIMITY_CASES) + 1
    print(f"\n{total - len(failures)}/{total} pattern cases passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
