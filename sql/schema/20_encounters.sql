-- Timestamps contain UTC values. Record IDs are opaque synthetic keys.
CREATE TABLE IF NOT EXISTS hospital.silver.encounters (
    encounter_id VARCHAR NOT NULL,
    patient_token VARCHAR NOT NULL,
    start_time TIMESTAMP(6),
    stop_time TIMESTAMP(6),
    encounter_class VARCHAR,
    code VARCHAR,
    description VARCHAR,
    reason_code VARCHAR,
    reason_description VARCHAR
)
WITH (format = 'PARQUET', format_version = 2);
