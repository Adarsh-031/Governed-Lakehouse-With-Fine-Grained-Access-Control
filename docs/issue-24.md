# Issue #24 — researcher privacy-risk measurements

**Scope:** Measure residual disclosure in the researcher-visible Silver projection. This is separate from checking whether Trino correctly applies row filters and column masks.

## Attack surface and measurements

The analysis reads only the fields exposed to the researcher role:

- Patient demographics: `age_band`, `sex` (Synthea gender), and masked `zip` prefix.
- Clinical quasi-identifiers: uncommon values of `encounters.reason_code`, which is visible to the researcher. They are summarized per patient into a rare-condition signature; hidden free-text descriptions and birthdates are not used.

For each quasi-identifier set, the implementation groups records by the exact released values and reports:

- `k_min`: the smallest equivalence-class size (minimum k for this tested projection).
- `unique_records` and `unique_percent`: the number and percentage of records in singleton classes.
- `equivalence_classes` and `class_size_histogram`: supporting cohort distribution.

Null values are treated as an explicit released value for equivalence-class counts, but null and blank quasi-identifier values are treated as incomplete and excluded from attacker linkage.

Rare-condition signatures use the patient-level prevalence of each visible reason code. A code is considered rare when it occurs in at most `max(1, ceil(N * 0.05))` distinct patients. The threshold is an explicit attacker-feature assumption for this experiment, not a deployed suppression rule or privacy control. The code also reports the demographic-only model so the effect of adding the clinical signature can be compared.

## Aggregate inference

The live researcher SQL test:

1. Executes a `GROUP BY age_band, sex, zip` query and compares every group count with the independent pre-write synthetic oracle.
2. Records singleton groups and the minimum equivalence-class size from the same released view.
3. Executes exact total-count versus count-excluding-one-visible-token queries and checks whether the one-record difference reconstructs the selected synthetic patient's presence.
4. Retains the finding as `leaked` when the query exposes the selected patient's presence. A successful test means the risk was measured, not that the disclosure was blocked.

## Quasi-identifier linkage

The linkage case simulates an attacker with exact auxiliary age band, sex, masked ZIP prefix, and rare-condition signatures. It reports:

- the number of complete auxiliary records;
- the number of unique (non-ambiguous) matches;
- the number of those matches that are correct against the independently saved source oracle;
- population linkage percentage and precision among unique matches.

Incomplete keys and ambiguous released matches are not counted as identified people. The deterministic `patient_token` is used only to score a candidate match against synthetic ground truth; it is never included in the quasi-identifier key.

## Previously recorded baseline — 11 synthetic patients

The existing #23 investigation used the two-patient committed Synthea fixture plus nine boundary fixtures (11 synthetic patients). Its recorded demographic-only baseline found:

- `age_band + sex + masked ZIP`: (k=1), with 4 singleton records among 11 (36.36%).
- Exact count differencing reconstructed the selected patient's presence (difference = 1).
- Exact auxiliary linkage on those three fields correctly linked 3/11 patients (27.27% population linkage); 5 auxiliary rows were complete and all 3 unique matches were correct (100% match precision).

These values describe that recorded small synthetic run and the stated attacker model only. They are **not** results for the added rare-condition feature set, the new 1,000-patient CI run, or any real hospital population.

## Reproducing the 1,000-patient run

The CI quality job now installs Java 17, generates the pinned Synthea 3.4.0 sample with seed 42 (1,000 source patients), then runs the bypass suite against that source. The fixture setup adds its own boundary patients; use the measured `population` in the private per-run evidence as the actual denominator rather than assuming the test population is exactly 1,000.

Local reproduction:

```bash
./scripts/generate_synthea.sh --patients 1000 --seed 42
ACCESS_TEST_SOURCE="$PWD/data/raw/synthea/1000-seed42" \
  RUN_BYPASS_INTEGRATION=1 uv run --locked --group dev \
  pytest tests/bypass -v --strict-config --strict-markers
```

Unit regression tests:

```bash
uv run --locked --group dev pytest tests/unit/test_privacy_risk.py -v
```

Each integration run retains its complete patient-level evidence in the private `results/raw/bypass-*/` directory. CI publishes only two aggregate-only files, `issue-24-aggregate-inference.json` and `issue-24-quasi-identifier-linkage.json`, as the `issue-24-privacy-metrics` artifact. These summaries contain counts, rates, field names and attack assumptions, not patient tokens or quasi-identifier rows. The stable `tests/bypass/cases.csv` inventory continues to use `not_run`; actual measured statuses and metrics belong to the generated per-run report.

## Limitations

- A measured k value is not proof of anonymity, and a high k for one selected feature set does not rule out linkage using other auxiliary information.
- Rare reason-code signatures are a deliberately simplified attacker feature. They are not a clinical diagnosis classifier.
- Aggregate counts remain vulnerable to differencing unless a separate query-governance mechanism (such as small-cell suppression, query-history controls, or a privacy-budgeted interface) is implemented.
- HMAC tokens are pseudonyms, not anonymization. Re-identification risk depends on the released fields, auxiliary data, population and attack model.
- Synthetic results do not estimate real-world re-identification probabilities. The mitigation/acceptance decision remains tracked in [issue #46](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/46).
