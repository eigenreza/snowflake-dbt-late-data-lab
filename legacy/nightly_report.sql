-- NIGHTLY_REPORT.SQL
--
-- Monthly transaction value and count by merchant category and customer region, in NOK.
-- Runs on the core system after the end-of-day batch.
-- Rebuilds every month from &FROM_MONTH up to &RUN_DATE (normally the current month only).
--
-- Parameters substituted by the scheduler:
--   &RUN_DATE    date of the run, YYYY-MM-DD
--   &FROM_MONTH  first day of the first month to rebuild, YYYY-MM-DD

CREATE SCHEMA IF NOT EXISTS report;

CREATE TABLE IF NOT EXISTS report.monthly_category_region (
    report_month   DATE,
    category_code  VARCHAR,
    region         VARCHAR,
    value_nok      DECIMAL(18, 2),
    txn_count      INTEGER,
    loaded_at      TIMESTAMP
);

-- Step 1: collect the transactions in scope.
CREATE OR REPLACE TEMP TABLE tmp_txn AS
SELECT t.txn_id,
       t.event_date,
       t.amount,
       t.currency,
       a.customer_id,
       m.category_code
FROM core.transactions t
JOIN core.accounts a ON a.account_no = t.account_no
JOIN core.merchants m ON m.merchant_id = t.merchant_id
WHERE t.txn_type IN ('CARD_PURCHASE', 'BILL_PAYMENT')
  AND t.event_date >= DATE '&FROM_MONTH'
  AND t.event_date <= DATE '&RUN_DATE';

-- Step 2: convert to NOK with the latest rate on or before the booking date.
CREATE OR REPLACE TEMP TABLE tmp_txn_nok AS
SELECT x.txn_id,
       x.event_date,
       x.customer_id,
       x.category_code,
       CASE WHEN x.currency = 'NOK' THEN x.amount
            ELSE ROUND(x.amount * (SELECT f.rate_nok
                                   FROM core.fx_rates f
                                   WHERE f.currency = x.currency
                                     AND f.rate_date <= x.event_date
                                   ORDER BY f.rate_date DESC
                                   LIMIT 1), 2)
       END AS amount_nok
FROM tmp_txn x;

-- Step 3: replace the months in scope.
DELETE FROM report.monthly_category_region
WHERE report_month >= DATE '&FROM_MONTH';

INSERT INTO report.monthly_category_region
SELECT CAST(date_trunc('month', x.event_date) AS DATE) AS report_month,
       x.category_code,
       c.region,
       SUM(-1 * x.amount_nok) AS value_nok,
       COUNT(*) AS txn_count,
       CURRENT_TIMESTAMP AS loaded_at
FROM tmp_txn_nok x
JOIN core.customers c ON c.customer_id = x.customer_id
GROUP BY 1, 2, 3;

DROP TABLE tmp_txn_nok;
DROP TABLE tmp_txn;
