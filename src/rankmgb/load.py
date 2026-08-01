"""Sections 2, 3, 9 -- load archived files into the analysis-ready award-year table.

Produces two artifacts:
  data/processed/award_years.parquet   one row per APPLICATION_ID (the primary
                                       observation), post-inclusion-rules
  data/processed/pi_links.parquet      one row per (APPLICATION_ID, PI), the
                                       raw material for the any-PI models

Every inclusion rule is applied as a named, counted filter and the counts are
written to logs/inclusion_audit.csv so the exclusion cascade is reproducible.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .paths import PROCESSED, RAW, REFERENCE, LOGS
from .schema import REQUIRED_COLUMNS, validate_columns, validate_row_count
from .util import PipelineError, get_logger

log = get_logger("load")

_CONTACT = re.compile(r"\s*\(contact\)\s*", flags=re.IGNORECASE)


def _read_year(fy: int) -> pd.DataFrame:
    zpath = RAW / f"RePORTER_PRJ_C_FY{fy}.zip"
    if not zpath.exists():
        raise PipelineError(f"{zpath} missing. Run `make acquire` first.")
    with zipfile.ZipFile(zpath) as zf:
        member = zf.namelist()[0]
        with zf.open(member) as fh:
            df = pd.read_csv(fh, encoding="latin-1", low_memory=False, dtype=str)
    validate_columns(fy, list(df.columns))
    validate_row_count(fy, len(df))
    df = df[list(REQUIRED_COLUMNS)].copy()
    log.info("FY%s: read %s rows from %s", fy, f"{len(df):,}", member)
    return df


def _numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.replace(",", "", regex=False), errors="coerce")


def _dates(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce", format="mixed")


def apply_inclusion_rules(df: pd.DataFrame, cfg: dict, fy: int) -> tuple[pd.DataFrame, list[dict]]:
    """Each rule is counted so the audit trail shows what every filter removed."""
    inc = cfg["inclusion"]
    audit: list[dict] = []
    n0 = len(df)

    def step(name: str, mask: pd.Series, note: str) -> None:
        nonlocal df
        before = len(df)
        df = df[mask].copy()
        audit.append(
            {
                "fiscal_year": fy,
                "rule": name,
                "rows_before": before,
                "rows_removed": before - len(df),
                "rows_after": len(df),
                "note": note,
            }
        )

    step(
        "nih_administering_ic",
        df.ADMINISTERING_IC.isin(inc["nih_administering_ics"]),
        "ExPORTER redistributes VA/AHRQ/CDC/FDA/HRSA awards; only NIH ICs are retained",
    )
    step(
        "grants_and_cooperative_agreements",
        ~df.FUNDING_MECHANISM.isin(inc["excluded_funding_mechanisms"]),
        "removes intramural research, R&D contracts, and interagency agreements",
    )
    step(
        "activity_code_prefix",
        ~df.ACTIVITY.str[0].isin(inc["excluded_activity_prefixes"]),
        "belt-and-braces removal of Z* intramural and N* contract activity codes",
    )
    # Other Transaction awards (OT2, OT3) are filed under FUNDING_MECHANISM
    # "OTHERS" and so survive the grants-and-cooperative-agreements filter
    # despite being neither. They are kept by default and the switch is
    # counted either way, so the census never silently contains a category its
    # own filter name excludes.
    if not inc.get("include_other_transactions", True):
        step(
            "other_transaction_authority",
            ~df.ACTIVITY.astype(str).str.upper().str.startswith("OT"),
            "OT2/OT3 are Other Transaction authority, not grants or cooperative agreements",
        )
    if inc.get("exclude_subproject_rows", True):
        step(
            "parent_awards_only",
            df.SUBPROJECT_ID.isna() | (df.SUBPROJECT_ID.astype(str).str.strip() == ""),
            "subproject rows are nested inside a parent P/U/M award that already "
            "carries the full TOTAL_COST; retaining both double counts",
        )
    log.info("FY%s: %s -> %s award-years after inclusion rules", fy, f"{n0:,}", f"{len(df):,}")
    return df, audit


def _index_date(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Section 4 -- preferred index date with a recorded fallback."""
    order = cfg["index_date"]["fallback_order"]
    md = cfg["index_date"]["fy_midpoint_month_day"]
    idx = pd.Series(pd.NaT, index=df.index, dtype="datetime64[ns]")
    used = pd.Series("", index=df.index, dtype=object)
    for source in order:
        if source == "FY_MIDPOINT":
            cand = pd.to_datetime(df.fiscal_year.astype(int).astype(str) + "-" + md)
        else:
            cand = df[source.lower()]
        fill = idx.isna() & cand.notna()
        idx = idx.where(~fill, cand)
        used = used.where(~fill, source)
    df["index_date"] = idx
    df["index_date_source"] = used
    return df


