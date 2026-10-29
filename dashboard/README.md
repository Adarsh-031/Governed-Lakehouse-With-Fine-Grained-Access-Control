# Optional Streamlit demo dashboard (issue #30)

This is a demonstration client only. **Trino remains the authorization boundary.**
The dashboard has no policy engine, service account, or elevated credentials. The
server-side file policy is implemented by [PR #44](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/pull/44);
this UI does not create or modify grants.

## Requirements

- Python 3.11+ and the repository's locked development dependencies.
- The secured Trino service started using [the Trino setup](../docker/trino/README.md).
- A personal Trino username/password and the CA certificate for that endpoint.
- Synthetic tables loaded and the reviewed Trino policy deployed.

## Install and run

From the repository root, install the locked development dependencies and start
Trino as documented above. Run Streamlit as an ephemeral tool dependency so the
project dependency lockfile does not need to change:

```sh
uv sync --locked --group dev
export TRINO_HOST=localhost
export TRINO_PORT=8443
export TRINO_CA_BUNDLE=data/secrets/ca.crt
uv run --no-sync --with streamlit streamlit run dashboard/app.py
```

Open the local Streamlit URL printed by the command. Do not expose this demo on a
public interface or commit local secrets. CA verification stays enabled; the
dashboard uses HTTPS and the supplied CA bundle.

## Login and queries

Choose your own Trino identity (`doctor_alice`, `doctor_bob`, `researcher`,
`billing`, or `admin`) and enter that user's own password. The password input
is masked and used only for that user's connection. The dashboard never reads a
shared service password or uses a privileged service identity.

The examples are role-aware and explicitly list columns so hidden-column rules
do not make an otherwise permitted `SELECT *` fail:

- **Doctor:** patients and encounters are filtered by the active care-team assignment for the authenticated doctor. The claims example is expected to be denied.
- **Researcher:** patients and encounters are available within the MVP cohort; ZIP and encounter times are masked by Trino, birthdate and free-text descriptions are hidden. The claims example is expected to be denied.
- **Billing:** claims are available with approved reconciliation fields; procedure descriptions are hidden. Patient demographics are expected to be denied.
- **Admin:** patients, encounters, claims, and care-team verification are read-only; restricted identity records remain denied. Restricted audit query text is not exposed.

Use the “Current identity and groups” example to confirm the identity and server-managed
group for the authenticated session. Each “Expected denial” example should fail at
Trino. If one succeeds, treat that as a policy regression and investigate the server
policy rather than relying on the dashboard to block it. The UI's read-only SQL
prefix check is only a usability guardrail; it is not a security mechanism.

## Scope and verification

The role grants, doctor row filters, masks, and default-deny behavior now live in
Trino's reviewed `policies/rules.json` policy (PR #44). This dashboard sends each
query through Trino using the selected user's credentials; it never substitutes
UI checks for authorization. Keep the independent direct-Trino access tests in
place: authorization must remain effective even when the dashboard is not installed
or running.

Before closing issue #30, run the UI once for each of the five available identities
against the live stack. Confirm the identity/groups query, the permitted role query,
and the expected-denial query; record actual results rather than assuming that CI
proves interactive behavior.
