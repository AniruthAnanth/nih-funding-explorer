"""Alignment with the Blue Ridge Institute for Medical Research rankings.

BRIMR compiles the reference NIH departmental rankings from the same year-end
data this pipeline reads. Agreeing with them is the strongest external evidence
that the row set, the department field and the cost field are being read
correctly -- and every place the two disagree is either a bug here or a
documented difference in convention. This module makes that distinction
explicit rather than leaving it in a spreadsheet.

Three conventions differ, and none of them is a defect on either side:

**Institution transfers.** When a PI moves mid-year, ExPORTER files a type-7
award at the destination and leaves a partial-year record at the origin. This
pipeline credits each institution with the money it actually received. BRIMR
generally keeps the project with the institution that held it, so a department
does not lose a grant it ran for four years because the PI left in September.
Both are defensible; they are different questions.

**Academic affiliates.** BRIMR reports Vanderbilt University Medical Center's
surgery under "Vanderbilt University". VUMC has been a separate legal entity
since 2016 and NIH files it under its own IPF code with no department at all --
524 awards and $451.6M in FY2025, every one uncoded. This pipeline keeps them
separate because they are separate recipients.

That last case is the only external calibration the reconstruction has, and the
arithmetic has to be stated carefully because there are three defensible
denominators. BRIMR publishes $14,408,389 of Vanderbilt surgery for FY2025. NIH
codes $336,630 of it, at Vanderbilt University. The per-investigator
reconstruction adds $10,858,628 at VUMC, from publication affiliations alone and
without ever seeing BRIMR's number. So:

    coded + reconstructed, over BRIMR's total     77.70%
    reconstructed alone, over the uncoded part    77.17%
    reconstructed alone, over BRIMR's total       75.36%

The first is the like-for-like comparison and is the figure quoted. The third is
what you get by pairing the reconstruction with the wrong denominator, and it
was briefly published that way.

**Scope.** BRIMR's departmental tables cover medical schools. MD Anderson holds
$14.1M of NIH-coded surgery here and appears nowhere in their surgery table,
because it is a cancer centre rather than a medical school.

A second, independent calibration comes from departments that publish their own
figures. MGH's Department of Surgery reports $35M of NIH expenditures for 2024
against $74M of total research expenditures; the reconstruction here gives
$30,750,166 of NIH awards for FY2024, or 87.86%. Vanderbilt via BRIMR gave
77.70% on the like-for-like comparison above.
Two hospitals, two unrelated sources, both in the high seventies to high
eighties, both short -- which is the whole claim being made for these figures.

The $74M is worth carrying separately. NIH is 47% of that department's research
expenditures, so an NIH-derived ranking is measuring roughly half of what a
department actually spends, and none of the DOD, foundation, industry or
philanthropic support that makes up the rest.
"""
from __future__ import annotations

import re

import pandas as pd

from .paths import PROCESSED, REFERENCE, TABLES
from .util import get_logger

log = get_logger("external")

BENCHMARK = REFERENCE / "external" / "brimr_surgery_2025.csv"
BENCHMARK_FY = 2025
SELF_REPORTED = REFERENCE / "external" / "self_reported_departments_v1.csv"

