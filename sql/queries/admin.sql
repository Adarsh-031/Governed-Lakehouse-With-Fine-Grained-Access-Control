-- Role: admin (admin)
-- Scope: ALL_MVP on patients, encounters, claims and care_team.
-- identity is denied. audit.query_events is a reserved future read path and
-- is not a currently provisioned production table. Avoid SELECT *.

-- A1. Verify table row counts.
-- Purpose: Check the current synthetic MVP inventory.
-- Expected columns: table_name, row_count.
SELECT 'patients' AS table_name, count(*) AS row_count
FROM hospital.silver.patients
UNION ALL
SELECT 'encounters', count(*) FROM hospital.silver.encounters
UNION ALL
SELECT 'claims', count(*) FROM hospital.silver.claims
UNION ALL
SELECT 'care_team', count(*) FROM hospital.silver.care_team
ORDER BY table_name;

-- A2. Encounter volume by class and code.
-- Purpose: Inspect broad clinical workload composition.
-- Expected columns: encounter_class, code, encounter_count.
SELECT encounter_class, code, count(*) AS encounter_count
FROM hospital.silver.encounters
GROUP BY encounter_class, code
ORDER BY encounter_count DESC
LIMIT 100;

-- A3. Claims reconciliation totals by payer.
-- Purpose: Compare charge totals and payer coverage.
-- Expected columns: payer, claim_count, total_cost, payer_coverage.
SELECT
    payer,
    count(*) AS claim_count,
    sum(total_cost) AS total_cost,
    sum(payer_coverage) AS payer_coverage
FROM hospital.silver.claims
GROUP BY payer
ORDER BY payer;

-- A4. Current synthetic care-team assignment inventory.
-- Purpose: Validate assignment counts and validity-window coverage.
-- Expected columns: doctor_user, assignment_count, ongoing_assignments,
-- earliest_valid_from, latest_valid_from.
SELECT
    doctor_user,
    count(*) AS assignment_count,
    count_if(valid_to IS NULL) AS ongoing_assignments,
    min(valid_from) AS earliest_valid_from,
    max(valid_from) AS latest_valid_from
FROM hospital.silver.care_team
GROUP BY doctor_user
ORDER BY doctor_user;
