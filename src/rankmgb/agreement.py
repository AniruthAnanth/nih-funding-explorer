"""How far the publication-derived department agrees with NIH's own.

The publication method is the primary measurement because it is the only one
that covers hospitals and universities alike. NIH's ORG_DEPT is then an
independent second opinion available on the subset of award-years where NIH
supplies one, which makes it a validation set rather than the measurement.

Reported per institution and overall:

  sensitivity  of the award-years NIH calls surgical, the share the publication
               method also calls surgical. This is the number that says how much
               a publication-derived total undercounts.
  precision    of the award-years the publication method calls surgical, the
               share NIH agrees with.
  Cohen's κ    chance-corrected agreement on the surgical / not-surgical call.

An institution NIH does not department-code has no NIH label to compare
against, so it contributes to coverage but not to agreement. That is stated in
the output rather than hidden by dropping the rows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .affiliation import UNUSABLE_DEPT
from .paths import TABLES
from .util import get_logger

log = get_logger("agreement")

NIH_SURGICAL_DEPTS = {
    "SURGERY", "NEUROSURGERY", "ORTHOPEDICS", "OTOLARYNGOLOGY", "UROLOGY", "PLASTIC SURGERY",
}


def _kappa(a: pd.Series, b: pd.Series) -> float:
    n = len(a)
    if n == 0:
        return float("nan")
    po = float((a == b).mean())
    pa1, pb1 = float(a.mean()), float(b.mean())
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    return float("nan") if pe == 1 else (po - pe) / (1 - pe)


def _rates(sub: pd.DataFrame) -> dict:
    nih = sub.nih_surgical
    pub = sub.pub_surgical
    tp = int((nih & pub).sum())
    fn = int((nih & ~pub).sum())
    fp = int((~nih & pub).sum())
    tn = int((~nih & ~pub).sum())
    return {
        "comparable_award_years": len(sub),
        "nih_surgical": int(nih.sum()),
        "pub_surgical": int(pub.sum()),
        "both": tp,
        "nih_only": fn,
        "publication_only": fp,
        "neither": tn,
        "sensitivity_pct": round(100 * tp / (tp + fn), 1) if (tp + fn) else np.nan,
        "precision_pct": round(100 * tp / (tp + fp), 1) if (tp + fp) else np.nan,
        "raw_agreement_pct": round(100 * (tp + tn) / len(sub), 1) if len(sub) else np.nan,
        "cohens_kappa": round(_kappa(nih, pub), 3),
    }


def run(attribution: pd.DataFrame) -> dict[str, pd.DataFrame]:
    d = attribution.copy()
    d["has_nih_dept"] = ~d.nih_org_dept.isin(UNUSABLE_DEPT)
    d["nih_surgical"] = d.nih_org_dept.isin(NIH_SURGICAL_DEPTS)
    d["pub_surgical"] = d.pub_is_surgical_narrow.fillna(False).astype(bool)

    comparable = d[d.has_nih_dept]
    overall = pd.DataFrame([{"scope": "all institutions with an NIH department code",
                             **_rates(comparable)}])

    per = (
        comparable.groupby(["canonical_org_id", "display_name"])
        .apply(lambda g: pd.Series(_rates(g)), include_groups=False)
        .reset_index()
        .sort_values("nih_surgical", ascending=False)
    )

    # Institutions with no NIH department code at all: no comparison is
    # possible, and saying so is more useful than omitting them.
    uncoded = (
        d[~d.has_nih_dept].groupby(["canonical_org_id", "display_name"])
        .agg(award_years=("application_id", "size"),
             publication_surgical=("pub_surgical", "sum"))
        .reset_index().sort_values("award_years", ascending=False)
    )
    uncoded["note"] = "NIH supplies no department code; no agreement can be computed"

    overall.to_csv(TABLES / "agreement_overall.csv", index=False)
    per.to_csv(TABLES / "agreement_by_institution.csv", index=False)
    uncoded.to_csv(TABLES / "agreement_uncomparable.csv", index=False)

    r = overall.iloc[0]
    log.info(
        "agreement on %s comparable award-years: sensitivity %.1f%%, precision %.1f%%, "
        "kappa %.3f",
        f"{int(r.comparable_award_years):,}", r.sensitivity_pct, r.precision_pct, r.cohens_kappa,
    )
    log.info(
        "%s award-years at %d institutions have no NIH department code to compare against",
        f"{int(uncoded.award_years.sum()):,}", len(uncoded),
    )
    return {"overall": overall, "by_institution": per, "uncomparable": uncoded}
