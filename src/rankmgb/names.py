"""Readable institution names.

NIH recipient names are shouty and inconsistent — "UNIVERSITY OF CALIFORNIA,
SAN FRANCISCO", "UNIVERSITY OF TX MD ANDERSON CAN CTR", "NEW YORK UNIVERSITY
SCHOOL OF MEDICINE". Charts made from them are hard to scan.

Curated names in reference/institution_display_names_v1.csv win. Everything
else goes through a deterministic tidy-up: cut the "doing business as" tail,
expand NIH's abbreviations, drop the corporate suffixes that carry no
information in a ranking of institutions, and title-case what is left while
protecting acronyms.

Every rule here is meant to be safe for a *class* of names. A rule that would
only ever fire on one recipient belongs in the override CSV instead, where the
decision is visible and dated.
"""
from __future__ import annotations

import re

import pandas as pd

from .paths import REFERENCE

# "X DBA Y" / "X D/B/A Y". NIH is inconsistent about which side is the trading
# name ("SHIRLEY RYAN ABILITYLAB" is the modern name of the Rehabilitation
# Institute of Chicago, but "RESEARCH FOUNDATION FOR MENTAL HYGIENE" is only
# the fiscal agent for the New York State Psychiatric Institute). Keeping the
# registered entity on the left is right far more often; the exceptions are
# handled by override.
DBA = re.compile(r"\s+D\.?/?B\.?/?A\.?\s+", re.IGNORECASE)

# NIH's abbreviations, longest first so "CAN CTR" resolves before "CTR".
ABBREV = [
    (r"\bHLTH SCIENCES CTR\b", "Health Sciences Center"),
    (r"\bHEALTH SCIENCES CTR\b", "Health Sciences Center"),
    (r"\bHLTH SCI CTR\b", "Health Science Center"),
    (r"\bHEALTH SCI CTR\b", "Health Science Center"),
    (r"\bHEALTH SCIS CTR\b", "Health Sciences Center"),
    (r"\bHLTH SCIS CTR\b", "Health Sciences Center"),
    (r"\bMED BR\b", "Medical Branch"),
    (r"\bSCH OF MED/DNT\b", "School of Medicine and Dental Medicine"),
    (r"\bSCH OF MED\b", "School of Medicine"),
    # "ST" is "State" only in front of an institution word; everywhere else it
    # is "Saint" and the title-caser punctuates it. Without this, "VIRGINIA
    # POLYTECHNIC INST AND ST UNIV" came out as "... and St. University".
    (r"\bST\s+(UNIV|COLL|COL|AGRIC)\b", r"STATE \1"),
    (r"\bCAN CTR\b", "Cancer Center"),
    (r"\bCAN RESEARCH\b", "Cancer Research"),
    (r"\bMED CTR\b", "Medical Center"),
    (r"\bMEDICAL CTR\b", "Medical Center"),
    (r"\bRES INST\b", "Research Institute"),
    (r"\bCHILDRENS\b", "CHILDREN'S"),
    (r"\bWOMENS\b", "WOMEN'S"),
    (r"\bHOSP\b", "Hospital"),
    (r"\bINST\b", "Institute"),
    (r"\bUNIV\b", "University"),
    (r"\bCOLL\b", "College"),
    (r"\bCOL\b", "College"),
    (r"\bSCH\b", "School"),
    (r"\bCTR\b", "Center"),
    (r"\bHLTH\b", "Health"),
    (r"\bSCIS\b", "Sciences"),
    (r"\bSCI\b", "Sciences"),
    (r"\bRES\b", "Research"),
    (r"\bEDU\b", "Education"),
    (r"\bFDN\b", "Foundation"),
    (r"\bAGRIC\b", "Agricultural"),
    # "MED" is the noun "Medicine" after "of" and at the end of a name
    # ("PHILADELPHIA COLLEGE OF OSTEOPATHIC MED"), and the adjective
    # "Medical" everywhere else ("MED SCIS", "MED RES INST"). The lookbehind
    # keeps hyphenated company names such as "COHERE-MED" intact.
    (r"\bOF MED\b", "of Medicine"),
    (r"(?<!-)\bMED\b(?=\s*&)", "Medicine"),
    (r"(?<!-)\bMED$", "Medicine"),
    (r"(?<!-)\bMED\b", "Medical"),
    (r"\bTX\b", "Texas"),
    (r"\bNY\b", "New York"),
    (r"\bN\.J\.", "New Jersey"),
    (r"\bNJ\b", "New Jersey"),
    (r"\bMASS\b", "Massachusetts"),
    (r"\bPA\b", "Pennsylvania"),
    (r"\bSO\b", "Southern"),
    (r"\bNO\b", "Northern"),
]

