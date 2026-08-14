# Power BI model guide

How the bank report in `report/` is built in Power BI Desktop on the star schema in `MARTS`.
Import mode is used so the report keeps working when the Snowflake trial ends.

## 1. Connect

The Snowflake connector in Power BI Desktop signs in with a user name and password, a
Microsoft Entra ID account or a key pair. It cannot open the browser sign-in that a Google or
other SSO user needs and it cannot answer an MFA prompt, so the report connects as a
reporting service user with a key pair: `LDL_REPORT_SVC`, type SERVICE, default role
`REPORTER`, default warehouse `TRANSFORM_WH`, with the regional reporter roles granted as
well. `snowflake/02_roles_and_users.sql` creates it; make its key with
`python snowflake/make_keypair.py --out ~/.snowflake/ldl_report_rsa_key.p8` and keep the
key and its passphrase outside the repository.

The connection details live in two model parameters (`expressions.tmdl`):

| Parameter | Value in the repository | Set it to |
|---|---|---|
| `SnowflakeServer` | `<organization>-<account>.snowflakecomputing.com` | your account host |
| `SnowflakeRole` | `REPORTER` | `REPORTER_VESTLAND` or `REPORTER_OSLO` to see the row access policy at work |
| `DataFolder` | `C:\snowflake-dbt-late-data-lab\demo\data` | the `demo/data` folder of your checkout (for the truth table) |

Every Snowflake table is read with `Snowflake.Databases(SnowflakeServer, "TRANSFORM_WH",
[Role = SnowflakeRole, Implementation = "2.0"])`, database `LATE_DATA_LAB`, schema `MARTS`.

To open and load the report:

1. Open `report/late_data_lab_bank_report.pbip` in Power BI Desktop (any version with
   Power BI project files; the model is TMDL, the report is PBIR).
2. Home > Transform data > Edit parameters: set `SnowflakeServer` and `DataFolder`.
3. Home > Refresh. In the credentials dialog pick **Key Pair**, user name `LDL_REPORT_SVC`,
   paste the content of the private key file and enter its passphrase. Key-pair sign-in
   always uses the connector's ADBC implementation. For the Parquet file, choose the
   Anonymous credential if asked.

The data cache (`.pbi/cache.abf`) and the machine-specific settings are ignored by Git, so
a fresh checkout opens without data and needs this one refresh.

Without Snowflake, the same tables are available as Parquet files in `demo/data/`. The
column names are identical, so the partitions can be pointed at the files instead.

## 2. Tables

From schema `MARTS`, all in Import mode:

| Table | Role in the model |
|---|---|
| `FCT_TRANSACTIONS` | transaction fact, 1.4 million rows |
| `FCT_ACCOUNT_BALANCE_DAILY` | daily balance fact, 3.4 million rows |
| `DIM_CUSTOMER` | customer versions (SCD2) |
| `DIM_ACCOUNT` | accounts |
| `DIM_MERCHANT` | merchants and categories |
| `DIM_DATE` | calendar, marked as the date table on `date_day` |
| `DIM_CURRENCY` | currencies and the latest rate |
| `MART_MONTHLY_CATEGORY_REGION` | the rewritten monthly report, for the reconciliation page |

Four tables do not come from Snowflake:

| Table | Source |
|---|---|
| `Truth Monthly` | `demo/data/truth_monthly.parquet`, the monthly values computed from the complete source |
| `Region Access` | an inline table of user principal name to region for the dynamic security role |
| `Customer Current` | a calculated table, `FILTER ( DIM_CUSTOMER, DIM_CUSTOMER[is_current] = TRUE () )` |
| `Bank Measures` | an empty table that holds the measures |

Key columns and audit columns (`source_updated_at`, `loaded_at`, `extract_day`, the
surrogate keys) are hidden. `STAGING` and `RAW` stay out of the model; `REPORTER` cannot
see them anyway.

## 3. Current customers

`DIM_CUSTOMER` has one row per customer version, so it cannot be the "one" side of a
relationship on `customer_id`. `Customer Current` keeps the current version of each
customer. `DIM_CUSTOMER` (all versions) serves the transaction fact through `customer_sk`,
`Customer Current` serves accounts and balances through `customer_id`, and the region of a
customer in the report is the current one.

