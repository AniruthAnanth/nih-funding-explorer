# NIH funding rankings by institution and department

A reproducible longitudinal census of NIH extramural grants and cooperative
agreements FY2021–FY2025, ranked three ways: by recipient organization, by
department, and by institution–department pair. 315,717 award-years across 5,007
organizations and 9,683 institution–department pairs, plus a second measure of
each department built from publication output and citation impact.

An interactive explorer over the whole dataset is published from `docs/`.

Every published table, ranking and figure is regenerated from archived source
files by the code here. There are no manual spreadsheet edits.

## Read this first

**NIH does not assign a department to independent hospitals.** The `ORG_DEPT`
field in NIH ExPORTER is populated only for recipients NIH classifies as
schools. In FY2025 that leaves **30.0% of US NIH dollars, $10.5B, with no usable
department code**, including the largest and third-largest uncoded recipients in
the country, MGH ($639M) and BWH ($411M). Also uncoded: Vanderbilt University
Medical Center, Mayo, Fred Hutch, Boston Children's, Memorial Sloan Kettering,
CHOP, Cincinnati Children's, Beth Israel Deaconess, Dana-Farber and
Cedars-Sinai.

That 30.0% is the wide definition: the field is absent (`__MISSING__`) or
carries one of the three placeholders NIH writes instead of a department,
`NONE`, `MISCELLANEOUS` or `NO CODE ASSIGNED`. On `__MISSING__` alone the same
quantity is **25.2%, $8.84B**. The institution counts move with the definition
too: of the 140 US institutions above $50M in FY2025, 27 are at exactly 100%
uncoded on the wide definition and 25 on the narrow one. Every figure of this
kind in this repository means the wide definition unless it says otherwise.

A departmental ranking taken straight from NIH data silently omits all of them.
That is why **`ORG_DEPT` is not the primary source of PI department in this
pipeline.** The primary source is dated PubMed author affiliations, applied
identically to every institution in the comparison set so that hospitals and
universities are measured by one rule. `ORG_DEPT` is retained as an independent
comparator on the subset where NIH supplies one, and the agreement between the
two is published. See `docs/report.md` section 3.

## Quick start

```bash
python3 run_pipeline.py all
```

Any stage runs on its own, for example `python3 run_pipeline.py rank`. Stages
are ordered; each depends on the tables the ones above it wrote.

| Stage | What it does |
|---|---|
| `acquire` | Downloads and checksums the NIH ExPORTER annual project files |
| `build` | Validates schema, applies inclusion rules, annotates, writes the analysis-ready tables |
| `rank` | Ranked tables at three grains (institution, department, institution-department) for three reporting periods |
| `affiliation` | Investigator identity resolution and dated affiliation spells |
| `harvest` | Collects PubMed author affiliations one query per institution, for every institution in the registry. Slow, cached, **not part of `all`** |
| `surgery` | Publication-derived department for every institution, plus the NIH agreement analysis and the like-for-like surgery ranking. Reads the `harvest` cache |
| `profile` | One PubMed query pair per contact PI at every recipient NIH leaves uncoded, with no topic filter, giving each investigator a department profile. Writes `data/processed/pi_departments.parquet`. Slow, cached per institution, resumable, **not part of `all`** |
| `mgb` | Departmental totals for every recipient NIH leaves uncoded — MGH and BWH, and the peer hospitals they are ranked against — from the per-investigator majority rule, plus the per-investigator evidence file and the combined context tables. Consumes what `profile` wrote |
| `mgb_legacy` | The superseded per-award evidence matcher that `mgb` replaced, kept runnable so the change of method can be reproduced and compared rather than asserted. Reads the `harvest` cache. **Not part of `all`** |
| `biblio` | Flagship-journal output and citation impact from NIH's iCite |
| `figures` | Regenerates every figure from the ranked CSVs |
| `validate` | Reconciliation and quality checks |

`harvest` and `profile` are excluded from `all` on purpose: between them they
make tens of thousands of NCBI E-utils requests and take hours. Each caches one
parquet per institution under `data/interim/` and is resumable from that cache.
Run each once, then `surgery`, `biblio` and `mgb_legacy` read
`data/processed/pubmed_author_affiliations.parquet` and `mgb` reads
`data/processed/pi_departments.parquet`. Every one of those stages exits with an
error rather than a partial result if its input is missing, which means `all` on
a clean checkout stops at `surgery`
until `harvest` has run and at `mgb` until `profile` has run.

The static site under `docs/` is built by `site.build()`, which is not yet a
`run_pipeline.py` stage. Call it directly after `figures`:

```bash
python3 -c "import sys; sys.path.insert(0,'src'); from rankmgb import site; site.build()"
```

## Layout

