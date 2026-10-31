# Data card



## Data card summary and data-use statement

**Primary governed workload: synthetic Synthea data only.** The shared
hospital Silver tables are generated from Synthea v3.4.0, a simulator. The
ETL entry point validates a Synthea 3.4.0 manifest and loads only that source.
Care-team memberships are also generated deterministically for this project;
they are not actual clinician/patient relationships. **No real patient data
is used in the governed synthetic workload or its policy tests.**

**Important distinction — optional MIMIC-IV Demo files.** The repository also
provides an optional downloader for MIMIC-IV Clinical Database Demo v2.2. This
is a 100-subject, de-identified clinical reference dataset derived from real
clinical records; it is not synthetic. It is kept in its own
`data/raw/mimic-iv-demo/2.2/` directory and is not accepted by the Synthea
Silver-ingestion pipeline, joined to Synthea patients, or used as the
governed synthetic benchmark workload. It may exist in a developer's local
checkout after the optional download. Therefore, the no-real-patient-data
statement applies to the governed Synthea workload, not to the separately
downloadable de-identified MIMIC-IV reference sample. It would be inaccurate
to describe every dataset obtainable from this repository as synthetic.

| Card field | Summary |
|---|---|
| Primary data source | Synthea v3.4.0 synthetic patient histories |
| Synthetic workload configuration | Massachusetts; patient seed 42; clinician/provider seed 42; reference/end date 2025-01-01 UTC; 10 years of exported history |
| Optional reference source | MIMIC-IV Clinical Database Demo v2.2; 100 de-identified subjects; separate from the governed synthetic workload |
| Governed Silver tables | patients, encounters, charge-level claims, care_team, and restricted identity |
| Main transformations | Source patient IDs HMAC-tokenized; age bands derived from the fixed reference date; names placed in a separate restricted identity warehouse; care-team membership generated with a fixed seed |
| Privacy posture | Pseudonymization and access controls reduce exposure; neither synthetic data nor de-identification proves anonymity |
| Intended audience | Engineering, governance, security, and data-platform evaluation using synthetic data |
| Clinical decision use | Not suitable for diagnosis, treatment, patient-specific decisions, or clinical deployment |

### Intended and prohibited uses

Intended uses are software engineering and controlled evaluation: testing
CSV acquisition and integrity checks, schema/type validation, ETL and Iceberg
read/write behavior, token consistency, Trino authorization rules, row
filters, column masks, audit design, and repeatable synthetic workloads.
The optional MIMIC-IV Demo subset may be used only as a separately governed,
de-identified reference sample for format and acquisition checks in accordance
with its PhysioNet terms; it is not a substitute for the synthetic benchmark.

Do not use these data to make clinical decisions, diagnose or treat a person,
estimate real-world disease prevalence or treatment effectiveness, make
insurance or eligibility decisions, evaluate named individuals, or claim
clinical validity or population representativeness. Do not attempt to
re-identify MIMIC subjects, link MIMIC records to external identities, or
combine the MIMIC and Synthea patient-ID domains. Do not expose raw exports,
identity tables, HMAC keys, or pseudonymous membership oracles publicly.
Any new use, especially model training or research conclusions, requires
independent review of source terms, privacy risks, representativeness, and
fitness for purpose.

### Privacy and identifier handling

Synthea records are simulated, but raw CSVs still contain names, source IDs,
dates of birth, ZIP codes, and other attributes that resemble sensitive
fields. Treat raw exports as restricted project data rather than assuming
that "synthetic" means safe to publish. The current ETL derives a versioned
HMAC token from the namespaced `synthea:` source patient ID; the HMAC key is
provided at runtime and is never written to manifests or Git. The derived
age band uses the manifest's fixed reference date. Names are written only to
the separate ETL-only identity warehouse, not the shared patient inventory.
The care-team table contains pseudonymous patient tokens and synthetic
doctor-user assignments; its private expected-membership oracle is also
sensitive linkage evidence. HMAC is pseudonymization, not anonymization.

The MIMIC-IV Demo release is de-identified upstream, not simulated. Keep it
separate from Synthea, retain its upstream license/README, use the published
versioned checksums, and follow PhysioNet's data-use conditions. De-identification
does not eliminate all linkage or inference risk.

### Known biases and limitations

- Synthea is a rule- and module-driven simulator, not a sample of the
  population. Its default modules, demographic assumptions, coding choices,
  generated encounters, and simulated payer/financial events may not match
  clinical practice or any target population.
- The fixed geography is Massachusetts and the configuration uses bundled
  U.S. reference data. Results may not transfer to other regions, health
  systems, populations, or payment arrangements.
- A fixed seed makes a run reproducible under the pinned configuration; it
  does not make the generated population statistically representative.
