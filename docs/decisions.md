# Decisions

Short notes on the engineering choices that are not obvious from the code.

## Python 3.11

The project pins Python 3.11 (3.12 also works). The dbt adapters for DuckDB and Snowflake publish wheels for these versions; newer interpreters are not yet supported by every dependency.

## DuckDB files are never committed

`onprem/core_bank.duckdb` and `data/late_data_lab.duckdb` are rebuilt from the generator and the pipeline, so they are ignored by git. The demo reads small Parquet exports in `demo/data/` instead of a database file, which keeps the repository light and the demo independent of any database engine.

## dbt profile lives outside the repository

`~/.dbt/profiles.yml` holds both targets. The `dev` target is DuckDB and needs no credentials. The `snowflake` target reads the account, user and private key path from environment variables, so no secret can end up in the project tree. The CI workflow writes its own DuckDB-only profile at run time.

The DuckDB file path in the profile is relative to the directory dbt is started from, so every dbt command runs from the repository root with `--project-dir transform`. Scripts that call dbt set `LDL_DUCKDB_PATH` to an absolute path instead.

## One dbt project, two targets

The dbt project is in `transform/`. Switching engines is only `--target dev` or `--target snowflake`. Engine differences are isolated in macros; models stay portable SQL.

## Schema names

A custom `generate_schema_name` macro returns the configured schema unchanged, so models land in `STAGING` and `MARTS` on both targets rather than in a prefixed schema such as `STAGING_MARTS`. Intermediate models and seeds live in `STAGING`; the SCD Type 2 snapshot lives in `MARTS` next to the dimensions it feeds.

## Record versions as the simulation model

The generator produces every record as a sequence of versions, each stamped with the time it reached the core system. The core system shows the latest version that has arrived; the ground truth keeps all of them. Advancing the core system by one day means applying the versions that arrive that day. The extract job only sees the core system and never the ground truth.

## Two notions of truth

KPI errors are measured against the truth *as of a cycle*: the latest versions that had arrived in the core system by the end of that day. This is what a perfect load could know, so a full refresh scores zero against it and the error of every other strategy is caused by the strategy alone. The *final* truth, which includes versions that arrive after the last replay day, is reported as a reference line for the error no loading strategy can remove.

## Region in the monthly report

The old nightly job knows only each customer's current region, so the rewritten report keeps that rule and the two can be reconciled like for like. The star schema still carries every customer version, so a report by region at the time of the transaction is a join away.

## The extract keeps its state in the source system

The watermark extract records one row per run day in a small control table inside the core system, with the window it used and whether it finished. The watermark only moves when every file of a day is on disk; a day that failed halfway blocks later days until it is rerun, and a rerun reuses the recorded window so it produces the same files.

## RAW keeps files exactly as delivered

Every RAW column is text, and every row carries its file name, extract day and load time. Duplicate lines and inconsistent casing survive into RAW on purpose; staging is where they are fixed. The trailer line of each legacy file has two fields instead of a full record, so it is rejected by the load and recorded as one rejected row per file in the load audit.

## One service user for Snowflake

The loader and dbt share one service user with key-pair authentication and two roles: the loader connects as `LOADER` on `LOAD_WH`, dbt as `TRANSFORMER` on `TRANSFORM_WH`. Each writing role owns the schema it writes. The row access policies are attached by dbt after every build on the Snowflake target, so a full refresh cannot drop them.

## Where the loading strategy applies

`fct_transactions` is the model that changes with the strategy: `append` inserts rows booked after the newest row already loaded, `merge` upserts by transaction id within the lookback window, and `delete+insert` replaces whole booking dates within it. The daily balance snapshot and the monthly report are derived from the fact, so they always recompute the same window with delete+insert: balances are cumulative and the report is an aggregate, and neither can be appended to safely. A full refresh rebuilds all three. Within a batch every strategy keeps the newest version of a transaction; the strategies differ only in which rows they look at and whether they can change a row that is already there.

## The old job rebuilds one month

The nightly reporting job rebuilds the current month only. A row that arrives in December for a November booking never reaches the old report, and that is the difference the reconciliation finds. A full rebuild of the old job matches the dbt model to the cent, which shows the rewrite reproduces the old logic.

## Overdraft limits and declined debits

