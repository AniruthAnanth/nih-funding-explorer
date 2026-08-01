# Validating the department rule

NIH publishes a department code only for recipients it classifies as schools.
30.0% of FY2025 US NIH dollars sit at recipients that get none — counting the
field absent or set to `NONE`, `MISCELLANEOUS` or `NO CODE ASSIGNED`; 25.2% on
absence alone — so those departments have to be derived some other way. This
directory holds the experiment that decides whether the substitute measures the
same thing.

## Design

Sample 300 contact PIs at institutions where NIH *does* publish a department,
150 whose NIH department is surgical and 150 not. For each, pull every PubMed
record for that author at that institution **with no topic filter**, classify
each of the author's own affiliation strings against
`reference/department_string_patterns_v1.csv`, and score candidate decision
rules against NIH's own field.

The unfiltered pull is the point. The production harvest in `pubmed_evidence.py`
queries `institution AND surgery`, which is right for finding surgical papers
and wrong for deciding a department: it never collects the non-surgical papers
that would form the denominator.

Three properties of that design bound everything in the results below, and they
are stated first because the headline numbers cannot be read without them.

**The draw is 1:1 case-control, and the population is not.** `PER_ARM = 150`
fills both arms to the same size, so surgical prevalence in the sample is 50%.
In the frame the sample comes from — contact PIs at the 32 harvested
institutions, excluding the four non-department placeholder codes — it is
**1,861 of 26,232, or 7.1%**. The same quantity counted in award-years is 8,229
of 116,660, 7.05%, which is the figure `outputs/tables/agreement_overall.csv`
carries. Precision and κ both depend on prevalence, so neither transfers from a
50% mix to a 7% one.

**The sample is drawn only where NIH publishes a department.** `sample_pis()`
has to score against `nih_org_dept`, so all 300 PIs sit at universities. The
rule is then applied at hospitals, where NIH publishes nothing. That gap is the
whole reason the method exists and it is the one thing this experiment cannot
measure.

**One binary outcome is scored: surgical or not.** `NIH_SURGICAL` is six
department codes and every rule below is a true/false call against that set.
Nothing here tests whether a PI the rule labels `INTERNAL_MEDICINE` is in a
department of medicine, or `PEDIATRICS` in a department of pediatrics. Every
non-surgical reconstructed department published anywhere in this project rests
on an extension of the rule that was never scored.

## Results

| Rule | n | Sensitivity | Precision | Cohen's κ |
|---|---|---|---|---|
| Any surgical affiliation | 260 | 99.3% | 96.4% | 0.954 |
| Modal department surgical | 260 | 92.6% | 100.0% | 0.923 |
| **Majority (>50%) — shipped** | 260 | 91.9% | **100.0%** | **0.916** |
| Share > 70% | 260 | 78.7% | 100.0% | 0.779 |

260 of 300 sampled investigators resolve to a department (87%), 136 of the
surgical arm and 124 of the non-surgical one. The rest are published as unknown
rather than guessed.

Modal and majority are within noise of each other on this sample and share the
same precision. Majority is adopted as the more conservative claim: a plurality
is weaker evidence of an appointment than an outright majority.

That distinction is not honoured everywhere in the code.
`pi_department.summarise_surgery` applies the majority rule as validated, but
`pi_department.summarise_all_departments`, which produces
`outputs/tables/mgb_departments_all.csv`, selects rows on
`modal_department.notna()` — the plurality department, with no majority
threshold at all. Its docstring says "the same validated majority rule
produces all of them", and that is not what it does. The two rules scored
within noise on the surgical call, κ 0.923 against 0.916, which is the only
evidence that the substitution is harmless, and that evidence covers the
surgical call alone.

The majority rule is adopted for the published figures. Precision matters more
than recall here because an entire award is credited to a department on the
strength of the call.

**Read the 100.0% as a sample statistic, not as a population precision.** It is
100.0% because the rule returned zero false positives on the 124 resolved
non-surgical PIs. Zero of 124 puts the 95% upper bound on the false-positive
rate at 3/124, about 2.4%. Carry that upper bound to the 7% base rate above,
holding sensitivity at 91.9%, and precision comes out near **75%**: the true
positives are 0.919 × 0.07 of the population and the false positives are up to
0.024 × 0.93 of it, which is a quarter of everything the rule calls surgical.
A published departmental total is therefore consistent with one award-year in
four being misassigned, not with none. The honest reading of the table is that
no false positive was observed in 124 tries at an artificial 50% prevalence.

"Any surgical affiliation" is the higher-recall alternative and is scored in the
table above, but it is not published as a second set of figures. There is one
shipped rule and one set of statistics measured on it; the `all_evidence` floor
that used to sit beside `corroborated` has been withdrawn, because it selected
the identical set of investigators and the band between the two was zero wide.

## How this number moved

Three defects were found and fixed by this experiment, and each is worth knowing
because any one of them alone would have sunk the method:

1. The classifier had 19 positive patterns and **every one was surgical**, so
   "Department of Medicine" returned nothing and was dropped. The
   share-of-affiliations-that-are-surgical statistic was therefore 1.0 for
   anyone the classifier recognised at all — 127 of 132 investigators scored
   exactly 1.0. κ was 0.21.
2. After 25 non-surgical patterns were added, the generic `\bsurg` catch-all was
   still ranked above every one of them, so any string mentioning surgery
   incidentally beat a named department. A named department now always beats a
   bare keyword.
3. Authors were matched on surname plus first initial, which pools distinct
   people. The key "Jain R" at Massachusetts General collects Rohil Jain in the
   Department of Surgery and Radhika Jain in Internal Medicine, and handed one
   of those departments to Rakesh Jain, who is in Radiation Oncology. The match
   now requires the whole first name wherever NIH supplies one, and refuses the
   match outright when NIH supplies only an initial and more than one forename
   answers to it. That refusal costs coverage, and it is the right trade: an
   unresolved investigator is published as unknown, a mismatched one silently
   moves money between departments. 260 of 300 sampled PIs resolve under the
   shipped matcher (`logs/validate_final.log`); the 40 that do not are the price.

κ moved 0.21 → 0.825 → 0.827 → 0.906 → 0.916, and precision 90.0% → 95.6% →
98.1% → 99.1% → 100.0%. Each step came from a defect found by checking the output rather than
from tuning a threshold. Every value in those two sequences except the last is
an intermediate one, superseded by the fix that followed it; 98.1% in particular
is a rule two defects old and is not the shipped precision.

## Reproducing

```bash
python3 docs/validation/validate_department_rule.py
```

The harness calls `rankmgb.pi_department.profile_one` directly rather than
reimplementing the matcher, so what is scored is what ships. An earlier version
kept its own copy of the matching logic and drifted from it, which is exactly
the failure a validation harness exists to prevent.

Roughly 10 minutes against NCBI E-utilities. The sample is seeded (`random.seed(7)`)
so the draw is reproducible.
