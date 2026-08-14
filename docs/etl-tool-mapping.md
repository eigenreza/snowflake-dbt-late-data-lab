# Design note: this pipeline in an enterprise ETL tool

This project builds its pipeline with Python, SQL and dbt. The same design maps cleanly
onto an enterprise integration platform such as Informatica Intelligent Data Management
Cloud (IDMC). This note records the mapping, and what a migration from PowerCenter to IDMC
would involve for a pipeline like this one. It is a design note; the project itself does
not run on Informatica.

## Mapping the pieces

| This project | IDMC concept | Notes |
|---|---|---|
| On-prem core system (DuckDB file standing in for a database) | **Connection** (JDBC or native database connector) through a **Secure Agent** installed on-prem | The agent runs inside the network and is the only piece that touches the source |
| Watermark extract job (`extract/extract_core.py`) | **Mapping** with an **in-out parameter** holding the watermark, a source filter `updated_at > $LastWatermark`, and a post-task that writes the new watermark; or **Mass Ingestion** with incremental load on `updated_at` | The control table becomes a parameter file or a small Snowflake table the taskflow updates after a successful run |
| Legacy daily files (semicolon, Latin-1, decimal comma, DDMMYYYY) | **Flat file connection** with code page ISO-8859-1, delimiter `;`, header row; an **Expression** transformation for `TO_DECIMAL(REPLACE_CHR(0, AMT, ',', '.'))`, `TO_DATE(BOOK_DT, 'DDMMYYYY')`, `UPPER`, `INITCAP` | The same parsing rules as `docs/legacy-mapping.md`; a reusable **mapplet** holds them |
| Norges Bank FX feed (`extract/extract_fx.py`) | **REST V2 connection** and a mapping that flattens the SDMX CSV, or a scheduled file download | Cache as a table rather than a committed CSV |
| Loader (`PUT` and `COPY INTO`) | **Snowflake Data Cloud connection** with bulk load through an internal stage (the connector stages and copies for you) | Reject handling becomes the connector's error file plus an **audit table** written by the mapping |
| Load audit | **Taskflow** variables written to an audit target after each task; **Operational Insights** for history | |
| dbt staging and marts | **Mappings with pushdown optimization (full)**, so the SQL runs inside Snowflake on `TRANSFORM_WH` | The model SQL translates almost one to one; incremental logic becomes an **Update Strategy** or a pre-SQL delete plus insert |
| Incremental strategies | **Update Strategy** transformation (`DD_INSERT`, `DD_UPDATE`) for merge; **pre-SQL** `DELETE ... WHERE event_date >= $WindowStart` for delete+insert; truncate-and-load for full refresh | The lookback window is a mapping parameter |
| SCD type 2 customers (dbt snapshot) | **Slowly Changing Dimension** mapping template (type 2) with effective and expiry dates | |
| Tests (dbt tests, pytest) | **Data Quality** rules and **Data Profiling**; a taskflow **Decision** step that stops on rule failures | |
| Reconciliation of old and new report | A mapping that joins the two outputs and writes the differences; a **Notification** task on any difference | |
| Query tags and cost collection | **Taskflow** naming and the Snowflake session parameter set in the connection; cost from Snowflake query history as here | |
| Scheduling (`Makefile`, GitHub Actions) | **Schedules** on taskflows, **file listener** that starts the load when the day's files arrive | |
| Row access policies | Unchanged; they live in Snowflake | |

## A day in the taskflow

1. A **file listener** or schedule starts the daily taskflow after the core batch.
2. **Extract**: the watermark mapping reads the changed rows through the Secure Agent and writes the legacy files, or writes straight to Snowflake RAW. A migration project would keep the files for a while, so the old consumers keep working.
3. **Load**: one mapping task per entity loads the day's file into RAW and writes the audit row. A parallel step loads the exchange rates.
4. **Transform**: pushdown mappings build staging and marts in Snowflake. The strategy and window are parameters.
5. **Check**: data quality rules run; a decision step routes failures to a notification and stops the taskflow.
6. **Report**: the reconciliation mapping compares the old report with the new one while both exist.

## PowerCenter to IDMC for a pipeline like this

- **Inventory**: list workflows, sessions, mappings, mapplets, parameter files and connections. Legacy pipelines like the nightly reporting job are usually a session running a SQL override; those are the first candidates for pushdown mappings.
- **Conversion**: the PowerCenter to Cloud Data Integration conversion utility imports repository objects and reports what it could not convert. Expect manual work on SQL overrides, stored procedure calls, custom transformations and session-level properties.
- **Connections**: re-point every connection. The on-prem source needs a Secure Agent; Snowflake replaces the old target with a native connector; flat files move from a server share to a location the agent can reach.
- **Parameters and watermarks**: parameter files become mapping parameters and taskflow variables; watermark logic that lived in shell scripts moves into the taskflow.
- **Incremental logic**: PowerCenter mappings that used `DELETE` then `INSERT` keep the same shape as pre-SQL plus insert; mappings that depended on target-side lookups usually become Update Strategy with pushdown.
- **Testing**: run old and new side by side and reconcile, exactly as `reconcile/reconcile.py` does here: row counts, sums per key, differing keys. Decide which side is right before cutover.
- **Operations**: schedules, monitoring and alerting are rebuilt in IDMC; the Secure Agent becomes a managed piece of infrastructure with its own upgrade cycle.
- **Cutover**: switch consumers to the new tables, keep the old job for one more cycle as a fallback, then retire it.

The lesson from this project carries over: the hard part of such a migration is rarely the
mapping itself. It is late and corrected data, which decides whether the new pipeline and
the old report agree, and the loading strategy, which decides what that agreement costs.