# BRIMR writes recipient names its own way, and in one case combines two NIH
# recipients onto one line (the CWRU / Cleveland Clinic Lerner joint college).
# Spelled out rather than fuzzy-matched, so a near-miss can never be scored as
# agreement.
ALIAS: dict[str, list[str]] = {
    "WASHINGTON UNIVERSITY ST LOUIS": ["WASHINGTON UNIVERSITY"],
    "UNIVERSITY OF PITTSBURGH": ["UNIVERSITY OF PITTSBURGH AT PITTSBURGH"],
    "CORNELL UNIVERSITY WEILL MEDICAL COLLEGE": ["WEILL MEDICAL COLL OF CORNELL UNIV"],
    "UNIVERSITY OF NORTH CAROLINA CHAPEL HILL": ["UNIV OF NORTH CAROLINA CHAPEL HILL"],
    "RUTGERS, THE STATE UNIVERSITY OF NEW JERSEY": ["RUTGERS BIOMEDICAL AND HEALTH SCIENCES"],
    "INDIANA UNIVERSITY": ["INDIANA UNIVERSITY INDIANAPOLIS"],
    "UNIVERSITY OF TEXAS SOUTHWESTERN DALLAS": ["UT SOUTHWESTERN MEDICAL CENTER"],
    "WAKE FOREST UNIVERSITY HLTH SCIS": ["WAKE FOREST UNIVERSITY HEALTH SCIENCES"],
    "UNIVERSITY OF OKLAHOMA HLTH SCIS CTR": ["UNIVERSITY OF OKLAHOMA HLTH SCIENCES CTR"],
    "UNIVERSITY OF TENNESSEE HLTH SCI CTR": ["UNIVERSITY OF TENNESSEE HEALTH SCI CTR"],
    "PENNSYLVANIA STATE UNIV MED CTR HERSHEY": ["PENNSYLVANIA STATE UNIV HERSHEY MED CTR"],
    "UNIVERSITY OF WASHINGTON SEATTLE": ["UNIVERSITY OF WASHINGTON"],
    "UNIVERSITY OF VERMONT": ["UNIVERSITY OF VERMONT & ST AGRIC COLLEGE"],
    "CASE WESTERN RESERVE U & CLEVELAND CLINIC LERNER COM":
        ["CASE WESTERN RESERVE UNIVERSITY", "CLEVELAND CLINIC LERNER COM-CWRU"],
    "MOUNT SINAI ICAHN SCHOOL OF MEDICINE": ["ICAHN SCHOOL OF MEDICINE AT MOUNT SINAI"],
    "TEXAS TECH UNIVERSITY HSC LUBBOCK": ["TEXAS TECH UNIVERSITY HEALTH SCIS CENTER"],
    "UNIVERSITY OF MASSACHUSETTS MED SCH WORCESTER": ["UNIV OF MASSACHUSETTS MED SCH WORCESTER"],
    "TULANE UNIVERSITY": ["TULANE UNIVERSITY OF LOUISIANA"],
}

# BRIMR reports these hospitals' departments under their academic partner.
# Keyed by the recipient this pipeline reconstructs, valued by BRIMR's line.
AFFILIATE_ROLLUP = {"IPF10040927": "VANDERBILT UNIVERSITY"}

_DROP = ("UNIVERSITY", "THE", "OF", "AT", "SCHOOL", "MEDICINE", "MEDICAL",
         "COLLEGE", "HEALTH SCIENCES", "CENTER", "CTR", "HLTH", "SCI")


def norm(s: str) -> str:
    """Reduce a recipient name to the words the two sources agree on.

    Deliberately not fuzzy. Two names either reduce to the same token string or
    they are not paired at all, so an approximate match can never be counted as
    agreement.
    """
    out = re.sub(r"[^A-Z ]", " ", str(s).upper())
    for w in _DROP:
        out = out.replace(w, " ")
    return " ".join(out.split())


def apply_project_continuity(fy: pd.DataFrame, full: pd.DataFrame) -> pd.DataFrame:
    """Keep a transferred project with the institution that held it.

    A core project split across institutions inside one fiscal year is almost
    always a PI who moved. Under this convention the whole fiscal year goes to
    last year's holder, provided that institution still has a record this year.
    """
    out = fy.copy()
    multi = out.groupby("core_project_num").canonical_org_id.transform("nunique") > 1
    moved = 0
    for cp, g in out[multi].groupby("core_project_num"):
        hist = full[(full.core_project_num == cp) & (full.fiscal_year < fy.fiscal_year.max())]
        if hist.empty:
            continue
        prior = hist.sort_values("fiscal_year").iloc[-1]
        keep = g[g.canonical_org_id == prior.canonical_org_id]
        if keep.empty:
            continue
        out.loc[g.index, ["canonical_name", "canonical_org_id", "nih_org_dept"]] = \
            keep.iloc[0][["canonical_name", "canonical_org_id", "nih_org_dept"]].values
        moved += len(g) - len(keep)
    log.info("project-continuity attribution moved %d award-year(s)", moved)
    return out


