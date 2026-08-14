-- 01: database, schemas, virtual warehouses and the resource monitor.
-- Run once as ACCOUNTADMIN in a Snowsight worksheet.
--
-- Two extra-small warehouses keep loading and transformation apart, so the
-- credits of each workload can be read straight from the warehouse metering
-- history. Both suspend after 60 seconds of inactivity and resume on demand.
-- New accounts create generation 2 warehouses, which bill 1.35 credits per hour
-- for an X-Small (1.0 for generation 1); the credit estimates use that rate.

USE ROLE ACCOUNTADMIN;

CREATE DATABASE IF NOT EXISTS LATE_DATA_LAB
    COMMENT = 'Late-arriving data lab: synthetic retail bank, legacy extracts, loading-strategy experiment';

CREATE SCHEMA IF NOT EXISTS LATE_DATA_LAB.RAW
    COMMENT = 'Landed files as delivered: every column text, plus file name and load time';
CREATE SCHEMA IF NOT EXISTS LATE_DATA_LAB.STAGING
    COMMENT = 'Cleaned, typed and deduplicated views built by dbt';
CREATE SCHEMA IF NOT EXISTS LATE_DATA_LAB.MARTS
    COMMENT = 'Star schema and reports built by dbt';

CREATE WAREHOUSE IF NOT EXISTS LOAD_WH
    WITH WAREHOUSE_SIZE = XSMALL
         AUTO_SUSPEND = 60
         AUTO_RESUME = TRUE
         INITIALLY_SUSPENDED = TRUE
         COMMENT = 'PUT and COPY INTO of the legacy extracts';

CREATE WAREHOUSE IF NOT EXISTS TRANSFORM_WH
    WITH WAREHOUSE_SIZE = XSMALL
         AUTO_SUSPEND = 60
         AUTO_RESUME = TRUE
         INITIALLY_SUSPENDED = TRUE
         COMMENT = 'dbt builds and the loading-strategy experiment';

-- Credit cap for the two warehouses: notify early, suspend before the trial credits run out.
CREATE RESOURCE MONITOR IF NOT EXISTS LATE_DATA_LAB_MONITOR
    WITH CREDIT_QUOTA = 35
         FREQUENCY = MONTHLY
         START_TIMESTAMP = IMMEDIATELY
         TRIGGERS ON 50 PERCENT DO NOTIFY
                  ON 80 PERCENT DO NOTIFY
                  ON 100 PERCENT DO SUSPEND
                  ON 110 PERCENT DO SUSPEND_IMMEDIATE;

ALTER WAREHOUSE LOAD_WH SET RESOURCE_MONITOR = LATE_DATA_LAB_MONITOR;
ALTER WAREHOUSE TRANSFORM_WH SET RESOURCE_MONITOR = LATE_DATA_LAB_MONITOR;

-- A second, account-wide cap catches any warehouse created later (for example by Partner Connect).
CREATE RESOURCE MONITOR IF NOT EXISTS ACCOUNT_MONITOR
    WITH CREDIT_QUOTA = 50
         FREQUENCY = MONTHLY
         START_TIMESTAMP = IMMEDIATELY
         TRIGGERS ON 75 PERCENT DO NOTIFY
                  ON 100 PERCENT DO SUSPEND
                  ON 105 PERCENT DO SUSPEND_IMMEDIATE;

ALTER ACCOUNT SET RESOURCE_MONITOR = ACCOUNT_MONITOR;

SHOW WAREHOUSES;
SHOW RESOURCE MONITORS;
