-- Protected authorization data, not a doctor-facing directory.
-- UTC dates form [valid_from, valid_to). NULL valid_to means ongoing.
CREATE TABLE IF NOT EXISTS hospital.silver.care_team (
    doctor_user VARCHAR NOT NULL,
    patient_token VARCHAR NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE
)
WITH (format = 'PARQUET', format_version = 2);
