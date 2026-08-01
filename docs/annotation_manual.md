# PI-affiliation annotation manual

**Version 1.1, 2026-08-01.** Changes to this manual are versioned. When a
definition changes, every record classified under the prior definition is
reprocessed.

Change in 1.1: publication affiliations (tier F) became the primary source of a
PI's department and NIH's `ORG_DEPT` (tiers A and B) became an independent
comparator, reversing the order in 1.0. No tier definition changed and no tier
letter was reassigned, so no record needs relabelling, but every departmental
total is recomputed under the new primary source. Sections 1, 5, 9, 12 and 13
are affected.

This manual must be read before any record is classified. It exists so that two
annotators working independently reach the same answer, and so that a reader of
the final rankings can tell exactly which rule produced any given number.

## 1. Why this layer exists

NIH's `ORG_DEPT` field is populated only for recipients NIH classifies as
schools. Independent hospitals and research institutes carry no department code
at all. In FY2025 that covers **30.0% of US NIH dollars**, including the largest
and third-largest uncoded recipients in the country, Massachusetts General
Hospital and Brigham and Women's Hospital.

No departmental ranking built from `ORG_DEPT` alone can contain MGH or BWH. But
the deeper problem is not the gap, it is the asymmetry: a ranking that reads
`ORG_DEPT` for universities and reads something else for hospitals measures the
two with different instruments and reports the difference as a fact about the
institutions. So this layer is not a patch applied where NIH data runs out. It
is the primary measurement, applied identically to every institution in the
comparison set, with `ORG_DEPT` retained as an independent check.

## 2. Investigator identity resolution

1. The NIH PI profile ID is the identity key whenever NIH supplies one. In
   FY2021–FY2025, NIH supplies one for **100%** of named PIs, so name-based
   identity is not used for NIH-internal linkage.
2. Names are recorded as variants against the identifier, never as the key.
3. A name alone is never sufficient to establish identity. When external
   evidence (a publication, a faculty page) must be matched to an NIH PI, match
   on surname plus first initial **and** institution, and treat any
   surname/initial pair covering more than one distinct forename at that
   institution as **ambiguous, and do not match it**.
4. Proposed merges and splits are recorded in
   `reference/overrides_v1.csv` with a reason, reviewer, timestamp, and the
   evidence. Every merge is reversible: the pre-merge identifiers are retained.

## 3. Institution normalization

1. The NIH `ORG_IPF_CODE` is the institution key. It is stable across name
   changes and is preferred over any name string.
2. `reference/organization_crosswalk_v1.csv` documents only the deviations from
   that default: legal name changes, mergers, system membership, and the dated
   effective ranges for each.
3. **MGH and BWH are always reported separately.** The combined
   Mass General Brigham figure is a third row built from the dated membership
   crosswalk, never from name matching.
4. **Harvard Medical School is a separate recipient.** An award to MGH or BWH is
   never reassigned to Harvard, and a Harvard award is never reassigned to an
   affiliated hospital.

## 4. Date assignment

The index date for an award-year is, in order:

1. `BUDGET_START`;
2. `PROJECT_START`;
3. the fiscal-year midpoint (1 April).

The fallback actually used is stored on every record in `index_date_source`.
The affiliation credited to an award is the one held **at the index date**, not
the investigator's current affiliation.

## 5. Evidence hierarchy

The tier letters are stable identifiers, not a ranking. They were assigned in
draft and are kept so that already-coded records do not have to be relabelled.
**Tier F, publication affiliations, is the primary source.** Tier A, NIH's own
department field, is a comparator. The rest are ordered by how directly they
attest an appointment, and where two of them conflict the more directly attested
and more closely dated one wins. Record the tier on every record.

| Tier | Source | Role | Status |
|---|---|---|---|
| F | Publication affiliations close to the award date | **Primary measurement.** Available for every institution in `reference/pubmed_institutions_v1.csv`, so it is the only source that lets hospitals and universities be ranked against each other | Implemented, PubMed author affiliation strings |
| C | Dated institutional faculty or departmental pages | Would supersede F where both exist: a roster states the appointment directly, and it reaches faculty who do not publish | **Not yet collected** |
| D | Archived institutional webpages (durable snapshots) | Dates a tier-C roster to a point in time, which is what makes it usable for a historical award | **Not yet collected** |
| E | Institutional CVs, biosketches, ORCID employment history | Per-person, dated, and independent of both the institution's website and PubMed | **Not yet collected** |
| A | NIH `ORG_DEPT` for the contact PI | **Independent comparator, not the measurement.** Exists only for recipients NIH classifies as schools, so it cannot be primary without measuring hospitals and universities by different rules | Implemented, deterministic |
| B | The same PI's dated NIH `ORG_DEPT` on another award, carried within 5 years | Extends the tier-A comparator across a PI's award history; inherits tier A's coverage gap | Implemented, confidence by time gap |
| G | Grant-specific announcements, other dated authoritative sources | Case-by-case corroboration | **Not yet collected** |

Tiers A and B are never mixed into a publication-derived total, and a
publication-derived total is never substituted into an `ORG_DEPT` ranking. They
are separate columns that get compared. `src/rankmgb/agreement.py` reports the
comparison as sensitivity, precision and Cohen's κ on the subset where NIH
supplies a department, and lists the institutions where no comparison is
possible instead of dropping them.