```
config/config.yaml          every analytic choice that could change a number
reference/                  dated crosswalks: mechanisms, organizations,
                            departments, surgical taxonomy, string patterns,
                            display names, roll-up definitions, the PubMed
                            institution registry
src/rankmgb/                the pipeline
  acquire.py                archiving with checksums and schema fingerprints
  schema.py                 the schema contract; fails loudly on drift
  load.py                   inclusion rules, PI explosion, duplicate resolution
  annotate.py               joins to the dated reference tables
  names.py                  readable institution names for tables and charts
  affiliation.py            investigator identity and affiliation spells
  pubmed_evidence.py        the PubMed harvest, one query per institution
  surgical_attribution.py   publication-derived department, all institutions
  agreement.py              publication method vs NIH ORG_DEPT: sensitivity,
                            precision, Cohen's kappa, and what cannot compare
  surgery_ranking.py        the like-for-like surgery ranking and its figures
  bibliometrics.py          flagship-journal output and citation impact via iCite
  pi_department.py          per-investigator department profiles for the
                            recipients NIH leaves uncoded, and the departmental
                            totals built from them; the `profile` and `mgb`
                            stages
  mgb_surgery.py            the superseded per-award MGH/BWH attribution chain,
                            plus the affiliation classifier and taxonomy sets
                            the current rule still imports; the `mgb_legacy`
                            stage
  mgb_context.py            context tables for the reconstructed roll-ups
  rank.py                   ranked tables
  figures.py                figures
  site.py                   compact JSON payload for the static site in docs/
  validate.py               reconciliation and sensitivity
data/raw/                   immutable source archive + manifest.json
data/interim/               cached PubMed harvest, one parquet per institution
data/processed/             analysis-ready tables
outputs/tables/             every published table
outputs/figures/            every published figure
logs/                       inclusion audit, duplicate resolution, run log,
                            harvest log
docs/                       report, annotation manual, validation write-up, and
                            the published static site (index.html, assets/,
                            data/, mirrored tables and figures). The mirror is a
                            subset: the superseded institution-level ranking
                            `surgery_ranking_FY*.csv` and the superseded
                            per-award evidence file
                            `mgb_surgical_award_years_evidence.csv` stay under
                            outputs/tables/ only
tests/test_patterns.py      regression tests for the affiliation classifier
```

## Unit of analysis

One row per **`APPLICATION_ID`**, which is an award-year. Both counts are
reported everywhere:

- **award-years**, the count of annually funded applications;
- **distinct core projects**, so continuation years are not read as new grants.

## Inclusion rules

Applied as named, counted filters. The full cascade is in
`logs/inclusion_audit.csv`.

| Rule | Effect |
|---|---|
| `nih_administering_ic` | ExPORTER also carries VA, AHRQ, CDC, FDA and HRSA awards; only the 27 NIH ICs are retained |
| `grants_and_cooperative_agreements` | Removes intramural research, R&D contracts, interagency agreements |
| `activity_code_prefix` | Removes `Z*` intramural and `N*` contract activity codes |
| `parent_awards_only` | Subproject rows are nested inside a parent that already carries the full `TOTAL_COST`; keeping both would double count |

FY2021–FY2025 yields **315,717 award-years**, $33.2B to $35.4B per year in US
obligations.

## How a PI's department is determined

Three measurements, never added together and never substituted for one another.

**Shipped for the uncoded recipients: the per-investigator majority rule**
(`pi_department.py`, stage `profile` then stage `mgb`). One PubMed query pair
per contact PI, with no topic filter, classifying that author's own affiliation
strings and taking the department holding a majority of them. This is what
produces every reconstructed departmental figure published for MGH, BWH and
their uncoded peers. Validated at **κ 0.916, sensitivity 91.9%, precision
100.0%** on a 300-PI sample; read `docs/validation/README.md` before quoting any
of those three, because the sample is 1:1 case-control at 50% surgical
prevalence against a population rate near 7%, it is drawn only at universities
and applied at hospitals, and it scores one binary call — surgical or not —
which says nothing about whether a PI labelled `INTERNAL_MEDICINE` is in a
department of medicine. `summarise_all_departments`, which writes
`mgb_departments_all.csv`, additionally selects on `modal_department.notna()`,
the plurality department rather than the validated majority, and its docstring
misdescribes this as "the same validated majority rule".

**Comparator across all harvested institutions: publication-derived**
(`surgical_attribution.py`, evidence from `pubmed_evidence.py`). For each of the
32 harvested institutions in
`reference/pubmed_institutions_v1.csv`, dated PubMed author affiliation strings
are classified against `reference/department_string_patterns_v1.csv`, matched to
NIH contact PIs, and credited to an award when the evidence falls within three
years of the award's index date. The same chain runs for MGH and for Duke, which
is what makes the ranking internally comparable. Guards: institution and
department must be adjacent in the same segment of one string; composite
multi-institution affiliation blocks are rejected outright; a surname plus first
initial covering more than one forename at that institution is dropped as
ambiguous; anything unmatched stays unknown. Scored against NIH's field this
matcher reaches **Cohen's κ 0.267**, and that is a different number about a
different rule from the κ 0.916 above. The two are not interchangeable and no
document should quote one in support of the other.