Every account carries an overdraft limit: none on savings accounts, none or 2,000 NOK for
students, up to 20,000, 50,000 and 100,000 NOK for the other segments. The core system books
each account's rows in date order and declines a debit that would take the balance below
minus the limit, so no balance in the warehouse is ever lower. To make that hold for every
load and not only the final one, the generator authorizes a debit against its most expensive
version (a correction can make a charge larger), and incoming credits are never late or
corrected, so the balance the warehouse shows for any day is never lower than the final one.

## Rounding is done the same way on both sides

NOK amounts are rounded to the cent with SQL DECIMAL arithmetic, half away from zero, both in the warehouse and in the ground truth. Rounding floats in Python instead would differ by a cent on exact ties and show up as false KPI errors.

## Makefile as the entry point

The `Makefile` lists every end-to-end step and is what CI calls. On Windows without `make`, the commands inside each target run unchanged from PowerShell with the virtual environment's `python.exe` and `dbt.exe`.

## Each strategy run on Snowflake starts from empty schemas

On Snowflake the strategies share one database, so each run gets its own staging and marts
schemas by suffix. A dbt full refresh rebuilds models but never a snapshot, so a snapshot left
from an earlier run would already know customer moves that happen later in the replay and
would attribute old bookings to new regions. The harness therefore drops a strategy's schemas
and empties RAW before cycle 0, and loads the days one by one from there.

## A reset of RAW on Snowflake empties the stages too

The Snowflake loader uploads a landed file only when no file of that name is on the stage,
because landed files never change once written. The two replay scenarios break that
assumption: they land files of the same names with different contents. A reset therefore
removes the staged files along with the RAW rows, and the next load uploads what it needs
again. The `--reset` flag of the loader does the same from the command line.

## Snowflake costs are collected from a bounded window

Every experiment query carries a tag, and the cost collector sums execution time per tag from
the query history. Trial runs and an aborted run carry the same tags as the final grid, so
the collector takes a start time, an end time and windows to skip, and the results record
which window they came from.

## The race figure shows two cost measures

On an extra-small Snowflake warehouse the ten strategies cost nearly the same credits for a
month of this data: the fixed cost of each statement outweighs the rows it reads, and the
incremental strategies run more statements per cycle than a full refresh. On DuckDB, where
the cost is the execution time of the three strategy-dependent models, the full refresh
costs two to three times the windowed strategies. Both are true, so the figure shows one
row per measure, the demo lets the reader switch between credits, DuckDB seconds and rows
read, and the tables keep rows read as the measure that does not depend on the engine.

## The balance KPI is scored on a sample of accounts on Snowflake

Scoring a cycle compares the warehouse's KPI cells with the truth as of that day. For the daily
balance that is every account on every day of the window, 372,000 cells at the first cycle
and more later, pulled through the driver thirty times per strategy. On Snowflake the score
uses one account in ten (account numbers ending in 7), which keeps a cycle's scoring under five
seconds and leaves the share of wrong cells within a few tenths of a percent of the full
count; the monthly and FX cells are scored in full and match the DuckDB run exactly.

## The Power BI report is a project of text files

The report is saved as a Power BI project: the semantic model as TMDL files, the pages and
visuals as JSON. Both are written from the documented model (tables, relationships, the
measures in `measures.dax`, the roles in `rls.md`) rather than clicked together, so the model
can be reviewed and changed like any other code, and Power BI Desktop reloads the files when
they change. Two names had to give way to reserved words: the measure table is `Bank
Measures`, and the variable that holds the truth cells in `KPI Cells Wrong %` is `TruthCells`.
The connection details are two parameters with neutral values in the repository, and the
local data cache stays out of version control.

## Customer history needs a snapshot run per extract day

A dbt snapshot records a change only if it runs between the load that holds the old row and
the load that holds the new one. Loading all 31 extract days at once and building once gives
one version per customer, which is what the Snowflake warehouse had after the cost grids. The
history was rebuilt by loading one day, running the snapshot, and repeating, which is also
how the pipeline runs in production: 3,043 versions for 3,000 customers. Two details matter
on the way: the tests that compare a mart with the reset intermediate tables are left out of
the per-day runs, and the FX cache is loaded once more, because a day-restricted load skips it.

## The demo runs on a free hosting tier

The demo reads only the small exported files in `demo/data`, so it needs no database, no
credentials and very little compute. It is hosted on the free tier of Azure App Service from
a package that holds the `demo` folder and its own short requirements file, not the whole
project. The free tier stops the app when nobody uses it, which is why the first load after a
quiet period is slow, and it caps daily compute, which a read-only demo of this size stays
well under.
