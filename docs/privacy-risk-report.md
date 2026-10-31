# Issue #24 — researcher-visible privacy risk measurement

## Scope

This report defines reproducible measurements over the Synthea source patients
that are visible to the researcher role. It separates the source cohort from
the nine synthetic policy-boundary patients appended by the access-test fixture.
Those boundary patients are useful for authorization tests but are not part of
the 1k or 10k privacy denominator.

The released demographic quasi-identifiers are:

- `age_band`
- `sex`
- `zip`, as returned to the researcher after the three-digit prefix mask

The current silver schema has no dedicated condition/diagnosis table. It does
expose `encounters.reason_code` to researchers. This PR therefore measures
rare reason-code profiles as a **condition-like encounter proxy**, not as a
complete condition history.

## Metrics

The privacy analysis uses a k threshold of 5 as an explicit reporting
threshold, not as a universal legal or scientific guarantee.

For every requested QI combination, the report records population size,
equivalence-class count, minimum class size (minimum k), singleton class count,
number and percentage of singleton records, number of classes below k=5,
number and percentage of records in those classes, and records with missing
QI values. It reports both an all-record calculation (NULL is an explicit
class value) and a complete-case calculation (rows with missing QIs excluded).

QI combinations:

1. Age band and sex.
2. Age band, sex and researcher-masked ZIP prefix.
3. Age band, sex, masked ZIP prefix and the tuple of reason codes that appear
   for fewer than five distinct source patients.

Reason-code prevalence counts distinct patient tokens, not encounter rows.
Repeated encounters for one patient do not inflate prevalence. The report also
records the number of rare codes and the number of source patients with at least
one rare code.

## Aggregate inference

The test compares exact researcher-visible `GROUP BY age_band, sex, zip`
counts with independent source-derived Python expectations. It reports the
number of groups below five and the number/percentage of source patients in
those small groups. It also performs an exact count-differencing check:
the source-patient count is compared with the count after excluding one
selected source patient. A difference of one demonstrates a disclosure of that
patient's presence under this query model; it is not treated as an
authorization failure.

The reason-code prevalence query is executed in Trino and compared with the
source oracle to verify the field's researcher-visible values are reflected in
the condition-proxy calculations.

## Linkage model

The simulated auxiliary-data attacker has exact age band, sex and masked ZIP
prefix for records in the same synthetic cohort. A second profile adds rare
reason-code tuples. The patient HMAC token is never a feature; it is used only
to score whether a unique match was correct. Ambiguous equivalence classes
do not count as links. This is a strong, same-cohort synthetic linkage model,
not an estimate of an attacker's real-world auxiliary knowledge.

## Reproduction

The repository already supports deterministic Synthea generation. The
workflow `.github/workflows/privacy-risk.yml` runs on relevant pull requests
with 1,000 source patients and can also be manually run at 1,000 or 10,000
patients (seed 42 by default). It generates the pinned Synthea source, loads
it into an isolated test lakehouse, runs only the aggregate-inference and
quasi-identifier-linkage cases, and publishes the aggregate-only
`privacy-risk-summary.json` artifact. Full bypass evidence and HMAC tokens
remain in the private test workspace.

Local reproduction:

```sh
uv sync --locked --group dev
uv run --locked python scripts/source_data.py synthea --patients 1000 --seed 42
ACCESS_TEST_SOURCE="$PWD/data/raw/synthea/1000-seed42" \
RUN_BYPASS_INTEGRATION=1 \
uv run --locked --group dev pytest tests/bypass/test_bypass.py \
  -k "aggregate_inference or quasi_identifier_linkage" -v --strict-config \
  --bypass-results-dir=results/raw/privacy-risk-1000-seed-42
```

To measure 10,000 patients, use `--patients 10000` and select that generated
path as `ACCESS_TEST_SOURCE`, or run the workflow manually with population
10,000. Docker/Compose, Java 17+, and the locked development dependencies are
required.

## Evidence and result status

The pre-existing issue #23 validation used two source patients plus nine
boundary fixtures (11 total). Its reported 4 singleton demographic groups
among 11 patients (36.36%) and 3 correct unique links among 11 (27.27%) are
**historical small-fixture results only**. They must not be presented as
1,000-patient findings.

The 1,000-patient Synthea source and Silver ingestion already have data-card
acceptance evidence, but the researcher-visible privacy metrics are produced
by the new #24 workflow rather than inferred from row counts. No 1k/10k
percentage is hard-coded into this document. Use the workflow's
`privacy-risk-summary.json` artifact for actual measured values. A 10k result
remains unmeasured until a 10k workflow run completes.

## Limitations

Synthea is synthetic and not population-representative. The QI profile uses
only fields released under the current researcher policy. k-anonymity is
sensitive to the chosen QIs, generalization, missingness and cohort definition;
it does not protect against arbitrary background knowledge or all aggregate
differencing. A passing measurement does not establish anonymity, HIPAA
compliance, or safety for real clinical data. Any measured risk remains
visible as a `leaked` bypass-case outcome and is tracked alongside the
researcher privacy-contract follow-up #46.