**Independent check: NIH `ORG_DEPT`** (`agreement.py`). On the subset where NIH
supplies a department, the publication matcher and NIH's field are compared and
the result published as sensitivity, precision and Cohen's kappa, overall and
per institution. Institutions NIH does not code appear in
`agreement_uncomparable.csv` rather than being dropped, because "no comparison
is possible here" is the finding.

One caution on the classifier both publication measurements share. Priority 99
in `reference/department_string_patterns_v1.csv` is a bare `\bsurg` mapping to
`OTHER_EXPLICIT_SURGERY`, a specialty inside the Department of Surgery set. It
is ranked last so a named department always beats it, but it is not carrying a
small load: `OTHER_EXPLICIT_SURGERY` is **12.6% of the current reconstructed MGB
Department of Surgery total**, 12 investigators and 60 award-years, and about
two thirds of the distinct MGH and BWH strings in that bucket were matched by
the bare catch-all rather than by the named pattern above it. The wider evidence
base is stronger than that suggests — median 22 classified affiliation strings
per investigator in the surgical set, and only 2 of the 80 decided on a single
string.

Anything derived from the publication method is a **lower bound**. Any table or
figure that publishes such a number has to say so on its face, either in an
`evidence_basis` column or in the subtitle. Treat that as a review checklist
item, not a guarantee the code enforces.

## Attribution models

| Model | Question it answers | Status |
|---|---|---|
| Recipient-institution | Which organizations directly received NIH awards? | Complete |
| Contact-PI departmental | Which department did the lead PI sit in? | Publication-derived for the 32 registry institutions; NIH `ORG_DEPT` retained as the comparator |
| Any-PI participation | Which departments had any named PI on the award? | Requires tiers C–E of the evidence hierarchy |
| Fractional | Additive allocation across represented institutions | Rule declared in `config.yaml` before ranking; not combined with participation dollars |

Participation dollars intentionally double count collaborative awards and can
never be summed to a national total. They are labelled as such wherever they
appear.

## Organizations

`ORG_IPF_CODE` is the institution key, stable across name changes.
`reference/organization_crosswalk_v1.csv` documents only the deviations:
mergers, system membership, effective dates. Roll-ups are defined in
`reference/rollups_v1.csv`.

- MGH and BWH are always reported **separately**.
- `MGB_CORE` (MGH + BWH) is the headline roll-up, generated from the dated
  membership crosswalk, never from name matching.
- `MGB_SYSTEM` adds Mass Eye and Ear, McLean and Spaulding as a sensitivity.
- **Harvard Medical School stays its own recipient.** MGH and BWH awards are
  never reassigned to Harvard and Harvard awards are never reassigned to a
  hospital. `HARVARD_ENTITIES` exists for context and is never merged with MGB.
- Roll-up rows carry `is_rollup = True`. Filter them out before reading the
  `rank_total_funding` column as a rank among real institutions.

**One recipient is knowingly split across two rows.** NIH issued Fred
Hutchinson a new `ORG_IPF_CODE` when the Fred Hutchinson Cancer Research Center
merged with the Seattle Cancer Care Alliance in April 2022, so FY2021–FY2025
contains both `IPF861001` ($430M, awarded under the old code) and
`IPF10068583` ($1,439M). They are one legal entity and the combined figure is
$1.87B. They are left separate, and labelled "Fred Hutch" and "Fred Hutch
(pre-2022)", rather than merged: merging them means changing a
`canonical_org_id`, which is the key the per-investigator profile cache and
every ranked table are built on, and the payoff is confined to an institution
with two surgical investigators in the whole period. A search that scans for
similar unnormalized pairs above $50M finds no others — Vanderbilt University
and Vanderbilt University Medical Center are separate legal entities after the
2016 split, and Harvard Medical School is separate by design.

Display names come from `names.py`. Curated entries in
`reference/institution_display_names_v1.csv` win; everything else goes through a
deterministic tidy-up that expands NIH's abbreviations, drops corporate suffixes
and title-cases the remainder while protecting acronyms. A rule that would fire
on only one recipient belongs in the override CSV, not in the code.

## Reproducibility

`outputs/workflow_manifest.json` records the config, the source-file manifest
with SHA-256 checksums, package versions, and the git commit for each run.
`data/raw/manifest.json` records for every source file: URL, download date,
fiscal year, byte size, checksum, member name, column count, and a header
fingerprint. Re-running `acquire` verifies checksums rather than re-downloading;
a changed checksum is a hard error and the prior digest is retained as
`supersedes_sha256`.

