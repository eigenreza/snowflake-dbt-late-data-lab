-- 04: regional row access policy on the customer-facing marts.
-- Run as TRANSFORMER after 02_roles_and_users.sql. The second policy refers to
-- MARTS.DIM_CUSTOMER, so run the script once more after the first dbt build of that
-- table (dbt build --select +dim_customer); dbt then attaches the policies to the marts
-- after every build on the Snowflake target (see transform/macros/snowflake_session.sql).
--
-- The rule mirrors the Power BI row-level security: a regional reporting role
-- sees only the customers, accounts and transactions of its region. The
-- unrestricted roles see everything.

USE ROLE TRANSFORMER;
USE WAREHOUSE TRANSFORM_WH;
USE SCHEMA LATE_DATA_LAB.MARTS;

-- Which role may see which region. Roles not listed here fall back to the unrestricted list below.
CREATE TABLE IF NOT EXISTS REGION_ACCESS (
    ROLE_NAME VARCHAR NOT NULL,
    REGION    VARCHAR NOT NULL
);
MERGE INTO REGION_ACCESS t
USING (SELECT * FROM VALUES ('REPORTER_VESTLAND', 'Vestland'), ('REPORTER_OSLO', 'Oslo')) s (ROLE_NAME, REGION)
   ON t.ROLE_NAME = s.ROLE_NAME AND t.REGION = s.REGION
 WHEN NOT MATCHED THEN INSERT (ROLE_NAME, REGION) VALUES (s.ROLE_NAME, s.REGION);

-- For tables that carry a region column (dim_customer, the monthly report).
CREATE ROW ACCESS POLICY IF NOT EXISTS REGION_POLICY AS (region VARCHAR) RETURNS BOOLEAN ->
    CURRENT_ROLE() IN ('ACCOUNTADMIN', 'SYSADMIN', 'TRANSFORMER', 'REPORTER')
    OR EXISTS (
        SELECT 1 FROM LATE_DATA_LAB.MARTS.REGION_ACCESS a
        WHERE a.ROLE_NAME = CURRENT_ROLE() AND a.REGION = region
    );

-- For fact tables that carry a customer key: the customer's current region decides.
CREATE ROW ACCESS POLICY IF NOT EXISTS CUSTOMER_REGION_POLICY AS (customer_id INTEGER) RETURNS BOOLEAN ->
    CURRENT_ROLE() IN ('ACCOUNTADMIN', 'SYSADMIN', 'TRANSFORMER', 'REPORTER')
    OR EXISTS (
        SELECT 1
        FROM LATE_DATA_LAB.MARTS.REGION_ACCESS a
        JOIN LATE_DATA_LAB.MARTS.DIM_CUSTOMER c
          ON c.REGION = a.REGION AND c.IS_CURRENT
        WHERE a.ROLE_NAME = CURRENT_ROLE() AND c.CUSTOMER_ID = customer_id
    );

SHOW ROW ACCESS POLICIES IN SCHEMA LATE_DATA_LAB.MARTS;
