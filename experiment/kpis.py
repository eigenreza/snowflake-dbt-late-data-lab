"""KPI definitions on the warehouse side, and the scoring of warehouse cells against the truth.

Scoring runs inside DuckDB: the truth KPI and the warehouse KPI are two CTEs
joined on their keys, so nothing large is pulled into Python. On the DuckDB
target the warehouse marts are read directly with the truth attached; on the
Snowflake target the warehouse KPI is fetched as a small frame and registered.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

import duckdb
import pandas as pd

from experiment.config import BALANCE_SAMPLE_FILTER, TOLERANCE_NOK
from generator import truth


@dataclass(frozen=True)
class KpiSpec:
    keys: tuple[str, ...]
    date_keys: tuple[str, ...]
    value: str
    count: str | None


SPECS: dict[str, KpiSpec] = {
    "daily_balance": KpiSpec(("account_no", "balance_date"), ("balance_date",), "balance_nok", None),
    "monthly_category_region": KpiSpec(("report_month", "category_code", "region"), ("report_month",), "value_nok", "txn_count"),
    "fx_daily": KpiSpec(("event_date", "currency"), ("event_date",), "amount_nok", "txn_count"),
}


def _month_start(day: date) -> date:
    return day.replace(day=1)


def truth_kpi_sql(
    kind: str, as_of: datetime, start: date, end: date, schema: str = truth.SCHEMA, account_filter: str = ""
) -> str:
    if kind == "daily_balance":
        return truth.daily_balance_sql(as_of, start, end, schema, account_filter)
    if kind == "monthly_category_region":
        return truth.monthly_by_event_region_sql(as_of, start, end, schema)
    if kind == "fx_daily":
        return truth.fx_daily_sql(as_of, start, end, schema)
    raise ValueError(kind)


def warehouse_kpi_sql(kind: str, marts_schema: str, start: date, end: date, account_filter: str = "") -> str:
    """The same KPI read from the marts; portable SQL for DuckDB and Snowflake."""
    s, e = truth.date_literal(start), truth.date_literal(end)
    if kind == "daily_balance":
        extra = f"AND {account_filter}" if account_filter else ""
        return f"""
            SELECT account_no, balance_date, balance_nok
            FROM {marts_schema}.fct_account_balance_daily
            WHERE balance_date >= {s} AND balance_date <= {e} {extra}
        """
    if kind == "monthly_category_region":
        # Region of the customer on the booking date (SCD2 join), so that a customer who moves
        # later does not rewrite the history of every cell; the monthly report mart keeps the
        # old job's current-region rule and is checked by the reconciliation instead.
        return f"""
            SELECT CAST(date_trunc('month', f.event_date) AS DATE) AS report_month, m.category_code, c.region,
                   ROUND(sum(-f.amount_nok), 2) AS value_nok, count(*) AS txn_count
            FROM {marts_schema}.fct_transactions f
            JOIN {marts_schema}.dim_merchant m ON m.merchant_id = f.merchant_id
            JOIN {marts_schema}.dim_customer c ON c.customer_sk = f.customer_sk
            WHERE f.txn_type IN ('CARD_PURCHASE', 'BILL_PAYMENT')
              AND f.event_date >= {s} AND f.event_date <= {e}
            GROUP BY 1, 2, 3
        """
    if kind == "fx_daily":
        return f"""
            SELECT event_date, currency, ROUND(sum(-amount), 2) AS amount_ccy, ROUND(sum(-amount_nok), 2) AS amount_nok,
                   count(*) AS txn_count
            FROM {marts_schema}.fct_transactions
            WHERE currency <> 'NOK' AND event_date >= {s} AND event_date <= {e}
            GROUP BY 1, 2
        """
    raise ValueError(kind)


def score_sql(kind: str, truth_sql: str, warehouse_sql: str) -> str:
    """Full outer join of truth and warehouse cells, aggregated to the error metrics."""
    spec = SPECS[kind]

    def typed(alias: str, col: str) -> str:
        return f"CAST({alias}.{col} AS DATE)" if col in spec.date_keys else f"{alias}.{col}"

    join = " AND ".join(f"{typed('t', k)} = {typed('w', k)}" for k in spec.keys)
    diff = f"abs(coalesce(t.{spec.value}, 0) - coalesce(w.{spec.value}, 0))"
    count_wrong = f"OR coalesce(t.{spec.count}, 0) <> coalesce(w.{spec.count}, 0)" if spec.count else ""
    first_key = spec.keys[0]
    return f"""
        WITH truth_kpi AS ({truth_sql}),
        warehouse_kpi AS ({warehouse_sql}),
        joined AS (
            SELECT
                {diff} AS abs_diff,
                (w.{first_key} IS NULL) AS missing_in_warehouse,
                (t.{first_key} IS NULL) AS extra_in_warehouse,
                ((w.{first_key} IS NULL) OR (t.{first_key} IS NULL) OR {diff} > {TOLERANCE_NOK} {count_wrong}) AS wrong
            FROM truth_kpi t
            FULL OUTER JOIN warehouse_kpi w ON {join}
        )
        SELECT
            count(*)                                            AS cells,
            coalesce(sum(CASE WHEN wrong THEN 1 ELSE 0 END), 0) AS wrong_cells,
            coalesce(avg(abs_diff), 0)                          AS mae_nok,
            coalesce(max(abs_diff), 0)                          AS max_abs_error_nok,
            coalesce(sum(abs_diff), 0)                          AS total_abs_error_nok,
            coalesce(sum(CASE WHEN missing_in_warehouse THEN 1 ELSE 0 END), 0) AS missing_in_warehouse,
            coalesce(sum(CASE WHEN extra_in_warehouse THEN 1 ELSE 0 END), 0)   AS extra_in_warehouse
        FROM joined
    """


def score(con: duckdb.DuckDBPyConnection, kind: str, truth_sql: str, warehouse_sql: str) -> dict:
    result = con.execute(score_sql(kind, truth_sql, warehouse_sql))
    columns = [d[0] for d in result.description]
    row = dict(zip(columns, result.fetchall()[0]))
    cells = int(row["cells"])
    return dict(
        kpi=kind,
        cells=cells,
        wrong_cells=int(row["wrong_cells"]),
        wrong_share=(int(row["wrong_cells"]) / cells) if cells else 0.0,
        mae_nok=float(row["mae_nok"]),
        max_abs_error_nok=float(row["max_abs_error_nok"]),
        total_abs_error_nok=float(row["total_abs_error_nok"]),
        missing_in_warehouse=int(row["missing_in_warehouse"]),
        extra_in_warehouse=int(row["extra_in_warehouse"]),
    )


def score_local_warehouse(
    warehouse_db, truth_db, kind: str, as_of: datetime, start: date, end: date, marts_schema: str = "marts"
) -> dict:
    """Score a DuckDB warehouse against the truth file, attached read-only."""
    con = duckdb.connect(str(warehouse_db))
    try:
        con.execute(f"ATTACH IF NOT EXISTS '{truth_db.as_posix()}' AS truthdb (READ_ONLY)").fetchall()
        result = score(
            con, kind,
            truth_kpi_sql(kind, as_of, start, end, schema=f"truthdb.{truth.SCHEMA}"),
            warehouse_kpi_sql(kind, marts_schema, start, end),
        )
        con.execute("DETACH truthdb").fetchall()
        return result
    finally:
        con.close()


def score_frame(
    truth_db, kind: str, as_of: datetime, start: date, end: date, warehouse_frame: pd.DataFrame, account_filter: str = ""
) -> dict:
    """Score a warehouse KPI fetched as a frame (Snowflake) against the truth."""
    con = duckdb.connect(str(truth_db), read_only=True)
    try:
        con.register("warehouse_frame", warehouse_frame)
        return score(con, kind, truth_kpi_sql(kind, as_of, start, end, account_filter=account_filter), "SELECT * FROM warehouse_frame")
    finally:
        con.close()


def local_values(warehouse_db, kind: str, marts_schema: str, start: date, end: date, account_filter: str = "") -> pd.DataFrame:
    """KPI cells of a DuckDB warehouse, for the demo's per-cycle series."""
    con = duckdb.connect(str(warehouse_db))
    try:
        return _dated(con.execute(warehouse_kpi_sql(kind, marts_schema, start, end, account_filter)).fetchdf(), kind)
    finally:
        con.close()