def _fy_of(dates: pd.Series) -> pd.Series:
    """Federal fiscal year: FY N runs 1 Oct N-1 through 30 Sep N."""
    return dates.dt.year + (dates.dt.month >= 10).astype("Int64")


def _resolve_cross_year_duplicates(df: pd.DataFrame) -> pd.DataFrame:
    """Section 2 -- linked applications appearing in more than one annual file.

    A small number of applications (in practice type-7 change-of-institution
    transfers) are published in two consecutive ExPORTER annual files with
    different obligated amounts. Keeping both would count one award twice.

    Resolution, in order:
      1. keep the row whose fiscal year matches the fiscal year of its
         budget-start date, which is the year the obligation belongs to;
      2. otherwise keep the row from the most recently published annual file,
         since NIH corrects prior-year records in later releases.

    Every resolved case is written to logs/duplicate_application_resolution.csv.
    """
    dup_ids = df.loc[df.application_id.duplicated(keep=False), "application_id"].unique()
    if len(dup_ids) == 0:
        return df

    grp = df[df.application_id.isin(dup_ids)].copy()
    grp["budget_start_fy"] = _fy_of(grp.budget_start)
    grp["_matches_budget_fy"] = (grp.fiscal_year == grp.budget_start_fy).astype(int)
    grp = grp.sort_values(
        ["application_id", "_matches_budget_fy", "fiscal_year"], ascending=[True, False, False]
    )
    grp["_keep"] = ~grp.application_id.duplicated()
    grp["rule"] = np.where(
        grp._matches_budget_fy == 1, "budget_start_fiscal_year", "latest_published_file"
    )
    grp[
        [
            "application_id",
            "fiscal_year",
            "budget_start",
            "budget_start_fy",
            "activity_code",
            "application_type",
            "core_project_num",
            "org_name_raw",
            "total_cost",
            "rule",
            "_keep",
        ]
    ].rename(columns={"_keep": "retained"}).to_csv(
        LOGS / "duplicate_application_resolution.csv", index=False
    )

    drop_index = grp.index[~grp._keep]
    log.warning(
        "resolved %d application(s) appearing in multiple annual files; dropped %d row(s). "
        "See logs/duplicate_application_resolution.csv",
        len(dup_ids),
        len(drop_index),
    )
    out = df.drop(index=drop_index)
    if out.application_id.duplicated().any():
        raise PipelineError("duplicate APPLICATION_IDs survived resolution")
    return out