- The MIMIC-IV Demo subset is only 100 de-identified subjects and is a
  deliberately small clinical reference sample. Its dates/ages are subject
  to de-identification transformations and must not be treated as ordinary
  birthdates or as calendar-comparable dates across patients.
- The two sources have different schemas and ID domains. There is no
  validated patient-level crosswalk between them; combining them would
  introduce invalid joins and unsupported conclusions.
- Current source counts and ingestion acceptance do not establish benchmark
  performance, privacy guarantees, fairness, or clinical validity.

### Benchmark size and measurement status

The available generation commands and benchmark plan define target Synthea
populations of **1,000, 10,000, and 100,000** patients. Only the 1,000-patient
seed-42 source and its Silver ingestion have acceptance evidence so far.
The 10k and 100k generations/benchmarks have not been run for this work.

| Measurement | Status |
|---|---|
| 1k source CSV row counts | Measured; see Local sample inventory |
| 1k Silver table row counts | Measured; see Silver table inventory and ingestion evidence |
| 10k / 100k generation and load sizes | TODO — benchmark not run |
| Stored bytes per table / total warehouse size | TODO — measure during benchmark |
| Query input/output bytes | TODO — measure during benchmark |
| Median and p95 latency, cold and warm | TODO — measure during benchmark |
| CPU and memory under repeated workloads | TODO — measure during benchmark |
| Baseline versus policy-enforced overhead | TODO — measure equivalent outputs during benchmark |
| Researcher-visible k-anonymity / small-cell risk at 1k | Automated by issue #24; use the privacy risk workflow artifact for the measured run |
| Researcher-visible k-anonymity / small-cell risk at 10k | Optional manual issue #24 workflow run; not assumed until executed |

Do not infer storage footprint, scale capacity, latency, or policy overhead
from source row counts. Record hardware, image versions, seed, table snapshots,
policy digest, repetition count, and cache conditions with benchmark results,
as specified in [the benchmark plan](../bench/plan.yaml).

### Governed Silver table inventory

Observed readback for the accepted 1,000-patient Synthea seed-42 load:

| Silver table | Rows | Description |
|---|---:|---|
| `silver.patients` | 1,000 | Tokenized patient attributes and derived age band |
| `silver.encounters` | 48,976 | Patient-linked encounter history |
| `silver.claims` | 203,698 | Charge-level claim rows derived from transaction CHARGE records |
| `silver.care_team` | 1,200 | Seeded synthetic doctor/patient assignments |
| `identity.patient_identity` | 1,000 | Names in the separate restricted identity warehouse |

These are observed row counts, not byte-size or performance measurements.
Storage bytes, table sizes on disk, and the 10k/100k benchmark inventories
remain TODO until the benchmark is run.

## Sources and purpose

