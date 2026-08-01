"""Builds the static site payload under docs/.

The site is data-driven: this module emits compact JSON from the same ranked
CSVs that back the figures, so the published site can never drift from the
tables. Rows are emitted as arrays with a shared column header rather than as
objects, which roughly halves the payload.
"""
from __future__ import annotations

import json
import shutil

import numpy as np
import pandas as pd

from .paths import FIGURES, REFERENCE, ROOT, TABLES
from .pi_department import FLOORS
from .util import get_logger, read_json, utcnow

log = get_logger("site")

# Produced by rules this project replaced. They stay in outputs/ so the change
# in method can be reproduced and compared, and they are not published: the
# superseded institution-level ranking puts Duke's department of surgery at
# $427M where the shipped one puts it at $221M, and the superseded per-award
# evidence file covers about a third of the award-years now published. Serving
# both from the same folder, unlabelled, is not transparency.
SUPERSEDED_PREFIXES = ("surgery_ranking_FY", "mgb_surgical_award_years_evidence")

SITE = ROOT / "docs"
SITE_DATA = SITE / "data"
SITE_FIGS = SITE / "figures"

PERIODS = ["FY2025", "FY2024_FY2025", "FY2021_FY2025"]

PAIR_COLS = [
    "rank_total_funding", "rank_total_funding_if_single_entity", "n_ranked",
    "canonical_org_id", "canonical_name", "display_name", "org_country", "nih_org_dept", "specialty",
    "total_funding", "award_years", "distinct_projects", "r01_funding", "r01_award_years",
    "m_award_years", "funded_investigators", "funding_R01", "funding_R_OTHER", "funding_U",
    "funding_P", "funding_K", "funding_T", "funding_F", "funding_OTHER", "is_rollup",
    "funding_per_investigator", "funding_per_project", "mean_award_size",
    "r01_funding_per_investigator", "projects_per_investigator", "r01_share_of_funding",
    "meets_intensity_floor", "is_reconstructed",
]
INST_COLS = [
    "rank_total_funding", "rank_total_funding_if_single_entity", "n_ranked",
    "canonical_org_id", "canonical_name", "display_name", "org_country", "total_funding", "award_years",
    "distinct_projects", "r01_funding", "r01_award_years", "m_award_years",
    "funded_investigators", "funding_R01", "funding_R_OTHER", "funding_U", "funding_P",
    "funding_K", "funding_T", "funding_F", "funding_OTHER", "is_rollup",
    "funding_per_investigator", "funding_per_project", "mean_award_size",
    "r01_funding_per_investigator", "projects_per_investigator", "r01_share_of_funding",
    "meets_intensity_floor",
]
DEPT_COLS = [
    "nih_org_dept", "specialty", "total_funding", "award_years", "distinct_projects",
    "r01_funding", "r01_award_years", "m_award_years", "funded_investigators",
    "funding_per_investigator", "funding_per_project", "mean_award_size",
    "r01_funding_per_investigator", "projects_per_investigator", "r01_share_of_funding",
    "meets_intensity_floor",
]


def _pack(df: pd.DataFrame, cols: list[str]) -> dict:
    cols = [c for c in cols if c in df.columns]
    sub = df[cols].copy()
    # Dollars and counts are whole numbers and rounding them keeps the payload
    # small. Everything else is a rate, a ratio or a statistic, where rounding to
    # zero decimals destroys the value: Cohen's kappa of 0.267 became 0.0 that
    # way, and mean RCR of 2.24 became 2.0. Round by what the number *is*, not by
    # a list of exceptions that a new column silently falls off.
    WHOLE_PREFIXES = ("funding", "total_funding", "r01_funding", "direct", "indirect")
    WHOLE_EXACT = {
        "total_citations", "papers", "award_years", "distinct_projects",
        "funded_investigators", "mean_award_size", "funding_per_project",
        "funding_per_investigator", "r01_funding_per_investigator",
        "r01_award_years", "m_award_years", "comparable_award_years",
        "nih_surgical", "pub_surgical", "both", "nih_only", "publication_only",
    }
    for c in sub.columns:
        if sub[c].dtype.kind == "f":
            whole = c in WHOLE_EXACT or c.startswith(WHOLE_PREFIXES)
            vals = sub[c].round(0) if whole else sub[c].round(3)
            sub[c] = vals.astype(object).where(sub[c].notna(), None)
        elif sub[c].dtype == bool:
            sub[c] = sub[c].astype(int)
    sub = sub.astype(object).where(sub.notna(), None)
    return {"cols": cols, "rows": sub.to_numpy().tolist()}