def _explode_pis(df: pd.DataFrame) -> pd.DataFrame:
    """One row per named PI on an award-year, with the contact flag preserved.

    ExPORTER encodes PIs as two parallel semicolon-delimited lists with
    '(contact)' appended to the contact PI in each. Position, not name, is the
    join key between the two lists.
    """
    ids = df.pi_ids_raw.fillna("").str.split(";")
    names = df.pi_names_raw.fillna("").str.split(";")

    recs = []
    for appl, org_ipf, fy, id_list, name_list in zip(
        df.application_id, df.org_ipf_code, df.fiscal_year, ids, names
    ):
        n = max(len(id_list), len(name_list))
        for i in range(n):
            raw_id = id_list[i] if i < len(id_list) else ""
            raw_nm = name_list[i] if i < len(name_list) else ""
            is_contact = bool(_CONTACT.search(raw_id) or _CONTACT.search(raw_nm))
            pid = _CONTACT.sub("", raw_id).strip()
            nm = _CONTACT.sub("", raw_nm).strip()
            if not pid and not nm:
                continue
            recs.append(
                {
                    "application_id": appl,
                    "org_ipf_code": org_ipf,
                    "fiscal_year": fy,
                    "pi_position": i,
                    "nih_pi_profile_id": pid or None,
                    "pi_name_raw": nm or None,
                    "is_contact_pi": is_contact,
                }
            )
    out = pd.DataFrame.from_records(recs)
    # Some award-years mark no contact; the sole PI is then the contact by
    # construction. Multi-PI awards with no marked contact are left unflagged
    # and counted in the coverage statistics.
    counts = out.groupby("application_id").is_contact_pi.transform("sum")
    sole = (counts == 0) & (out.groupby("application_id").pi_position.transform("size") == 1)
    out.loc[sole, "is_contact_pi"] = True
    out["pi_name_norm"] = (
        out.pi_name_raw.fillna("")
        .str.upper()
        .str.replace(r"[^A-Z, ]", "", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )
    return out


def build(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    frames, audits = [], []
    for fy in cfg["fiscal_years"]:
        raw = _read_year(fy)
        raw["fiscal_year"] = pd.to_numeric(raw.FY, errors="coerce")
        if raw.fiscal_year.nunique(dropna=True) != 1 or int(raw.fiscal_year.iloc[0]) != fy:
            raise PipelineError(f"FY{fy}: file contains FY values {raw.fiscal_year.unique()[:5]}")
        kept, audit = apply_inclusion_rules(raw, cfg, fy)
        frames.append(kept)
        audits.extend(audit)

    df = pd.concat(frames, ignore_index=True)
    pd.DataFrame(audits).to_csv(LOGS / "inclusion_audit.csv", index=False)

    out = pd.DataFrame(
        {
            "application_id": df.APPLICATION_ID.astype(str).str.strip(),
            "fiscal_year": df.fiscal_year.astype(int),
            "core_project_num": df.CORE_PROJECT_NUM.astype(str).str.strip(),
            "full_project_num": df.FULL_PROJECT_NUM.astype(str).str.strip(),
            "activity_code": df.ACTIVITY.astype(str).str.strip().str.upper(),
            "application_type": _numeric(df.APPLICATION_TYPE.fillna("")),
            "support_year": _numeric(df.SUPPORT_YEAR.fillna("")),
            "administering_ic": df.ADMINISTERING_IC.astype(str).str.strip(),
            "ic_name": df.IC_NAME,
            "funding_mechanism": df.FUNDING_MECHANISM,
            "project_start": _dates(df.PROJECT_START),
            "project_end": _dates(df.PROJECT_END),
            "budget_start": _dates(df.BUDGET_START),
            "budget_end": _dates(df.BUDGET_END),
            "total_cost": _numeric(df.TOTAL_COST.fillna("")),
            "direct_cost": _numeric(df.DIRECT_COST_AMT.fillna("")),
            "indirect_cost": _numeric(df.INDIRECT_COST_AMT.fillna("")),
            "org_name_raw": df.ORG_NAME.astype(str).str.strip(),
            "org_ipf_code": df.ORG_IPF_CODE.astype(str).str.strip().str.replace(r"\.0$", "", regex=True),
            "org_duns_uei_raw": df.ORG_DUNS,
            "org_city": df.ORG_CITY,
            "org_state": df.ORG_STATE,
            "org_country": df.ORG_COUNTRY.astype(str).str.strip().str.upper(),
            "nih_org_dept": df.ORG_DEPT.fillna("__MISSING__").astype(str).str.strip().str.upper(),
            "project_title": df.PROJECT_TITLE,
            "pi_ids_raw": df.PI_IDS,
            "pi_names_raw": df["PI_NAMEs"],
        }
    )

    # UEI where NIH supplies one alongside DUNS (12-char alphanumeric, no O/I).
    duns_uei = out.org_duns_uei_raw.fillna("").astype(str)
    out["org_duns"] = duns_uei.str.extract(r"(\b\d{9}\b)", expand=False)
    out["org_uei"] = duns_uei.str.extract(r"\b([A-HJ-NP-Z0-9]{12})\b", expand=False)
    out = out.drop(columns=["org_duns_uei_raw"])

    out = _resolve_cross_year_duplicates(out)

    out["has_total_cost"] = out.total_cost.notna()
    out["total_cost"] = out.total_cost.fillna(0.0)
    out = _index_date(out, cfg)

    pis = _explode_pis(out)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    out.to_parquet(PROCESSED / "award_years.parquet", index=False)
    pis.to_parquet(PROCESSED / "pi_links.parquet", index=False)
    log.info(
        "built %s award-years and %s PI links across FY%s-FY%s",
        f"{len(out):,}",
        f"{len(pis):,}",
        min(cfg["fiscal_years"]),
        max(cfg["fiscal_years"]),
    )
    return out, pis