## 4. Relationships

All many-to-one, single direction, from the fact to the dimension:

| From | To | Columns |
|---|---|---|
| `FCT_TRANSACTIONS` | `DIM_CUSTOMER` | `customer_sk` |
| `FCT_TRANSACTIONS` | `DIM_ACCOUNT` | `account_no` |
| `FCT_TRANSACTIONS` | `DIM_MERCHANT` | `merchant_id` |
| `FCT_TRANSACTIONS` | `DIM_DATE` | `event_date` to `date_day` |
| `FCT_TRANSACTIONS` | `DIM_CURRENCY` | `currency` to `currency_code` |
| `FCT_ACCOUNT_BALANCE_DAILY` | `DIM_ACCOUNT` | `account_no` |
| `FCT_ACCOUNT_BALANCE_DAILY` | `DIM_DATE` | `balance_date` to `date_day` |
| `DIM_ACCOUNT` | `Customer Current` | `customer_id` |
| `MART_MONTHLY_CATEGORY_REGION` | `DIM_DATE` | `report_month` to `date_day` |
| `Truth Monthly` | `DIM_DATE` | `report_month` to `date_day` |

The report mart and the truth table share only the date table, which is what the KPI
error measures rely on.

## 5. Measures

`measures.dax` is the readable copy of the 22 measures in `Bank Measures`; the model
holds the same expressions in `tables/Bank Measures.tmdl`, one display folder per
section of the file. They cover volume, value, active customers, average and closing
balance, month-over-month change, foreign-currency share and the KPI error against the
truth.

`KPI Cells Wrong %` compares every month, category and region cell of the truth table
with the report mart. The two tables have no direct relationship, so the measure carries
the three keys over with `TREATAS`; a cell missing from the report counts as wrong.

## 6. Row-level security

The roles follow `rls.md`:

| Role | Filter |
|---|---|
| `Region Vestland`, `Region Oslo` | `'Customer Current'[region] = "<region>"` and the same on the current rows of `DIM_CUSTOMER` |
| `All regions` | no filter, for members who may see everything |
| `Regional manager` | `'Customer Current'[region]` in the regions mapped to `USERPRINCIPALNAME ()` in `Region Access` |

Test with Modeling > View as: pick a static role, or tick **Other user**, enter
`vestland.manager@example.com` and tick `Regional manager`. Either way the Overview page
drops to the 352 customers of Vestland and the region bar keeps one bar. The screenshots
`screenshots/rls_region_vestland.png` and `screenshots/rls_regional_manager.png` show both.

## 7. Pages

Five pages, 1920 by 1080, built in the report definition under `definition/pages/`:

1. **Overview**: month slicer; cards for spend, volume, active customers and average
   balance; spend by month; spend by current region.
2. **Categories**: spend by category group; a category table with spend, volume and
   average transaction; spend and month-over-month change by month.
3. **Customers**: spend by segment; active customers by region; customer count and the
   customers who moved; a table of the customer versions after a move (`version_no` > 1).
4. **Currency**: foreign-currency share, foreign-currency spend and the average rate used;
   spend by currency; foreign-currency share by month; the latest rates.
5. **Reconciliation**: report value, truth value, error and share of wrong cells; report
   versus truth by month; the error by month as a table.

`late_data_lab_bank_report.pdf` is the export of the five pages (File > Export > Export
to PDF) and `screenshots/` holds one image per page.

## 8. Save, publish and share

- The report is saved as a **Power BI project (PBIP)**: `report/late_data_lab_bank_report.pbip`
  with `late_data_lab_bank_report.Report/` (PBIR, pages and visuals as JSON) and
  `late_data_lab_bank_report.SemanticModel/` (TMDL under `definition/`). Everything is plain
  text and reviewable; Desktop detects edits made to the files and offers to reload them.
- Publishing to the Power BI service needs a workspace in a Pro or Premium capacity. The
  steps are standard: Publish > choose workspace; in the service, set the dataset's
  credentials (key pair for the reporting user), schedule a refresh, assign the role
  members under Security, and share as an **app** so viewers get the report without edit
  rights. None of that is required to use the report locally.
