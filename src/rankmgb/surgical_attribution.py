"""Publication-derived departmental attribution, applied to every institution.

This is the primary method. For each institution in
reference/pubmed_institutions_v1.csv it takes the dated author affiliation
strings harvested from PubMed, decides which of them describe a surgical
appointment, matches those authors to NIH contact PIs, and credits the award to
the surgical department when the evidence lands near the award's index date.

The chain is identical for MGH and for Duke, which is the point: NIH's own
department field only exists for university recipients, so using it as the
primary source would measure hospitals and universities by different rules.

Guards, all of which cost recall on purpose:
  1. institution and surgical unit must be named adjacently in the same
     segment of one affiliation string;
  2. composite multi-institution affiliation blocks are rejected outright;
  3. a surname plus first initial covering more than one forename at that
     institution is ambiguous and is dropped rather than guessed;
  4. evidence must fall within a window of the award's index date;
  5. anything unmatched stays unknown.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .mgb_surgery import (
    BROAD,
    DOS,
    EVIDENCE_WINDOW_YEARS,
    NARROW,
    _name_key,
    _patterns,
    classify_near_institution,
    is_composite,
)
from .paths import PROCESSED, TABLES
from .util import get_logger

log = get_logger("surgical_attribution")


def build_person_evidence(pub: pd.DataFrame) -> pd.DataFrame:
    from .pubmed_evidence import INSTITUTION_PATTERNS

    pats = _patterns()
    res = [
        classify_near_institution(t, INSTITUTION_PATTERNS[i], pats)
        if i in INSTITUTION_PATTERNS else (None, None)
        for t, i in zip(pub.affiliation, pub.institution_id)
    ]
    pub = pub.assign(specialty=[r[0] for r in res], matched_pattern=[r[1] for r in res])
    log.info(
        "%s author-affiliation records | %s composite blocks rejected | %s classified surgical",
        f"{len(pub):,}",
        f"{int(pub.affiliation.map(is_composite).sum()):,}",
        f"{int(pub.specialty.notna().sum()):,}",
    )

    # The classifier now labels non-surgical departments too, so "has a label"
    # no longer means "is surgical". Filtering on NARROW is what keeps a
    # Department of Medicine affiliation out of the surgical evidence set.
    surg = pub[pub.specialty.isin(NARROW)].copy()
    keys = [_name_key(l, f) for l, f in zip(surg.last_name, surg.fore_name)]
    surg["last_key"] = [k[0] for k in keys]
    surg["first_initial"] = [k[1] for k in keys]
    surg = surg[(surg.last_key != "") & (surg.first_initial != "")]

    amb = (
        surg.groupby(["institution_id", "last_key", "first_initial"])
        .fore_name.nunique().rename("n_forenames").reset_index()
    )
    surg = surg.merge(amb, on=["institution_id", "last_key", "first_initial"], how="left")
    log.info("  %s ambiguous name keys dropped", f"{int((amb.n_forenames > 1).sum()):,}")
    return surg


def contact_pis(df: pd.DataFrame, pis: pd.DataFrame, org_ids: list[str]) -> pd.DataFrame:
    tgt = df[df.canonical_org_id.isin(org_ids)]
    pis = pis.drop(columns=[c for c in ("fiscal_year", "org_ipf_code") if c in pis.columns])
    c = pis[pis.is_contact_pi].merge(
        tgt[["application_id", "canonical_org_id", "canonical_name", "display_name",
             "fiscal_year", "index_date", "total_cost", "is_r01", "mechanism_family",
             "core_project_num", "activity_code", "full_project_num", "project_title",
             "nih_org_dept", "specialty"]].rename(columns={"specialty": "nih_specialty"}),
        on="application_id", how="inner",
    )
    parts = c.pi_name_raw.fillna("").str.split(",", n=1)
    c["last_key"] = parts.str[0].str.upper().str.replace(r"[^A-Z\- ]", "", regex=True).str.strip()
    fore = parts.str[1].fillna("").str.upper().str.replace(r"[^A-Z\- ]", "", regex=True).str.strip()
    c["first_initial"] = fore.str[:1]
    c["institution_id"] = c.canonical_org_id
    return c


def attribute(df: pd.DataFrame, pis: pd.DataFrame, pub: pd.DataFrame) -> pd.DataFrame:
    """One row per contact-PI award-year, with the publication verdict attached.

    Award-years with no surgical evidence are kept with specialty NaN, so the
    denominator for coverage and for the agreement analysis is the full set of
    award-years, not just the matched ones.
    """
    surg = build_person_evidence(pub)
    usable = surg[surg.n_forenames == 1]
    org_ids = sorted(pub.institution_id.unique())
    contacts = contact_pis(df, pis, org_ids)
    log.info("contact-PI award-years across %d institutions: %s", len(org_ids), f"{len(contacts):,}")

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
    m = m.sort_values(["application_id", "year_gap", "n_records"], ascending=[True, True, False])
    best = m[~m.application_id.duplicated()].copy()

    strength = (
        m[m.specialty.notna()].groupby("application_id").n_records.sum()
        .rename("total_evidence_records")
    )
    best = best.merge(strength, on="application_id", how="left")
    best["confidence"] = np.select(
        [best.specialty.isna(),
         best.total_evidence_records >= 5,
         best.total_evidence_records >= 2],
        ["none", "high", "medium"], default="low",
    )
    best["pub_is_surgical_narrow"] = best.specialty.isin(NARROW)
    best["pub_is_surgical_broad"] = best.specialty.isin(BROAD)
    best["pub_is_department_of_surgery"] = best.specialty.isin(DOS)
    best["evidence_tier"] = np.where(best.specialty.notna(), "PUBMED_AUTHOR_AFFILIATION", "NONE")
    best["source_citation"] = np.where(
        best.specialty.notna(),
        "PubMed PMID " + best.example_pmid.astype(str) + " — " + best.example_affiliation.astype(str),
        "",
    )
    best["source_accessed"] = "2026-08-01"

    matched = int(best.specialty.notna().sum())
    log.info(
        "publication evidence resolves %s of %s contact-PI award-years to a surgical "
        "department (%.1f%%)",
        f"{matched:,}", f"{len(best):,}", 100 * matched / max(len(best), 1),
    )
    best.to_parquet(PROCESSED / "surgical_attribution_all.parquet", index=False)
    return best


def summarise(best: pd.DataFrame, cfg: dict, min_records: int = 2) -> pd.DataFrame:
    """Department-of-surgery totals per institution, publication-derived."""
    rows = []
    for period, years in cfg["reporting_periods"].items():
        for floor, pool in [
            ("corroborated", best[best.confidence.isin(["high", "medium"])]),
            ("all_evidence", best[best.specialty.notna()]),
        ]:
            sub = pool[pool.fiscal_year.isin(years) & pool.pub_is_department_of_surgery]
            g = sub.groupby(["canonical_org_id", "display_name"], dropna=False)
            agg = g.agg(
                total_funding=("total_cost", "sum"),
                award_years=("application_id", "size"),
                distinct_projects=("core_project_num", "nunique"),
                r01_award_years=("is_r01", "sum"),
                funded_investigators=("nih_pi_profile_id", "nunique"),
            ).reset_index()
            r01 = sub[sub.is_r01].groupby("canonical_org_id").total_cost.sum().rename("r01_funding")
            agg = agg.merge(r01, on="canonical_org_id", how="left").fillna({"r01_funding": 0})
            agg["m_award_years"] = 0
            agg["period"] = period
            agg["confidence_floor"] = floor
            rows.append(agg)
    out = pd.concat(rows, ignore_index=True)
    _ = min_records
    out.to_csv(TABLES / "surgery_publication_derived.csv", index=False)
    return out
