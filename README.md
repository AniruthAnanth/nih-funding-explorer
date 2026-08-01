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
schools. In FY2025 that leaves **30.0% of US NIH dollars, $10.5B, with no
department code at all**, including the largest and third-largest uncoded
recipients in the country, MGH ($639M) and BWH ($411M). Also uncoded:
Vanderbilt University Medical Center, Mayo, Fred Hutch, Boston Children's,
Memorial Sloan Kettering, CHOP, Dana-Farber and Cedars-Sinai.

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
| `harvest` | Collects PubMed author affiliations for every institution in the registry. Slow, cached, **not part of `all`** |
| `surgery` | Publication-derived department for every institution, plus the NIH agreement analysis and the like-for-like surgery ranking |
| `mgb` | The roll-up-specific attribution and context tables |
| `biblio` | Flagship-journal output and citation impact from NIH's iCite |
| `figures` | Regenerates every figure from the ranked CSVs |
| `validate` | Reconciliation and quality checks |

`harvest` is excluded from `all` on purpose: it makes thousands of NCBI E-utils
requests and takes about an hour. Run it once, then `surgery` and `mgb` read the
cached parquet files under `data/interim/`. Both stages exit with an error
rather than a partial result if the harvest has not been run.

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
  mgb_surgery.py            the MGH/BWH attribution chain and its patterns
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
docs/                       report, annotation manual, and the published static
                            site (index.html, assets/, data/, mirrored tables
                            and figures)
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

Two independent measurements, never added together and never substituted for
one another.

**Primary: publication-derived** (`surgical_attribution.py`, evidence from
`pubmed_evidence.py`). For each of the 32 institutions in
`reference/pubmed_institutions_v1.csv`, dated PubMed author affiliation strings
are classified against `reference/department_string_patterns_v1.csv`, matched to
NIH contact PIs, and credited to an award when the evidence falls within three
years of the award's index date. The same chain runs for MGH and for Duke, which
is what makes the ranking internally comparable. Guards: institution and
department must be adjacent in the same segment of one string; composite
multi-institution affiliation blocks are rejected outright; a surname plus first
initial covering more than one forename at that institution is dropped as
ambiguous; anything unmatched stays unknown.

**Secondary: NIH `ORG_DEPT`** (`agreement.py`). On the subset where NIH supplies
a department, the two measurements are compared and the result published as
sensitivity, precision and Cohen's kappa, overall and per institution.
Institutions NIH does not code appear in `agreement_uncomparable.csv` rather
than being dropped, because "no comparison is possible here" is the finding.

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
unmapped activity codes rather than silently accepting them. 16 of 16 checks in
`outputs/tables/validation_report.csv` currently pass.

`tests/test_patterns.py` pins the two classification bugs an audit found: the
urology pattern `urolog` also matched "ne**urolog**y", and composite "From the …"
affiliation blocks were crediting one author's department to every co-author.
Run it before changing `reference/department_string_patterns_v1.csv`.

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
5. **The publication registry is 32 institutions, not all of them.** They cover
   every one of the top 20 NIH-coded departments of surgery and 80.4% of coded
   surgery dollars, but an institution outside the registry has no
   publication-derived department at all. Adding one means adding a row to
   `reference/pubmed_institutions_v1.csv` and re-running `harvest`.
6. **M-series awards do not exist in this period.** M01 GCRC was retired into
   the CTSA program. `m_award_years` is zero everywhere by design, not by
   omission; the successors UL1 and UM1 sit in the U family.
7. **`reference/overrides_v1.csv` is referenced by `config.yaml` and the
   annotation manual but has not been created.** No adjudicated override exists
   yet, so nothing currently depends on it.
