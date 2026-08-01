"""Unbiased per-investigator department profiles.

The institution-level harvest in pubmed_evidence.py filters PubMed to records
mentioning surgery, which is right for finding surgical papers and wrong for
deciding a person's department: it never collects the non-surgery papers that
would form the denominator. Measured against NIH's own department field on a
260-PI sample, a rule built on that biased harvest reached Cohen's kappa 0.21. Matching authors on surname plus initial alone
cost another 0.08: the key "Jain R" at MGH pools three different people.

This module instead pulls every paper for a given author at a given institution
with no topic filter, classifies each of that author's own affiliation strings,
and takes the department holding a majority of them. On the same sample that
rule reaches **kappa 0.916, sensitivity 91.9%, precision 100.0%**, which is what
licenses using it where NIH supplies no department at all.

It is applied to every uncoded clinical recipient in the comparison set, not
only to MGH and BWH. Reconstructing one hospital's departments and leaving its
uncoded peers out of the table does not remove the bias in NIH's coverage, it
just moves it: the hospital then competes against a field with the other
hospitals deleted from it. See RECONSTRUCTED_ORGS.

One query pair per investigator, cached per institution, resumable.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import pandas as pd

from .mgb_surgery import NARROW, _patterns, classify_near_institution
from .paths import INTERIM, PROCESSED, TABLES
from .pubmed_evidence import INSTITUTION_PATTERNS, _REG
from .util import get_logger, utcnow

log = get_logger("pi_department")

# Every recipient in the comparison set that NIH leaves without a department
# code, not just the two that prompted the question.
#
# Reconstructing MGH and BWH alone ranked them against a field their own
# uncoded peers were absent from: nine recipients above $1B carry no ORG_DEPT
# at all, among them Vanderbilt University Medical Center, Mayo Clinic
# Rochester, Boston Children's and Memorial Sloan Kettering. A hospital cannot
# be placed against a peer group that has been filtered to exclude hospitals,
# so the peers are profiled by the same validated rule and enter the ranking on
# the same footing.
#
# Non-clinical recipients (RTI, Westat, Broad, Scripps, Salk, the Jackson
# Laboratory and the rest) are deliberately absent: they have no clinical
# departments to reconstruct, and inventing one for them would be a different
# and much weaker claim than the one this rule was validated for.
RECONSTRUCTED_ORGS: tuple[str, ...] = (
    "MGH", "BWH",
    "IPF10040927",   # Vanderbilt University Medical Center
    "IPF10068583",   # Fred Hutchinson Cancer Center
    "IPF861001",     # Fred Hutchinson Cancer Research Center (pre-2022 code)
    "IPF4976101",    # Mayo Clinic Rochester
    "IPF1504801",    # Boston Children's Hospital
    "IPF5079202",    # Memorial Sloan Kettering
    "IPF615001",     # Cincinnati Children's Hospital
    "DFCI",          # Dana-Farber Cancer Institute
    "IPF1499101",    # Children's Hospital of Philadelphia
    "BIDMC",         # Beth Israel Deaconess Medical Center
    "IPF1225501",    # Cedars-Sinai Medical Center
    "IPF7893501",    # St. Jude Children's Research Hospital
    "IPF1531401",    # Seattle Children's Hospital
    "IPF3058203",    # City of Hope / Beckman Research Institute
    "IPF1495302",    # Nationwide Children's Hospital
    "IPF4976105",    # Mayo Clinic Jacksonville
    "IPF3736101",    # Moffitt Cancer Center
    "IPF10005742",   # Houston Methodist
    "IPF6959701",    # Rhode Island Hospital
    "IPF1520001",    # Children's Hospital of Los Angeles
    "IPF1518602",    # Children's National, Washington DC
    "IPF4155008",    # Feinstein Institutes for Medical Research
    "IPF3617301",    # Boston Medical Center
    "IPF1525701",    # Lurie Children's Hospital of Chicago
    "MEEI",          # Massachusetts Eye and Ear
)

# MGB_CORE stays what rollups_v1.csv says it is: MGH plus BWH, and nothing
# else. It is a roll-up over two of the profiled institutions, never over all
# of them.
MGB_CORE_MEMBERS: tuple[str, ...] = ("MGH", "BWH")

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
MAJORITY = 0.5          # validated rule: department holding >50% of affiliations
MAX_PAPERS_PER_PI = 120  # enough to establish a majority; caps the tail


def _req(endpoint: str, params: dict, retries: int = 4) -> bytes | None:
    url = f"{EUTILS}/{endpoint}?" + urllib.parse.urlencode(
        {**params, "tool": "RankMGB", "email": "aniruth@stanford.edu"}
    )
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - transient; retried
            # NCBI returns 429 when the 3-per-second ceiling is crossed; back off
            # harder for that than for an ordinary network blip.
            hard = "429" in str(exc)
            time.sleep((6 if hard else 1.5) * (attempt + 1))
    return None


def profile_one(name: str, org_id: str, inst_q: str, rx, pats,
                y0: int = 2020, y1: int = 2026) -> dict:
    """Department profile for one investigator at one institution.

    Split out so docs/validation/validate_department_rule.py scores the
    code that actually ships instead of a reimplementation of it.
    """
    last = str(name).split(",")[0].strip()
    fore = str(name).split(",")[1].strip() if "," in str(name) else ""
    # NIH gives "SURNAME, FORENAME M".
    #
    # Two failure modes have to be avoided at once. Matching on the initial
    # alone pools distinct people: the key "Jain R" at Massachusetts General
    # collects Rohil Jain in the Department of Surgery and Radhika Jain in
    # Internal Medicine. But *querying* PubMed for the full forename loses
    # people outright, because the author index cannot match "Madsen Joren"
    # against records filed as "Madsen JC" -- that search returns nothing
    # while "Madsen JC" returns 51.
    #
    # So: query broadly on the surname, then decide identity from the
    # ForeName carried on each record.
    first = fore.split()[0].strip(".") if fore else ""
    ini = first[:1]
    rec = {"pi_name_raw": name, "institution_id": org_id, "n_affiliations": 0,
           "surgical_share": None, "modal_department": None, "is_surgical": False,
           "name_match": "none", "n_forenames_seen": 0}
    if last and ini:
        # A single-token author must go unquoted: "Madsen"[Author] returns
        # nothing where Madsen[Author] returns 107. Multi-word surnames
        # still need the quotes.
        author_term = f'"{last}"[Author]' if (" " in last or "-" in last) else f"{last}[Author]"
        term = f"({author_term}) AND ({inst_q}) AND {y0}:{y1}[dp]"
        js = _req("esearch", {"db": "pubmed", "term": term,
                              "retmax": str(MAX_PAPERS_PER_PI), "retmode": "json"})
        ids = json.loads(js)["esearchresult"].get("idlist", []) if js else []
        # (forename_first_token, is_initials_only, affiliation_strings)
        hits: list[tuple[str, bool, list[str]]] = []
        if ids:
            xb = _req("efetch", {"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
            if xb:
                try:
                    root = ET.fromstring(xb)
                except ET.ParseError:
                    root = None
                if root is not None:
                    for art in root.iter("PubmedArticle"):
                        for au in art.iter("Author"):
                            if (au.findtext("LastName") or "").upper() != last.upper():
                                continue
                            fn = (au.findtext("ForeName") or "").strip().upper()
                            inits = (au.findtext("Initials") or "").strip().upper()
                            tok = fn.split()[0].strip(".") if fn else ""
                            initials_only = len(tok) < 2
                            key = tok if not initials_only else (inits[:1] or "")
                            if not key.startswith(ini.upper()):
                                continue
                            affs = [(a.text or "").strip() for a in au.iter("Affiliation")]
                            hits.append((key, initials_only, [a for a in affs if a and rx.search(a)]))

        named = {k for k, io, _ in hits if not io}
        rival = {k for k in named if k != first.upper()}
        # Initials-only records are only safe when nobody else with this
        # initial publishes here under a full name.
        accept_initials = not rival
        specs: list[str] = []
        for key, initials_only, affs in hits:
            if initials_only:
                if not accept_initials:
                    continue
            elif key != first.upper():
                continue
            for t in affs:
                sp = classify_near_institution(t, rx, pats)[0]
                if sp:
                    specs.append(sp)

        rec["n_forenames_seen"] = len(named)
        if first.upper() in named:
            rec["name_match"] = "full_forename"
        elif accept_initials and hits:
            rec["name_match"] = "initials_only_unambiguous"
        elif rival:
            rec["name_match"] = "ambiguous_surname"
        if specs and rec["name_match"] in ("full_forename", "initials_only_unambiguous"):
            sp_series = pd.Series(specs)
            share = float(sp_series.isin(NARROW).mean())
            rec.update(n_affiliations=len(specs), surgical_share=round(share, 4),
                       modal_department=sp_series.value_counts().index[0],
                       is_surgical=share > MAJORITY)
        time.sleep(0.4)
    return rec


def profile_institution(org_id: str, pi_names: list[str], y0: int = 2020, y1: int = 2026) -> pd.DataFrame:
    cache = INTERIM / f"pi_departments_{org_id}.parquet"
    done: dict[str, dict] = {}
    if cache.exists():
        prior = pd.read_parquet(cache)
        done = {r.pi_name_raw: r._asdict() for r in prior.itertuples(index=False)}
        log.info("%s: %s investigators already cached", org_id, f"{len(done):,}")

    inst_q = _REG.set_index("canonical_org_id").pubmed_query[org_id]
    rx = INSTITUTION_PATTERNS[org_id]
    pats = _patterns()
    rows = list(done.values())

    todo = [n for n in pi_names if n not in done]
    log.info("%s: profiling %s investigators", org_id, f"{len(todo):,}")
    for i, name in enumerate(todo, 1):
        rec = profile_one(name, org_id, inst_q, rx, pats, y0, y1)
        rows.append(rec)
        if i % 200 == 0:
            pd.DataFrame(rows).to_parquet(cache, index=False)
            log.info("  %s: %s/%s", org_id, f"{i:,}", f"{len(todo):,}")

    out = pd.DataFrame(rows)
    out["profiled_at"] = utcnow()
    out.to_parquet(cache, index=False)
    log.info("%s: %s investigators, %s surgical by majority rule",
             org_id, f"{len(out):,}", f"{int(out.is_surgical.sum()):,}")
    return out


def run(org_ids: list[str] | tuple[str, ...] = RECONSTRUCTED_ORGS) -> pd.DataFrame:
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    pis = pd.read_parquet(PROCESSED / "pi_links.parquet")
    pis = pis[pis.is_contact_pi].drop(
        columns=[c for c in ("fiscal_year", "org_ipf_code") if c in pis.columns])
    d = pis.merge(df[["application_id", "canonical_org_id"]], on="application_id")

    frames = []
    for org in org_ids:
        names = sorted(d[d.canonical_org_id == org].pi_name_raw.dropna().unique())
        frames.append(profile_institution(org, list(names)))
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(PROCESSED / "pi_departments.parquet", index=False)
    log.info("wrote pi_departments.parquet: %s rows", f"{len(out):,}")
    return out


# One rule, published once.
#
# Two "confidence floors" used to be emitted, sold in the prose as a bracket on
# the answer and exposed on the site as a control. They were not a bracket. The
# second floor was implemented as "the investigator's modal department is
# surgical", which on the real profiles selects the identical set of people as
# the majority rule: the two CSVs were byte-identical, the site control changed
# nothing, and the band the prose described was zero wide. Worse, the second
# floor carried the validation statistics of a *different* rule (any surgical
# affiliation at all, kappa 0.954), so 18 published rows were labelled with
# numbers that had never been measured on the computation that produced them.
#
# Rather than repair a control nobody could act on, there is now one rule and
# one set of validation statistics, measured on it. `corroborated` is kept as
# the filename token so existing links and downloads do not break.
FLOOR = "corroborated"
FLOORS = {
    # majority of the investigator's classified affiliations are surgical
    "corroborated": dict(rule="majority", sens=91.9, prec=100.0, kappa=0.916),
}


def _entity_slice(frame: pd.DataFrame, entity: str) -> pd.DataFrame:
    """Rows belonging to one reported entity.

    MGB_CORE is the MGH+BWH roll-up; every other entity is a single recipient.
    Summing MGB_CORE over whatever happens to be in `org_ids` was safe only
    while org_ids *was* MGH and BWH, and would silently turn the roll-up into
    "every uncoded hospital in the country" now that it is not.
    """
    if entity == "MGB_CORE":
        return frame[frame.canonical_org_id.isin(MGB_CORE_MEMBERS)]
    return frame[frame.canonical_org_id == entity]


def _profiled(prof: pd.DataFrame, org_ids: tuple[str, ...]) -> tuple[str, ...]:
    """The subset of `org_ids` that actually has investigator profiles.

    An institution listed in RECONSTRUCTED_ORGS but not yet profiled merges to
    all-NaN and summarises to a clean-looking $0. Publishing that is strictly
    worse than omitting it: a reader sees Mayo Clinic in a ranked table at zero
    dollars and concludes Mayo has no department of surgery, when what happened
    is that nobody asked PubMed. Absent is honest, zero is a false statement.
    """
    have = set(prof.institution_id.dropna().unique())
    kept = tuple(o for o in org_ids if o in have)
    missing = [o for o in org_ids if o not in have]
    if missing:
        log.warning(
            "%d of %d reconstructed institutions have no profile and are excluded "
            "from the tables entirely (not ranked at zero): %s",
            len(missing), len(org_ids), ", ".join(missing))
    return kept


def summarise_surgery(cfg: dict, org_ids: tuple[str, ...] = RECONSTRUCTED_ORGS) -> pd.DataFrame:
    """Departmental totals for recipients NIH does not department-code.

    Emits the schema `mgb_surgery_summary.csv` already has, so the site and the
    context tables consume it unchanged, but the underlying rule is now the
    validated per-investigator one rather than per-award evidence matching.

    Covers every uncoded recipient in the comparison set, not only MGH and BWH,
    so that the surgery ranking MGB is placed into contains its peers.
    """
    from .mgb_surgery import DOS, NARROW

    prof = pd.read_parquet(PROCESSED / "pi_departments.parquet")
    org_ids = _profiled(prof, org_ids)
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    pis = pd.read_parquet(PROCESSED / "pi_links.parquet")
    pis = pis[pis.is_contact_pi].drop(
        columns=[c for c in ("fiscal_year", "org_ipf_code") if c in pis.columns])
    a = pis.merge(
        df[["application_id", "canonical_org_id", "fiscal_year", "total_cost", "is_r01",
            "core_project_num", "mechanism_family"]], on="application_id")
    a = a[a.canonical_org_id.isin(org_ids)]
    m = a.merge(
        prof[["institution_id", "pi_name_raw", "is_surgical", "modal_department",
              "n_affiliations", "surgical_share"]],
        left_on=["canonical_org_id", "pi_name_raw"],
        right_on=["institution_id", "pi_name_raw"], how="left")

    resolved = (m.n_affiliations.fillna(0) > 0)
    log.info("%s contact-PI award-years, %s with a resolved department (%.1f%%)",
             f"{len(m):,}", f"{int(resolved.sum()):,}", 100 * resolved.mean())

    entities = (*org_ids, *(("MGB_CORE",) if set(MGB_CORE_MEMBERS) <= set(org_ids) else ()))
    rows = []
    for floor, meta in FLOORS.items():
        surgical = m.is_surgical.fillna(False)
        for scope, sel in [
            ("Department of Surgery", surgical & m.modal_department.isin(DOS)),
            ("All surgical departments (narrow)", surgical & m.modal_department.isin(NARROW)),
        ]:
            for period, years in cfg["reporting_periods"].items():
                base = m[sel & m.fiscal_year.isin(years)]
                for entity in entities:
                    s = _entity_slice(base, entity)
                    rows.append({
                        "period": period, "confidence_floor": floor, "scope": scope,
                        "entity": entity,
                        "total_funding": s.total_cost.sum(),
                        "award_years": len(s),
                        "distinct_projects": s.core_project_num.nunique(),
                        "r01_funding": s.loc[s.is_r01, "total_cost"].sum(),
                        "r01_award_years": int(s.is_r01.sum()),
                        "m_award_years": int((s.mechanism_family == "M").sum()),
                        "funded_investigators": s.pi_name_raw.nunique(),
                        "rule": meta["rule"],
                        "validation_kappa": meta["kappa"],
                        "validation_precision_pct": meta["prec"],
                        "validation_sensitivity_pct": meta["sens"],
                    })
    out = pd.DataFrame(rows)
    out.to_csv(TABLES / "mgb_surgery_summary.csv", index=False)
    log.info("wrote mgb_surgery_summary.csv (%d rows) from the validated majority rule", len(out))
    return out


def emit_evidence(cfg: dict, org_ids: tuple[str, ...] = RECONSTRUCTED_ORGS) -> pd.DataFrame:
    """One row per reconstructed surgical award-year, with what decided it.

    The audit trail has to be produced by the rule that ships. The previous
    evidence file was the output of the retired per-award matcher: it covered
    117 of the 364 published award-years and listed 202 that are no longer in
    the total, so a surgeon looking up a colleague's grant would more often
    than not fail to find it, and would find awards that are not counted.

    What this rule decides on is the investigator, not the award, so the
    evidence is per investigator: how many of their dated affiliation strings
    were classified, what share were surgical, which department held the
    majority, and how their name was matched. It does not carry a PMID per
    award, because no award is attributed by a PMID -- claiming otherwise was
    the previous file's second problem.
    """
    prof = pd.read_parquet(PROCESSED / "pi_departments.parquet")
    org_ids = _profiled(prof, org_ids)
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    pis = pd.read_parquet(PROCESSED / "pi_links.parquet")
    pis = pis[pis.is_contact_pi].drop(
        columns=[c for c in ("fiscal_year", "org_ipf_code") if c in pis.columns])
    cols = ["application_id", "canonical_org_id", "fiscal_year", "total_cost", "is_r01",
            "core_project_num", "activity_code", "mechanism_family", "project_title"]
    cols = [c for c in cols if c in df.columns]
    a = pis.merge(df[cols], on="application_id")
    a = a[a.canonical_org_id.isin(org_ids)]
    m = a.merge(prof, left_on=["canonical_org_id", "pi_name_raw"],
                right_on=["institution_id", "pi_name_raw"], how="inner")

    from .mgb_surgery import DOS, NARROW

    m = m[m.is_surgical & m.modal_department.isin(NARROW)].copy()
    m["nih_org_dept"] = m.modal_department.map(SPECIALTY_TO_NIH_DEPT)
    m["is_department_of_surgery"] = m.modal_department.isin(DOS)
    m["rule"] = FLOORS[FLOOR]["rule"]
    m["validation_kappa"] = FLOORS[FLOOR]["kappa"]
    keep = ["application_id", "core_project_num", "canonical_org_id", "pi_name_raw",
            "fiscal_year", "total_cost", "is_r01", "activity_code", "mechanism_family",
            "project_title", "modal_department", "nih_org_dept",
            "is_department_of_surgery", "surgical_share", "n_affiliations",
            "name_match", "n_forenames_seen", "rule", "validation_kappa", "profiled_at"]
    out = m[[c for c in keep if c in m.columns]].sort_values(
        ["canonical_org_id", "pi_name_raw", "fiscal_year"]).reset_index(drop=True)
    path = TABLES / "reconstructed_department_evidence.csv"
    out.to_csv(path, index=False)
    log.info("wrote %s (%d award-years, %d investigators, %d in a department of surgery)",
             path.name, len(out), out.pi_name_raw.nunique(),
             int(out.is_department_of_surgery.sum()))
    return out


# The classifier's specialty codes map to NIH's own department vocabulary so a
# reconstructed row sits in the same bucket as its NIH-coded peers in the
# explorer. Several surgical divisions collapse into NIH's single SURGERY code,
# which is exactly how NIH treats them for universities.
SPECIALTY_TO_NIH_DEPT = {
    "GENERAL_AND_UNSPECIFIED_SURGERY": "SURGERY",
    "CARDIAC_CARDIOTHORACIC_SURGERY": "SURGERY",
    "VASCULAR_SURGERY": "SURGERY",
    "TRANSPLANT_SURGERY": "SURGERY",
    "SURGICAL_ONCOLOGY": "SURGERY",
    "TRAUMA_ACUTE_CARE_SURGERY": "SURGERY",
    "COLORECTAL_SURGERY": "SURGERY",
    "PEDIATRIC_SURGERY": "SURGERY",
    "OTHER_EXPLICIT_SURGERY": "SURGERY",
    "NEUROSURGERY": "NEUROSURGERY",
    "ORTHOPEDIC_SURGERY": "ORTHOPEDICS",
    "UROLOGY": "UROLOGY",
    "OTOLARYNGOLOGY_HNS": "OTOLARYNGOLOGY",
    "PLASTIC_SURGERY": "PLASTIC SURGERY",
    "OPHTHALMIC_SURGERY": "OPHTHALMOLOGY",
    "OBGYN": "OBSTETRICS & GYNECOLOGY",
    "INTERNAL_MEDICINE": "INTERNAL MEDICINE/MEDICINE",
    "PEDIATRICS": "PEDIATRICS",
    "NEUROLOGY": "NEUROLOGY",
    "PSYCHIATRY": "PSYCHIATRY",
    "RADIOLOGY_RADONC": "RADIATION-DIAGNOSTIC/ONCOLOGY",
    "PATHOLOGY": "PATHOLOGY",
    "ANESTHESIOLOGY": "ANESTHESIOLOGY",
    "DERMATOLOGY": "DERMATOLOGY",
    "EMERGENCY_MEDICINE": "EMERGENCY MEDICINE",
    "FAMILY_MEDICINE": "FAMILY MEDICINE",
    "PMR": "PHYSICAL MEDICINE & REHAB",
    "PUBLIC_HEALTH": "PUBLIC HEALTH & PREV MEDICINE",
    "QUANTITATIVE": "BIOSTATISTICS & OTHER MATH SCI",
    "BASIC_SCIENCE": "OTHER BASIC SCIENCES",
    "ENGINEERING": "BIOMEDICAL ENGINEERING",
    "DENTISTRY": "DENTISTRY",
    "OTHER_HEALTH_PROF": "OTHER HEALTH PROFESSIONS",
}


def summarise_all_departments(cfg: dict, org_ids: tuple[str, ...] = RECONSTRUCTED_ORGS) -> pd.DataFrame:
    """Every department, not just surgery, for recipients NIH does not code.

    Surgery was the question that prompted this, but publishing only surgery
    leaves the rest of the institution invisible in the explorer and makes any
    cross-specialty comparison unverifiable. The same validated majority rule
    produces all of them.
    """
    prof = pd.read_parquet(PROCESSED / "pi_departments.parquet")
    org_ids = _profiled(prof, org_ids)
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    pis = pd.read_parquet(PROCESSED / "pi_links.parquet")
    pis = pis[pis.is_contact_pi].drop(
        columns=[c for c in ("fiscal_year", "org_ipf_code") if c in pis.columns])
    a = pis.merge(
        df[["application_id", "canonical_org_id", "fiscal_year", "total_cost", "is_r01",
            "core_project_num", "mechanism_family"]], on="application_id")
    a = a[a.canonical_org_id.isin(org_ids)]
    m = a.merge(prof[["institution_id", "pi_name_raw", "is_surgical", "modal_department"]],
                left_on=["canonical_org_id", "pi_name_raw"],
                right_on=["institution_id", "pi_name_raw"], how="left")
    m = m[m.modal_department.notna()].copy()
    m["nih_org_dept"] = m.modal_department.map(SPECIALTY_TO_NIH_DEPT)
    m = m[m.nih_org_dept.notna()]

    entities = (*org_ids, *(("MGB_CORE",) if set(MGB_CORE_MEMBERS) <= set(org_ids) else ()))
    rows = []
    for period, years in cfg["reporting_periods"].items():
        sub = m[m.fiscal_year.isin(years)]
        for entity in entities:
            sel = _entity_slice(sub, entity)
            for dept, g in sel.groupby("nih_org_dept"):
                rows.append({
                    "period": period, "entity": entity, "nih_org_dept": dept,
                    "total_funding": g.total_cost.sum(),
                    "award_years": len(g),
                    "distinct_projects": g.core_project_num.nunique(),
                    "r01_funding": g.loc[g.is_r01, "total_cost"].sum(),
                    "r01_award_years": int(g.is_r01.sum()),
                    "m_award_years": int((g.mechanism_family == "M").sum()),
                    "funded_investigators": g.pi_name_raw.nunique(),
                })
    out = pd.DataFrame(rows)
    out.to_csv(TABLES / "mgb_departments_all.csv", index=False)
    log.info("wrote mgb_departments_all.csv (%d rows, %d departments)",
             len(out), out.nih_org_dept.nunique())
    return out
