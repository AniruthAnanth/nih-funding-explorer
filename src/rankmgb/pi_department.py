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


def run(org_ids: list[str]) -> pd.DataFrame:
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


# Both floors are published because they bracket the answer, and the validation
# sample says what each costs. Figures quoted in prose use `corroborated`.
FLOORS = {
    # majority of the investigator's affiliations are surgical
    "corroborated": dict(rule="majority", sens=91.9, prec=100.0, kappa=0.916),
    # any surgical affiliation at all: higher recall, lower precision
    "all_evidence": dict(rule="any", sens=99.3, prec=96.4, kappa=0.954),
}


def summarise_surgery(cfg: dict, org_ids: tuple[str, ...] = ("MGH", "BWH")) -> pd.DataFrame:
    """Departmental totals for recipients NIH does not department-code.

    Emits the schema `mgb_surgery_summary.csv` already has, so the site and the
    context tables consume it unchanged, but the underlying rule is now the
    validated per-investigator one rather than per-award evidence matching.
    """
    from .mgb_surgery import DOS, NARROW

    prof = pd.read_parquet(PROCESSED / "pi_departments.parquet")
    df = pd.read_parquet(PROCESSED / "award_years_annotated.parquet")
    pis = pd.read_parquet(PROCESSED / "pi_links.parquet")
    pis = pis[pis.is_contact_pi].drop(
        columns=[c for c in ("fiscal_year", "org_ipf_code") if c in pis.columns])
    a = pis.merge(
        df[["application_id", "canonical_org_id", "fiscal_year", "total_cost", "is_r01",
            "core_project_num", "mechanism_family"]], on="application_id")
    a = a[a.canonical_org_id.isin(org_ids)]
    m = a.merge(
        prof[["institution_id", "pi_name_raw", "is_surgical", "modal_department", "n_affiliations"]],
        left_on=["canonical_org_id", "pi_name_raw"],
        right_on=["institution_id", "pi_name_raw"], how="left")

    resolved = (m.n_affiliations.fillna(0) > 0)
    log.info("%s contact-PI award-years, %s with a resolved department (%.1f%%)",
             f"{len(m):,}", f"{int(resolved.sum()):,}", 100 * resolved.mean())

    rows = []
    for floor, meta in FLOORS.items():
        surgical = m.is_surgical.fillna(False) if meta["rule"] == "majority" \
            else (m.n_affiliations.fillna(0) > 0) & m.modal_department.isin(NARROW)
        for scope, sel in [
            ("Department of Surgery", surgical & m.modal_department.isin(DOS)),
            ("All surgical departments (narrow)", surgical & m.modal_department.isin(NARROW)),
        ]:
            for period, years in cfg["reporting_periods"].items():
                base = m[sel & m.fiscal_year.isin(years)]
                for entity in (*org_ids, "MGB_CORE"):
                    s = base if entity == "MGB_CORE" else base[base.canonical_org_id == entity]
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
