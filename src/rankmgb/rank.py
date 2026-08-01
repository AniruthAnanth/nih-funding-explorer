"""Section 10 -- rankings.

Three grains, each produced for every reporting period:
  institution                      canonical recipient organization
  department (specialty)           NIH department, pooled across institutions
  institution x department         the institution-specialty pair

The Mass General Brigham roll-ups are materialised as additional synthetic
institutions built from reference/rollups_v1.csv, so MGH, BWH and the combined
entity all appear in the same ranked table without any name matching.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .paths import REFERENCE, TABLES
from .util import get_logger

log = get_logger("rank")

MECH_FAMILIES = ["R01", "R_OTHER", "U", "P", "K", "T", "F", "M", "OTHER", "UNMAPPED"]

# A per-investigator figure computed over one or two investigators is noise.
# Units below this floor keep their ratio but are excluded from intensity ranks.
INTENSITY_MIN_INVESTIGATORS = 5


def _prepare_metric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Materialise the conditional columns the aggregation sums over.

    Doing this once up front keeps the group-by a single vectorised pass
    instead of re-filtering the frame per group.
    """
    d = df.copy()
    d["_r01_cost"] = d.total_cost.where(d.is_r01, 0.0)
    d["_r01_flag"] = d.is_r01.astype(int)
    d["_r01_proj"] = d.core_project_num.where(d.is_r01)
    is_m = d.mechanism_family == "M"
    d["_m_cost"] = d.total_cost.where(is_m, 0.0)
    d["_m_flag"] = is_m.astype(int)
    d["_m_proj"] = d.core_project_num.where(is_m)
    d["_missing_cost"] = (~d.has_total_cost).astype(int)
    for fam in MECH_FAMILIES:
        sel = d.mechanism_family == fam
        d[f"_cost_{fam}"] = d.total_cost.where(sel, 0.0)
        d[f"_n_{fam}"] = sel.astype(int)
    return d


_AGG_SPEC: dict[str, tuple[str, str]] = {
    "total_funding": ("total_cost", "sum"),
    "award_years": ("application_id", "size"),
    "distinct_projects": ("core_project_num", "nunique"),
    "direct_cost": ("direct_cost", "sum"),
    "indirect_cost": ("indirect_cost", "sum"),
    "award_years_missing_cost": ("_missing_cost", "sum"),
    "r01_funding": ("_r01_cost", "sum"),
    "r01_award_years": ("_r01_flag", "sum"),
    "r01_distinct_projects": ("_r01_proj", "nunique"),
    "m_funding": ("_m_cost", "sum"),
    "m_award_years": ("_m_flag", "sum"),
    "m_distinct_projects": ("_m_proj", "nunique"),
}


def _pi_key(pis: pd.DataFrame) -> pd.DataFrame:
    p = pis.copy()
    # NIH profile ID is the stable identifier; fall back to the normalised name
    # only when NIH supplies no ID, and mark those so coverage is reportable.
    p["pi_key"] = p.nih_pi_profile_id.where(
        p.nih_pi_profile_id.notna() & (p.nih_pi_profile_id != ""), "NAME:" + p.pi_name_norm
    )
    p["pi_key_source"] = p.nih_pi_profile_id.notna().map({True: "NIH_PROFILE_ID", False: "NAME_FALLBACK"})
    return p