def truth_values(truth_db, kind: str, as_of: datetime, start: date, end: date, account_filter: str = "") -> pd.DataFrame:
    con = duckdb.connect(str(truth_db), read_only=True)
    try:
        return _dated(con.execute(truth_kpi_sql(kind, as_of, start, end, account_filter=account_filter)).fetchdf(), kind)
    finally:
        con.close()


def _dated(frame: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Normalize a KPI frame: lower-case names, date keys as dates, decimals as floats."""
    from decimal import Decimal

    frame.columns = [c.lower() for c in frame.columns]
    for col in SPECS[kind].date_keys:
        frame[col] = pd.to_datetime(frame[col]).dt.date
    for col in frame.columns:
        if frame[col].dtype == object:
            sample = frame[col].dropna()
            if len(sample) and isinstance(sample.iloc[0], Decimal):
                frame[col] = frame[col].astype(float)
    return frame


def score_truth_against_final(truth_db, kind: str, as_of: datetime, start: date, end: date) -> dict:
    """The lateness floor: how wrong the as-of truth is against the final truth."""
    con = duckdb.connect(str(truth_db), read_only=True)
    try:
        return score(
            con, kind,
            truth_kpi_sql(kind, truth.FINAL, start, end),
            truth_kpi_sql(kind, as_of, start, end),
        )
    finally:
        con.close()
