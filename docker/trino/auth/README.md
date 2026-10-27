# docker/trino/auth

Password hashes and TLS configuration. Actual credentials and certificates are gitignored.

Generate them with `./scripts/init_secrets.sh`. The canonical local outputs are
`data/secrets/password.db` and `data/secrets/trino.pem`; Compose mounts them
read-only for the file authenticator and HTTPS keystore. Only the public CA
certificate is additionally mounted for internal HTTPS trust and health checks.
The CA private key and full runtime env file stay on the host.

`groups.txt` binds the existing generated users to server-managed groups. Group
membership supplies policy inputs; it does not grant access by itself. Clients
cannot assign these groups. Impersonation remains denied by `rules.json`.
The issue's example `doctor_1` is represented by the existing `doctor_alice` and
`doctor_bob` identities, preserving issue #4's generated passwords.

See [secret setup](../../../scripts/README.md) and [Trino operations](../README.md).
