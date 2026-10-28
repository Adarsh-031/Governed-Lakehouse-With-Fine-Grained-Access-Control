CREATE TABLE IF NOT EXISTS hospital.silver.claims (
    claim_id VARCHAR NOT NULL,
    patient_token VARCHAR NOT NULL,
    encounter_id VARCHAR NOT NULL,
    payer VARCHAR,
    procedure_code VARCHAR,
    procedure_description VARCHAR,
    total_cost DECIMAL(18,2),
    payer_coverage DECIMAL(18,2)
)
WITH (format = 'PARQUET', format_version = 2);