def _aggregate(df: pd.DataFrame, pis: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    spec = dict(_AGG_SPEC)
    for fam in MECH_FAMILIES:
        spec[f"funding_{fam}"] = (f"_cost_{fam}", "sum")
        spec[f"awards_{fam}"] = (f"_n_{fam}", "sum")

    agg = df.groupby(keys, dropna=False, observed=True).agg(**spec).reset_index()
    agg["non_r01_funding"] = agg.total_funding - agg.r01_funding
    agg["non_r01_award_years"] = agg.award_years - agg.r01_award_years

    # Investigator counts need the PI-level frame, so they are a second pass.
    joined = pis.merge(df[["application_id", *keys]], on="application_id", how="inner")
    inv = (
        joined.groupby(keys, dropna=False, observed=True)
        .agg(funded_investigators=("pi_key", "nunique"))
        .reset_index()
    )
    contacts = (
        joined[joined.is_contact_pi]
        .groupby(keys, dropna=False, observed=True)
        .agg(funded_contact_pis=("pi_key", "nunique"))
        .reset_index()
    )
    agg = agg.merge(inv, on=keys, how="left").merge(contacts, on=keys, how="left")
    agg[["funded_investigators", "funded_contact_pis"]] = (
        agg[["funded_investigators", "funded_contact_pis"]].fillna(0).astype(int)
    )
    return _add_intensity(agg)


def _add_intensity(agg: pd.DataFrame) -> pd.DataFrame:
    """Size-normalised metrics.

    Total dollars mostly measure how big a department is. These divide it out
    so a small, densely funded department is visible next to a large one.

    NIH publishes no faculty headcount, so the denominator is the count of
    distinct NIH-funded investigators — the size of the *funded* faculty, not
    the size of the department. A department with many unfunded faculty looks
    identical here to one with none, which is worth remembering before reading
    these as productivity.
    """
    def per(num: str, den: str) -> pd.Series:
        d = agg[den].replace(0, np.nan)
        return (agg[num] / d).round(0)

    agg["funding_per_investigator"] = per("total_funding", "funded_investigators")
    agg["funding_per_project"] = per("total_funding", "distinct_projects")
    agg["mean_award_size"] = per("total_funding", "award_years")
    agg["r01_funding_per_investigator"] = per("r01_funding", "funded_investigators")
    agg["projects_per_investigator"] = (
        agg.distinct_projects / agg.funded_investigators.replace(0, np.nan)
    ).round(2)
    agg["r01_share_of_funding"] = (
        agg.r01_funding / agg.total_funding.replace(0, np.nan) * 100
    ).round(1)
    return agg


# Department values that record no department. Ranking against them would
# place a real department below a bucket of unclassified awards.
NON_DEPARTMENT = {"__MISSING__", "NONE", "MISCELLANEOUS", "NO CODE ASSIGNED"}


def _add_ranks(agg: pd.DataFrame, grain: str) -> pd.DataFrame:
    """Rank against a defensible peer set, not against every row in the table.

    Two kinds of row would otherwise inflate every rank number:

      * roll-ups (MGB_CORE, MGB_SYSTEM, HARVARD_ENTITIES) are synthetic
        aggregates of rows that are already present, so counting them makes a
        real institution look one or more places worse than it is;
      * at the institution-department grain, rows whose department is
        `__MISSING__` or one of NIH's placeholder values are not departments.

    Those rows keep every metric and are still published, but they are excluded
    from the denominator and receive no rank. `rank_peer_set` records which
    population a rank was computed against, so a rank is never ambiguous.
    """
    eligible = ~agg.is_rollup if "is_rollup" in agg.columns else pd.Series(True, index=agg.index)
    if grain != "institution" and "nih_org_dept" in agg.columns:
        eligible &= ~agg.nih_org_dept.isin(NON_DEPARTMENT)

    ranked_metrics = {
        "rank_total_funding": "total_funding",
        "rank_award_years": "award_years",
        "rank_distinct_projects": "distinct_projects",
        "rank_r01_funding": "r01_funding",
        "rank_r01_award_years": "r01_award_years",
        "rank_m_award_years": "m_award_years",
    }
    for rank_col, metric in ranked_metrics.items():
        agg[rank_col] = agg[metric].where(eligible).rank(ascending=False, method="min")

    # Intensity ranks additionally require enough investigators for the ratio to
    # mean anything; below the floor they are blank rather than letting a
    # one-investigator unit top the table.
    big = eligible & (agg.funded_investigators >= INTENSITY_MIN_INVESTIGATORS)
    agg["meets_intensity_floor"] = big
    agg["rank_funding_per_investigator"] = (
        agg.funding_per_investigator.where(big).rank(ascending=False, method="min")
    )

    # A roll-up is excluded from the peer-set rank above because it aggregates
    # rows that are already in the table, and counting it would push every real
    # institution down a place. But "where would Mass General Brigham sit if it
    # were a single institution" is a real question with a defensible answer, so
    # each roll-up also gets the rank it would take when inserted on its own
    # into the non-roll-up ranking. Only the roll-up itself is inserted, never
    # two roll-ups at once, since they overlap.
    # Only roll-ups get this. `~eligible` also covers rows whose department is a
    # placeholder, and "where would this sit as a single institution" says
    # nothing about, say, Michigan's MISCELLANEOUS row: it aggregates nothing.
    rollup_mask = agg.is_rollup.astype(bool) if "is_rollup" in agg.columns \
        else pd.Series(False, index=agg.index)
    for rank_col, metric in ranked_metrics.items():
        peer = agg.loc[eligible, metric]
        as_single = pd.Series(np.nan, index=agg.index)
        for i in agg.index[rollup_mask]:
            v = agg.at[i, metric]
            if pd.notna(v):
                as_single.at[i] = int((peer > v).sum()) + 1
        agg[rank_col + "_if_single_entity"] = as_single

    agg["is_ranked"] = eligible
    agg["n_ranked"] = int(eligible.sum())
    agg["rank_peer_set"] = (
        "non-rollup institutions"
        if grain == "institution"
        else "non-rollup institution-department pairs with a recorded department"
    )
    if grain == "department":
        agg["rank_peer_set"] = "NIH department values with a recorded department"
    return agg


def _apply_rollups(df: pd.DataFrame, rollups: pd.DataFrame) -> pd.DataFrame:
    """Duplicate member award-years under a synthetic roll-up institution.

    The roll-up rows are additional rows in the ranked tables, not replacements.
    MGH and BWH continue to appear separately, as Section 9 requires.
    """
    from .names import load_overrides

    overrides = load_overrides()
    extra = []
    for _, r in rollups.iterrows():
        members = str(r.member_canonical_org_ids).split("|")
        sub = df[df.canonical_org_id.isin(members)].copy()
        if sub.empty:
            log.warning("roll-up %s matched no award-years", r.rollup_id)
            continue
        sub["canonical_org_id"] = r.rollup_id
        sub["canonical_name"] = r.rollup_name
        # The roll-up's display label comes from the same override table every
        # other institution uses, so "Mass General Brigham" reads the same way
        # wherever it appears.
        sub["display_name"] = overrides.get(r.rollup_id, r.rollup_name)
        sub["is_rollup"] = True
        extra.append(sub)
    df = df.copy()
    df["is_rollup"] = False
    return pd.concat([df, *extra], ignore_index=True) if extra else df


def build_rankings(df: pd.DataFrame, pis: pd.DataFrame, cfg: dict, ref: dict) -> dict[str, pd.DataFrame]:
    pis = _pi_key(pis)
    df = _prepare_metric_columns(_apply_rollups(df, ref["rollups"]))
    results: dict[str, pd.DataFrame] = {}

    for period, years in cfg["reporting_periods"].items():
        sub = df[df.fiscal_year.isin(years)].copy()
        sub_pis = pis[pis.application_id.isin(set(sub.application_id))]
        log.info("%s: %s award-year rows (incl. roll-up duplicates)", period, f"{len(sub):,}")

        for grain, keys in {
            "institution": ["canonical_org_id", "canonical_name", "display_name", "org_country", "is_rollup"],
            "department": ["specialty", "nih_org_dept"],
            "institution_department": [
                "canonical_org_id",
                "canonical_name",
                "display_name",
                "org_country",
                "specialty",
                "nih_org_dept",
                "is_rollup",
            ],
        }.items():
            agg = _aggregate(sub, sub_pis, keys)
            agg.insert(0, "period", period)
            agg = agg.sort_values("total_funding", ascending=False).reset_index(drop=True)
            agg = _add_ranks(agg, grain)
            results[f"{grain}__{period}"] = agg
            path = TABLES / f"rank_{grain}_{period}.csv"
            agg.to_csv(path, index=False)
            log.info("  wrote %s (%s rows)", path.name, f"{len(agg):,}")

    return results


def build_trend(df: pd.DataFrame, pis: pd.DataFrame, cfg: dict, ref: dict) -> dict[str, pd.DataFrame]:
    """Per-fiscal-year tables, so rank can be plotted as a trajectory.

    The three reporting periods are cumulative windows and cannot show movement:
    FY2021-FY2025 contains FY2025. A trend needs one observation per year, which
    is what this produces, with the rank recomputed within each year against the
    same peer set the period tables use.
    """
    pis = _pi_key(pis)
    df = _prepare_metric_columns(_apply_rollups(df, ref["rollups"]))
    out: dict[str, pd.DataFrame] = {}

    grains = {
        "institution": ["canonical_org_id", "canonical_name", "display_name", "org_country", "is_rollup"],
        "institution_department": [
            "canonical_org_id", "canonical_name", "display_name", "org_country",
            "specialty", "nih_org_dept", "is_rollup",
        ],
    }
    for grain, keys in grains.items():
        frames = []
        for fy in sorted(cfg["fiscal_years"]):
            sub = df[df.fiscal_year == fy]
            sub_pis = pis[pis.application_id.isin(set(sub.application_id))]
            agg = _aggregate(sub, sub_pis, keys)
            agg = agg.sort_values("total_funding", ascending=False).reset_index(drop=True)
            agg = _add_ranks(agg, grain)
            agg.insert(0, "fiscal_year", fy)
            frames.append(agg)
        tidy = pd.concat(frames, ignore_index=True)
        path = TABLES / f"trend_{grain}.csv"
        tidy.to_csv(path, index=False)
        out[grain] = tidy
        log.info(
            "  wrote %s (%s rows, %d years)", path.name, f"{len(tidy):,}", tidy.fiscal_year.nunique()
        )
    return out
