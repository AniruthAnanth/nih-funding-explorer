"""A second measure of a department: what it publishes and how often it is cited.

NIH dollars measure one thing. This measures another — output in the general
medical journals that carry the most weight, and citation impact across
everything a department publishes.

Both come from the PubMed harvest already on disk plus NIH's own iCite service,
which returns the journal, publication year, citation count and Relative
Citation Ratio for a PMID. iCite is used rather than a commercial index because
it is free, NIH-operated, keyed on the same PMIDs the affiliation harvest
already carries, and its RCR is field- and time-normalised, which a raw citation
count is not.

The unit is the same as the funding analysis: an institution's surgical
department, identified by the same publication-affiliation classifier. A paper
counts for an institution's surgery department when at least one author's own
affiliation string names both that institution and a surgical unit, adjacent to
each other in the same segment.

Two things to keep in mind when reading the output:

  * A paper with surgical authors at two institutions counts once for each.
    These are participation counts and must not be summed to a national total.
  * Citation counts accumulate with age, so a five-year window favours the
    earlier years in it. `mean_rcr` is the age-adjusted figure and is the one to
    compare across departments.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

import numpy as np
import pandas as pd

from .mgb_surgery import NARROW, _patterns, classify_near_institution
from .paths import INTERIM, PROCESSED, TABLES
from .util import PipelineError, get_logger, utcnow

log = get_logger("bibliometrics")

ICITE = "https://icite.od.nih.gov/api/pubs"
# iCite takes the id list on the query string, so the batch is bounded by URI
# length rather than by any documented API limit. 200 nine-digit PMIDs is ~1.8kB.
ICITE_BATCH = 200

# The three journals asked for, plus The Lancet, which sits in the same tier and
# would look odd omitted, and the two general surgery journals a department of
# surgery would actually be judged on. Matching is on iCite's abbreviated
# journal string, which is the NLM title abbreviation.
FLAGSHIP_JOURNALS = {
    "NEJM": [r"^N Engl J Med$"],
    "JAMA": [r"^JAMA$"],
    "BMJ": [r"^BMJ$", r"^BMJ \(Clinical research ed\.?\)$"],
    "Lancet": [r"^Lancet$"],
}
SURGERY_JOURNALS = {
    "Ann Surg": [r"^Ann Surg$"],
    "JAMA Surg": [r"^JAMA Surg$"],
}
ALL_JOURNAL_GROUPS = {**FLAGSHIP_JOURNALS, **SURGERY_JOURNALS}

# "Top-tier general medical" is the headline roll-up the user asked for.
FLAGSHIP_KEYS = list(FLAGSHIP_JOURNALS)


def surgical_papers(pub: pd.DataFrame) -> pd.DataFrame:
    """One row per (institution, pmid) where a surgical affiliation was attested.

    Classification runs on the distinct affiliation strings rather than on all
    964k rows, because the same string repeats across authors and papers.
    """
    from .pubmed_evidence import INSTITUTION_PATTERNS

    pats = _patterns()
    uniq = pub[["institution_id", "affiliation"]].drop_duplicates()
    log.info("classifying %s distinct institution-affiliation strings", f"{len(uniq):,}")
    verdict = [
        classify_near_institution(a, INSTITUTION_PATTERNS[i], pats)[0]
        if i in INSTITUTION_PATTERNS else None
        for i, a in zip(uniq.institution_id, uniq.affiliation)
    ]
    uniq = uniq.assign(specialty=verdict)
    surgical = uniq[uniq.specialty.isin(NARROW)]
    log.info("  %s of them describe a surgical unit", f"{len(surgical):,}")

    hit = pub.merge(surgical, on=["institution_id", "affiliation"], how="inner")
    out = hit[["institution_id", "pmid", "pub_year", "specialty"]].drop_duplicates(
        ["institution_id", "pmid"]
    )
    log.info(
        "%s institution-paper pairs across %s distinct papers",
        f"{len(out):,}", f"{out.pmid.nunique():,}",
    )
    return out


def _icite_batch(pmids: list[str]) -> list[dict]:
    url = f"{ICITE}?{urllib.parse.urlencode({'pmids': ','.join(pmids)})}"
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=180) as resp:
                return json.loads(resp.read()).get("data", [])
        except Exception as exc:  # noqa: BLE001 - network faults are retried
            last = exc
            time.sleep(2 * (attempt + 1))
    raise PipelineError(f"iCite request failed after retries: {last}")


def fetch_citations(pmids: list[str]) -> pd.DataFrame:
    """Journal, year, citation count and RCR for every PMID, cached on disk."""
    cache = INTERIM / "icite_metrics.parquet"
    have = pd.read_parquet(cache) if cache.exists() else pd.DataFrame(columns=["pmid"])
    known = set(have.pmid.astype(str)) if len(have) else set()
    todo = [p for p in dict.fromkeys(str(x) for x in pmids) if p not in known]
    log.info("iCite: %s cached, %s to fetch", f"{len(known):,}", f"{len(todo):,}")

    rows: list[dict] = []
    for i in range(0, len(todo), ICITE_BATCH):
        batch = todo[i : i + ICITE_BATCH]
        for rec in _icite_batch(batch):
            rows.append(
                {
                    "pmid": str(rec.get("pmid")),
                    "year": rec.get("year"),
                    "journal": (rec.get("journal") or "").strip(),
                    "citation_count": rec.get("citation_count"),
                    "rcr": rec.get("relative_citation_ratio"),
                    "is_research_article": rec.get("is_research_article"),
                }
            )
        if (i // ICITE_BATCH) % 20 == 0:
            log.info("  %s/%s fetched", f"{i:,}", f"{len(todo):,}")
        time.sleep(0.2)

    fresh = pd.DataFrame(rows)
    out = pd.concat([have, fresh], ignore_index=True).drop_duplicates("pmid") if len(have) else fresh
    out["fetched_at"] = utcnow()
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(cache, index=False)
    log.info("iCite: %s papers with citation metrics", f"{len(out):,}")
    return out


def _journal_flags(journal: pd.Series) -> pd.DataFrame:
    j = journal.fillna("")
    flags = {}
    for key, patterns in ALL_JOURNAL_GROUPS.items():
        m = pd.Series(False, index=j.index)
        for pat in patterns:
            m |= j.str.match(pat, case=False, na=False)
        flags[key] = m
    out = pd.DataFrame(flags)
    out["TOP_TIER_GENERAL"] = out[FLAGSHIP_KEYS].any(axis=1)
    return out


ROLLUPS = {"MGB_CORE": ("MGH", "BWH")}


def _add_rollup(df: pd.DataFrame) -> pd.DataFrame:
    """Append combined rows for the institution roll-ups.

    A union over papers, not a sum over institutions. A paper with a surgical
    author at MGH and another at BWH is one paper for Mass General Brigham, and
    adding the two institution rows would count it and its citations twice.
    Deduplicating on PMID before aggregation is what makes the combined figure
    correct rather than merely larger.
    """
    extra = []
    for rollup_id, members in ROLLUPS.items():
        sub = df[df.institution_id.isin(members)]
        if sub.empty:
            continue
        # Keep one row per paper. Citation metrics are properties of the paper,
        # so any of the duplicate rows carries the same values.
        merged = sub.sort_values("institution_id").drop_duplicates("pmid").copy()
        merged["institution_id"] = rollup_id
        overlap = len(sub) - len(merged)
        log.info(
            "%s: %s papers across %s, %s counted at more than one member and "
            "deduplicated",
            rollup_id, f"{len(merged):,}", "+".join(members), f"{overlap:,}",
        )
        extra.append(merged)
    return pd.concat([df, *extra], ignore_index=True) if extra else df


def build(cfg: dict) -> pd.DataFrame:
    pub = pd.read_parquet(PROCESSED / "pubmed_author_affiliations.parquet")
    papers = surgical_papers(pub)
    metrics = fetch_citations(papers.pmid.astype(str).unique().tolist())

    df = papers.assign(pmid=papers.pmid.astype(str)).merge(metrics, on="pmid", how="left")
    df = pd.concat([df, _journal_flags(df.journal)], axis=1)

    df = _add_rollup(df)

    names = pd.read_csv(TABLES / "rank_institution_FY2021_FY2025.csv")[
        ["canonical_org_id", "display_name"]
    ].drop_duplicates()

    rows = []
    for period, years in cfg["reporting_periods"].items():
        # Publication year, not fiscal year: a paper is dated by when it appeared.
        sub = df[df.year.isin(years)]
        g = sub.groupby("institution_id")
        agg = g.agg(
            papers=("pmid", "nunique"),
            total_citations=("citation_count", "sum"),
            median_citations=("citation_count", "median"),
            mean_rcr=("rcr", "mean"),
        ).reset_index()
        for key in list(ALL_JOURNAL_GROUPS) + ["TOP_TIER_GENERAL"]:
            counts = sub[sub[key]].groupby("institution_id").pmid.nunique().rename(f"papers_{key}")
            agg = agg.merge(counts, on="institution_id", how="left")
        agg = agg.fillna({f"papers_{k}": 0 for k in list(ALL_JOURNAL_GROUPS) + ["TOP_TIER_GENERAL"]})
        agg["period"] = period
        rows.append(agg)

    out = pd.concat(rows, ignore_index=True).rename(columns={"institution_id": "canonical_org_id"})
    out = out.merge(names, on="canonical_org_id", how="left")
    out["citations_per_paper"] = (
        out.total_citations / out.papers.replace(0, np.nan)
    ).round(1)
    out["mean_rcr"] = out.mean_rcr.round(2)
    out["median_citations"] = out.median_citations.round(1)

    intcols = ["papers", "total_citations"] + [
        f"papers_{k}" for k in list(ALL_JOURNAL_GROUPS) + ["TOP_TIER_GENERAL"]
    ]
    out[intcols] = out[intcols].fillna(0).astype("int64")

    out = out.sort_values(["period", "total_citations"], ascending=[True, False])
    out.to_csv(TABLES / "bibliometrics_surgery.csv", index=False)
    log.info("wrote bibliometrics_surgery.csv (%d rows)", len(out))
    return out
