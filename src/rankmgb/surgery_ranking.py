"""The surgery ranking, measured the same way for every institution.

Primary measurement: publication-derived department, from dated PubMed author
affiliations (surgical_attribution.py). It is applied identically to MGH and to
Duke, so the ranking is internally comparable.

Secondary column: the same department total computed from NIH's own ORG_DEPT.
It exists for comparison only. It is blank for every institution NIH does not
department-code, which is precisely why it cannot be the primary measurement.

The two are never added together and never substituted for one another.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from .figures import BASE, HIGHLIGHT, _money, _period_label, _short, _subtitle
from .paths import FIGURES, TABLES
from .util import get_logger

log = get_logger("surgery_ranking")

# Reported only as the combined entity, never split, per the analysis brief.
COLLAPSE = {"MGH", "BWH", "MGB_SYSTEM"}


def build_table(period: str, floor: str = "corroborated") -> pd.DataFrame:
    pub = pd.read_csv(TABLES / "surgery_publication_derived.csv")
    pub = pub[(pub.period == period) & (pub.confidence_floor == floor)].copy()

    nih = pd.read_csv(TABLES / f"rank_institution_department_{period}.csv")
    nih = nih[(nih.nih_org_dept == "SURGERY") & (nih.org_country == "UNITED STATES")]
    nih = nih[["canonical_org_id", "total_funding", "award_years", "funded_investigators"]].rename(
        columns={
            "total_funding": "nih_total_funding",
            "award_years": "nih_award_years",
            "funded_investigators": "nih_funded_investigators",
        }
    )

    out = pub.merge(nih, on="canonical_org_id", how="outer")
    out = out[~out.canonical_org_id.isin(COLLAPSE)].copy()
    out["period"] = period
    out["confidence_floor"] = floor
    out = out.dropna(subset=["total_funding"])

    def per(num: str, den: str) -> pd.Series:
        return (out[num] / out[den].replace(0, np.nan)).round(0)

    out["funding_per_investigator"] = per("total_funding", "funded_investigators")
    out["mean_award_size"] = per("total_funding", "award_years")
    out["funding_per_project"] = per("total_funding", "distinct_projects")
    out["r01_share_of_funding"] = (
        out.r01_funding / out.total_funding.replace(0, np.nan) * 100
    ).round(1)
    # How far the NIH-based figure sits from the publication-based one. Blank
    # where NIH supplies no department at all.
    out["nih_vs_publication_ratio"] = (
        out.nih_total_funding / out.total_funding.replace(0, np.nan)
    ).round(2)
    out["nih_comparable"] = out.nih_total_funding.notna()

    out = out.sort_values("total_funding", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", out.index + 1)
    path = TABLES / f"surgery_ranking_{period}_{floor}.csv"
    out.to_csv(path, index=False)
    log.info("wrote %s (%d departments)", path.name, len(out))
    return out


def figure(period: str, floor: str = "corroborated", top_n: int = 20) -> None:
    t = build_table(period, floor).head(top_n)
    fig, ax = plt.subplots(figsize=(9.8, 0.34 * len(t) + 2.1))
    y = np.arange(len(t))
    ax.barh(y, t.total_funding, color=[HIGHLIGHT.get(i, BASE) for i in t.canonical_org_id],
            edgecolor="white", linewidth=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels([_short(x) for x in t.display_name])
    ax.invert_yaxis()
    span = t.total_funding.max()
    for i, v in enumerate(t.total_funding):
        ax.text(v + span * 0.01, i, _money(v), va="center", fontsize=8, fontweight="bold")
    ax.set_xlim(0, span * 1.3)
    ax.set_title("Departments of surgery by NIH funding", fontsize=12.5)
    _subtitle(ax, _period_label(period)
              + " · department from dated publication affiliations, applied identically "
                "to every institution")
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    out = FIGURES / "surgery"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"surgery_ranking_{period}_{floor}.png")
    plt.close(fig)
    log.info("  surgery/surgery_ranking_%s_%s.png", period, floor)


def figure_agreement(period: str = "FY2021_FY2025", top_n: int = 18) -> None:
    """Publication-derived beside NIH-derived, for the institutions where both exist."""
    t = build_table(period)
    t = t[t.nih_comparable].sort_values("total_funding", ascending=False).head(top_n).iloc[::-1]
    y = np.arange(len(t))
    h = 0.38
    fig, ax = plt.subplots(figsize=(9.8, 0.52 * len(t) + 2.2))
    ax.barh(y + h / 2, t.total_funding, height=h, color="#1b3a5c",
            label="Publication-derived (primary)", edgecolor="white", linewidth=0.5)
    ax.barh(y - h / 2, t.nih_total_funding, height=h, color="#c9a227",
            label="NIH ORG_DEPT (comparator)", edgecolor="white", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels([_short(x) for x in t.display_name])
    ax.set_title("The two measurements side by side", fontsize=12.5)
    _subtitle(ax, _period_label(period)
              + " · only institutions NIH department-codes; the rest have no NIH figure at all")
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    ax.legend(frameon=False, fontsize=8, loc="lower right",
              handles=[mpatches.Patch(facecolor="#1b3a5c", label="Publication-derived (primary)"),
                       mpatches.Patch(facecolor="#c9a227", label="NIH ORG_DEPT (comparator)")])
    out = FIGURES / "surgery"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"method_comparison_{period}.png")
    plt.close(fig)
    log.info("  surgery/method_comparison_%s.png", period)


def build_all(cfg: dict) -> None:
    for period in cfg["reporting_periods"]:
        for floor in ("corroborated", "all_evidence"):
            figure(period, floor)
    figure_agreement()
