-- patient_token is the logical patient key, never the source patient ID.
CREATE TABLE IF NOT EXISTS hospital.silver.patients (
    patient_token VARCHAR NOT NULL,
    birthdate DATE,
    age_band VARCHAR,
    sex VARCHAR,
    race VARCHAR,
    ethnicity VARCHAR,
    zip VARCHAR
)
WITH (format = 'PARQUET', format_version = 2);
