"""Sections 3, 7, 9 -- attach mechanism family, department specialty, and
canonical organization identity to the award-year table.

Every mapping comes from a dated reference table under reference/. Nothing is
classified by logic embedded here; this module only joins and reports coverage.
"""
from __future__ import annotations

import pandas as pd

from . import names
from .paths import PROCESSED, REFERENCE
from .util import PipelineError, get_logger

log = get_logger("annotate")


def load_reference(cfg: dict) -> dict[str, pd.DataFrame]:
    r = cfg["reference_tables"]
    ref = {
        "mechanism": pd.read_csv(REFERENCE / "mechanism_crosswalk_v1.csv"),
        "nih_dept": pd.read_csv(REFERENCE / "nih_department_crosswalk_v1.csv"),
        "taxonomy": pd.read_csv(REFERENCE / "surgical_taxonomy_v1.csv"),
        "orgs": pd.read_csv(REFERENCE / "organization_crosswalk_v1.csv", dtype={"org_ipf_code": str}),
        "rollups": pd.read_csv(REFERENCE / "rollups_v1.csv"),
    }
    if ref["mechanism"].activity_code.duplicated().any():
        raise PipelineError("mechanism crosswalk maps an activity code more than once")
    if ref["nih_dept"].nih_org_dept.duplicated().any():
        raise PipelineError("NIH department crosswalk maps a department more than once")
    _ = r
    return ref


def annotate(df: pd.DataFrame, cfg: dict, ref: dict[str, pd.DataFrame]) -> pd.DataFrame:
    out = df.copy()

    # --- Section 3: mechanism family -------------------------------------
    mech = ref["mechanism"][["activity_code", "mechanism_family", "is_r01"]]
    out = out.merge(mech, on="activity_code", how="left")
    unmapped = out[out.mechanism_family.isna()]
    if len(unmapped):
        codes = sorted(unmapped.activity_code.unique())
        log.warning(
            "%s award-years carry %d activity codes absent from the crosswalk: %s. "
            "They are classified UNMAPPED and reported separately, never folded into OTHER.",
            f"{len(unmapped):,}",
            len(codes),
            ", ".join(codes[:25]),
        )
        pd.DataFrame({"activity_code": codes}).to_csv(
            REFERENCE / "_unmapped_activity_codes.csv", index=False
        )
    out["mechanism_family"] = out.mechanism_family.fillna("UNMAPPED")
    out["is_r01"] = out.is_r01.astype("boolean").astype("boolean").fillna(False).astype(bool)
    # First letter of the activity code, for the coarse series view.
    out["activity_series"] = out.activity_code.str[0]

    # --- Section 7: department specialty ---------------------------------
    dept = ref["nih_dept"][["nih_org_dept", "specialty", "surgical_narrow", "surgical_broad"]]
    out = out.merge(dept, on="nih_org_dept", how="left")
    miss = out.specialty.isna()
    if miss.any():
        vals = sorted(out.loc[miss, "nih_org_dept"].unique())
        log.warning(
            "%s award-years carry department values absent from the crosswalk: %s. "
            "Classified UNKNOWN; never counted as surgical.",
            f"{miss.sum():,}",
            ", ".join(vals[:20]),
        )
    out["specialty"] = out.specialty.fillna("UNKNOWN")
    out["surgical_narrow"] = out.surgical_narrow.astype("boolean").fillna(False).astype(bool)
    out["surgical_broad"] = out.surgical_broad.astype("boolean").fillna(False).astype(bool)
    out["dept_evidence_source"] = "NIH_ORG_DEPT"
    out.loc[out.nih_org_dept.isin(["__MISSING__", "NONE", "MISCELLANEOUS"]), "dept_evidence_source"] = "NONE"

    # --- Section 9: canonical organization -------------------------------
    orgs = ref["orgs"][
        ["org_ipf_code", "canonical_org_id", "canonical_name", "parent_system_id", "parent_system_name"]
    ]
    out = out.merge(orgs, on="org_ipf_code", how="left")
    # Default identity: the stable NIH IPF code, labelled with its modal name.
    # The crosswalk exists to document deviations, not to enumerate every org.
    modal = (
        out.groupby("org_ipf_code").org_name_raw.agg(lambda s: s.value_counts().index[0]).rename("modal_name")
    )
    out = out.merge(modal, on="org_ipf_code", how="left")
    out["canonical_org_id"] = out.canonical_org_id.fillna("IPF" + out.org_ipf_code.astype(str))
    out["canonical_name"] = out.canonical_name.fillna(out.modal_name)
    out = out.drop(columns=["modal_name"])
    # Readable label for every chart and table. The legal recipient name is
    # retained in canonical_name; this is presentation only.
    out["display_name"] = names.display_names(out.canonical_org_id, out.canonical_name)

    log.info(
        "annotated %s award-years | %d mechanism families | %d specialties | %d canonical orgs",
        f"{len(out):,}",
        out.mechanism_family.nunique(),
        out.specialty.nunique(),
        out.canonical_org_id.nunique(),
    )
    out.to_parquet(PROCESSED / "award_years_annotated.parquet", index=False)
    return out