**Synthea is the primary synthetic workload** for hospital governance and scale
experiments. Patient histories are simulated, not copied from real patients.
The generator is [Synthea v3.4.0](https://github.com/synthetichealth/synthea/releases/tag/v3.4.0),
released under [Apache-2.0](https://github.com/synthetichealth/synthea/blob/v3.4.0/LICENSE).
Its bundled geography/provider reference data can describe real organizations;
synthetic patient histories do not make every reference attribute fictional.

**MIMIC-IV Clinical Database Demo v2.2 is a separate clinical reference sample**,
not synthetic data or extra Synthea patients. The
[open PhysioNet demo](https://physionet.org/content/mimic-iv-demo/2.2/)
contains 100 deidentified patients and is licensed under ODbL-1.0. Full MIMIC-IV
requires credentialed access; this project downloads only versioned public demo
URLs. Cite Johnson, A., Bulgarelli, L., Pollard, T., Horng, S., Celi, L. A., &
Mark, R. (2023), *MIMIC-IV Clinical Database Demo (version 2.2)*, PhysioNet,
[doi:10.13026/dp1f-ex47](https://doi.org/10.13026/dp1f-ex47).
The downloader retains the upstream license and README alongside the files.

## Reproduction

Prerequisites: Bash, Python 3.11+, Java 17+ for Synthea, and outbound HTTPS for
the initial JAR download and demo downloads. No Python packages, Docker stack,
PhysioNet account, clinical credentials, or HMAC secrets are needed.
From the repository root:

```sh
./scripts/generate_synthea.sh --patients 1000 --seed 42
./scripts/generate_synthea.sh --patients 1000 --seed 42  # repeat/count check
./scripts/download_mimic_demo.sh
./scripts/download_mimic_demo.sh                       # repeat/count check

# Generate larger, independent synthetic populations with the same settings.
./scripts/generate_synthea.sh --patients 10000 --seed 42
./scripts/generate_synthea.sh --patients 100000 --seed 42
```

Outputs are `data/raw/synthea/<patients>-seed<seed>/` and
`data/raw/mimic-iv-demo/2.2/`. These CSVs, manifests, temporary runs, and the
cached JAR in `data/tools/` are git-ignored. Both scripts resolve paths relative
to the repository and can be launched from another working directory.
Use `--output /path/to/new-directory` for an alternative destination or changed
configuration. Existing datasets are preserved: repeating a command generates
or downloads into temporary staging, checks the published sample's integrity,
and requires the new row counts to match before reporting success. Different
parameters or modified published CSVs fail without overwriting the sample.
Incomplete runs never publish a dataset directory.

Select Java with `--java /path/to/bin/java` (or `JAVA`), a predownloaded official
JAR with `--jar /path/to/synthea.jar`, and maximum heap with `--heap 4g`.
`PYTHON=/path/to/python3` selects the launcher interpreter. An explicit JAR is
still verified against the pinned checksum. Initial acceptance used a temporary
Eclipse Temurin **17.0.21+9** runtime; the host's Java 8 was left unchanged.
The 10k/100k commands are supported but were not executed for this issue;
generation time and disk usage must be measured before larger benchmarks.

## Fixed Synthea configuration

- Generator: **3.4.0**, official `synthea-with-dependencies.jar`;
  SHA-256 `38678aab1e667d26671163824aad60ee5e30fa366f3d59d5ea5765ebb5702432`.
- Patient seed **42** and clinician/provider seed **42**.
- Reference date and simulation end date **2025-01-01 UTC** (`20250101`),
  overridable together with `--reference-date YYYYMMDD`.
- Geography **Massachusetts**, bundled default clinical modules/demographics,
  no age/sex restrictions, simulation timestep from the pinned release.
- **One worker**, UTF-8, UTC, English/US JVM locale, **10 years** of exported
  history; other model settings use the pinned JAR defaults.
- Overflow **disabled** (`-o false`): exactly 1,000 patients, including **907
  alive and 93 deceased**. Default overflow would add deceased patients while
  trying to reach a requested living population. No replication or resampling
  is used; larger populations are generated by Synthea.
- CSV-only allowlist in [synthea.properties](../scripts/synthea.properties):
  patients, encounters, claims, claims_transactions, providers, organizations,
  and payers. FHIR and metadata exports are disabled; CSV appending is disabled.
  Logs omit patient names. Each run uses a fresh isolated working directory.

The seed alone is insufficient: generator/model version, date, provider seed,
configuration, and runtime all affect results. `manifest.json` records those
parameters, the configuration digest, Java version, source URL/license, CSV
headers, row counts, and file SHA-256 digests. The acceptance guarantee is
**matching row counts for repeated settings**, not universal byte identity
across Java versions/platforms. Checksums identify the published files.

## Local sample inventory

Observed with the settings above; counts exclude CSV headers and count quoted
multiline fields correctly.

Synthea, `data/raw/synthea/1000-seed42/`:

- `patients.csv`: **1,000** rows.
- `encounters.csv`: **48,976** rows.
- `claims.csv`: **83,829** rows.
- `claims_transactions.csv`: **660,227** rows.
- `providers.csv`: **780** rows.
- `organizations.csv`: **780** rows.
- `payers.csv`: **10** rows.

MIMIC-IV demo, `data/raw/mimic-iv-demo/2.2/`:

- `demo_subject_id.csv`: **100** rows.
- `hosp/patients.csv`: **100** rows.
- `hosp/admissions.csv`: **275** rows.
- `hosp/diagnoses_icd.csv`: **4,506** rows.
- `hosp/procedures_icd.csv`: **722** rows.
- `icu/icustays.csv`: **140** rows.

Only this selected demo subset is downloaded. Each original file is checked
against its pinned checksum from PhysioNet's versioned
[SHA256SUMS.txt](https://physionet.org/files/mimic-iv-demo/2.2/SHA256SUMS.txt)
before gzip decompression. Validation requires exactly the published 100-person
cohort, unique admission IDs, and matching patient/admission references in the
retained diagnosis, procedure, and ICU tables. No missing billing costs,
identities, or care-team assignments are fabricated from the demo.

## Acceptance evidence

Verified locally for issue #3 on **2026-10-28**:

- Ran the 1,000-patient Synthea command twice with seed 42 and the settings
  above. All seven CSV row counts matched; the second run reported
  `Repeat verified` and retained the original sample.
- Ran the public demo downloader twice. All six CSV row counts matched;
  upstream checksum and cohort/reference checks passed on both downloads.
- Confirmed both datasets' CSVs and manifests are ignored by Git.
- All **349 unit tests** passed, including 24 source-data checks covering
  corrupt downloads/cache entries, CSV record accounting, rerun preservation,
  parameter/count mismatches, demo reference failures, CLI scale inputs,
  Java prerequisites, and refusing partial/wrong-sized datasets.
- Repository Python lint/format, shell syntax, and whitespace checks passed.

These checks establish source acquisition and row-count reproducibility. They
do not establish silver-schema integrity, Iceberg loading, runtime clinical
authorization, or large-population benchmark performance.

## Preprocessing, decisions, and limits

This issue produces **bronze source files**, not loaded Iceberg tables. Native
CSV columns/values are preserved; demo gzip files are decompressed losslessly.
No tokenization, normalization, age bands, masking, or clinical grants happen
here. Token key version is **not applicable** (`null` in manifests); keys are
never recorded. Names and synthetic identifiers remain in restricted local raw
exports until later ETL applies the existing classification/access contracts.

The seven Synthea CSVs support the MVP while avoiding unrelated exports.
`claims_transactions.csv` supplies line-level procedure and financial details
not available in the native claims header alone; native claims are not yet the
project's normalized silver claims schema. Providers and organizations preserve
source relationships, but a generated provider ID does not establish a login
or authorize a doctor. Trusted care-team assignments remain separate work.

The two sources keep separate directories, schemas, and patient-ID domains.
No patient-level join between Synthea and MIMIC is implied. Future ingestion
must namespace source IDs before tokenization and explicitly map each schema.
The demo's admissions are not claims, and deidentified MIMIC dates/anchor ages
cannot be treated as ordinary birthdates or calendar-comparable dates across
patients. A 100-person demo cannot support representative clinical conclusions
or substitute for Synthea scale benchmarks. Synthetic data and deidentification
also do not prove that future HMAC/generalization policies provide anonymity.

## Silver ingestion evidence (issue #16)

On **2026-10-28**, the generated 1k seed-42 sample passed the isolated Docker
ETL acceptance test against pinned Lakekeeper/Silo with fresh service credentials
and disposable volumes. The one-command Compose ETL dependency path loaded and
read back every value from 1,000 patients, 48,976 encounters, and **203,698 charge
lines**, representing all **83,829 native claims headers**. Source patient and
encounter counts matched exactly, and charge counts matched the source CHARGE
records. Patient tokens were consistent across all three tables; validated
patient and encounter orphan counts were zero.

The fixed `age_reference_date` was **2025-01-01**, taken from the source manifest.
Token version was **v1**, with the source patient ID domain `synthea:`. The test
used an ephemeral random HMAC key, never recorded in this evidence or Git.
An identical rerun verified all existing values without appending; a token-version
change was rejected before table writes. The tests deleted only their own stack
and volumes, so this evidence does not mean a persistent developer warehouse was
populated. Run `./scripts/ingest.sh` to load that warehouse.

Claims use charge-level `ID` as `claim_id`, retain exact procedure codes and
DECIMAL(18,2) costs, resolve the primary payer name, and sum insurance payments
(primary plus secondary) for coverage. Copays, patient payments, transfers and
adjustments are not extra charges or payer coverage. The single primary-payer
label does not identify every insurer contributing to coverage. See the
[mapping and retry decisions](../ingest/README.md#claims-grain-charge-lines-not-headers).
No names/SSNs enter the shared patient inventory. Care-team/identity loading is
now available in #17, below. Clinical SQL policies are now implemented by the reviewed policy in PR #44.
MIMIC transformation remains outside the current Synthea ingestion pipeline;
larger-population memory/scale measurements remain future benchmark work.

## Care-team and identity evidence (issue #17)

On **2026-10-29**, the full 1k source passed isolated Docker ingestion and
Trino/Iceberg DDL acceptance. The normal launcher also loaded the persistent
local warehouse and verified exact readback: 1,000 patients, 48,976 encounters,
203,698 charge lines, **1,200 care-team assignments**, and **1,000 restricted
identity rows**. This persistent load is separate from the disposable tests.
An identical launcher rerun verified all five existing tables without appending.

Assignments are a seeded synthetic authorization fixture: **600 patients per
doctor**, **200 shared**, **400 exclusive to each**. Use authenticated logins
`doctor_alice`/`doctor_bob`, seed **42**, UTC start **2025-01-01** and NULL end.
The expected token sets are saved privately before loading at
`data/staged/issue17/care-team-oracle.json`. They reuse the `synthea:` HMAC token
domain and version **v1**; neither names, source patient IDs nor keys appear in
the membership oracle. The file remains pseudonymous, sensitive linkage data.

Names are assembled only from approved Synthea name components and stored with
the same patient token in the separate ETL-only identity warehouse. Trino's
catalog and object credentials were tested against populated identity metadata
and Parquet and denied. These are synthetic memberships, not source-provider
authorization or consent. SQL doctor filters and validity enforcement are implemented by the reviewed
clinical policy in PR #44. See the [decisions and verification record](issue-17.md).