**A current faculty page is never used to assign a historical affiliation
without dated corroboration.** For FY2021 records this means an archived
snapshot contemporaneous with the award, not today's roster.

## 6. Department and specialty definitions

Governed by `reference/surgical_taxonomy_v1.csv` (concept definitions and
inclusion/exclusion rules) and `reference/department_string_patterns_v1.csv`
(the ordered, machine-readable patterns that map free text to those concepts).
Exclusion patterns are evaluated first and win.

Classification rests on the investigator's **organizational appointment**, not
on whether they operate or study surgical topics. A bioengineer appointed in a
Department of Surgery counts. A surgeon appointed in a Department of Medicine
does not.

Preserve the original department and division wording verbatim on every record
alongside the mapped category.

### Borderline categories

| Category | Narrow | Broad | Rule |
|---|---|---|---|
| Ophthalmology | No | Yes | Included only with dated evidence the unit is institutionally organized as a surgical department |
| Obstetrics & gynecology | No | Yes | Procedural but not enumerated in the taxonomy; sensitivity analysis only |
| Anesthesiology | No | No | Perioperative, not surgical |
| Oral & maxillofacial surgery | No | No | Not separable from dentistry in NIH coding |
| Dermatology | No | No | Procedural but organizationally nonsurgical |
| Surgical pathology | No | No | A pathology unit; the word "surgical" must not trigger inclusion |

## 7. Joint appointments

Retained explicitly. A PI may hold more than one valid affiliation at an award
date. Which of them qualifies for a given analysis is fixed **before** rankings
are generated:

- **Contact-PI surgical model.** The award counts if any qualifying surgical
  appointment is held at the index date.
- **Primary-appointment sensitivity.** Only the appointment the institution
  designates primary counts. Awards where no primary can be established are
  reported as unknown, not dropped.

## 8. Faculty transitions

An affiliation spell ends at the last dated evidence for it and the next begins
at the first dated evidence for the successor. The gap between them is left
**unknown**; it is never bridged by interpolation. An award whose index date
falls in a gap is unresolved and is reported as such.

## 9. Confidence categories

| Level | Meaning |
|---|---|
| high | ≥5 concordant dated records within the window with no conflicting evidence |
| medium | 2–4 concordant dated records |
| low | A single record |
| unknown | No qualifying evidence. **Retained as unknown; never forced into a class.** |

Two floors are published for every departmental total. **Corroborated** admits
high and medium only. **All evidence** additionally admits single-record
matches, among which spot-checking found real false positives. The two are
reported side by side; the gap between them is an uncertainty band, not a range
of equally good estimates.

Tier A and tier B carry their own confidence when they are used as the
comparator: tier A is deterministic, and tier B is graded by the gap in years
between the award and the award the department was carried from. Comparator
confidence is never merged into the primary confidence field.

## 10. Inter-rater agreement

Before any classification at scale, two annotators independently code a
stratified random sample (minimum 200 award-years, oversampling high-value
awards and hospital recipients). Report Cohen's κ separately for:

- investigator identity
- institution
- department
- surgical versus nonsurgical
- affiliation start and end dates

κ below 0.80 on any dimension sends the manual back for revision before
production coding resumes.

## 11. Adjudication

Disagreements go to a third reviewer who did not produce either original code.
The adjudicated value, the reviewer, the timestamp, and the reasoning are
written to `reference/overrides_v1.csv`. **No ranking may depend on an
undocumented spreadsheet edit.** Every override is structured, version-controlled
data.

## 12. Automated classification

Automated classifications prioritise records for review. They are **not ground
truth** until validated against a human-coded sample. Every published table
reports the proportion of affiliations derived from:

- publication affiliations (tier F, the primary measurement)
- other authoritative dated sources (tiers C, D, E, G)
- NIH data used as a comparator (tiers A and B)
- inference
- manual adjudication

Agreement against NIH's field is not a substitute for the human-coded sample in
section 10. It measures whether two automated sources agree, which they can do
while both being wrong.

## 13. Known limitation of the v1.0 layer

The v1.0 layer implements tiers A, B and F only, and F is the primary
measurement. Tier F captures investigators who publish with a departmental
affiliation string and misses those who do not. That miss is not random. It
falls hardest on basic scientists holding appointments in surgical departments,
on investigators whose institution standardises affiliation strings to the
hospital or university name alone, and on anyone whose publishing is sparse near
the award's index date. Every departmental total derived this way is therefore
an **evidence-backed lower bound** and must be labelled as such wherever it
appears, for MGH and BWH and equally for every comparator institution.

Because the same lossy method now runs for all institutions, the ranking is
internally comparable even while each row is low: the shortfall is in the same
direction everywhere. It is not uniform in size, though, so the rank order is
also provisional until the size of the shortfall per institution is measured.
That measurement is the sensitivity column in `agreement_by_institution.csv`,
which reports, for institutions NIH does code, what fraction of the awards NIH
calls surgical the publication method also catches.

The layer is also bounded by the registry. An institution absent from
`reference/pubmed_institutions_v1.csv` has no tier-F evidence at all and cannot
appear in a publication-derived ranking.

Closing the gap requires tiers C, D and E: dated faculty rosters, archived
snapshots of them, and per-person employment history. That work is scoped in
`docs/report.md` section 9.
