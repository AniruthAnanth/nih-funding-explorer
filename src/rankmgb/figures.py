"""Section 10 -- figure generation. Every figure is rebuilt from the ranked
tables in outputs/tables, never from an intermediate in memory, so a figure can
always be traced to the CSV behind it."""
from __future__ import annotations

import textwrap

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.ticker import FuncFormatter

from .paths import FIGURES, TABLES
from .util import get_logger

log = get_logger("figures")

plt.rcParams.update(
    {
        "figure.dpi": 130,
        "savefig.dpi": 160,
        "savefig.bbox": "tight",
        "font.size": 9,
        "axes.titlesize": 12.5,
        "axes.titleweight": "normal",
        "axes.titlelocation": "left",
        "axes.titlepad": 12,
        "axes.labelsize": 9.5,
        "axes.grid": False,
        "axes.axisbelow": True,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)

BASE = "#9BA7B4"


def _load_institution_colors() -> dict[str, str]:
    """Per-institution colours from reference/institution_colors_v1.csv.

    These approximate each institution's own brand colour so a reader can find
    their institution at a glance. They are identification colours, not official
    brand assets, and the reference table says so per row.
    """
    import pandas as _pd

    from .paths import REFERENCE as _REF

    path = _REF / "institution_colors_v1.csv"
    if not path.exists():
        return {}
    t = _pd.read_csv(path)
    return dict(zip(t.canonical_org_id, t.color))


HIGHLIGHT = _load_institution_colors()
MECH_ORDER = ["R01", "R_OTHER", "U", "P", "K", "T", "F", "M", "OTHER"]
MECH_COLORS = dict(
    zip(
        MECH_ORDER,
        ["#1B3A5C", "#2E6B9E", "#4E9AC4", "#7FBFD8", "#A8CBA0", "#D9B36A", "#C97C5D", "#8E5A78", "#B9BDC2"],
    )
)


RECON_NOTE = (
    " · hatched: NIH codes no department for this recipient; department reconstructed "
    "from publication affiliations (lower bound)"
)


def _period_label(period: str) -> str:
    return period.replace("FY2024_FY2025", "FY2024–FY2025").replace(
        "FY2021_FY2025", "FY2021–FY2025")


def _subtitle(ax, text: str, width: int = 128) -> None:
    """A quiet line under the title. Keeps the title itself to one short clause.

    The subtitle occupies the space immediately above the axes, so the title has
    to be pushed clear of it or the two render on the same line. A subtitle that
    has to name an evidence tier and a roll-up convention does not fit on one
    line at this width, and matplotlib will happily run it off the canvas rather
    than say so, so it is wrapped here and the title pad grows to match.
    """
    lines = textwrap.wrap(text, width=width) or [""]
    ax.text(0, 1.012, "\n".join(lines), transform=ax.transAxes, fontsize=8.8,
            color="#5a6672", va="bottom", ha="left", linespacing=1.35)
    # rcParams put titles on the left, and ax.get_title() defaults to the centre
    # title, which is empty. Asking for the wrong one silently skipped the pad
    # and the subtitle rendered on top of the title.
    for loc in ("left", "center"):
        title = ax.get_title(loc=loc)
        if title:
            ax.set_title(title, loc=loc, pad=14 + 12 * len(lines),
                         fontsize=plt.rcParams["axes.titlesize"])
            break


def _money(x, _pos=None) -> str:
    a = abs(x)
    if a >= 1e9:
        return f"${x/1e9:.2f}B"
    if a >= 1e6:
        # Per-investigator figures cluster between $1M and $4M, where whole
        # millions would print every bar as the same number.
        return f"${x/1e6:.1f}M" if a < 1e7 else f"${x/1e6:.0f}M"
    return f"${x/1e3:.0f}K"


def _colors(ids: pd.Series) -> list[str]:
    return [HIGHLIGHT.get(i, BASE) for i in ids]


def _labelled_barh(ax, labels, values, ids, fmt=_money, pad_frac=0.30) -> None:
    y = np.arange(len(labels))
    ax.barh(y, values, color=_colors(ids), edgecolor="white", linewidth=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels(labels)
    ax.invert_yaxis()
    span = max(values) if len(values) and max(values) > 0 else 1
    for i, v in enumerate(values):
        ax.text(v + span * 0.01, i, fmt(v), va="center", ha="left", fontsize=8, fontweight="bold")
    ax.set_xlim(0, span * (1 + pad_frac))


def _save(fig, name: str, subdir: str) -> None:
    out = FIGURES / subdir
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / name)
    plt.close(fig)
    log.info("  %s/%s", subdir, name)


def _short(name: str, n: int = 42) -> str:
    return name if len(name) <= n else name[: n - 1] + "…"


def _load(grain: str, period: str) -> pd.DataFrame:
    return pd.read_csv(TABLES / f"rank_{grain}_{period}.csv")


# ---------------------------------------------------------------------------


def _surgery_frame(period: str) -> tuple[pd.DataFrame, bool]:
    """US surgery departments, including every recipient NIH leaves uncoded.

    NIH codes no department for independent hospitals, so a table built from
    ORG_DEPT alone silently omits all of them — MGH and BWH, but equally
    Vanderbilt University Medical Center, Mayo Clinic Rochester, Memorial Sloan
    Kettering and the children's hospitals. Every surgery figure therefore
    reads the combined table when it exists, and marks the reconstructed rows
    so they are never mistaken for like-for-like measurements.
    """
    combined = TABLES / f"surgery_ranking_with_mgb_{period}_corroborated.csv"
    if combined.exists():
        s = pd.read_csv(combined)
        # MGH and BWH are retained in the underlying CSVs for audit, but every
        # published chart shows the single combined Mass General Brigham entity.
        s = s[~s.canonical_org_id.isin(("MGH", "BWH"))].copy()
        # Hatch by evidence tier rather than by institution: marking only MGB
        # would imply its uncoded peers were measured from NIH's own field.
        s["is_reconstructed"] = s.evidence_basis.fillna("").str.startswith("Reconstructed")
        return s, True
    d = _load("institution_department", period)
    s = d[(d.nih_org_dept == "SURGERY") & (d.org_country == "UNITED STATES")].copy()
    s["is_reconstructed"] = False
    log.warning(
        "%s: combined table absent, surgery figures will omit every uncoded recipient. "
        "Run the mgb stage.",
        period,
    )
    return s, False


def _hatch_reconstructed(ax, frame: pd.DataFrame) -> None:
    for bar, flag in zip(ax.patches, frame.is_reconstructed):
        if flag:
            bar.set_hatch("//")


def fig_surgery_rankings(period: str, top_n: int = 20) -> None:
    d, has_mgb = _surgery_frame(period)

    for metric, title, fmt, fname in [
        ("total_funding", "total NIH funding", _money, "1_surgery_total_funding"),
        ("award_years", "funded award-years", lambda v, _=None: f"{int(v)}", "2_surgery_award_years"),
        ("distinct_projects", "distinct core projects", lambda v, _=None: f"{int(v)}", "3_surgery_projects"),
        ("r01_funding", "R01 funding", _money, "4_surgery_r01_funding"),
        ("r01_award_years", "R01 award-years", lambda v, _=None: f"{int(v)}", "5_surgery_r01_count"),
        ("funded_investigators", "funded investigators", lambda v, _=None: f"{int(v)}", "6_surgery_investigators"),
    ]:
        if metric not in d.columns:
            continue
        sub = d.sort_values(metric, ascending=False).head(top_n)
        fig, ax = plt.subplots(figsize=(9.8, 0.34 * len(sub) + 1.9))
        _labelled_barh(ax, [_short(x) for x in sub.display_name], sub[metric].to_numpy(),
                       sub.canonical_org_id, fmt)
        _hatch_reconstructed(ax, sub)
        ax.set_title(f"Departments of surgery by {title}", fontsize=12.5)
        _subtitle(ax, _period_label(period) + (RECON_NOTE if has_mgb else ""))
        ax.set_xlabel("Total NIH funding" if metric.endswith("funding") else title.capitalize())
        if metric.endswith("funding"):
            ax.xaxis.set_major_formatter(FuncFormatter(_money))
        _save(fig, f"{fname}.png", period)


def fig_surgery_mechanism_mix(period: str, top_n: int = 15) -> None:
    d = _load("institution_department", period)
    s = d[(d.nih_org_dept == "SURGERY") & (d.org_country == "UNITED STATES")]
    s = s.sort_values("total_funding", ascending=False).head(top_n).iloc[::-1]

    fig, ax = plt.subplots(figsize=(10, 0.38 * len(s) + 1.8))
    left = np.zeros(len(s))
    y = np.arange(len(s))
    for fam in MECH_ORDER:
        col = f"funding_{fam}"
        if col not in s or s[col].sum() == 0:
            continue
        ax.barh(y, s[col], left=left, color=MECH_COLORS[fam], label=fam, edgecolor="white", linewidth=0.4)
        left += s[col].to_numpy()
    ax.set_yticks(y)
    ax.set_yticklabels([_short(x) for x in s.display_name])
    ax.set_title("Surgery departments: funding by activity-code family", fontsize=12.5)
    _subtitle(ax, _period_label(period))
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    ax.legend(ncol=5, fontsize=8, loc="lower right", frameon=False)
    _save(fig, "7_surgery_mechanism_mix.png", period)


def fig_surgery_intensity(period: str, top_n: int = 20) -> None:
    """Size-normalised view: dollars per NIH-funded investigator.

    Raw totals mostly rank departments by how large they are. This divides that
    out. The floor keeps a two-investigator unit off the top of the table.
    """
    d, has_mgb = _surgery_frame(period)
    s = d[d.meets_intensity_floor.astype(str).str.lower() == "true"] if "meets_intensity_floor" in d else d
    s = s.dropna(subset=["funding_per_investigator"])
    s = s.sort_values("funding_per_investigator", ascending=False).head(top_n)

    fig, ax = plt.subplots(figsize=(9.8, 0.34 * len(s) + 2.1))
    _labelled_barh(
        ax,
        [_short(x) for x in s.display_name],
        s.funding_per_investigator.to_numpy(),
        s.canonical_org_id,
    )
    _hatch_reconstructed(ax, s)
    ax.set_title("Surgery departments by NIH funding per funded investigator", fontsize=12.5)
    _subtitle(ax, _period_label(period) + " · at least 5 funded investigators"
              + (RECON_NOTE if has_mgb else ""))
    ax.set_xlabel("NIH funding per funded investigator")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    _save(fig, "14_surgery_funding_per_investigator.png", period)

    for metric, title, fname in [
        ("mean_award_size", "mean size of a funded award-year", "15_surgery_mean_award_size"),
        ("funding_per_project", "funding per distinct project", "16_surgery_funding_per_project"),
    ]:
        sub = d[d.award_years >= 5].dropna(subset=[metric])
        sub = sub.sort_values(metric, ascending=False).head(top_n)
        fig, ax = plt.subplots(figsize=(9.8, 0.34 * len(sub) + 2.1))
        _labelled_barh(ax, [_short(x) for x in sub.display_name], sub[metric].to_numpy(),
                       sub.canonical_org_id)
        _hatch_reconstructed(ax, sub)
        ax.set_title(f"Surgery departments by {title}", fontsize=12.5)
        _subtitle(ax, _period_label(period) + " · at least 5 award-years"
                  + (RECON_NOTE if has_mgb else ""))
        ax.set_xlabel(title.capitalize())
        ax.xaxis.set_major_formatter(FuncFormatter(_money))
        _save(fig, f"{fname}.png", period)


def fig_specialty_totals(period: str) -> None:
    d = _load("department", period)
    d = d[~d.specialty.isin(["UNKNOWN", "UNCLASSIFIED"])]
    d = d.sort_values("total_funding", ascending=False).head(28)
    fig, ax = plt.subplots(figsize=(9.5, 0.32 * len(d) + 1.5))
    y = np.arange(len(d))
    ax.barh(y, d.total_funding, color=["#B8352C" if x == "SURGERY" else BASE for x in d.nih_org_dept],
            edgecolor="white", linewidth=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels([_short(x, 34) for x in d.nih_org_dept])
    ax.invert_yaxis()
    span = d.total_funding.max()
    for i, v in enumerate(d.total_funding):
        ax.text(v + span * 0.01, i, _money(v), va="center", fontsize=8, fontweight="bold")
    ax.set_xlim(0, span * 1.22)
    ax.set_title("NIH funding by department", fontsize=12.5)
    _subtitle(ax, _period_label(period) + " · all institutions pooled")
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    _save(fig, "8_specialty_totals.png", period)


def fig_all_pairs(period: str, top_n: int = 30) -> None:
    """Where surgery sits among every institution-department pair, any specialty."""
    d = _load("institution_department", period)
    d = d[~d.specialty.isin(["UNKNOWN", "UNCLASSIFIED"])]
    d = d.sort_values("total_funding", ascending=False).head(top_n)
    labels = [f"{_short(n, 30)} — {_short(dep, 22)}" for n, dep in zip(d.display_name, d.nih_org_dept)]
    colors = [HIGHLIGHT.get(i, "#B8352C" if x == "SURGERY" else BASE)
              for i, x in zip(d.canonical_org_id, d.nih_org_dept)]
    fig, ax = plt.subplots(figsize=(11, 0.34 * len(d) + 1.6))
    y = np.arange(len(d))
    ax.barh(y, d.total_funding, color=colors, edgecolor="white", linewidth=0.6)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    span = d.total_funding.max()
    for i, v in enumerate(d.total_funding):
        ax.text(v + span * 0.01, i, _money(v), va="center", fontsize=8, fontweight="bold")
    ax.set_xlim(0, span * 1.2)
    ax.set_title("Top institution-department pairs, all specialties", fontsize=12.5)
    _subtitle(ax, _period_label(period) + " · surgery departments in red")
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    _save(fig, "9_all_institution_department_pairs.png", period)


def fig_institutions(period: str, top_n: int = 25) -> None:
    d = _load("institution", period)
    d = d[(d.org_country == "UNITED STATES") & (~d.is_rollup)]
    d = d.sort_values("total_funding", ascending=False).head(top_n)
    fig, ax = plt.subplots(figsize=(9.5, 0.34 * len(d) + 1.5))
    _labelled_barh(ax, [_short(x) for x in d.display_name], d.total_funding.to_numpy(), d.canonical_org_id)
    ax.set_title("Top US NIH recipient organizations", fontsize=12.5)
    _subtitle(ax, _period_label(period) + " · all departments")
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    _save(fig, "10_top_institutions.png", period)


def fig_r01_vs_non(period: str, top_n: int = 15) -> None:
    d = _load("institution_department", period)
    s = d[(d.nih_org_dept == "SURGERY") & (d.org_country == "UNITED STATES")]
    s = s.sort_values("total_funding", ascending=False).head(top_n).iloc[::-1]
    y = np.arange(len(s))
    fig, ax = plt.subplots(figsize=(9.5, 0.36 * len(s) + 1.6))
    ax.barh(y, s.r01_funding, color="#1B3A5C", label="R01 (incl. R37)", edgecolor="white", linewidth=0.5)
    ax.barh(y, s.non_r01_funding, left=s.r01_funding, color="#C9A227", label="All other mechanisms",
            edgecolor="white", linewidth=0.5)
    ax.set_yticks(y)
    ax.set_yticklabels([_short(x) for x in s.display_name])
    ax.set_title("R01 versus non-R01 funding, surgery departments", fontsize=12.5)
    _subtitle(ax, _period_label(period))
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    _save(fig, "11_surgery_r01_vs_non_r01.png", period)


def fig_fy_change(top_n: int = 15) -> None:
    a = _load("institution_department", "FY2025")
    prior = pd.read_csv(TABLES / "rank_institution_department_FY2024_FY2025.csv")
    a = a[(a.nih_org_dept == "SURGERY") & (a.org_country == "UNITED STATES")]
    p = prior[(prior.nih_org_dept == "SURGERY") & (prior.org_country == "UNITED STATES")]
    m = a.merge(p[["canonical_org_id", "total_funding"]], on="canonical_org_id", suffixes=("_25", "_2yr"))
    # FY2024 = two-year window minus FY2025
    m["fy2024"] = m.total_funding_2yr - m.total_funding_25
    m["delta"] = m.total_funding_25 - m.fy2024
    m = m.reindex(m.delta.abs().sort_values(ascending=False).index).head(top_n).sort_values("delta")
    fig, ax = plt.subplots(figsize=(9.5, 0.36 * len(m) + 1.6))
    y = np.arange(len(m))
    ax.barh(y, m.delta, color=["#B8352C" if v < 0 else "#2E7D5B" for v in m.delta],
            edgecolor="white", linewidth=0.5)
    ax.axvline(0, color="#444", linewidth=0.9)
    ax.set_yticks(y)
    ax.set_yticklabels([_short(x) for x in m.display_name])
    for i, v in enumerate(m.delta):
        ax.text(v + (abs(m.delta).max() * 0.015 * (1 if v >= 0 else -1)), i, _money(v),
                va="center", ha="left" if v >= 0 else "right", fontsize=8, fontweight="bold")
    lim = abs(m.delta).max() * 1.35
    ax.set_xlim(-lim, lim)
    ax.set_title("Change in NIH funding, FY2024 to FY2025", fontsize=12.5)
    _subtitle(ax, "Surgery departments · largest absolute movers")
    ax.set_xlabel("Change in NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    _save(fig, "12_surgery_fy2024_to_fy2025.png", "comparisons")


def fig_period_comparison(top_n: int = 12) -> None:
    frames = {}
    for period in ["FY2025", "FY2024_FY2025", "FY2021_FY2025"]:
        d = _load("institution_department", period)
        d = d[(d.nih_org_dept == "SURGERY") & (d.org_country == "UNITED STATES")]
        frames[period] = d.set_index("canonical_name").total_funding
    order = frames["FY2021_FY2025"].sort_values(ascending=False).head(top_n).index[::-1]
    y = np.arange(len(order))
    h = 0.26
    fig, ax = plt.subplots(figsize=(10, 0.55 * len(order) + 1.8))
    for k, (period, colour) in enumerate(
        zip(["FY2025", "FY2024_FY2025", "FY2021_FY2025"], ["#4E9AC4", "#2E6B9E", "#1B3A5C"])
    ):
        vals = frames[period].reindex(order).fillna(0)
        ax.barh(y + (k - 1) * h, vals, height=h, color=colour,
                label=period.replace("_", " to "), edgecolor="white", linewidth=0.4)
    ax.set_yticks(y)
    ax.set_yticklabels([_short(x) for x in order])
    ax.set_title("Surgery departments across the reporting windows", fontsize=12.5)
    _subtitle(ax, "One-, two- and five-year totals")
    ax.set_xlabel("NIH funding")
    ax.xaxis.set_major_formatter(FuncFormatter(_money))
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    _save(fig, "13_surgery_period_comparison.png", "comparisons")


def fig_coverage() -> None:
    path = TABLES / "coverage_department_evidence.csv"
    if not path.exists():
        return
    c = pd.read_csv(path)
    piv = c.pivot(index="fiscal_year", columns="dept_tier", values="funding").fillna(0)
    order = [x for x in ["A_NIH_ORG_DEPT", "B_NIH_LINKED", "UNRESOLVED"] if x in piv.columns]
    labels = {
        "A_NIH_ORG_DEPT": "NIH supplies a department code",
        "B_NIH_LINKED": "Inferred from the same PI's dated NIH department elsewhere",
        "UNRESOLVED": "No department evidence in NIH data",
    }
    colours = {"A_NIH_ORG_DEPT": "#2E6B9E", "B_NIH_LINKED": "#A8CBA0", "UNRESOLVED": "#B8352C"}
    fig, ax = plt.subplots(figsize=(9, 4.6))
    bottom = np.zeros(len(piv))
    x = np.arange(len(piv))
    for tier in order:
        ax.bar(x, piv[tier], bottom=bottom, color=colours[tier], label=labels[tier],
               edgecolor="white", linewidth=0.6)
        bottom += piv[tier].to_numpy()
    ax.set_xticks(x)
    ax.set_xticklabels(piv.index.astype(int))
    ax.set_title("A quarter of NIH funding cannot be attributed to a department", fontsize=12.5)
    _subtitle(ax, "NIH does not department-code independent hospitals, including MGH and BWH")
    ax.set_ylabel("NIH funding")
    ax.yaxis.set_major_formatter(FuncFormatter(_money))
    ax.legend(frameon=False, fontsize=8, loc="lower center", bbox_to_anchor=(0.5, -0.32))
    _save(fig, "0_department_evidence_coverage.png", "comparisons")


def build_all(cfg: dict) -> None:
    for period in cfg["reporting_periods"]:
        log.info("figures for %s", period)
        fig_surgery_rankings(period)
        fig_surgery_intensity(period)
        fig_surgery_mechanism_mix(period)
        fig_specialty_totals(period)
        fig_all_pairs(period)
        fig_institutions(period)
        fig_r01_vs_non(period)
    log.info("comparison figures")
    fig_fy_change()
    fig_period_comparison()
    fig_coverage()