The pipeline fails explicitly on schema drift, implausible record counts and
unmapped activity codes rather than silently accepting them. 17 of 17 checks in
`outputs/tables/validation_report.csv` currently pass.

`tests/test_patterns.py` pins the two classification bugs an audit found: the
urology pattern `urolog` also matched "ne**urolog**y", and composite "From the …"
affiliation blocks were crediting one author's department to every co-author.
Run it before changing `reference/department_string_patterns_v1.csv`.

## Verifying a number before you quote it

`docs/validation/verify_email_claims.py` re-derives every numeric claim that has
been sent to a reader, from the published tables, and prints each one beside the
file it came from:

    python3 docs/validation/verify_email_claims.py

88 claims, non-zero exit if any fails. It exists because a claim can be true of
the data and still be wrong on the page. Two failures it now guards against both
happened here: quoting a figure that had since been regenerated, and pairing a
correct numerator with the wrong denominator. "The reconstruction recovers
$10.86M, 77.7% of BRIMR's figure" was two true numbers in a false sentence --
$10.86M is 75.36% of BRIMR's total, and 77.70% is what you get once the $336,630
NIH codes directly is added in.

## External benchmarks

Two outside sources, both archived under `reference/external/` with URL and
retrieval date, both checked on every run by `src/rankmgb/external.py`:

| Benchmark | Result |
|---|---|
| BRIMR FY2025 departments of surgery, matched institution by institution | 55 of 75 agree **to the dollar**, 60 within 1% |
| MGH Department of Surgery, its own published 2024 figures | reconstruction recovers 87.9% of $35M |
| BRIMR's Vanderbilt line, where NIH codes almost nothing | coded + reconstructed is 77.7% of $14.4M |

The first says the NIH-coded side is read correctly. The second and third are the
only outside numbers that exist to test the reconstruction, and both land below
100%, which is what "lower bound" has to mean to be worth writing down.

## Known limitations

1. **NIH funding is not revenue.** These are NIH obligations only. No
   philanthropy, industry, foundation, institutional or clinical revenue.
2. **NIH data is not a world ranking.** Non-US organizations received 0.69% of
   NIH funding in FY2021–FY2025 and China's five NIH recipients total $5.9M
   across the period. NIH data cannot rank a Chinese or European department
   against a US one. That needs OpenAlex, Dimensions or national funder data.
3. **NIH's department taxonomy is coarse.** 47 department values. Cardiac,
   vascular, transplant, colorectal, surgical oncology and trauma all collapse
   into `SURGERY`.
4. **The affiliation layer is incomplete.** Tiers A, B and F of the evidence
   hierarchy in `docs/annotation_manual.md` are implemented; C, D, E and G are
   not. Every publication-derived departmental figure is a lower bound.
5. **The publication registry is 57 institutions, not all of them.** The 32
   harvested for `surgical_attribution.py` cover every one of the top 20
   NIH-coded departments of surgery and 80.6% of coded surgery dollars, $2.06B
   of $2.56B. The other 25 are the uncoded clinical recipients profiled per
   investigator by `pi_department.py`; they carry a query and a regex but no
   `harvest` run, so they have a reconstructed department and no
   institution-level affiliation evidence. An institution outside the registry
   has neither. Adding one means adding a row to
   `reference/pubmed_institutions_v1.csv`, then re-running `harvest` for the
   agreement tables or the pi-departments profiling for the reconstructed ones.
6. **The bibliometric comparison set inherits the bias it is meant to
   correct.** `outputs/tables/bibliometrics_surgery.csv` is 33 rows per period:
   the 32 harvested institutions plus the MGB_CORE roll-up. Those 32 were picked
   as the top 20 NIH-coded departments of surgery and their neighbours, which
   keys the set on NIH's coding. Mayo, Vanderbilt, Memorial Sloan Kettering,
   Boston Children's, Fred Hutchinson, CHOP, Dana-Farber and Cedars-Sinai are
   absent because NIH codes no department for them, and so are five of the 30
   largest US recipients: University of Washington ($2.71B), UNC Chapel Hill
   ($2.64B), Mount Sinai ($2.22B), USC ($1.70B) and OHSU ($1.44B). No statement
   from that table is a national ranking. MGB_CORE also leads on total citations
   only as a merged entity — MGH has 68,561 and BWH 68,131 over five years,
   while Johns Hopkins leads these 32 at 79,811.
7. **M-series awards do not exist in this period.** M01 GCRC was retired into
   the CTSA program. `m_award_years` is zero everywhere by design, not by
   omission; the successors UL1 and UM1 sit in the U family.
8. **`reference/overrides_v1.csv` is referenced by `config.yaml` and the
   annotation manual but has not been created.** No adjudicated override exists
   yet, so nothing currently depends on it.