def _pairs_with_reconstructed(period: str) -> pd.DataFrame:
    """Institution-department pairs, including the departments NIH never coded.

    NIH assigns no department to independent hospitals, so an institution-by-
    department table built from ORG_DEPT alone simply has no Mass General
    Brigham row — the entity vanishes from the view rather than ranking low.
    The same is true of every other uncoded recipient, so all of them are
    appended, flagged is_reconstructed so the interface can mark them as lower
    bounds and never present them as like-for-like with an NIH-coded department.
    """
    from .mgb_context import _labels

    pairs = pd.read_csv(TABLES / f"rank_institution_department_{period}.csv")
    pairs["is_reconstructed"] = 0

    recon_path = TABLES / "mgb_departments_all.csv"
    if not recon_path.exists():
        return pairs
    r = pd.read_csv(recon_path)
    # All three of MGH, BWH and the MGB_CORE roll-up are shipped. Dropping the
    # members here is what left the site with no way to show that the headline
    # figure is two hospitals' departments added together: the roll-up appeared
    # as an ordinary row and its own components existed nowhere in the payload.
    # Double counting is prevented by the is_rollup flag the explorer already
    # honours, not by deleting the evidence.
    r = r[r.period == period].copy()
    if r.empty:
        return pairs

    r = r.rename(columns={"entity": "canonical_org_id"})
    label = {**_labels(), "MGB_CORE": "Mass General Brigham"}
    named = r.canonical_org_id.map(label).fillna(r.canonical_org_id)
    r["canonical_name"] = named
    r["display_name"] = named
    r["org_country"] = "UNITED STATES"
    r["is_rollup"] = r.canonical_org_id == "MGB_CORE"
    r["is_reconstructed"] = 1
    # Carry the specialty label its NIH-coded peers use, so the explorer's
    # specialty filter groups them together.
    dept_map = pd.read_csv(REFERENCE / "nih_department_crosswalk_v1.csv")
    r = r.merge(dept_map[["nih_org_dept", "specialty"]], on="nih_org_dept", how="left")
    r["specialty"] = r.specialty.fillna("UNKNOWN")
    for col in pairs.columns:
        if col not in r.columns:
            r[col] = np.nan
    r["funding_per_investigator"] = (
        r.total_funding / r.funded_investigators.replace(0, np.nan)
    ).round(0)
    r["mean_award_size"] = (r.total_funding / r.award_years.replace(0, np.nan)).round(0)
    r["funding_per_project"] = (r.total_funding / r.distinct_projects.replace(0, np.nan)).round(0)
    r["r01_share_of_funding"] = (
        r.r01_funding / r.total_funding.replace(0, np.nan) * 100
    ).round(1)
    r["meets_intensity_floor"] = r.funded_investigators >= 5

    out = pd.concat([pairs, r[pairs.columns]], ignore_index=True)
    log.info("%s: appended %d reconstructed department row(s) across %d departments "
             "and %d recipients", period, len(r), r.nih_org_dept.nunique(),
             r.canonical_org_id.nunique())
    return out


