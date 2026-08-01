# Validating the department rule

NIH publishes a department code only for recipients it classifies as schools.
Roughly 30% of US NIH dollars sit at recipients that get none, so those
departments have to be derived some other way. This directory holds the
experiment that decides whether the substitute measures the same thing.

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

## Results

| Rule | n | Sensitivity | Precision | Cohen's κ |
|---|---|---|---|---|
| Any surgical paper | 230 | 93.5% | 92.0% | 0.843 |
| Modal department surgical | 230 | 87.0% | 97.3% | 0.835 |
| **Majority (>50%) — adopted** | 230 | 85.4% | **98.1%** | 0.827 |
| Share > 70% | 230 | 77.2% | 99.0% | 0.751 |
| Share > 50% and ≥3 records | 230 | 63.4% | 98.7% | 0.609 |

The majority rule is adopted for the published figures. Precision matters more
than recall here because an entire award is credited to a department on the
strength of the call; at 98.1%, two award-years in a hundred are misassigned.
"Any surgical paper" is published alongside it as the higher-recall bound.

## How this number moved

Two defects were found and fixed by this experiment, and both are worth knowing
because each one alone would have sunk the method:

1. The classifier had 19 positive patterns and **every one was surgical**, so
   "Department of Medicine" returned nothing and was dropped. The
   share-of-affiliations-that-are-surgical statistic was therefore 1.0 for
   anyone the classifier recognised at all — 127 of 132 investigators scored
   exactly 1.0. κ was 0.21.
2. After 25 non-surgical patterns were added, the generic `\bsurg` catch-all was
   still ranked above every one of them, so any string mentioning surgery
   incidentally beat a named department. Spot-checking the top MGB attributions
   caught a radiation oncologist and a psychiatrist being counted as surgical
   faculty. A named department now always beats a bare keyword.

κ moved 0.21 → 0.825 → 0.827, and precision 90.0% → 95.6% → 98.1%.

## Reproducing

```bash
python3 docs/validation/validate_department_rule.py
```

Roughly 10 minutes against NCBI E-utilities. The sample is seeded (`random.seed(7)`)
so the draw is reproducible.
