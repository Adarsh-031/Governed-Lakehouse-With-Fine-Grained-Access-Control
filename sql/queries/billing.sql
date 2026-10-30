-- Role: billing (billing)
-- Scope: ALL_MVP claims. Patients, encounters, care_team, identity and audit
-- are denied. claims.procedure_description is hidden; procedure_code is
-- visible by explicit access-matrix decision. Do not SELECT *.

-- B1. Claim totals and coverage by payer.
-- Purpose: Reconcile billed charges against payer coverage.
-- Expected columns: payer, claim_count, total_cost, payer_coverage.
SELECT
    payer,
    count(*) AS claim_count,
    sum(total_cost) AS total_cost,
    sum(payer_coverage) AS payer_coverage
FROM hospital.silver.claims
GROUP BY payer
ORDER BY total_cost DESC;

-- B2. Charge totals by procedure code.
-- Purpose: Review billed amount and payer coverage by visible procedure code.
-- Expected columns: procedure_code, claim_count, total_cost, payer_coverage.
SELECT
    procedure_code,
    count(*) AS claim_count,
    sum(total_cost) AS total_cost,
    sum(payer_coverage) AS payer_coverage
FROM hospital.silver.claims
GROUP BY procedure_code
ORDER BY total_cost DESC, procedure_code
LIMIT 100;

-- B3. Claims with no payer coverage recorded.
-- Purpose: Identify charge lines needing payer-coverage review.
-- Expected columns: claim_id, payer, procedure_code, total_cost, payer_coverage.
SELECT claim_id, payer, procedure_code, total_cost, payer_coverage
FROM hospital.silver.claims
WHERE payer_coverage = DECIMAL '0.00'
ORDER BY total_cost DESC
LIMIT 100;

-- B4. Highest-value charge lines by payer.
-- Purpose: Prioritize large charge lines for financial reconciliation.
-- Expected columns: claim_id, payer, procedure_code, total_cost, payer_coverage.
SELECT claim_id, payer, procedure_code, total_cost, payer_coverage
FROM hospital.silver.claims
ORDER BY total_cost DESC
LIMIT 100;
