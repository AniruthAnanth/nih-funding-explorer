"""Places the reconstructed surgical figures into the national ranking.

Two evidence tiers meet in this table. NIH supplies the department for
recipients it classifies as schools; for the recipients it does not, the
department has to be reconstructed from publication affiliations. That
difference is carried through into the output as an explicit column and drawn
differently in the figure, because the two are not measured the same way and
should never be read as if they were.

Both tiers now cover the whole field. Earlier versions reconstructed only MGH
and BWH, which left every other uncoded hospital — Vanderbilt University
Medical Center, Mayo Clinic Rochester, Memorial Sloan Kettering, the
children's hospitals — out of the ranking entirely rather than ranked below or
above MGB. A ranking is only a ranking against the institutions that are in
it, so all of them are reconstructed and all of them are marked.
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
from .paths import FIGURES, PROCESSED, REFERENCE, TABLES
from .util import get_logger

log = get_logger("mgb_context")

# Roll-ups: kept in the CSV, never given a peer rank. A roll-up and its members
# cannot both be ranked -- the institution would compete with itself and push
# every other department down a place -- and the rest of the pipeline settles
# this by ranking the members, which are real single institutions with their
# own chairs, and publishing the roll-up's position in a separate
# `rank_*_if_single_entity` column. This table now does the same.
#
# MGH and BWH used to be the unranked ones so that MGB_CORE could hold a peer
# rank. That inverted the convention: it printed a merged two-hospital figure
# in the same column as every single department's rank, unmarked, and deleted
# the only rows a reader could have used to notice.
ROLLUPS = ("MGB_CORE", "MGB_SYSTEM", "HARVARD_ENTITIES")

RECONSTRUCTED_BASIS = "Reconstructed from publication affiliations (lower bound)"
NIH_BASIS = "NIH ORG_DEPT (contact PI)"

# Both countings of a grant are ranked, because they answer different questions
# and disagree. An award-year is one grant counted once per fiscal year it was
# funded, so a five-year grant contributes five; nationally there are 2.65
# award-years per distinct grant. Ranking on award-years rewards a department
# whose grants run longer, which is a real fact about it but is not "how many
# grants does it hold". On R01s the two orderings genuinely differ: Michigan
# holds 59 distinct R01s to MGB's 56, while MGB has 179 R01 award-years to
# Michigan's 165.
RANKED_METRICS = {
    "rank": "total_funding",
    "rank_award_years": "award_years",
    "rank_distinct_projects": "distinct_projects",
    "rank_r01_funding": "r01_funding",
    "rank_r01_award_years": "r01_award_years",
    "rank_r01_distinct_projects": "r01_distinct_projects",
}


def _labels() -> dict[str, str]:
    """Display label per recipient, from the reference tables rather than a
    hardcoded dict, so a newly reconstructed institution cannot end up nameless
    in the ranking."""
    names = pd.read_csv(REFERENCE / "institution_display_names_v1.csv")
    out = dict(zip(names.canonical_org_id, names.display_name))
    reg = pd.read_csv(REFERENCE / "pubmed_institutions_v1.csv")
    for org, short in zip(reg.canonical_org_id, reg.short_name):
        out.setdefault(org, short)
    return out


def combined_table(period: str, floor: str = "corroborated") -> pd.DataFrame:
    nih = pd.read_csv(TABLES / f"rank_institution_department_{period}.csv")
    nih = nih[(nih.nih_org_dept == "SURGERY") & (nih.org_country == "UNITED STATES")].copy()
    # `rank_institution_department` carries roll-ups alongside their members.
    # Harvard University entities rolls up HMS, Harvard University and HSPH, and
    # only HMS has a department of surgery, so the roll-up was a verbatim
    # duplicate of HMS ranked one place above it, displacing 35 departments and
    # inflating the published denominator by one in every period.
    if "is_rollup" in nih.columns:
        nih = nih[~nih.is_rollup.astype(bool)]
    nih = nih[["canonical_org_id", "canonical_name", "display_name", "total_funding", "award_years",
               "distinct_projects", "r01_funding", "r01_award_years", "r01_distinct_projects",
               "m_award_years", "m_distinct_projects", "funded_investigators"]]
    nih["evidence_basis"] = NIH_BASIS
    nih["confidence_floor"] = "n/a"

    mgb = pd.read_csv(TABLES / "mgb_surgery_summary.csv")
    mgb = mgb[(mgb.period == period) & (mgb.scope == "Department of Surgery")
              & (mgb.confidence_floor == floor)].copy()
    mgb = mgb.rename(columns={"entity": "canonical_org_id"})
    label = _labels()
    missing = sorted(set(mgb.canonical_org_id) - set(label))
    if missing:
        log.warning("no display label for reconstructed recipient(s): %s", ", ".join(missing))
    named = mgb.canonical_org_id.map(label).fillna(mgb.canonical_org_id)
    mgb["canonical_name"] = named
    mgb["display_name"] = named
    mgb["evidence_basis"] = RECONSTRUCTED_BASIS
    mgb["confidence_floor"] = floor
    mgb = mgb[nih.columns]

    # A recipient that was profiled and turned out to have no surgical
    # investigator still gets a row, at zero: absent reads as "not measured",
    # zero reads as "measured, and there is nothing there", and conflating the
    # two would reinstate the bias this table exists to remove. A recipient that
    # has not been profiled at all is a different case and never reaches here --
    # pi_department._profiled drops it, because ranking Mayo Clinic at $0
    # because nobody asked PubMed is a false statement, not a measurement.
    out = pd.concat([nih, mgb], ignore_index=True)

    # Size-normalised columns, recomputed here so the reconstructed rows carry
    # them too and the intensity figures can include MGB.
    def per(num: str, den: str) -> pd.Series:
        return (out[num] / out[den].replace(0, np.nan)).round(0)

    out["funding_per_investigator"] = per("total_funding", "funded_investigators")
    out["r01_funding_per_investigator"] = per("r01_funding", "funded_investigators")
    out["funding_per_project"] = per("total_funding", "distinct_projects")
    out["mean_award_size"] = per("total_funding", "award_years")
    # How long the department's grants run, which is what separates the two
    # ways of counting them.
    out["award_years_per_project"] = (
        out.award_years / out.distinct_projects.replace(0, np.nan)).round(2)
    out["r01_share_of_funding"] = (
        out.r01_funding / out.total_funding.replace(0, np.nan) * 100
    ).round(1)
    out["meets_intensity_floor"] = out.funded_investigators >= 5

    out = out.sort_values("total_funding", ascending=False).reset_index(drop=True)
    out["is_rollup"] = out.canonical_org_id.isin(ROLLUPS)
    ranked = ~out.is_rollup
    out.insert(0, "is_ranked", ranked)
    # An explicit flag rather than a list of institution ids the site has to
    # keep in step: any recipient reconstructed in a later run is marked without
    # anyone remembering to add it.
    out.insert(1, "is_reconstructed", out.evidence_basis == RECONSTRUCTED_BASIS)
    out.insert(0, "period", period)
    for col, metric in RANKED_METRICS.items():
        out[col] = out[metric].where(ranked).rank(ascending=False, method="min")
        # What a roll-up would rank if its members were one department. Its own
        # column, so it can never be read as a place in the peer set.
        peer = out.loc[ranked, metric]
        as_single = pd.Series(np.nan, index=out.index)
        for i in out.index[out.is_rollup]:
            v = out.at[i, metric]
            if pd.notna(v):
                as_single.at[i] = int((peer > v).sum()) + 1
        out[col + "_if_single_entity"] = as_single
    out.insert(3, "rank", out.pop("rank"))
    out["n_ranked"] = int(ranked.sum())

    path = TABLES / f"surgery_ranking_with_mgb_{period}_{floor}.csv"
    out.to_csv(path, index=False)
    rec = out[ranked & (out.evidence_basis == RECONSTRUCTED_BASIS)]
    log.info("wrote %s (%d rows; %d ranked, of which %d reconstructed and %d from NIH ORG_DEPT)",
             path.name, len(out), int(ranked.sum()), len(rec), int(ranked.sum()) - len(rec))
    if not rec.empty:
        top = rec.nsmallest(1, "rank").iloc[0]
        log.info("  highest-ranked reconstructed department: %s at rank %d of %d",
                 top.display_name, int(top["rank"]), int(ranked.sum()))
    core = out[out.canonical_org_id == "MGB_CORE"]
    if not core.empty:
        r = core.iloc[0]
        log.info("  MGB_CORE is a roll-up: not ranked; would be %s of %d as one department",
                 "n/a" if pd.isna(r.rank_if_single_entity) else int(r.rank_if_single_entity),
                 int(ranked.sum()))
        for member in ("MGH", "BWH"):
            mrow = out[out.canonical_org_id == member]
            if not mrow.empty and pd.notna(mrow.iloc[0]["rank"]):
                log.info("    %s alone ranks %d", member, int(mrow.iloc[0]["rank"]))
    return out


def figure(period: str, floor: str = "corroborated", top_n: int = 22) -> None:
    t = combined_table(period, floor)
    sub = t[t.is_ranked].head(top_n).copy()
    sub["label_rank"] = sub["rank"].map(lambda r: f"{int(r)}.")

    # A roll-up holds no place in the peer set, but leaving it off the chart
    # would hide the answer the table exists to give. It is drawn at the
    # position it would hold as one department, in (parentheses), never
    # numbered like a peer.
    roll = t[t.is_rollup & t.rank_if_single_entity.notna() & (t.total_funding > 0)].copy()
    if len(roll):
        roll = roll[roll.rank_if_single_entity <= top_n]
        roll["label_rank"] = roll.rank_if_single_entity.map(lambda r: f"({int(r)})")
        sub = pd.concat([sub, roll], ignore_index=True)
        sub = sub.sort_values("total_funding", ascending=False).head(top_n)

    colors = [HIGHLIGHT.get(i, BASE) for i in sub.canonical_org_id]
    # Hatching marks the evidence tier, not the institution. Marking only MGB
    # would have implied its peers were NIH-coded when they are reconstructed
    # by exactly the same rule.
    hatch = ["//" if b == RECONSTRUCTED_BASIS else "" for b in sub.evidence_basis]

    fig, ax = plt.subplots(figsize=(10.5, 0.36 * len(sub) + 2.6))
    y = np.arange(len(sub))
    bars = ax.barh(y, sub.total_funding, color=colors, edgecolor="white", linewidth=0.7)
    for b, h, is_roll in zip(bars, hatch, sub.is_rollup):
        if h:
            b.set_hatch(h)
        if is_roll:
            b.set_edgecolor("#16202a")
            b.set_linewidth(1.1)
            b.set_linestyle((0, (2.5, 1.5)))
    ax.set_yticks(y)
    ax.set_yticklabels([f"{lr} {_short(n, 40)}" for lr, n in zip(sub.label_rank, sub.display_name)])
    ax.invert_yaxis()
    span = sub.total_funding.max()
    for i, (v, n) in enumerate(zip(sub.total_funding, sub.award_years)):
        ax.text(v + span * 0.01, i, f"  {_money(v)} ({int(n)} award-years)",
                va="center", fontsize=8)
    ax.set_xlim(0, span * 1.36)
    from .pi_department import FLOORS
    from .figures import _period_label, _subtitle
    meta = FLOORS[floor]
    ax.set_title("Departments of surgery by NIH funding", fontsize=12.5)
    _subtitle(ax, _period_label(period)
              + f" · hatched: NIH codes no department for this recipient, so it was derived "
                f"from publication affiliations ({meta['rule']} rule, kappa {meta['kappa']:.3f}) "
                "and is a lower bound · a rank in (parentheses) is a multi-hospital roll-up "
                "shown where it would fall as one department, not a place in the ranking")
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    ax.legend(
        handles=[
            mpatches.Patch(facecolor=BASE, label="Department from NIH ORG_DEPT"),
            mpatches.Patch(facecolor=BASE, hatch="//",
                           label="Department reconstructed from publication affiliations"),
            mpatches.Patch(facecolor="none", edgecolor="#16202a", linestyle="--",
                           label="Roll-up over two or more hospitals (not ranked)"),
        ],
        frameon=False, fontsize=8, loc="lower right",
    )
    out = FIGURES / "mgb"
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"surgery_ranking_with_mgb_{period}_{floor}.png")
    plt.close(fig)
    log.info("  mgb/surgery_ranking_with_mgb_%s_%s.png", period, floor)


def build_all(cfg: dict) -> None:
    from .pi_department import FLOORS

    for period in cfg["reporting_periods"]:
        for floor in FLOORS:
            figure(period, floor)
