#!/usr/bin/env python3
"""Re-derive every numeric claim sent to a reader, from the published tables.

Run this before sending anything. It exists because a claim can be true of the
data and still be wrong on the page: the two failures this catches are quoting
a number that has since been regenerated, and pairing a correct numerator with
the wrong denominator. Both happened here. "The reconstruction recovers $10.86M,
77.7% of BRIMR's figure" was two true numbers and a false sentence -- $10.86M is
75.36% of BRIMR's total, and 77.70% is what you get after adding the $336,630
NIH codes directly.

Every row prints the claim, the value re-derived now, and the file it came from,
so a reader who doubts a figure can open that file and check it. Exit status is
non-zero if any claim fails.

    python3 docs/validation/verify_email_claims.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
pd.set_option("future.no_silent_downcasting", True)

TABLES = ROOT / "outputs" / "tables"
PROCESSED = ROOT / "data" / "processed"
REF = ROOT / "reference" / "external"

RESULTS: list[dict] = []


def claim(what: str, expected, got, source: str, tol: float = 0.0) -> None:
    if isinstance(expected, (int, float)) and isinstance(got, (int, float)):
        ok = abs(float(got) - float(expected)) <= tol
    else:
        ok = str(expected) == str(got)
    RESULTS.append({"claim": what, "expected": expected, "derived": got,
                    "source": source, "ok": ok})


# --------------------------------------------------------------------------
# The published tables the email quotes.
t5 = pd.read_csv(TABLES / "surgery_ranking_with_mgb_FY2021_FY2025_corroborated.csv")
i5 = pd.read_csv(TABLES / "rank_institution_FY2021_FY2025.csv")
i1 = pd.read_csv(TABLES / "rank_institution_FY2025.csv")
bench = pd.read_csv(TABLES / "external_benchmark_brimr_surgery_2025.csv")
brimr = pd.read_csv(REF / "brimr_surgery_2025.csv")
selfrep = pd.read_csv(REF / "self_reported_departments_v1.csv")
awards = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")

S5 = "outputs/tables/surgery_ranking_with_mgb_FY2021_FY2025_corroborated.csv"
SB = "outputs/tables/external_benchmark_brimr_surgery_2025.csv"
SI5 = "outputs/tables/rank_institution_FY2021_FY2025.csv"
SI1 = "outputs/tables/rank_institution_FY2025.csv"

core = t5[t5.canonical_org_id == "MGB_CORE"].iloc[0]
row = lambda name: t5[t5.display_name == name].iloc[0]

# --- MGB's own departmental figures -------------------------------------
claim("MGB Surgery total funding FY2021-FY2025", 266_033_307, int(core.total_funding), S5)
claim("MGB Surgery distinct grants", 134, int(core.distinct_projects), S5)
claim("MGB Surgery award-years", 364, int(core.award_years), S5)
claim("MGB Surgery distinct R01 grants", 56, int(core.r01_distinct_projects), S5)
claim("MGB Surgery R01 award-years", 179, int(core.r01_award_years), S5)
claim("MGB Surgery would rank 1st of 119 on funding", "1 of 119",
      f"{int(core.rank_if_single_entity)} of {int(core.n_ranked)}", S5)
claim("MGB Surgery would rank 2nd on distinct R01s", 2,
      int(core.rank_r01_distinct_projects_if_single_entity), S5)
claim("MGB Surgery would rank 1st on R01 award-years", 1,
      int(core.rank_r01_award_years_if_single_entity), S5)
claim("MGB Surgery would rank 1st on grant count", 1,
      int(core.rank_distinct_projects_if_single_entity), S5)

# --- the peers named in the email ---------------------------------------
for name, usd, rk, grants, r01s in [
        ("Duke", 220_811_823, 1, 97, 34),
        ("WashU St. Louis", 154_066_689, 3, 65, 39),
        ("Michigan", 131_746_006, 4, 106, 59),
        ("Massachusetts General Hospital", 160_711_713, 2, 102, 40),
        ("Brigham and Women's Hospital", 105_321_594, 5, 32, 16)]:
    r = row(name)
    claim(f"{name} surgery funding", usd, int(r.total_funding), S5)
    claim(f"{name} surgery rank", rk, int(r["rank"]), S5)
    claim(f"{name} surgery distinct grants", grants, int(r.distinct_projects), S5)
    claim(f"{name} surgery distinct R01s", r01s, int(r.r01_distinct_projects), S5)

claim("MGH + BWH award-years sum to MGB's", int(core.award_years),
      int(row("Massachusetts General Hospital").award_years
          + row("Brigham and Women's Hospital").award_years), S5)
claim("MGH + BWH funding sums to MGB's", int(core.total_funding),
      int(row("Massachusetts General Hospital").total_funding
          + row("Brigham and Women's Hospital").total_funding), S5)

# --- the national recipient claim ---------------------------------------
mgb5 = i5[i5.canonical_org_id == "MGB_CORE"].iloc[0]
mgb1 = i1[i1.canonical_org_id == "MGB_CORE"].iloc[0]
claim("MGB total NIH FY2021-FY2025 ($5.11B)", 5.107, round(mgb5.total_funding / 1e9, 3), SI5, 0.001)
claim("MGB total NIH FY2025 ($1.05B)", 1.050, round(mgb1.total_funding / 1e9, 3), SI1, 0.001)
claim("Johns Hopkins FY2021-FY2025 ($4.13B)", 4.129,
      round(float(i5[i5.display_name == "Johns Hopkins"].total_funding.iloc[0]) / 1e9, 3), SI5, 0.001)
claim("MGB is 1st nationally only as a roll-up", 1,
      int(mgb5.rank_total_funding_if_single_entity), SI5)
claim("MGH alone ranks 7th nationally", 7,
      int(i5[i5.canonical_org_id == "MGH"].rank_total_funding.iloc[0]), SI5)
claim("BWH alone ranks 22nd nationally", 22,
      int(i5[i5.canonical_org_id == "BWH"].rank_total_funding.iloc[0]), SI5)

# --- Department of Medicine ---------------------------------------------
med = pd.read_csv(TABLES / "mgb_departments_all.csv")
med = med[(med.period == "FY2021_FY2025") & (med.nih_org_dept == "INTERNAL MEDICINE/MEDICINE")]
claim("MGB Medicine FY2021-FY2025 ($1.66B)", 1658.1,
      round(float(med[med.entity == "MGB_CORE"].total_funding.iloc[0]) / 1e6, 1),
      "outputs/tables/mgb_departments_all.csv", 0.1)

# --- M-series ------------------------------------------------------------
claim("no M-series activity code exists FY2021-FY2025", 0,
      int(awards.activity_code.astype(str).str.startswith("M").sum()),
      "data/processed/award_years_annotated.parquet")
claim("no award-year is in mechanism family M", 0,
      int((awards.mechanism_family == "M").sum()),
      "data/processed/award_years_annotated.parquet")

# --- Blue Ridge agreement ------------------------------------------------
paired = bench[bench.brimr_usd.notna()]
claim("Blue Ridge institutions in their FY2025 surgery table", 75, len(brimr),
      "reference/external/brimr_surgery_2025.csv")
claim("of those, paired to a figure here", 74, len(paired), SB)
exact_aw = int(paired.as_awarded_usd.sub(paired.brimr_usd).abs().lt(1).sum())
claim("agree to the dollar, as awarded", 55, exact_aw, SB)
claim("agree to within 1%, as awarded", 60,
      int(paired.as_awarded_usd.sub(paired.brimr_usd).abs()
          .div(paired.brimr_usd).le(0.01).sum()), SB)
claim("agree to the dollar under BRIMR's conventions", 57,
      int(paired.agrees_to_the_dollar.sum()), SB)

for nm, usd in [("WASHINGTON UNIVERSITY ST LOUIS", 31_820_064),
                ("UNIVERSITY OF CALIFORNIA SAN FRANCISCO", 27_918_696),
                ("STANFORD UNIVERSITY", 17_940_324),
                ("UNIVERSITY OF MINNESOTA", 16_514_952),
                ("JOHNS HOPKINS UNIVERSITY", 12_290_262),
                ("COLUMBIA UNIVERSITY HEALTH SCIENCES", 11_205_970),
                ("CORNELL UNIVERSITY WEILL MEDICAL COLLEGE", 10_000_633),
                ("UNIVERSITY OF NORTH CAROLINA CHAPEL HILL", 7_865_510),
                ("RUTGERS, THE STATE UNIVERSITY OF NEW JERSEY", 7_245_955),
                ("INDIANA UNIVERSITY", 6_633_379),
                ("UNIVERSITY OF TEXAS SOUTHWESTERN DALLAS", 5_144_776),
                ("UNIVERSITY OF CALIFORNIA LOS ANGELES", 7_314_362),
                ("UNIVERSITY OF CALIFORNIA SAN DIEGO", 9_176_097)]:
    r = bench[bench.brimr_institution == nm]
    if r.empty:
        claim(f"named as an exact match: {nm}", "present", "MISSING FROM BENCHMARK", SB)
        continue
    r = r.iloc[0]
    claim(f"{nm} matches Blue Ridge to the dollar", usd, int(r.as_awarded_usd), SB)
    claim(f"  ... and Blue Ridge itself says", usd, int(r.brimr_usd),
          "reference/external/brimr_surgery_2025.csv")

for nm, diff in [("DUKE UNIVERSITY", 15_375), ("UNIVERSITY OF MICHIGAN ANN ARBOR", -10_795)]:
    r = bench[bench.brimr_institution == nm].iloc[0]
    claim(f"{nm} differs by ${diff:,}", diff, int(r.as_awarded_usd - r.brimr_usd), SB)

# --- the two hospital calibrations --------------------------------------
sr = pd.read_csv(TABLES / "external_benchmark_self_reported.csv")
srow = sr.iloc[0]
claim("MGH Surgery, published NIH figure for 2024", 35_000_000, int(srow.self_reported_usd),
      "reference/external/self_reported_departments_v1.csv")
claim("MGH Surgery, this analysis FY2024", 30_750_166, int(srow.reconstructed_usd),
      "outputs/tables/external_benchmark_self_reported.csv")
claim("MGH recovery", 87.9, round(100 * srow.reconstructed_usd / srow.self_reported_usd, 1),
      "outputs/tables/external_benchmark_self_reported.csv", 0.05)
claim("MGH total research expenditures 2024", 74_000_000,
      int(selfrep[selfrep.metric == "total_research_expenditures"].value_usd.iloc[0]),
      "reference/external/self_reported_departments_v1.csv")
claim("NIH is 47% of MGH Surgery research spending", 47,
      round(100 * 35_000_000 / 74_000_000),
      "reference/external/self_reported_departments_v1.csv")

from rankmgb import external  # noqa: E402
from rankmgb.mgb_surgery import DOS  # noqa: E402

v_brimr = float(brimr[brimr.name == "VANDERBILT UNIVERSITY"].surgery_usd.iloc[0])
v_coded = float(awards[(awards.canonical_org_id == "IPF8721001")
                       & (awards.nih_org_dept == "SURGERY")
                       & (awards.fiscal_year == 2025)].total_cost.sum())
v_recon = external._reconstructed_surgery("IPF10040927", 2025)
claim("Blue Ridge Vanderbilt surgery FY2025", 14_408_389, int(v_brimr),
      "reference/external/brimr_surgery_2025.csv")
claim("NIH-coded Vanderbilt University surgery FY2025", 336_630, int(v_coded),
      "data/processed/award_years_annotated.parquet")
claim("VUMC reconstruction FY2025", 10_858_628, int(v_recon),
      "data/processed/pi_departments.parquet")
claim("Vanderbilt recovery, coded + reconstructed over BRIMR total", 77.70,
      round(100 * (v_coded + v_recon) / v_brimr, 2),
      "derived from the three rows above", 0.02)
claim("NOT the figure to quote: reconstruction alone over BRIMR total", 75.36,
      round(100 * v_recon / v_brimr, 2), "derived; kept here so the two cannot be confused", 0.02)

# --- scope counts the email uses ---------------------------------------
claim("ranked departments in the surgery table", 119, int(core.n_ranked), S5)
# Two different counts, both correct, for two different sheets. Boston Medical
# Center has surgical funding across the five-year window but none in FY2025.
t25 = pd.read_csv(TABLES / "surgery_ranking_with_mgb_FY2025_corroborated.csv")
r25 = t25[t25.is_reconstructed.astype(bool) & (t25.total_funding > 0)]
r5 = t5[t5.is_reconstructed.astype(bool) & (t5.total_funding > 0)]
claim("FY2025 sheet: reconstructed hospitals, excluding the roll-up", 18,
      int((~r25.is_rollup.astype(bool)).sum()),
      "outputs/tables/surgery_ranking_with_mgb_FY2025_corroborated.csv")
claim("FY2025 sheet: shaded rows including the roll-up", 19, len(r25),
      "outputs/tables/surgery_ranking_with_mgb_FY2025_corroborated.csv")
claim("five-year sheet: reconstructed hospitals, excluding the roll-up", 19,
      int((~r5.is_rollup.astype(bool)).sum()), S5)
claim("uncoded recipients profiled", 27,
      pd.read_parquet(PROCESSED / "pi_departments.parquet").institution_id.nunique(),
      "data/processed/pi_departments.parquet")

# --- report --------------------------------------------------------------
res = pd.DataFrame(RESULTS)
res.to_csv(ROOT / "outputs" / "tables" / "email_claim_verification.csv", index=False)
width = max(len(str(r["claim"])) for r in RESULTS) + 2
print(f"\n{'CLAIM':<{width}}{'QUOTED':>18}{'DERIVED NOW':>18}   SOURCE")
print("-" * (width + 40))
for r in RESULTS:
    mark = " " if r["ok"] else "X"
    print(f"{mark}{str(r['claim']):<{width-1}}{str(r['expected']):>18}{str(r['derived']):>18}   {r['source']}")
bad = res[~res.ok]
print("-" * (width + 40))
print(f"{len(res)} claims checked, {len(bad)} failed")
if len(bad):
    print("\nFAILURES:")
    print(bad[["claim", "expected", "derived", "source"]].to_string(index=False))
sys.exit(1 if len(bad) else 0)