def build() -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    SITE_FIGS.mkdir(parents=True, exist_ok=True)
    (SITE / ".nojekyll").write_text("")

    payload: dict = {"generated_at": utcnow(), "periods": PERIODS}

    # The institution-department tables are by far the largest (9,683 rows for
    # the five-year window). They go in their own files and are fetched only
    # when the explorer needs them, so the page paints immediately.
    for period in PERIODS:
        pairs = _pairs_with_reconstructed(period)
        p = SITE_DATA / f"pairs_{period}.json"
        p.write_text(json.dumps(_pack(pairs, PAIR_COLS), separators=(",", ":")))
        log.info("wrote %s (%.1f MB)", p.relative_to(ROOT), p.stat().st_size / 1e6)

        inst = pd.read_csv(TABLES / f"rank_institution_{period}.csv")
        payload[f"inst_{period}"] = _pack(inst, INST_COLS)
        dept = pd.read_csv(TABLES / f"rank_department_{period}.csv")
        payload[f"dept_{period}"] = _pack(dept, DEPT_COLS)
        for floor in FLOORS:
            t = pd.read_csv(TABLES / f"surgery_ranking_with_mgb_{period}_{floor}.csv")
            payload[f"mgb_{period}_{floor}"] = _pack(
                t,
                ["rank", "canonical_org_id", "canonical_name", "display_name", "total_funding",
                 "award_years", "distinct_projects", "r01_funding", "r01_award_years",
                 "funded_investigators", "funding_per_investigator", "mean_award_size",
                 "funding_per_project", "r01_share_of_funding", "evidence_basis",
                 # The site marks a row as a lower bound from this flag. Without
                 # it, only the handful of ids hardcoded in app.js get marked,
                 # and every other reconstructed peer reads as NIH-coded.
                 "is_reconstructed",
                 # A roll-up holds no place in the peer set. Both flags and the
                 # as-single ranks have to reach the browser, or the site has no
                 # way to tell a merged two-hospital figure from a department.
                 "is_rollup", "is_ranked", "n_ranked",
                 "rank_if_single_entity", "rank_award_years_if_single_entity",
                 "rank_r01_funding_if_single_entity",
                 "rank_r01_award_years_if_single_entity"],
            )

    cov = pd.read_csv(TABLES / "coverage_department_evidence.csv")
    payload["coverage"] = _pack(
        cov, ["fiscal_year", "dept_tier", "award_years", "funding", "pct_award_years", "pct_funding"]
    )
    val = pd.read_csv(TABLES / "validation_report.csv")
    payload["validation"] = _pack(val, ["check", "passed", "value", "detail"])
    sens = TABLES / "sensitivity_surgical_definition.csv"
    if sens.exists():
        payload["sensitivity"] = _pack(
            pd.read_csv(sens),
            ["period", "definition", "total_funding", "award_years", "distinct_projects", "institutions"],
        )

    for name, cols in [
        ("agreement_overall", ["scope", "comparable_award_years", "nih_surgical", "pub_surgical",
                               "both", "nih_only", "publication_only", "sensitivity_pct",
                               "precision_pct", "raw_agreement_pct", "cohens_kappa"]),
        ("agreement_by_institution", ["canonical_org_id", "display_name", "comparable_award_years",
                                      "nih_surgical", "pub_surgical", "both", "nih_only",
                                      "publication_only", "sensitivity_pct", "precision_pct",
                                      "cohens_kappa"]),
        ("agreement_uncomparable", ["canonical_org_id", "display_name", "award_years",
                                    "publication_surgical", "note"]),
    ]:
        path = TABLES / f"{name}.csv"
        payload[name] = _pack(pd.read_csv(path), cols) if path.exists() else {"cols": [], "rows": []}

    biblio = TABLES / "bibliometrics_surgery.csv"
    if biblio.exists():
        b = pd.read_csv(biblio)
        # MGB_CORE is MGH and BWH de-duplicated at the paper level, so all three
        # rows are in the file and only one of them may be counted. The flag
        # goes to the browser rather than the members being deleted: MGB_CORE
        # leads on citations only as a merged figure -- neither hospital leads
        # alone -- and a reader cannot see that if the members are not there.
        b["is_rollup"] = b.canonical_org_id.isin(("MGB_CORE", "MGB_SYSTEM")).astype(int)
        for period in PERIODS:
            payload[f"biblio_{period}"] = _pack(
                b[b.period == period],
                ["canonical_org_id", "display_name", "papers", "papers_TOP_TIER_GENERAL",
                 "papers_NEJM", "papers_JAMA", "papers_BMJ", "papers_Lancet",
                 "papers_Ann Surg", "papers_JAMA Surg", "total_citations",
                 "citations_per_paper", "median_citations", "mean_rcr", "is_rollup"],
            )

    # Per-year trajectories. Emitted as their own files: the pair-level trend is
    # 32k rows and only the trend chart needs it.
    TREND_COLS = [
        "fiscal_year", "canonical_org_id", "display_name", "org_country", "is_rollup",
        "total_funding", "award_years", "distinct_projects", "r01_funding",
        "r01_award_years", "funded_investigators", "funding_per_investigator",
        "mean_award_size", "r01_share_of_funding",
        "rank_total_funding", "rank_award_years", "rank_distinct_projects",
        "rank_r01_funding", "rank_r01_award_years", "is_ranked", "n_ranked",
        # Roll-ups are excluded from the peer-set rank but carry the rank they
        # would take if inserted into it as a single institution.
        "rank_total_funding_if_single_entity", "rank_award_years_if_single_entity",
        "rank_distinct_projects_if_single_entity", "rank_r01_funding_if_single_entity",
        "rank_r01_award_years_if_single_entity",
    ]
    for grain in ("institution", "institution_department"):
        src = TABLES / f"trend_{grain}.csv"
        if not src.exists():
            continue
        t = pd.read_csv(src)
        # MGH, BWH and the roll-ups all ship. `is_rollup` and the as-single rank
        # columns are already in TREND_COLS, so the chart can hold a roll-up out
        # of the ranked line and still draw it; deleting the members instead
        # made "Mass General Brigham" the only trajectory available for two
        # hospitals whose trajectories differ.
        cols = TREND_COLS + (["nih_org_dept", "specialty"] if grain != "institution" else [])
        dst = SITE_DATA / f"trend_{grain}.json"
        dst.write_text(json.dumps(_pack(t, cols), separators=(",", ":")))
        log.info("wrote %s (%.1f MB)", dst.relative_to(ROOT), dst.stat().st_size / 1e6)

    colors = pd.read_csv(REFERENCE / "institution_colors_v1.csv")
    payload["colors"] = dict(zip(colors.canonical_org_id, colors.color))
    payload["color_notes"] = dict(zip(colors.canonical_org_id, colors.color_note))

    manifest = read_json(ROOT / "data" / "raw" / "manifest.json") or {}
    payload["sources"] = [
        {k: v for k, v in rec.items() if k in
         ("file", "source_url", "downloaded_at", "fiscal_year", "bytes", "sha256", "member_name", "column_count")}
        for rec in (manifest.get("files") or {}).values()
    ]

    out = SITE_DATA / "core.json"
    out.write_text(json.dumps(payload, separators=(",", ":")))
    log.info("wrote %s (%.1f MB)", out.relative_to(ROOT), out.stat().st_size / 1e6)

    # Figures the site links to, copied so docs/ is self-contained for Pages.
    kept: set[str] = set()
    for src in FIGURES.rglob("*.png"):
        rel = src.relative_to(FIGURES)
        dst = SITE_FIGS / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        kept.add(str(rel))
    # Same rule as the tables: a figure the pipeline stops drawing has to stop
    # being served, or docs/ keeps last run's chart next to this run's numbers.
    for stale in sorted(SITE_FIGS.rglob("*.png")):
        if str(stale.relative_to(SITE_FIGS)) not in kept:
            stale.unlink()
            log.info("removed docs/figures/%s (no longer produced)",
                     stale.relative_to(SITE_FIGS))
    log.info("copied %d figures into docs/figures", len(kept))

    # Downloadable tables.
    dl = SITE / "tables"
    dl.mkdir(exist_ok=True)
    published: set[str] = set()
    for src in sorted(TABLES.glob("*.csv")):
        if src.name.startswith(SUPERSEDED_PREFIXES):
            continue
        shutil.copy2(src, dl / src.name)
        published.add(src.name)
    # A table that stops being produced has to stop being downloadable. Without
    # this the site keeps serving the last run's file for anything the pipeline
    # no longer writes, which is how a retired confidence floor stayed
    # downloadable after it was removed from the code.
    for stale in sorted(dl.glob("*.csv")):
        if stale.name not in published:
            stale.unlink()
            log.info("removed docs/tables/%s (no longer produced or superseded)", stale.name)
    log.info("copied %d tables into docs/tables", len(published))
