-- Issue #46: aggregate-only research releases. Written by the trusted ETL
-- (ingest/release.py) after silver loads; cells below 11 patients are suppressed.
CREATE SCHEMA IF NOT EXISTS hospital.gold;
CREATE TABLE IF NOT EXISTS hospital.gold.researcher_demographics (
    age_band VARCHAR,
    sex VARCHAR,
    race VARCHAR,
    patient_count BIGINT
)
WITH (format = 'PARQUET', format_version = 2);
CREATE TABLE IF NOT EXISTS hospital.gold.researcher_geography (
    zip_prefix VARCHAR,
    patient_count BIGINT
)
WITH (format = 'PARQUET', format_version = 2);
CREATE TABLE IF NOT EXISTS hospital.gold.researcher_conditions (
    reason_code VARCHAR,
    patient_count BIGINT
)
WITH (format = 'PARQUET', format_version = 2);