def _reconstructed_surgery(org_id: str, fy: int) -> float:
    """This pipeline's reconstructed department-of-surgery total for one recipient."""
    from .mgb_surgery import DOS

    prof_path = PROCESSED / "pi_departments.parquet"
    if not prof_path.exists():
        return 0.0
    prof = pd.read_parquet(prof_path)
    if org_id not in set(prof.institution_id):
        return 0.0
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    pis = pd.read_parquet(PROCESSED / "pi_links.parquet")
    pis = pis[pis.is_contact_pi][["application_id", "pi_name_raw"]]
    a = pis.merge(df[["application_id", "canonical_org_id", "fiscal_year", "total_cost"]],
                  on="application_id")
    m = a.merge(prof[["institution_id", "pi_name_raw", "is_surgical", "modal_department"]],
                left_on=["canonical_org_id", "pi_name_raw"],
                right_on=["institution_id", "pi_name_raw"], how="left")
    sel = m[(m.canonical_org_id == org_id) & m.is_surgical.fillna(False).astype(bool)
            & m.modal_department.isin(DOS) & (m.fiscal_year == fy)]
    return float(sel.total_cost.sum())


def compare(cfg: dict) -> pd.DataFrame | None:
    """Row-by-row comparison against BRIMR under both conventions.

    Publishes every institution and the reason for any residual, so the
    agreement can be read rather than taken from a summary statistic. An
    aggregate is not enough: comparing grand totals put the two within 0.12%
    while MD Anderson and Vanderbilt were quietly cancelling each other out at
    $14.1M apiece.
    """
    if not BENCHMARK.exists():
        log.warning("no external benchmark at %s", BENCHMARK)
        return None
    b = pd.read_csv(BENCHMARK)
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    fy = df[df.fiscal_year == BENCHMARK_FY]

    aligned = apply_project_continuity(fy, df)
    affiliate = {name: _reconstructed_surgery(org, BENCHMARK_FY)
                 for org, name in AFFILIATE_ROLLUP.items()}

    def totals(frame: pd.DataFrame, extra: dict | None = None) -> dict:
        m = frame[(frame.nih_org_dept == "SURGERY") & (frame.org_country == "UNITED STATES")]
        by = m.groupby("canonical_name").total_cost.sum().to_dict()
        for k, v in (extra or {}).items():
            by[k] = by.get(k, 0.0) + v
        return by

    views = {
        "as_awarded": totals(fy),
        "brimr_aligned": totals(aligned, affiliate),
    }
    index = {}
    for by in views.values():
        for nm in by:
            index.setdefault(norm(nm), set()).add(nm)

    rows = []
    for _, r in b.iterrows():
        names = ALIAS.get(r["name"]) or sorted(index.get(norm(r["name"]), []))
        if not names:
            continue
        vals = {k: sum(by.get(n, 0.0) for n in names) for k, by in views.items()}
        if max(vals.values()) <= 0:
            continue
        want = float(r.surgery_usd)
        rows.append({
            "brimr_rank": int(r["rank"]), "brimr_institution": r["name"],
            "brimr_usd": want,
            "as_awarded_usd": vals["as_awarded"],
            "brimr_aligned_usd": vals["brimr_aligned"],
            "residual_usd": vals["brimr_aligned"] - want,
            "agrees_to_the_dollar": abs(vals["brimr_aligned"] - want) < 1.0,
        })
    out = pd.DataFrame(rows)
    out["reason"] = "agrees"
    out.loc[~out.agrees_to_the_dollar, "reason"] = "unexplained residual"
    out.loc[(~out.agrees_to_the_dollar)
            & (out.as_awarded_usd != out.brimr_aligned_usd), "reason"] = \
        "institution transfer, partly reconciled"
    out.loc[out.brimr_institution.isin(AFFILIATE_ROLLUP.values())
            & ~out.agrees_to_the_dollar, "reason"] = \
        "academic affiliate; reconstructed figure is a lower bound"
    out = out.sort_values("brimr_usd", ascending=False)
    out.to_csv(TABLES / "external_benchmark_brimr_surgery_2025.csv", index=False)

    ex_a = int((out.as_awarded_usd.sub(out.brimr_usd).abs() < 1).sum())
    ex_b = int(out.agrees_to_the_dollar.sum())
    log.info("BRIMR FY%d surgery: %d institutions matched; exact to the dollar %d as-awarded, "
             "%d BRIMR-aligned; residual $%s -> $%s",
             BENCHMARK_FY, len(out), ex_a, ex_b,
             f"{out.as_awarded_usd.sub(out.brimr_usd).abs().sum():,.0f}",
             f"{out.residual_usd.abs().sum():,.0f}")
    return out


