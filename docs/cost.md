# Cost: sizing, consumption control and what was used

Everything in this project runs on free tiers. Snowflake is the only component that meters
anything, and it is set up so that the work stays small and visible. The environment
configuration (database, schemas, virtual warehouses, monitors, roles, users, stages) is
kept as SQL scripts in `snowflake/` and applied in order, so the whole setup can be
reviewed, repeated and torn down.

## Sizing of the virtual warehouses

| Warehouse | Size | Auto-suspend | Auto-resume | Used by |
|---|---|---|---|---|
| `LOAD_WH` | X-Small (Gen 2) | 60 s | yes | `PUT` and `COPY INTO` of the daily files |
| `TRANSFORM_WH` | X-Small (Gen 2) | 60 s | yes | dbt builds, the experiment, reporting queries |

Two warehouses instead of one give per-workload cost figures for free: the metering history
is per warehouse, so loading and transformation can be read off separately. X-Small is enough
for 1.4 million transactions; the full build takes seconds, not minutes. New accounts create
generation 2 warehouses, which bill 1.35 credits per hour for an X-Small (generation 1 bills
1.0); the account used here did not allow switching generation, so 1.35 is the rate in the
credit estimate.

## Consumption control

- **Resource monitor** `LATE_DATA_LAB_MONITOR` on both warehouses: monthly credit quota,
  notify at 50 and 80 percent, suspend at 100 percent, suspend immediately at 110 percent.
- **Account-level monitor** as a second guard that also covers any warehouse created later,
  for example by a partner integration.
- **Auto-suspend after 60 seconds**, the shortest setting Snowflake allows. Idle time costs
  nothing; the price is a resume (and its 60-second minimum) when work starts again.
- **No result cache for the service user**, so that the experiment measures real work.
  Reports and ad hoc users keep the cache.
- Nothing that meters on its own: no Snowpipe, no tasks, no automatic clustering, no
  materialized views.

## Credit estimation method

Snowflake does not report credits per query. The experiment estimates them from the query
history:

1. Every experiment query carries a query tag `ldl:<scenario>:<strategy>:c<cycle>`.
2. `execution_time` is summed per tag.
3. Credits = execution hours x the warehouse's credit rate (1.35 per hour for an X-Small generation 2 warehouse).
4. Snowflake bills at least 60 seconds per resume. A resume is counted whenever a query starts
   after the warehouse sat idle for longer than its auto-suspend time. The estimate "with
   minimum" adds 60 seconds per resume. Both estimates are reported.
5. The warehouse metering history (credits actually billed per hour and warehouse) arrives
   with a delay and is collected afterwards as the check on the estimate.

The estimate ignores cloud services credits, which are free up to 10 percent of the day's
compute. On a grid day they do not stay below that: dbt runs thousands of small statements,
and cloud services came to about 8 percent of compute on `LOAD_WH` and about 23 percent on
`TRANSFORM_WH`. The metering totals below include them.

## Credits actually used

The grid ran on the trial account in one day, both scenarios, 310 cycles each, on the two
X-Small warehouses described above; the per-strategy estimates, the resume counts and the
metering per warehouse were collected with `python -m experiment.collect_snowflake_costs`
and are in `experiment/results/snowflake/`. Readings of the resource monitor along the way:

| Step | Credits | Running total |
|---|---|---|
| Setup, full build, reconciliation, dry runs of the harness | 0.76 | 0.76 |
| Mild grid, 310 cycles, 238 minutes | 12.1 | 12.9 |
| Aborted start of the heavy grid, 4 cycles | 0.35 | 13.2 |
| Heavy grid, 310 cycles, 237 minutes | 11.9 | 25.1 |
| Reload of the mild data and rebuild of the marts afterwards | 0.3 | 25.4 |

Metering history per warehouse over the day the two grids ran, compute and cloud services:

| Warehouse | Compute | Cloud services | Total |
|---|---|---|---|
| `LOAD_WH` | 11.00 | 0.81 | 11.80 |
| `TRANSFORM_WH` | 10.93 | 2.47 | 13.39 |

Per strategy and month, the tagged estimate on `TRANSFORM_WH` is 0.10 to 0.14 credits for
540 to 720 queries, with no resume, in both scenarios. The full refresh is in the middle
of the field in the mild scenario and a fifth above the cheapest strategy in the heavy
one, and the same strategy differs by as much between the two scenarios, so at this size
the ten cannot be told apart by credits; the per-strategy figures and the finding they
carry are in `docs/experiment.md`. The estimate adds up to 1.1 to 1.2 credits per scenario
against about 6.5 metered on that warehouse: the warehouse bills while it is resumed, and
a grid keeps it resumed for four hours, with the scoring queries, dbt's catalog queries
and the gaps between statements on the bill as well. The tagged estimate therefore
compares the strategies with each other, and the metering gives the bill.

In all, the Snowflake part of the project used about 25.4 credits of the roughly 102
the trial balance buys, under the 35-credit quota of the warehouse monitor.

## Operations

Day to day the platform needs little attention. Every load writes an audit row with rows
loaded and rejected; the extract keeps its watermark and refuses to skip a failed day; dbt
declares source freshness on RAW and tests every model; the monitors notify before they
suspend. A failed night is rerun with the same commands and produces the same result,
because every step is idempotent.

## What it would cost beyond the trial

At list prices an X-Small warehouse costs one credit per hour (1.35 for a generation 2
warehouse). Measured in the replay, loading one day took about 10 seconds on `LOAD_WH` and
the dbt run 25 to 35 seconds on `TRANSFORM_WH` whichever strategy ran, so each warehouse is
billed its 60-second minimum: about 0.045 credits per night, under 1.5 credits a month at
list price. The resume minimum, not the work, sets the floor. Cloud services add a few
percent for the many small statements dbt runs; they are free up to 10 percent of the daily
compute.
