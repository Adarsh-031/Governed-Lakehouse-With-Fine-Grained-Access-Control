# data/raw

Local bronze source exports. Never commit generated or downloaded datasets.

- `synthea/<patients>-seed<seed>/`: seeded synthetic patient, encounter, claim,
  claim transaction, provider, organization, and payer CSVs.
- `mimic-iv-demo/2.2/`: selected public demo CSVs plus upstream license/README.
  These are deidentified clinical records, not synthetic patients.

Each dataset has a `manifest.json` with source/configuration, row counts, CSV
headers, and checksums. See the [data card](../../docs/data-card.md) for commands,
licenses, measured counts, and limitations. Files stay in separate source
directories; neither source has been normalized, tokenized, or loaded to Iceberg.
