"""Publication affiliations as the primary source of PI department.

NIH's own department field covers only recipients it classifies as schools, so
using it as the primary source measures hospitals and universities by different
rules. This module instead harvests dated author affiliation strings from
PubMed for *every* institution in the comparison set, which makes the
departmental attribution like-for-like across the whole ranking. NIH ORG_DEPT
then becomes an independent comparator rather than the measurement itself, and
the agreement between the two is reported in agreement.py.

For each institution it stores the verbatim affiliation string, the publication
year, and the PMID, so every downstream classification is traceable to a
citable record. Nothing here decides a PI's department; it produces the evidence
that mgb_surgery.py classifies.
"""
from __future__ import annotations

import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

from .paths import INTERIM, PROCESSED, REFERENCE
from .util import PipelineError, get_logger, utcnow

log = get_logger("pubmed")

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "RankMGB"
EMAIL = "aniruth@stanford.edu"

# Records where some author's affiliation names the institution AND some
# affiliation on the record names surgery. The record-level query is only a
# candidate filter; the author-level test in _parse_batch is what decides
# whether a given author's own affiliation string is surgical.
def _registry() -> pd.DataFrame:
    """Institutions to harvest, from reference/pubmed_institutions_v1.csv.

    Every institution in the comparison set is harvested the same way, so the
    publication-derived department attribution is like-for-like across the
    ranking rather than applied only where NIH data is missing.
    """
    return pd.read_csv(REFERENCE / "pubmed_institutions_v1.csv")


_REG = _registry()
INSTITUTION_QUERIES = {
    r.canonical_org_id: f"({r.pubmed_query}) AND (surgery[Affiliation] OR surgical[Affiliation])"
    for _, r in _REG.iterrows()
}
INSTITUTION_PATTERNS = {
    r.canonical_org_id: re.compile(r.affiliation_regex, re.I) for _, r in _REG.iterrows()
}
INSTITUTION_NAMES = dict(zip(_REG.canonical_org_id, _REG.short_name))


def _get(endpoint: str, params: dict, retries: int = 4) -> bytes:
    params = {**params, "tool": TOOL, "email": EMAIL}
    url = f"{EUTILS}/{endpoint}?{urllib.parse.urlencode(params)}"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=120) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001 - network faults are retried
            last = exc
            time.sleep(2 * (attempt + 1))
    raise PipelineError(f"E-utilities request failed: {endpoint} {last}")


def _post(endpoint: str, params: dict, retries: int = 4) -> bytes:
    """E-utilities accepts POST, which is required once an id list is long."""
    params = {**params, "tool": TOOL, "email": EMAIL}
    data = urllib.parse.urlencode(params).encode()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(f"{EUTILS}/{endpoint}", data=data)
            with urllib.request.urlopen(req, timeout=180) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(2 * (attempt + 1))
    raise PipelineError(f"E-utilities POST failed: {endpoint} {last}")


def _pmids_for_slice(term: str, dfrom: str, dto: str) -> list[str]:
    """E-utilities caps a retrievable result set at 10,000 records, so the
    query is sliced until every slice fits under that ceiling."""
    import json

    payload = _get(
        "esearch",
        {
            "db": "pubmed",
            "term": term,
            "mindate": dfrom,
            "maxdate": dto,
            "datetype": "pdat",
            "retmax": "9999",
            "retmode": "json",
        },
    )
    res = json.loads(payload)["esearchresult"]
    count = int(res["count"])
    if count > 9999:
        raise PipelineError(f"slice {dfrom}-{dto} returned {count:,} records; narrow it further")
    return list(res.get("idlist", []))


def _parse_batch(xml_bytes: bytes, inst_id: str) -> list[dict]:
    pattern = INSTITUTION_PATTERNS[inst_id]
    out: list[dict] = []
    root = ET.fromstring(xml_bytes)
    for art in root.iter("PubmedArticle"):
        pmid_el = art.find(".//PMID")
        pmid = pmid_el.text if pmid_el is not None else None
        year = None
        for tag in (".//ArticleDate/Year", ".//PubDate/Year", ".//PubMedPubDate/Year"):
            el = art.find(tag)
            if el is not None and el.text and el.text.isdigit():
                year = int(el.text)
                break
        for author in art.iter("Author"):
            last = author.findtext("LastName")
            fore = author.findtext("ForeName") or ""
            if not last:
                continue
            for aff_el in author.iter("Affiliation"):
                aff = (aff_el.text or "").strip()
                if not aff or not pattern.search(aff):
                    continue
                out.append(
                    {
                        "institution_id": inst_id,
                        "pmid": pmid,
                        "pub_year": year,
                        "last_name": last.upper().strip(),
                        "fore_name": fore.upper().strip(),
                        "affiliation": aff,
                    }
                )
    return out


def harvest(inst_id: str, y0: int = 2020, y1: int = 2026, batch: int = 200) -> pd.DataFrame:
    cache = INTERIM / f"pubmed_affiliations_{inst_id}_{y0}_{y1}.parquet"
    if cache.exists():
        df = pd.read_parquet(cache)
        log.info("%s: %s cached author-affiliation records", inst_id, f"{len(df):,}")
        return df

    term = INSTITUTION_QUERIES[inst_id]

    pmids: list[str] = []
    for year in range(y0, y1 + 1):
        got = _pmids_for_slice(term, f"{year}/01/01", f"{year}/12/31")
        pmids.extend(got)
        log.info("  %s %d: %s candidate records", inst_id, year, f"{len(got):,}")
        time.sleep(0.34)
    pmids = list(dict.fromkeys(pmids))
    log.info("%s: %s distinct candidate records", inst_id, f"{len(pmids):,}")

    rows: list[dict] = []
    for start in range(0, len(pmids), batch):
        chunk = pmids[start : start + batch]
        xml_bytes = _post("efetch", {"db": "pubmed", "id": ",".join(chunk), "retmode": "xml"})
        rows.extend(_parse_batch(xml_bytes, inst_id))
        if (start // batch) % 10 == 0:
            log.info(
                "  %s: %s/%s records, %s affiliations",
                inst_id, f"{start:,}", f"{len(pmids):,}", f"{len(rows):,}",
            )
        time.sleep(0.34)  # NCBI: 3 requests/second without an API key

    df = pd.DataFrame(rows)
    df["harvested_at"] = utcnow()
    df["source_type"] = "PUBMED_AUTHOR_AFFILIATION"
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache, index=False)
    log.info("%s: harvested %s author-affiliation records", inst_id, f"{len(df):,}")
    return df


def run(y0: int = 2020, y1: int = 2026, only: list[str] | None = None) -> pd.DataFrame:
    ids = only or list(INSTITUTION_QUERIES)
    frames = []
    for n, inst in enumerate(ids, 1):
        log.info("[%d/%d] %s", n, len(ids), INSTITUTION_NAMES.get(inst, inst))
        frames.append(harvest(inst, y0, y1))
    out = pd.concat(frames, ignore_index=True)
    out.to_parquet(PROCESSED / "pubmed_author_affiliations.parquet", index=False)
    log.info("harvest complete: %s author-affiliation records across %d institutions",
             f"{len(out):,}", len(ids))
    return out
