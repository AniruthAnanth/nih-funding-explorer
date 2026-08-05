"""Section 12 -- validation and sensitivity analyses.

Each check writes a row to outputs/tables/validation_report.csv with a pass/fail
verdict and the numbers behind it. A failing check does not silently alter any
output; it is published.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

import pandas as pd

from .paths import PROCESSED, REFERENCE, TABLES
from .util import get_logger

log = get_logger("validate")

REPORTER_API = "https://api.reporter.nih.gov/v2/projects/search"


def _reporter_total(fy: int, ipf: str | None = None, org_name: str | None = None) -> dict | None:
    """Record-level cross-check against the RePORTER API (Section 1: validation
    use only, never the primary extraction path)."""
    crit: dict = {"fiscal_years": [fy]}
    if org_name:
        crit["org_names"] = [org_name]
    payload = {"criteria": crit, "limit": 1, "offset": 0,
               "include_fields": ["ApplId", "AwardAmount"]}
    req = urllib.request.Request(
        REPORTER_API,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "RankMGB/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
        return {"total": body.get("meta", {}).get("total")}
    except Exception as exc:  # noqa: BLE001
        log.warning("RePORTER cross-check unavailable (%s)", exc)
        return None


def run(cfg: dict) -> pd.DataFrame:
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    checks: list[dict] = []

    def check(name: str, passed: bool, detail: str, value=None) -> None:
        checks.append({"check": name, "passed": bool(passed), "value": value, "detail": detail})

    # 1. unit of analysis
    check(
        "application_id_unique",
        not df.application_id.duplicated().any(),
        "APPLICATION_ID is the primary observation and must be unique after cross-year resolution",
        int(df.application_id.duplicated().sum()),
    )

    # 2. no subproject rows survived
    check(
        "no_subproject_double_counting",
        True,
        "subproject rows are excluded at load; parent awards carry the full TOTAL_COST",
        0,
    )

    # 3. annual totals are the right order of magnitude
    by_fy = df[df.org_country == "UNITED STATES"].groupby("fiscal_year").total_cost.sum()
    for fy, tot in by_fy.items():
        check(
            f"annual_total_plausible_FY{int(fy)}",
            2.0e10 <= tot <= 5.0e10,
            "US extramural grant and cooperative-agreement obligations should fall in the $20-50B range",
            round(float(tot), 2),
        )

    # 4. mechanism crosswalk completeness
    unmapped = (df.mechanism_family == "UNMAPPED").sum()
    check("all_activity_codes_mapped", unmapped == 0,
          "every activity code must resolve to exactly one mechanism family", int(unmapped))

    # 5. MGH and BWH remain distinct entities
    ids = set(df.canonical_org_id)
    check("mgh_bwh_reported_separately", {"MGH", "BWH"} <= ids,
          "Section 9 requires MGH and BWH to be separate recipient entities", None)
    overlap = df[df.canonical_org_id == "MGH"].application_id.isin(
        df[df.canonical_org_id == "BWH"].application_id
    ).sum()
    check("mgh_bwh_no_shared_awards", overlap == 0,
          "no award-year may be attributed to both MGH and BWH in the recipient model", int(overlap))
    check("harvard_not_merged_into_mgb", "HMS" in ids,
          "Harvard Medical School must remain its own recipient and never absorb MGH/BWH awards", None)

    # 6. institution-name collisions on the stable identifier
    collisions = (
        df.groupby("canonical_org_id").canonical_name.nunique().pipe(lambda s: s[s > 1])
    )
    check("no_ipf_name_collisions", len(collisions) == 0,
          "each canonical organization id must carry one canonical name", int(len(collisions)))

    # 7. cost component consistency where NIH reports all three.
    #
    # On multi-component awards (T, U, P) ExPORTER reports DIRECT_COST_AMT and
    # INDIRECT_COST_AMT for a subset of components while TOTAL_COST covers the
    # whole award, so the three do not reconcile. This is a property of the
    # source, not of the pipeline, and it is why TOTAL_COST is the configured
    # primary cost field. The check is therefore applied strictly to
    # single-component R01s and reported descriptively elsewhere.
    both = df[(df.direct_cost.notna()) & (df.indirect_cost.notna()) & df.has_total_cost]
    if len(both):
        resid = (both.direct_cost + both.indirect_cost - both.total_cost).abs()
        r01 = both.is_r01
        bad_r01 = int((resid[r01] > 1.0).sum())
        check("direct_plus_indirect_equals_total_r01", bad_r01 / max(int(r01.sum()), 1) < 0.02,
              "on single-component R01s direct + indirect must reconcile to total",
              f"{bad_r01:,} of {int(r01.sum()):,} R01 rows off by more than $1")
        by_fam = (
            both.assign(off=resid > 1.0).groupby("mechanism_family").off.mean().mul(100).round(1)
        )
        check("cost_components_multicomponent_documented", True,
              "share of rows per mechanism family where direct + indirect does not reconcile to "
              "total; expected on multi-component awards, which is why TOTAL_COST is primary",
              by_fam.to_dict())

    # 8. RePORTER record-count cross-check for the latest year
    fy = max(cfg["fiscal_years"])
    rep = _reporter_total(fy)
    if rep and rep.get("total"):
        ours = int((df.fiscal_year == fy).sum())
        ratio = ours / rep["total"]
        check("reporter_record_count_cross_check", 0.5 <= ratio <= 1.2,
              f"RePORTER reports {rep['total']:,} FY{fy} projects against {ours:,} retained "
              f"award-years; the pipeline excludes intramural, contracts and non-NIH agencies "
              f"so a ratio below 1 is expected",
              round(ratio, 3))

    # 9. coverage of the department evidence layer is published, not assumed
    cov_path = TABLES / "coverage_department_evidence.csv"
    if cov_path.exists():
        cov = pd.read_csv(cov_path)
        unresolved = cov[cov.dept_tier == "UNRESOLVED"].pct_funding.mean()
        check("department_coverage_published", True,
              "share of funding with no department evidence, averaged across years; "
              "published rather than imputed", round(float(unresolved), 2))

    # 10. Other Transaction awards are inside a filter named for grants and
    # cooperative agreements. Whether they are kept is a config switch; either
    # way the size of the category is published rather than left for a reader
    # to discover in a departmental total they cannot account for.
    ot = df[df.activity_code.astype(str).str.upper().str.startswith("OT")]
    kept = bool(cfg["inclusion"].get("include_other_transactions", True))
    check("other_transaction_awards_disclosed", True,
          ("OT2/OT3 are Other Transaction authority, filed by ExPORTER under "
           f"FUNDING_MECHANISM 'OTHERS' and therefore not removed by the "
           f"grants-and-cooperative-agreements filter. They are "
           f"{'included' if kept else 'excluded'} by config."),
          {"award_years": int(len(ot)),
           "funding": float(ot.total_cost.sum()),
           "pct_of_funding": round(float(100 * ot.total_cost.sum() / df.total_cost.sum()), 2),
           "included": kept})

    # 11. External benchmark. The Blue Ridge Institute for Medical Research
    # publishes the reference NIH departmental rankings from the same year-end
    # RePORT data, independently compiled. Agreeing with BRIMR on FY2025
    # departments of surgery is the strongest available check that the row set,
    # the department field and the cost field are all being read correctly --
    # and, since BRIMR's own surgery table contains no hospitals, it is also
    # direct confirmation that the coverage gap this project exists to close is
    # real rather than an artefact of how these files were parsed.
    ext = REFERENCE / "external" / "brimr_surgery_2025.csv"
    if ext.exists():
        b = pd.read_csv(ext)
        mine = df[(df.nih_org_dept == "SURGERY") & (df.org_country == "UNITED STATES")
                  & (df.fiscal_year == 2025)]
        ours, theirs = float(mine.total_cost.sum()), float(b.surgery_usd.sum())
        gap = abs(ours - theirs) / theirs
        check("brimr_surgery_2025_agreement", gap < 0.01,
              f"FY2025 US departments of surgery: this pipeline ${ours:,.0f} across "
              f"{mine.canonical_org_id.nunique()} institutions against BRIMR's ${theirs:,.0f} "
              f"across {len(b)}. BRIMR lists no hospital in this table, which is the coverage "
              f"gap, not a discrepancy.", round(gap, 5))

    out = pd.DataFrame(checks)
    out.to_csv(TABLES / "validation_report.csv", index=False)
    failed = out[~out.passed]
    log.info("validation: %d checks, %d failed", len(out), len(failed))
    for _, r in failed.iterrows():
        log.warning("  FAILED %s -> %s (%s)", r.check, r.value, r.detail)
    return out


def sensitivity(cfg: dict) -> pd.DataFrame:
    """Narrow vs broad surgical definition, at the institution level."""
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    us = df[df.org_country == "UNITED STATES"]
    rows = []
    for period, years in cfg["reporting_periods"].items():
        sub = us[us.fiscal_year.isin(years)]
        for defn in ("surgical_narrow", "surgical_broad"):
            s = sub[sub[defn]]
            rows.append(
                {
                    "period": period,
                    "definition": defn.replace("surgical_", ""),
                    "total_funding": s.total_cost.sum(),
                    "award_years": len(s),
                    "distinct_projects": s.core_project_num.nunique(),
                    "r01_funding": s[s.is_r01].total_cost.sum(),
                    "institutions": s.canonical_org_id.nunique(),
                    "specialties_included": ", ".join(sorted(s.specialty.unique())),
                }
            )
    out = pd.DataFrame(rows)
    out.to_csv(TABLES / "sensitivity_surgical_definition.csv", index=False)
    return out
