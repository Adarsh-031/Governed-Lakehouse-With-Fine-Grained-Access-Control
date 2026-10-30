# Representative SQL workloads (issue #7)

These files contain executable Trino SQL examples for the four interactive
roles in [access-matrix.csv](../../policies/access-matrix.csv). They are
workload examples, not new views or grants. Authentication, row filters and
column visibility/masks remain enforced by Trino's filesystem access policy.

## Files and query coverage

| File | Role | Queries | Intended workload |
|---|---|---:|---|
| [doctor.sql](doctor.sql) | `doctor` | 4 | Recent encounters, encounter mix, assigned-panel demographics, patients without recent encounters |
| [researcher.sql](researcher.sql) | `researcher` | 4 | Cohort summaries, coded utilization, monthly trends, generalized ZIP-prefix summaries |
| [billing.sql](billing.sql) | `billing` | 4 | Payer reconciliation, procedure-code totals, zero-coverage charges, high-value charge lines |
| [admin.sql](admin.sql) | `admin` | 4 | Table inventory, encounter mix, payer reconciliation, care-team assignment inventory |

Each query is preceded by comments identifying the role, purpose, and expected
result columns. Queries use fully qualified `hospital.silver` table names.

## Run examples

Connect to the local Trino endpoint as the corresponding named user using the
CA-verified HTTPS connection described in [Trino setup](../../docker/trino/README.md).
Then execute only the matching file:

```sh
trino --server https://localhost:8443 --user doctor_alice --file sql/queries/doctor.sql
trino --server https://localhost:8443 --user researcher --file sql/queries/researcher.sql
trino --server https://localhost:8443 --user billing --file sql/queries/billing.sql
trino --server https://localhost:8443 --user admin --file sql/queries/admin.sql
```

Supply the project's trusted CA / authentication options for your environment;
do not disable certificate verification or put passwords in shell history.
A doctor login must be one of the provisioned identities, such as
`doctor_alice` or `doctor_bob`.

## Access-contract notes

- **Doctor:** only `patients` and `encounters` are granted. Trino applies
  `ACTIVE_CARE_TEAM` independently to each table at query time. The SQL does
  not read or join `care_team` directly.
- **Researcher:** only `patients` and `encounters` are granted. Birthdate and
  free-text encounter descriptions are hidden; ZIP and encounter timestamps are
  masked by Trino. Grouping/selection of those masked values observes the mask.
- **Billing:** only `claims` is granted. `procedure_code` is visible but
  `procedure_description` is hidden. Patient and encounter tables are denied.
- **Admin:** patients, encounters, claims and care_team are granted. Identity
  remains denied; the future audit read path is not treated as a currently
  provisioned production table.

All files use explicit column lists rather than `SELECT *`, so a newly added
restricted column cannot accidentally become part of an example. A successful
query is still subject to the live user's group and policy; SQL comments do not
grant access. For the authoritative per-column contract and limitations, see
[policies/README.md](../../policies/README.md).
