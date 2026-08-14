# Rewriting the old reporting job

## What the old job did

`legacy/nightly_report.sql` ran on the core system after the end-of-day batch. In the old
procedural style it built a temp table of the month's card purchases and bill payments,
converted foreign amounts to NOK with a correlated subquery that picked the latest rate on
or before the booking date, deleted the current month from the report table and inserted
it again. It produced one table: monthly transaction value and count by merchant category
and customer region, in NOK. It had no tests, and its only parameters were the run date and
the first month to rebuild, substituted by the scheduler.

## How it was rewritten

The report is now the dbt model `mart_monthly_category_region`, built on the star schema
from `fct_transactions`, `dim_merchant` and the current version of `dim_customer`. It keeps
the old grain and columns (`report_month`, `category_code`, `region`, `value_nok`,
`txn_count`) and the old rules: card purchases and bill payments only, spend as a positive
value, the customer's current region. Like the old job it is incremental by month
(delete+insert), but its window follows the loading strategy of the warehouse rather than
a hard-coded "current month". It has tests on its keys and references and a description for
every column.

## How the reconciliation works

`reconcile/reconcile.py` compares the two outputs on the same data in two ways:

- **full**: the old job is run with a full rebuild of every month on the core system as it
  stands, and compared with the mart. This shows whether the rewrite reproduces the logic.
- **nightly**: the report table as the old job maintained it night after night during the
  replay is compared with the mart. This shows what the old job's own window did.

For both it reports row counts, sums of value and count, keys present on one side only,
keys with a value or count difference, and the largest differences. The full comparison is
a test: `tests/test_reconciliation.py` replays a small bank day by day, runs the old job
every night, builds the warehouse and asserts that the full rebuild matches to the cent and
that the nightly comparison differs only in months before the current one.

## What it found

The full rebuild matches in both scenarios: every month, category and region key present on
both sides, identical sums of value and count, no difference larger than half a cent. The
rewrite reproduces the old logic.

The nightly comparison does not match, for two reasons that the per-month totals separate.

**Late rows.** Transactions booked in November reached the core system in December, after
the old job had rebuilt November for the last time. In the mild scenario 326 November rows
arrived in December (301 new transactions and 25 corrections of rows already there), so the
old report is short 301 transactions and 176,206 NOK for November. In the heavy scenario it
is 2,807 rows (2,581 new, 226 corrections), 2,581 transactions and 1,626,129 NOK, plus two
October transactions (7.53 NOK) that arrived more than a month late. November is the only
month whose totals differ in the mild scenario; in the heavy scenario October differs by
those two rows as well.

**Region changes.** Sixteen (mild) and fifteen (heavy) customers moved to another region during
December. Both the old job and the rewrite report by the customer's *current* region, but
the rewrite applies the rule to all history every time it runs, while the old job applies it
only to the month it rebuilds. So the two agree on every month's total but split it
differently across regions for every month those customers were active: about 3,600 (mild)
and 3,800 (heavy) keys in the months before November differ, with 9.0 (mild) and 7.7 (heavy)
million NOK of spend moved between regions and the month totals unchanged.

The second finding matters beyond this job. A report keyed on a type 1 attribute of a
slowly changing dimension is unstable by construction: any change to the dimension changes
history. The star schema offers the alternative in one join: `fct_transactions` carries
`customer_sk`, the customer version that applied on the booking date, so a report by region
at the time of the transaction is stable and never needs its history rebuilt. The
experiment measures the monthly KPI that way for exactly this reason.

## Which version is right

The dbt model. The old job's result depends on when a row arrived relative to the month it
belongs to; the model's result depends only on what has arrived. A report that changes
meaning with the calendar of the load is not a report of the business. Where a consumer
needs the old numbers for continuity, the full-rebuild mode of the old job reproduces them
from the model exactly.
