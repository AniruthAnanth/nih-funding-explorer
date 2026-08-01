"""Sections 4-6 -- the PI-affiliation layer.

NIH populates ORG_DEPT only for recipients it classifies as schools; independent
hospitals and research institutes carry no department code at all. Roughly 30%
of FY2025 US NIH dollars sit at such recipients, including MGH (#1) and BWH
(#3). No department ranking built from ORG_DEPT alone can contain them.

This module builds the affiliation-spell table the methodology requires, and
populates the first two tiers of the Section 5 evidence hierarchy that can be
derived from NIH's own data:

  tier A  NIH_ORG_DEPT          the award's own department code (contact PI)
  tier B  NIH_ORG_DEPT_LINKED   a dated department code carried by the same NIH
                                PI profile ID on a different award, used to
                                infer the appointment at this award's index date

Tiers C and above (institutional faculty pages, archived snapshots, ORCID,
publication affiliations) require external evidence collection and are recorded
here as unresolved rather than guessed. Coverage statistics are published with
every table that depends on this layer.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .paths import PROCESSED, TABLES
from .util import get_logger

log = get_logger("affiliation")

UNUSABLE_DEPT = {"__MISSING__", "NONE", "MISCELLANEOUS", "NO CODE ASSIGNED"}

# How far a dated NIH department observation may be carried to an award-year
# with no department of its own, and the confidence that carry earns.
LINK_WINDOWS = [
    (365, "high", "same NIH department code observed within 1 year of the index date"),
    (1095, "medium", "same NIH department code observed within 3 years of the index date"),
    (1826, "low", "same NIH department code observed within 5 years of the index date"),
]


def build_identity_table(pis: pd.DataFrame) -> pd.DataFrame:
    """Section 4 -- investigator identity.

    The NIH PI profile ID is the stable internal identifier. Names are recorded
    as variants, never used as the identity key when an ID exists. Investigators
    known only by name are given a NAME: key and flagged, so every count that
    depends on investigator identity can be recomputed excluding them.
    """
    p = pis.copy()
    has_id = p.nih_pi_profile_id.notna() & (p.nih_pi_profile_id.astype(str).str.strip() != "")
    p["investigator_id"] = np.where(
        has_id, "NIH:" + p.nih_pi_profile_id.astype(str), "NAME:" + p.pi_name_norm
    )
    p["identity_source"] = np.where(has_id, "NIH_PI_PROFILE_ID", "NORMALISED_NAME_ONLY")

    ident = (
        p.groupby(["investigator_id", "identity_source"])
        .agg(
            nih_pi_profile_id=("nih_pi_profile_id", "first"),
            name_variants=("pi_name_raw", lambda s: "|".join(sorted({x for x in s if x}))),
            n_award_years=("application_id", "nunique"),
            first_fy=("fiscal_year", "min"),
            last_fy=("fiscal_year", "max"),
        )
        .reset_index()
    )
    ident["orcid"] = pd.NA
    ident["institutional_profile_id"] = pd.NA
    ident["merge_review_status"] = "not_reviewed"
    log.info(
        "identity table: %s investigators (%s by NIH profile ID, %s name-only)",
        f"{len(ident):,}",
        f"{(ident.identity_source == 'NIH_PI_PROFILE_ID').sum():,}",
        f"{(ident.identity_source == 'NORMALISED_NAME_ONLY').sum():,}",
    )
    return ident, p


def build_spells(df: pd.DataFrame, pis_keyed: pd.DataFrame) -> pd.DataFrame:
    """Tier A spells: every award-year whose recipient NIH does department-code
    yields a dated observation of its contact PI's department."""
    usable = df[~df.nih_org_dept.isin(UNUSABLE_DEPT)]
    contacts = pis_keyed[pis_keyed.is_contact_pi]
    obs = contacts.merge(
        usable[
            [
                "application_id",
                "index_date",
                "index_date_source",
                "nih_org_dept",
                "specialty",
                "surgical_narrow",
                "surgical_broad",
                "canonical_org_id",
                "canonical_name",
                "full_project_num",
            ]
        ],
        on="application_id",
        how="inner",
    )
    spells = pd.DataFrame(
        {
            "investigator_id": obs.investigator_id,
            "institution_id": obs.canonical_org_id,
            "institution": obs.canonical_name,
            "department": obs.nih_org_dept,
            "division": pd.NA,
            "specialty": obs.specialty,
            "surgical_narrow": obs.surgical_narrow,
            "surgical_broad": obs.surgical_broad,
            "start_date": obs.index_date,
            "end_date": obs.index_date,
            "source_type": "NIH_ORG_DEPT",
            "source_citation": "NIH ExPORTER annual project file, ORG_DEPT for application "
            + obs.application_id.astype(str)
            + " ("
            + obs.full_project_num.astype(str)
            + ")",
            "source_accessed": "2026-08-01",
            "extraction_method": "deterministic join to archived ExPORTER file",
            "reviewer": "pipeline",
            "confidence": "high",
            "adjudication_status": "not_required",
            "index_date_source": obs.index_date_source,
            "notes": pd.NA,
        }
    )
    log.info("tier A spells from NIH ORG_DEPT: %s", f"{len(spells):,}")
    return spells


