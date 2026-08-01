"""Places the MGH/BWH/MGB surgical figures into the national ranking.

The MGB rows come from a different evidence tier than every other row in the
table: NIH supplies the department for university recipients, while MGH and BWH
had to be reconstructed from publication affiliations. That difference is
carried through into the output as an explicit column and drawn differently in
the figure, because the two are not measured the same way and should never be
read as if they were.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from .figures import BASE, HIGHLIGHT, _money, _short
from .paths import FIGURES, PROCESSED, TABLES
from .util import get_logger

log = get_logger("mgb_context")

MGB_IDS = ("MGB_CORE", "MGH", "BWH")


def combined_table(period: str, floor: str = "corroborated") -> pd.DataFrame:
    nih = pd.read_csv(TABLES / f"rank_institution_department_{period}.csv")
    nih = nih[(nih.nih_org_dept == "SURGERY") & (nih.org_country == "UNITED STATES")].copy()
    nih = nih[["canonical_org_id", "canonical_name", "display_name", "total_funding", "award_years",
               "distinct_projects", "r01_funding", "r01_award_years", "m_award_years",
               "funded_investigators"]]
    nih["evidence_basis"] = "NIH ORG_DEPT (contact PI)"
    nih["confidence_floor"] = "n/a"

    mgb = pd.read_csv(TABLES / "mgb_surgery_summary.csv")
    mgb = mgb[(mgb.period == period) & (mgb.scope == "Department of Surgery")
              & (mgb.confidence_floor == floor)].copy()
    mgb = mgb.rename(columns={"entity": "canonical_org_id"})
    label = {"MGH": "Massachusetts General Hospital", "BWH": "Brigham and Women's Hospital",
             "MGB_CORE": "Mass General Brigham"}
    mgb["canonical_name"] = mgb.canonical_org_id.map(label)
    mgb["display_name"] = mgb.canonical_org_id.map(label)
    mgb["evidence_basis"] = "Reconstructed from publication affiliations (lower bound)"
    mgb["confidence_floor"] = floor
    mgb = mgb[nih.columns]

    # Member hospitals are retained in the CSV for audit but must not be ranked
    # alongside their own roll-up: they would compete with themselves and push
    # every other department down a place.
    out = pd.concat([nih, mgb], ignore_index=True)
    ranked = ~out.canonical_org_id.isin(("MGH", "BWH", "MGB_SYSTEM"))

    # Size-normalised columns, recomputed here so the reconstructed rows carry
    # them too and the intensity figures can include MGB.
    def per(num: str, den: str) -> pd.Series:
        return (out[num] / out[den].replace(0, np.nan)).round(0)

    out["funding_per_investigator"] = per("total_funding", "funded_investigators")
    out["r01_funding_per_investigator"] = per("r01_funding", "funded_investigators")
    out["funding_per_project"] = per("total_funding", "distinct_projects")
    out["mean_award_size"] = per("total_funding", "award_years")
    out["r01_share_of_funding"] = (
        out.r01_funding / out.total_funding.replace(0, np.nan) * 100
    ).round(1)
    out["meets_intensity_floor"] = out.funded_investigators >= 5

    out = out.sort_values("total_funding", ascending=False).reset_index(drop=True)
    ranked = ~out.canonical_org_id.isin(("MGH", "BWH", "MGB_SYSTEM"))
    out.insert(0, "rank", out.total_funding.where(ranked).rank(ascending=False, method="min"))
    out.insert(1, "is_ranked", ranked)
    out.insert(0, "period", period)
    for col, metric in [("rank_award_years", "award_years"),
                        ("rank_r01_funding", "r01_funding"),
                        ("rank_r01_award_years", "r01_award_years")]:
        out[col] = out[metric].where(ranked).rank(ascending=False, method="min")
    path = TABLES / f"surgery_ranking_with_mgb_{period}_{floor}.csv"
    out.to_csv(path, index=False)
    log.info("wrote %s (%d rows)", path.name, len(out))
    return out


def figure(period: str, floor: str = "corroborated", top_n: int = 22) -> None:
    t = combined_table(period, floor)
    sub = t[t.is_ranked].head(top_n)
    colors = [HIGHLIGHT.get(i, BASE) for i in sub.canonical_org_id]
    hatch = ["//" if i in MGB_IDS else "" for i in sub.canonical_org_id]

    fig, ax = plt.subplots(figsize=(10.5, 0.36 * len(sub) + 2.4))
    y = np.arange(len(sub))
    bars = ax.barh(y, sub.total_funding, color=colors, edgecolor="white", linewidth=0.7)
    for b, h in zip(bars, hatch):
        if h:
            b.set_hatch(h)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{int(r)}. {_short(n, 40)}" for r, n in zip(sub["rank"], sub.display_name)])
    ax.invert_yaxis()
    span = sub.total_funding.max()
    for i, (v, n) in enumerate(zip(sub.total_funding, sub.award_years)):
        ax.text(v + span * 0.01, i, f"  {_money(v)} ({int(n)} award-years)",
                va="center", fontsize=8)
    ax.set_xlim(0, span * 1.36)
    from .figures import _period_label, _subtitle
    ax.set_title("Departments of surgery by NIH funding", fontsize=12.5)
    _subtitle(ax, _period_label(period)
              + " · hatched: department derived from publication affiliations "
                "(majority rule, kappa 0.906) rather than NIH's field")
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    ax.legend(
        handles=[
            mpatches.Patch(facecolor=BASE, label="Department from NIH ORG_DEPT"),
            mpatches.Patch(facecolor=HIGHLIGHT.get("MGB_CORE", "#00558C"), hatch="//",
                           label="Department from publication affiliations"),
        ],
        frameon=False, fontsize=8, loc="lower right",
    )
    out = FIGURES / "mgb"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"surgery_ranking_with_mgb_{period}_{floor}.png")
    plt.close(fig)
    log.info("  mgb/surgery_ranking_with_mgb_%s_%s.png", period, floor)


def build_all(cfg: dict) -> None:
    for period in cfg["reporting_periods"]:
        for floor in ("corroborated", "all_evidence"):
            figure(period, floor)
