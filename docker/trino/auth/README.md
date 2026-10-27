# docker/trino/auth

Password hashes and TLS configuration. Actual credentials and certificates are gitignored.

Generate them with `./scripts/init_secrets.sh`. The canonical local outputs are
`data/secrets/password.db` and `data/secrets/trino.pem`; future service setup
should mount these files read-only and configure the Trino file authenticator
and HTTPS keystore paths. See [setup](../../../scripts/README.md).
