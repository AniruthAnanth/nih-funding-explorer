"""Score the shipped department rule against NIH's own department field.

NIH publishes a department code only for recipients it classifies as schools,
so roughly 30% of US NIH dollars sit at recipients whose departments have to be
derived some other way. This measures whether the substitute agrees with NIH
where both exist, which is what licenses using it where only one does.

The harness calls `rankmgb.pi_department.profile_one` directly rather than
reimplementing the matcher, so what is scored here is what ships.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pandas as pd

from rankmgb.mgb_surgery import NARROW, _patterns
from rankmgb.paths import PROCESSED, ROOT
from rankmgb.pi_department import profile_one
from rankmgb.pubmed_evidence import INSTITUTION_PATTERNS, _REG

NIH_SURGICAL = {"SURGERY", "NEUROSURGERY", "ORTHOPEDICS", "OTOLARYNGOLOGY",
                "UROLOGY", "PLASTIC SURGERY"}
NON_DEPARTMENT = {"__MISSING__", "NONE", "MISCELLANEOUS", "NO CODE ASSIGNED"}
PER_ARM = 150
SEED = 7


def sample_pis() -> pd.DataFrame:
    qmap = dict(zip(_REG.canonical_org_id, _REG.pubmed_query))
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    pis = pd.read_parquet(PROCESSED / "pi_links.parquet")
    pis = pis[pis.is_contact_pi].drop(
        columns=[c for c in ("fiscal_year", "org_ipf_code") if c in pis.columns])
    d = pis.merge(df[["application_id", "canonical_org_id", "nih_org_dept"]], on="application_id")
    d = d[d.canonical_org_id.isin(qmap) & ~d.nih_org_dept.isin(NON_DEPARTMENT)]
    g = (d.groupby(["canonical_org_id", "pi_name_raw"])
           .nih_org_dept.agg(lambda s: s.value_counts().index[0])
           .rename("nih_dept").reset_index())
    g["nih_surgical"] = g.nih_dept.isin(NIH_SURGICAL)
    # Both arms are oversampled to equal size so precision and sensitivity are
    # both estimated on enough cases to mean something.
    surg = g[g.nih_surgical].sample(min(PER_ARM, int(g.nih_surgical.sum())), random_state=SEED)
    non = g[~g.nih_surgical].sample(PER_ARM, random_state=SEED)
    return pd.concat([surg, non]).reset_index(drop=True)


def kappa(a: pd.Series, b: pd.Series) -> float:
    po = float((a == b).mean())
    pa, pb = float(a.mean()), float(b.mean())
    pe = pa * pb + (1 - pa) * (1 - pb)
    return float("nan") if pe >= 1 else (po - pe) / (1 - pe)


def main() -> int:
    random.seed(SEED)
    samp = sample_pis()
    qmap = dict(zip(_REG.canonical_org_id, _REG.pubmed_query))
    pats = _patterns()
    print(f"sample: {len(samp)} PI-institution pairs "
          f"({int(samp.nih_surgical.sum())} NIH-surgical, "
          f"{int((~samp.nih_surgical).sum())} not)", flush=True)

    rows = []
    for i, r in enumerate(samp.itertuples(index=False), 1):
        rec = profile_one(r.pi_name_raw, r.canonical_org_id, qmap[r.canonical_org_id],
                          INSTITUTION_PATTERNS[r.canonical_org_id], pats)
        rec["nih_surgical"] = bool(r.nih_surgical)
        rec["nih_dept"] = r.nih_dept
        rows.append(rec)
        if i % 40 == 0:
            print(f"  {i}/{len(samp)}", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(Path(__file__).with_name("validation_sample.csv"), index=False)
    res = out[out.n_affiliations > 0].copy()
    print(f"\nresolved {len(res)} of {len(samp)} sampled PIs "
          f"({100*len(res)/len(samp):.0f}%)\n", flush=True)

    print(f"{'rule':<34}{'n':>6}{'sens%':>8}{'prec%':>8}{'kappa':>8}")
    for name, pred in [
        ("any surgical affiliation", res.surgical_share > 0),
        ("modal department surgical", res.modal_department.isin(NARROW)),
        ("majority (>50%) — shipped", res.is_surgical.astype(bool)),
        ("share > 70%", res.surgical_share > 0.7),
    ]:
        tp = int((res.nih_surgical & pred).sum())
        fn = int((res.nih_surgical & ~pred).sum())
        fp = int((~res.nih_surgical & pred).sum())
        sens = 100 * tp / (tp + fn) if tp + fn else float("nan")
        prec = 100 * tp / (tp + fp) if tp + fp else float("nan")
        print(f"{name:<34}{len(res):>6}{sens:>8.1f}{prec:>8.1f}"
              f"{kappa(res.nih_surgical, pred):>8.3f}")
    _ = ROOT
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
