-- SEPARATE restricted warehouse/catalog prerequisite; never run under hospital.
-- No MVP interactive SQL group, including admin, may access this resource.
CREATE SCHEMA IF NOT EXISTS identity.identity;

CREATE TABLE IF NOT EXISTS identity.identity.patient_identity (
    patient_token VARCHAR NOT NULL,
    name VARCHAR
)
WITH (format = 'PARQUET', format_version = 2);
