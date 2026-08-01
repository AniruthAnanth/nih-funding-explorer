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
from .util import get_logger, read_json, utcnow

log = get_logger("site")

SITE = ROOT / "docs"
SITE_DATA = SITE / "data"
SITE_FIGS = SITE / "figures"

PERIODS = ["FY2025", "FY2024_FY2025", "FY2021_FY2025"]

PAIR_COLS = [
    "canonical_org_id", "canonical_name", "display_name", "org_country", "nih_org_dept", "specialty",
    "total_funding", "award_years", "distinct_projects", "r01_funding", "r01_award_years",
    "m_award_years", "funded_investigators", "funding_R01", "funding_R_OTHER", "funding_U",
    "funding_P", "funding_K", "funding_T", "funding_F", "funding_OTHER", "is_rollup",
    "funding_per_investigator", "funding_per_project", "mean_award_size",
    "r01_funding_per_investigator", "projects_per_investigator", "r01_share_of_funding",
    "meets_intensity_floor", "is_reconstructed",
]
INST_COLS = [
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
    for c in sub.columns:
        if sub[c].dtype.kind == "f":
            # Ratio columns are NaN where the denominator was zero. Round to
            # whole units but keep the null, so the site shows an em dash rather
            # than a fabricated zero.
            keep_decimals = c in ("projects_per_investigator", "r01_share_of_funding")
            vals = sub[c] if keep_decimals else sub[c].round(0)
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
    The publication-derived surgery rows are appended here, flagged
    is_reconstructed so the interface can mark them as lower bounds and never
    present them as like-for-like with an NIH-coded department.
    """
    pairs = pd.read_csv(TABLES / f"rank_institution_department_{period}.csv")
    pairs["is_reconstructed"] = 0

    recon_path = TABLES / "mgb_surgery_summary.csv"
    if not recon_path.exists():
        return pairs
    r = pd.read_csv(recon_path)
    r = r[(r.period == period) & (r.scope == "Department of Surgery")
          & (r.confidence_floor == "corroborated")
          & (r.entity == "MGB_CORE")].copy()
    if r.empty:
        return pairs

    r = r.rename(columns={"entity": "canonical_org_id"})
    r["canonical_name"] = "Mass General Brigham"
    r["display_name"] = "Mass General Brigham"
    r["org_country"] = "UNITED STATES"
    r["nih_org_dept"] = "SURGERY"
    r["specialty"] = "GENERAL_AND_UNSPECIFIED_SURGERY"
    r["is_rollup"] = True
    r["is_reconstructed"] = 1
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
    log.info("%s: appended %d reconstructed department row(s)", period, len(r))
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
        for floor in ("corroborated", "all_evidence"):
            t = pd.read_csv(TABLES / f"surgery_ranking_with_mgb_{period}_{floor}.csv")
            payload[f"mgb_{period}_{floor}"] = _pack(
                t,
                ["rank", "canonical_org_id", "canonical_name", "display_name", "total_funding",
                 "award_years", "distinct_projects", "r01_funding", "r01_award_years",
                 "funded_investigators", "funding_per_investigator", "mean_award_size",
                 "funding_per_project", "r01_share_of_funding", "evidence_basis"],
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
        # MGH and BWH are published only as the combined entity.
        b = b[~b.canonical_org_id.isin(("MGH", "BWH"))]
        for period in PERIODS:
            payload[f"biblio_{period}"] = _pack(
                b[b.period == period],
                ["canonical_org_id", "display_name", "papers", "papers_TOP_TIER_GENERAL",
                 "papers_NEJM", "papers_JAMA", "papers_BMJ", "papers_Lancet",
                 "papers_Ann Surg", "papers_JAMA Surg", "total_citations",
                 "citations_per_paper", "median_citations", "mean_rcr"],
            )

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
    n = 0
    for src in FIGURES.rglob("*.png"):
        rel = src.relative_to(FIGURES)
        dst = SITE_FIGS / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        n += 1
    log.info("copied %d figures into docs/figures", n)

    # Downloadable tables.
    dl = SITE / "tables"
    dl.mkdir(exist_ok=True)
    for src in TABLES.glob("*.csv"):
        shutil.copy2(src, dl / src.name)
    log.info("copied %d tables into docs/tables", len(list(TABLES.glob('*.csv'))))
