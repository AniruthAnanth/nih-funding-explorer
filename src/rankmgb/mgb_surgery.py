"""Sections 4-8 applied to MGH and BWH.

NIH assigns no department to either hospital, so their surgery departments
cannot appear in an ORG_DEPT ranking at all. This module builds the affiliation
evidence that puts them back on the board, using tier 6 of the Section 5
hierarchy: dated author affiliation strings recorded on PubMed records.

The chain is deliberately conservative:

  1. an affiliation string must name the institution AND a surgical unit in the
     same string, so the department and the institution are jointly attested;
  2. the string is classified by reference/department_string_patterns_v1.csv,
     an ordered, versioned pattern table with explicit exclusions;
  3. the author is matched to an NIH contact PI on surname plus first initial
     at the same institution, and any surname/initial shared by more than one
     distinct forename is flagged ambiguous rather than matched;
  4. evidence must fall within a window of the award's index date;
  5. everything unmatched stays UNKNOWN. Nothing is imputed.

The result is a defensible lower bound, published with its coverage rate.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from .paths import PROCESSED, REFERENCE, TABLES
from .util import get_logger

log = get_logger("mgb_surgery")

EVIDENCE_WINDOW_YEARS = 3
NARROW = {
    "GENERAL_AND_UNSPECIFIED_SURGERY",
    "CARDIAC_CARDIOTHORACIC_SURGERY",
    "VASCULAR_SURGERY",
    "TRANSPLANT_SURGERY",
    "SURGICAL_ONCOLOGY",
    "TRAUMA_ACUTE_CARE_SURGERY",
    "COLORECTAL_SURGERY",
    "PEDIATRIC_SURGERY",
    "NEUROSURGERY",
    "ORTHOPEDIC_SURGERY",
    "PLASTIC_SURGERY",
    "OTOLARYNGOLOGY_HNS",
    "UROLOGY",
    "OTHER_EXPLICIT_SURGERY",
}
BROAD = NARROW | {"OPHTHALMIC_SURGERY", "OBGYN"}

# The Department of Surgery proper, as an institution would name it. The other
# surgical specialties are free-standing departments at MGH and BWH and are
# reported separately, matching how NIH treats them for universities.
DOS = {
    "GENERAL_AND_UNSPECIFIED_SURGERY",
    "CARDIAC_CARDIOTHORACIC_SURGERY",
    "VASCULAR_SURGERY",
    "TRANSPLANT_SURGERY",
    "SURGICAL_ONCOLOGY",
    "TRAUMA_ACUTE_CARE_SURGERY",
    "COLORECTAL_SURGERY",
    "PEDIATRIC_SURGERY",
    "OTHER_EXPLICIT_SURGERY",
}


def _patterns() -> list[tuple[int, re.Pattern, str, str]]:
    tab = pd.read_csv(REFERENCE / "department_string_patterns_v1.csv").sort_values("priority")
    return [(int(r.priority), re.compile(r.pattern, re.I), r.specialty, r.kind) for _, r in tab.iterrows()]


# Some journals print one combined affiliation block naming every institution
# on the paper, tagged with author initials, and PubMed attaches that whole
# block to every author. A department named anywhere in such a block says
# nothing about a particular author, so these are rejected outright.
COMPOSITE_MARKERS = (
    re.compile(r"^\s*from the\b", re.I),
    re.compile(r"\([A-Z]\.[A-Z]?\.?(,\s*[A-Z]\.[A-Z]?\.?)*\)"),
)
COMPOSITE_MAX_CHARS = 400

# A department only describes an author if it sits next to that author's
# institution in the string. Real affiliations read
# "Department of X, Division of Y, Institution, City, State".
WINDOW_BEFORE = 130
WINDOW_AFTER = 60


def is_composite(text: str) -> bool:
    if len(text) > COMPOSITE_MAX_CHARS:
        return True
    return any(rx.search(text) for rx in COMPOSITE_MARKERS)


def classify_affiliation(text: str, pats: list) -> tuple[str | None, str | None]:
    """Return (specialty, matched_pattern) or (None, None). Exclusions win.

    Applied to a single affiliation segment, not to a composite block.
    """
    for _, rx, spec, kind in pats:
        if rx.search(text):
            return (None, rx.pattern) if kind == "exclude" else (spec, rx.pattern)
    return None, None


def classify_near_institution(
    text: str, inst_rx: re.Pattern, pats: list
) -> tuple[str | None, str | None]:
    """Classify only the text adjacent to a mention of the institution.

    This is what stops a department named at the far end of a multi-institution
    string from being credited to this institution's author.
    """
    if not text or is_composite(text):
        return None, None
    best: tuple[int, str, str] | None = None
    # A semicolon separates one institution's affiliation from the next, so a
    # window must never cross one.
    for segment in re.split(r"\s*;\s*", text):
        for m in inst_rx.finditer(segment):
            window = segment[max(0, m.start() - WINDOW_BEFORE) : m.end() + WINDOW_AFTER]
            for pri, rx, spec, kind in pats:
                if rx.search(window):
                    if kind == "exclude":
                        return None, rx.pattern
                    if best is None or pri < best[0]:
                        best = (pri, spec, rx.pattern)
                    break
    return (best[1], best[2]) if best else (None, None)


def _name_key(last: str, fore: str) -> tuple[str, str]:
    last = re.sub(r"[^A-Z\- ]", "", (last or "").upper()).strip()
    fore = re.sub(r"[^A-Z\- ]", "", (fore or "").upper()).strip()
    return last, (fore[:1] if fore else "")


def build_person_evidence(pub: pd.DataFrame) -> pd.DataFrame:
    from .pubmed_evidence import INSTITUTION_PATTERNS

    pats = _patterns()
    res = [
        classify_near_institution(t, INSTITUTION_PATTERNS[i], pats)
        for t, i in zip(pub.affiliation, pub.institution_id)
    ]
    pub = pub.assign(
        specialty=[r[0] for r in res],
        matched_pattern=[r[1] for r in res],
    )
    n_comp = int(pub.affiliation.map(is_composite).sum())
    log.info(
        "rejected %s composite affiliation blocks (multi-institution strings PubMed "
        "attaches to every author)",
        f"{n_comp:,}",
    )
    surg = pub[pub.specialty.notna()].copy()
    keys = [_name_key(l, f) for l, f in zip(surg.last_name, surg.fore_name)]
    surg["last_key"] = [k[0] for k in keys]
    surg["first_initial"] = [k[1] for k in keys]
    surg = surg[(surg.last_key != "") & (surg.first_initial != "")]

    # Flag surname+initial keys that cover more than one distinct forename at
    # the same institution; those are not safe to match on.
    amb = (
        surg.groupby(["institution_id", "last_key", "first_initial"])
        .fore_name.nunique()
        .rename("n_forenames")
        .reset_index()
    )
    surg = surg.merge(amb, on=["institution_id", "last_key", "first_initial"], how="left")
    log.info(
        "surgical author-affiliation records: %s (%s ambiguous name keys)",
        f"{len(surg):,}",
        f"{(amb.n_forenames > 1).sum():,}",
    )
    return surg


def _nih_contact_pis(df: pd.DataFrame, pis: pd.DataFrame, org_ids: list[str]) -> pd.DataFrame:
    tgt = df[df.canonical_org_id.isin(org_ids)]
    # pi_links carries fiscal_year and org_ipf_code too; drop them so the join
    # does not produce _x/_y suffixes on the award-level fields.
    pis = pis.drop(columns=[c for c in ("fiscal_year", "org_ipf_code") if c in pis.columns])
    c = pis[pis.is_contact_pi].merge(
        tgt[["application_id", "canonical_org_id", "canonical_name", "fiscal_year",
             "index_date", "total_cost", "is_r01", "mechanism_family", "core_project_num",
             "activity_code", "full_project_num", "project_title"]],
        on="application_id",
        how="inner",
    )
    parts = c.pi_name_raw.fillna("").str.split(",", n=1)
    c["last_key"] = parts.str[0].str.upper().str.replace(r"[^A-Z\- ]", "", regex=True).str.strip()
    fore = parts.str[1].fillna("").str.upper().str.replace(r"[^A-Z\- ]", "", regex=True).str.strip()
    c["first_initial"] = fore.str[:1]
    c["institution_id"] = c.canonical_org_id
    return c


def attribute(df: pd.DataFrame, pis: pd.DataFrame, pub: pd.DataFrame) -> dict[str, pd.DataFrame]:
    surg = build_person_evidence(pub)
    usable = surg[surg.n_forenames == 1]
    contacts = _nih_contact_pis(df, pis, ["MGH", "BWH"])
    log.info("MGH+BWH contact-PI award-years: %s", f"{len(contacts):,}")

    ev = (
        usable.groupby(["institution_id", "last_key", "first_initial", "pub_year", "specialty"])
        .agg(n_records=("pmid", "nunique"), example_pmid=("pmid", "first"),
             example_affiliation=("affiliation", "first"))
        .reset_index()
    )

    m = contacts.merge(ev, on=["institution_id", "last_key", "first_initial"], how="left")
    m["award_year"] = m.index_date.dt.year
    m["year_gap"] = (m.pub_year - m.award_year).abs()
    m = m[m.year_gap.isna() | (m.year_gap <= EVIDENCE_WINDOW_YEARS)]

    matched = m[m.specialty.notna()].copy()
    # One specialty per award-year: prefer the closest year, then the most
    # heavily attested specialty.
    matched = matched.sort_values(["application_id", "year_gap", "n_records"],
                                  ascending=[True, True, False])
    best = matched[~matched.application_id.duplicated()].copy()

    strength = matched.groupby("application_id").n_records.sum().rename("total_evidence_records")
    best = best.merge(strength, on="application_id", how="left")
    best["confidence"] = np.select(
        [best.total_evidence_records >= 5, best.total_evidence_records >= 2],
        ["high", "medium"],
        default="low",
    )
    best["evidence_tier"] = "F_PUBMED_AUTHOR_AFFILIATION"
    best["source_citation"] = "PubMed PMID " + best.example_pmid.astype(str) + " — " + best.example_affiliation
    best["source_accessed"] = "2026-08-01"
    best["surgical_narrow"] = best.specialty.isin(NARROW)
    best["surgical_broad"] = best.specialty.isin(BROAD)
    best["is_department_of_surgery"] = best.specialty.isin(DOS)

    unmatched = contacts[~contacts.application_id.isin(set(best.application_id))]
    log.info(
        "resolved %s of %s MGH+BWH contact-PI award-years to a surgical department (%.1f%%); "
        "%s carry no surgical evidence and stay UNKNOWN",
        f"{len(best):,}", f"{len(contacts):,}",
        100 * len(best) / max(len(contacts), 1), f"{len(unmatched):,}",
    )

    best.to_parquet(PROCESSED / "mgb_surgical_attribution.parquet", index=False)
    cols = ["application_id", "canonical_org_id", "canonical_name", "fiscal_year", "activity_code",
            "full_project_num", "project_title", "pi_name_raw", "nih_pi_profile_id", "specialty",
            "is_department_of_surgery", "confidence", "total_evidence_records", "year_gap",
            "evidence_tier", "source_citation", "source_accessed", "total_cost", "is_r01"]
    best[cols].to_csv(TABLES / "mgb_surgical_award_years_evidence.csv", index=False)
    return {"attribution": best, "contacts": contacts, "surgical_authors": usable}


def summarise(best: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Reported at two confidence floors.

    `corroborated` keeps only matches backed by two or more dated records and is
    the figure that should be quoted. `all_evidence` additionally keeps
    single-record matches, which spot-checking shows include false positives
    (an investigator named on one paper with a surgical unit but appointed
    elsewhere). The difference between the two is the uncertainty band.
    """
    rows = []
    for period, years in cfg["reporting_periods"].items():
        for floor, pool in [
            ("corroborated", best[best.confidence.isin(["high", "medium"])]),
            ("all_evidence", best),
        ]:
            rows.extend(_rows_for(pool[pool.fiscal_year.isin(years)], period, floor))
    out = pd.DataFrame(rows)
    out.to_csv(TABLES / "mgb_surgery_summary.csv", index=False)
    return out


def _rows_for(sub: pd.DataFrame, period: str, floor: str) -> list[dict]:
    rows = []
    if True:
        for scope, sel in [
            ("Department of Surgery", sub[sub.is_department_of_surgery]),
            ("All surgical departments (narrow)", sub[sub.surgical_narrow]),
            ("All surgical departments (broad)", sub[sub.surgical_broad]),
        ]:
            for org, s in [("MGH", sel[sel.canonical_org_id == "MGH"]),
                           ("BWH", sel[sel.canonical_org_id == "BWH"]),
                           ("MGB_CORE", sel)]:
                rows.append({
                    "period": period, "confidence_floor": floor, "scope": scope, "entity": org,
                    "total_funding": s.total_cost.sum(),
                    "award_years": len(s),
                    "distinct_projects": s.core_project_num.nunique(),
                    "r01_funding": s[s.is_r01].total_cost.sum(),
                    "r01_award_years": int(s.is_r01.sum()),
                    "m_award_years": int((s.mechanism_family == "M").sum()),
                    "funded_investigators": s.nih_pi_profile_id.nunique(),
                    "high_confidence_share": round(float((s.confidence == "high").mean() * 100), 1) if len(s) else 0.0,
                })
    return rows
