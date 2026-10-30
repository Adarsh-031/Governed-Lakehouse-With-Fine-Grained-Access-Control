-- Role: researcher (researcher)
-- Scope: ALL_MVP synthetic patients and encounters.
-- birthdate, encounter description and reason_description are hidden.
-- ZIP is masked to three digits plus **; encounter timestamps are masked to
-- UTC month-start timestamps. Use explicit projections; never SELECT *.
-- Claims, care_team, identity and audit are denied to this role.

-- R1. Patient cohort counts by age band and sex.
-- Purpose: Describe the synthetic cohort without exact birthdates.
-- Expected columns: age_band, sex, patient_count.
SELECT age_band, sex, count(*) AS patient_count
FROM hospital.silver.patients
GROUP BY age_band, sex
ORDER BY age_band, sex;

-- R2. Encounter counts by clinical code and encounter class.
-- Purpose: Summarize coded utilization without free-text descriptions.
-- Expected columns: code, encounter_class, encounter_count.
SELECT code, encounter_class, count(*) AS encounter_count
FROM hospital.silver.encounters
GROUP BY code, encounter_class
ORDER BY encounter_count DESC, code
LIMIT 100;

-- R3. Monthly encounter counts.
-- Purpose: Examine broad time trends; start_time is masked by Trino to
-- the first instant of its UTC month before the query uses it.
-- Expected columns: encounter_month, encounter_count.
SELECT
    date_trunc('month', start_time) AS encounter_month,
    count(*) AS encounter_count
FROM hospital.silver.encounters
GROUP BY date_trunc('month', start_time)
ORDER BY encounter_month;

-- R4. Patient counts by generalized ZIP prefix and demographic groups.
-- Purpose: Explore broad geography with the policy-masked ZIP value while
-- suppressing groups smaller than five to reduce small-cell disclosure risk.
-- Expected columns: zip_prefix_masked, age_band, race, patient_count.
SELECT zip AS zip_prefix_masked, age_band, race, count(*) AS patient_count
FROM hospital.silver.patients
GROUP BY zip, age_band, race
HAVING count(*) >= 5
ORDER BY patient_count DESC
LIMIT 100;