def self_reported(cfg: dict) -> pd.DataFrame | None:
    """Compare the reconstruction against departments' own published figures.

    The reconstruction is described everywhere in this project as a lower
    bound. That claim was, until these two checks, untested from outside: the
    kappa 0.916 validation is drawn at universities where NIH publishes a
    department, and says nothing about how much of a hospital department's
    money the rule actually recovers.

    A department publishing its own annual figure is the cleanest test there
    is. Note the units differ: a department reports *expenditures* drawn down
    during a year, while this pipeline sums *awards obligated* in that fiscal
    year. In steady state the two track each other; in a year of unusual growth
    or wind-down they need not.
    """
    if not SELF_REPORTED.exists():
        return None
    from .mgb_surgery import DOS

    ref = pd.read_csv(SELF_REPORTED)
    ref = ref[ref.metric == "nih_expenditures"]
    if ref.empty:
        return None
    prof_path = PROCESSED / "pi_departments.parquet"
    if not prof_path.exists():
        return None
    prof = pd.read_parquet(prof_path)
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    pis = pd.read_parquet(PROCESSED / "pi_links.parquet")
    pis = pis[pis.is_contact_pi][["application_id", "pi_name_raw"]]
    a = pis.merge(df[["application_id", "canonical_org_id", "fiscal_year", "total_cost"]],
                  on="application_id")
    m = a.merge(prof[["institution_id", "pi_name_raw", "is_surgical", "modal_department"]],
                left_on=["canonical_org_id", "pi_name_raw"],
                right_on=["institution_id", "pi_name_raw"], how="left")

    rows = []
    for _, r in ref.iterrows():
        sel = m[(m.canonical_org_id == r.canonical_org_id)
                & m.is_surgical.fillna(False).astype(bool)
                & m.modal_department.isin(DOS)
                & (m.fiscal_year == int(r.fiscal_year))]
        ours = float(sel.total_cost.sum())
        rows.append({
            "canonical_org_id": r.canonical_org_id, "department": r.department,
            "fiscal_year": int(r.fiscal_year), "self_reported_usd": float(r.value_usd),
            "reconstructed_usd": round(ours),
            "recovery_pct": round(100 * ours / float(r.value_usd), 1) if r.value_usd else None,
            "source": r.source,
        })
    out = pd.DataFrame(rows)
    out.to_csv(TABLES / "external_benchmark_self_reported.csv", index=False)
    for _, r in out.iterrows():
        log.info("%s %s FY%d: department reports $%s, reconstruction gives $%s (%.1f%%)",
                 r.canonical_org_id, r.department, r.fiscal_year,
                 f"{r.self_reported_usd:,.0f}", f"{r.reconstructed_usd:,.0f}", r.recovery_pct)
    return out
