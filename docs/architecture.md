# Architecture

Shared protected base tables; optional role views. Trino is the user query boundary. ETL and audit writers are separate trusted service paths. Private storage/catalog, TLS authentication, explicit policies, and separate secrets are required.