def infer_linked_affiliations(
    df: pd.DataFrame, pis_keyed: pd.DataFrame, spells: pd.DataFrame
) -> pd.DataFrame:
    """Tier B -- carry a dated department observation to an award-year that has none.

    For each contact-PI award-year at a recipient NIH does not department-code,
    find that investigator's nearest dated department observation and carry it,
    with confidence set by how far the observation had to travel in time.
    Nothing is carried beyond five years.
    """
    target = df[df.nih_org_dept.isin(UNUSABLE_DEPT)]
    contacts = pis_keyed[pis_keyed.is_contact_pi]
    tgt = contacts.merge(
        target[["application_id", "index_date", "canonical_org_id", "canonical_name"]],
        on="application_id",
        how="inner",
    )
    if tgt.empty or spells.empty:
        return pd.DataFrame()

    ev = spells[["investigator_id", "start_date", "department", "specialty", "surgical_narrow", "surgical_broad", "institution", "source_citation"]]
    merged = tgt.merge(ev, on="investigator_id", how="left", suffixes=("", "_ev"))

    resolved = merged[merged.start_date.notna()].copy()
    resolved["gap_days"] = (resolved.index_date - resolved.start_date).abs().dt.days
    resolved = resolved.sort_values(["application_id", "gap_days"])
    resolved = resolved[~resolved.application_id.duplicated()]

    max_gap = LINK_WINDOWS[-1][0]
    resolved = resolved[resolved.gap_days <= max_gap].copy()
    conf = pd.Series("low", index=resolved.index)
    rationale = pd.Series(LINK_WINDOWS[-1][2], index=resolved.index)
    for days, label, note in reversed(LINK_WINDOWS):
        sel = resolved.gap_days <= days
        conf[sel] = label
        rationale[sel] = note
    resolved["confidence"] = conf
    resolved["evidence_note"] = rationale

    unresolved = set(tgt.application_id) - set(resolved.application_id)
    log.info(
        "tier B: %s of %s uncoded contact-PI award-years resolved by linked NIH evidence "
        "(%.1f%%); %s remain unknown",
        f"{len(resolved):,}",
        f"{tgt.application_id.nunique():,}",
        100 * len(resolved) / max(tgt.application_id.nunique(), 1),
        f"{len(unresolved):,}",
    )
    return resolved


def coverage_report(df: pd.DataFrame, linked: pd.DataFrame) -> pd.DataFrame:
    """Section 11 -- publish coverage and uncertainty alongside the rankings."""
    d = df.copy()
    d["dept_tier"] = np.where(~d.nih_org_dept.isin(UNUSABLE_DEPT), "A_NIH_ORG_DEPT", "UNRESOLVED")
    if len(linked):
        d.loc[d.application_id.isin(set(linked.application_id)), "dept_tier"] = "B_NIH_LINKED"
    rep = (
        d.groupby(["fiscal_year", "dept_tier"])
        .agg(award_years=("application_id", "size"), funding=("total_cost", "sum"))
        .reset_index()
    )
    tot = rep.groupby("fiscal_year")[["award_years", "funding"]].transform("sum")
    rep["pct_award_years"] = (rep.award_years / tot.award_years * 100).round(2)
    rep["pct_funding"] = (rep.funding / tot.funding * 100).round(2)
    rep.to_csv(TABLES / "coverage_department_evidence.csv", index=False)
    return rep


def run(df: pd.DataFrame, pis: pd.DataFrame) -> dict[str, pd.DataFrame]:
    ident, pis_keyed = build_identity_table(pis)
    spells = build_spells(df, pis_keyed)
    linked = infer_linked_affiliations(df, pis_keyed, spells)
    cov = coverage_report(df, linked)

    ident.to_parquet(PROCESSED / "investigator_identity.parquet", index=False)
    spells.to_parquet(PROCESSED / "affiliation_spells.parquet", index=False)
    if len(linked):
        linked.to_parquet(PROCESSED / "linked_affiliations.parquet", index=False)
    pis_keyed.to_parquet(PROCESSED / "pi_links_keyed.parquet", index=False)
    return {"identity": ident, "spells": spells, "linked": linked, "coverage": cov}