# Corporate and article suffixes that add nothing when ranking institutions.
# Applied repeatedly until nothing more matches, because they stack:
# "WITS HEALTH CONSORTIUM (PTY), LTD" needs LTD off before (PTY) is at the end.
# "HEALTH SCIENCES" is stripped only after "UNIVERSITY", so that
# "COLUMBIA UNIVERSITY HEALTH SCIENCES" loses it but "RUTGERS BIOMEDICAL AND
# HEALTH SCIENCES" keeps it and stays a sentence.
STRIP_SUFFIX = [
    (r"(UNIVERSITY),?\s+HEALTH SCIENCES$", r"\1"),
    (r",?\s+\(THE\)$", ""),
    (r",\s+THE$", ""),
    (r",?\s+INC\.?$", ""),
    (r",?\s+LTD\.?$", ""),
    (r",?\s+L\.?L\.?C\.?$", ""),
    (r",?\s+L\.?L\.?P\.?$", ""),
    (r",?\s+\(?PTY\)?\.?$", ""),
]

# Stripping "SCHOOL OF MEDICINE" turns "UNIVERSITY OF MIAMI SCHOOL OF MEDICINE"
# into the university that contains it, but it would turn "MOREHOUSE SCHOOL OF
# MEDICINE" — a free-standing medical school — into "Morehouse", which is a
# different institution. Only strip when the remainder still names a parent.
SCHOOL_OF_MEDICINE = re.compile(r"^(.*?),?\s+SCHOOL OF MEDICINE$")
HAS_PARENT = re.compile(r"\b(UNIVERSITY|UNIV|COLLEGE|COLL)\b")

ACRONYMS = {
    "UCLA", "UAB", "NYU", "MIT", "UC", "USC", "UNC", "UT", "VA", "MD", "III", "II",
    "SUNY", "CUNY", "UCSF", "UCSD", "NIH", "LSU", "TCU", "SMU", "MGH", "BWH", "MGB",
    "RBHS", "CWRU", "COM", "UTMB", "UMASS", "SUNY", "CHOP", "MUSC", "OHSU", "VCU",
    "UNLV", "UTSW", "USF", "UCF", "FIU", "NJIT", "RTI", "SRI", "IBM", "DNA", "RNA",
    "HIV", "USA", "US", "UK", "PhD", "DC", "NC", "NY",
    "HSC", "UNT", "OSU", "UAMS", "GE", "A&M", "A&T",
}
# "LA" is deliberately absent from both sets: as an acronym it turned "LA JOLLA
# INSTITUTE" into "LA Jolla Institute", and as a lowercase particle it turned
# "WISCONSIN LA CROSSE" into "Wisconsin la Crosse". Plain capitalisation is
# right for every NIH recipient that contains it.
LOWER = {"of", "at", "and", "the", "for", "in", "de"}


def _cap_token(word: str) -> str:
    """Capitalise one whitespace-delimited token, respecting - / and & joins."""
    bare = word.strip("(),.")
    if bare.upper() in ACRONYMS:
        return word.upper()
    for sep in ("-", "/", "&"):
        if sep in bare:
            return sep.join(_cap_token(p) for p in word.split(sep))
    # capitalize() would leave "(charles" alone, so lift the first letter itself.
    lowered = word.lower()
    m = re.search(r"[a-z]", lowered)
    if not m:
        return word
    i = m.start()
    return lowered[:i] + lowered[i].upper() + lowered[i + 1:]


def _titlecase(s: str) -> str:
    out = []
    for i, word in enumerate(s.split()):
        bare = word.strip("(),.")
        if bare.lower() in LOWER and i > 0:
            out.append(word.lower())
        else:
            out.append(_cap_token(word))
    text = " ".join(out)
    # Possessives and initialisms the capitaliser gets wrong.
    text = re.sub(r"\bWomen'S\b", "Women's", text)
    text = re.sub(r"\bChildren'S\b", "Children's", text)
    # "ST LOUIS" gains a stop; "ST. JUDE" already has one and must not gain a second.
    text = re.sub(r"\bSt\b(?!\.)", "St.", text)
    return text


def tidy(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        return ""
    s = name.strip().upper()
    s = DBA.split(s)[0].strip(" ,")
    m = SCHOOL_OF_MEDICINE.match(s)
    if m and HAS_PARENT.search(m.group(1)):
        s = m.group(1)
    for _ in range(len(STRIP_SUFFIX)):
        before = s
        for pat, rep in STRIP_SUFFIX:
            s = re.sub(pat, rep, s)
        if s == before:
            break
    for pat, rep in ABBREV:
        s = re.sub(pat, rep.upper(), s)
    # "UNIVERSITY OF PITTSBURGH AT PITTSBURGH" — drop the campus when it merely
    # repeats a word already in the name.
    m = re.match(r"^(.*?)\s+AT\s+([A-Z .'-]+)$", s)
    if m and m.group(2).strip() in m.group(1).split():
        s = m.group(1)
    s = re.sub(r"\s+", " ", s).strip(" ,")
    return _titlecase(s)


def load_overrides() -> dict[str, str]:
    path = REFERENCE / "institution_display_names_v1.csv"
    if not path.exists():
        return {}
    t = pd.read_csv(path)
    return dict(zip(t.canonical_org_id, t.display_name))


def display_names(org_ids: pd.Series, raw_names: pd.Series) -> pd.Series:
    over = load_overrides()
    mapped = org_ids.map(over)
    return mapped.where(mapped.notna(), raw_names.map(tidy))
