-- Role: researcher (researcher)
-- Scope: ALL_MVP synthetic patients and encounters, plus the #46 gold releases.
-- Row-level demographics (age band, sex, race, ethnicity, ZIP, birthdate) and
-- encounter reason codes/descriptions are hidden. Demographics, geography and
-- conditions come only from hospital.gold aggregates, where every published cell
-- has at least 11 distinct patients. Encounter timestamps are masked to UTC
-- month-start timestamps. Use explicit projections; never SELECT *.
-- Claims, care_team, identity and audit are denied to this role.

-- R1. Patient cohort counts by age band, sex and race.
-- Purpose: Describe the synthetic cohort from the suppressed release only.
-- Expected columns: age_band, sex, race, patient_count.
SELECT age_band, sex, race, patient_count
FROM hospital.gold.researcher_demographics
ORDER BY age_band, sex, race;

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

-- R4. Patient counts by generalized ZIP prefix.
-- Purpose: Explore broad geography from the suppressed release; prefixes with
-- fewer than 11 patients are never published.
-- Expected columns: zip_prefix, patient_count.
SELECT zip_prefix, patient_count
FROM hospital.gold.researcher_geography
ORDER BY patient_count DESC, zip_prefix
LIMIT 100;
