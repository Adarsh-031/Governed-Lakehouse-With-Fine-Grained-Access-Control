-- Role: doctor (doctor_alice / doctor_bob)
-- Scope: ACTIVE_CARE_TEAM is enforced by Trino on patients and encounters.
-- Do not join or query care_team directly; its columns/table are denied.
-- No claims, identity, or audit resources are available to this role.

-- D1. Recent encounters for the current doctor's patients.
-- Purpose: Review the most recent clinical activity. Trino applies the
-- active care-team row filter before this ORDER/LIMIT.
-- Expected columns: encounter_id, patient_token, start_time, encounter_class, code.
SELECT encounter_id, patient_token, start_time, encounter_class, code
FROM hospital.silver.encounters
ORDER BY start_time DESC
LIMIT 100;

-- D2. Encounter activity by class over the last 90 days.
-- Purpose: Summarize visit mix for the doctor's visible patient cohort.
-- Expected columns: encounter_class, encounter_count, latest_encounter.
SELECT
    encounter_class,
    count(*) AS encounter_count,
    max(start_time) AS latest_encounter
FROM hospital.silver.encounters
WHERE start_time >= current_timestamp - INTERVAL '90' DAY
GROUP BY encounter_class
ORDER BY encounter_count DESC, encounter_class;

-- D3. Visible patient demographics for the current doctor's cohort.
-- Purpose: Review age-band and demographic distribution in the assigned panel.
-- Expected columns: age_band, sex, patient_count.
SELECT age_band, sex, count(*) AS patient_count
FROM hospital.silver.patients
GROUP BY age_band, sex
ORDER BY age_band, sex;

-- D4. Patients with no encounter in the last 30 days.
-- Purpose: Find assigned patients without recent recorded encounters.
-- Expected columns: patient_token, age_band, last_encounter.
-- Both tables independently apply the doctor's active-care-team filter.
WITH latest AS (
    SELECT patient_token, max(start_time) AS last_encounter
    FROM hospital.silver.encounters
    GROUP BY patient_token
)
SELECT p.patient_token, p.age_band, latest.last_encounter
FROM hospital.silver.patients AS p
LEFT JOIN latest ON latest.patient_token = p.patient_token
WHERE latest.last_encounter IS NULL
   OR latest.last_encounter < current_timestamp - INTERVAL '30' DAY
ORDER BY latest.last_encounter NULLS FIRST
LIMIT 100;
