"""The ground truth: every record version, when it arrived, and the KPIs computed from it.

Two notions of truth are used by the experiment:

* **as of a cycle**: what the core system contained at the end of that day,
  which is what a perfect warehouse load could know; and
* **final**: every version regardless of when it arrived, including the ones
  that reach the core system after the last replay day.

The KPI queries below take an ``as_of`` timestamp and work for both. They are
built as SQL text with literal values so the experiment can embed them in
larger queries, for example against a warehouse with the truth attached.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import duckdb
import pandas as pd

from generator.bank import BankEntities
from generator.scenarios import Scenario

FINAL = datetime(9999, 12, 31)
SCHEMA = "ground_truth"
REPORT_TXN_TYPES = ("CARD_PURCHASE", "BILL_PAYMENT")


def connect(path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def run(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> None:
    """Execute a statement and consume its result so the connection is free for the next one."""
    con.execute(sql, params or []).fetchall()


def _store(con: duckdb.DuckDBPyConnection, table: str, frame: pd.DataFrame, casts: dict[str, str]) -> None:
    con.register("src", frame)
    select = ", ".join(f"CAST({c} AS {casts[c]}) AS {c}" if c in casts else c for c in frame.columns)
    run(con, f"CREATE OR REPLACE TABLE {SCHEMA}.{table} AS SELECT {select} FROM src")
    con.unregister("src")


def write_truth_db(
    path: Path, scn: Scenario, entities: BankEntities, transactions: pd.DataFrame, fx_daily: pd.DataFrame
) -> None:
    if path.exists():
        path.unlink()
    con = connect(path)
    try:
        run(con, f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        _store(
            con, "customer_versions", entities.customer_versions,
            {"valid_from": "DATE", "birth_date": "DATE", "customer_since": "DATE", "arrival_ts": "TIMESTAMP"},
        )
        _store(
            con, "account_versions", entities.account_versions,
            {"open_date": "DATE", "close_date": "DATE", "arrival_ts": "TIMESTAMP", "opening_balance": "DECIMAL(18,2)",
             "credit_limit": "DECIMAL(18,2)"},
        )
        if entities.declined_attempts is not None:
            _store(con, "declined_attempts", entities.declined_attempts, {"event_date": "DATE", "amount": "DECIMAL(18,2)"})
        _store(con, "card_versions", entities.card_versions, {"issued_date": "DATE", "expiry_date": "DATE", "arrival_ts": "TIMESTAMP"})
        _store(con, "merchant_versions", entities.merchant_versions, {"arrival_ts": "TIMESTAMP"})
        _store(
            con, "transaction_versions", transactions,
            {"event_date": "DATE", "arrival_ts": "TIMESTAMP", "amount": "DECIMAL(18,2)", "amount_nok": "DECIMAL(18,2)",
             "txn_id": "BIGINT", "reversal_of": "BIGINT", "origin_txn_id": "BIGINT", "merchant_id": "INTEGER"},
        )
        _store(con, "fx_rates_daily", fx_daily, {"rate_date": "DATE", "rate_nok": "DECIMAL(18,6)"})
        run(
            con,
            f"""
            CREATE OR REPLACE TABLE {SCHEMA}.scenario AS
            SELECT ? AS name, ? AS seed, ? AS n_customers, CAST(? AS DATE) AS period_start,
                   CAST(? AS DATE) AS initial_extract_date, ? AS replay_days, CAST(? AS DATE) AS last_day
            """,
            [scn.name, scn.seed, scn.n_customers, scn.period_start, scn.initial_extract_date, scn.replay_days, scn.last_day],
        )
        run(con, f"CREATE TABLE IF NOT EXISTS {SCHEMA}.applied_days (day DATE PRIMARY KEY, applied_at TIMESTAMP)")
    finally:
        con.close()


def read_scenario(con: duckdb.DuckDBPyConnection) -> dict:
    result = con.execute(f"SELECT * FROM {SCHEMA}.scenario")
    columns = [d[0] for d in result.description]
    return dict(zip(columns, result.fetchall()[0]))


# --- SQL builders ----------------------------------------------------------

def ts_literal(ts: datetime) -> str:
    return f"TIMESTAMP '{ts.strftime('%Y-%m-%d %H:%M:%S.%f')}'"


def date_literal(day: date) -> str:
    return f"DATE '{day.isoformat()}'"


def effective_transactions_sql(as_of: datetime = FINAL, schema: str = SCHEMA) -> str:
    """Latest version of every transaction that had arrived by ``as_of``."""
    return f"""
        SELECT * EXCLUDE (rn) FROM (
            SELECT v.*, row_number() OVER (PARTITION BY txn_id ORDER BY version_no DESC) AS rn
            FROM {schema}.transaction_versions v
            WHERE arrival_ts <= {ts_literal(as_of)}
        ) WHERE rn = 1
    """


def current_customers_sql(as_of: datetime = FINAL, schema: str = SCHEMA) -> str:
    return f"""
        SELECT * EXCLUDE (rn) FROM (
            SELECT c.*, row_number() OVER (PARTITION BY customer_id ORDER BY version_no DESC) AS rn
            FROM {schema}.customer_versions c
            WHERE arrival_ts <= {ts_literal(as_of)}
        ) WHERE rn = 1
    """


def current_accounts_sql(as_of: datetime = FINAL, schema: str = SCHEMA, account_filter: str = "") -> str:
    extra = f"AND {account_filter}" if account_filter else ""
    return f"""
        SELECT * EXCLUDE (rn) FROM (
            SELECT a.*, row_number() OVER (PARTITION BY account_no ORDER BY version_no DESC) AS rn
            FROM {schema}.account_versions a
            WHERE arrival_ts <= {ts_literal(as_of)} {extra}
        ) WHERE rn = 1
    """


def daily_balance_sql(as_of: datetime, start: date, end: date, schema: str = SCHEMA, account_filter: str = "") -> str:
    """End-of-day balance per account for every day in [start, end] the account was open."""
    return f"""
        WITH eff AS ({effective_transactions_sql(as_of, schema)}),
        accounts AS (
            SELECT account_no, open_date, close_date, opening_balance
            FROM ({current_accounts_sql(as_of, schema, account_filter)})
        ),
        net AS (
            SELECT account_no, event_date, sum(amount_nok) AS net_nok
            FROM eff GROUP BY 1, 2
        ),
        spine AS (
            SELECT a.account_no, a.opening_balance, d.balance_date
            FROM accounts a
            JOIN (SELECT CAST(unnest(generate_series({date_literal(start)}, {date_literal(end)}, INTERVAL 1 DAY)) AS DATE) AS balance_date) d
              ON d.balance_date >= a.open_date AND (a.close_date IS NULL OR d.balance_date <= a.close_date)
        ),
        before AS (
            SELECT account_no, sum(net_nok) AS carried FROM net WHERE event_date < {date_literal(start)} GROUP BY 1
        )
        SELECT s.account_no, s.balance_date,
               ROUND(s.opening_balance + COALESCE(b.carried, 0)
                     + COALESCE(sum(n.net_nok) OVER (PARTITION BY s.account_no ORDER BY s.balance_date), 0), 2) AS balance_nok
        FROM spine s
        LEFT JOIN before b USING (account_no)
        LEFT JOIN net n ON n.account_no = s.account_no AND n.event_date = s.balance_date
    """


def monthly_category_region_sql(as_of: datetime, first_month: date, last_month: date, schema: str = SCHEMA) -> str:
    """Monthly spend and count by merchant category and the customer's current region."""
    return f"""
        WITH eff AS ({effective_transactions_sql(as_of, schema)}),
        cust AS ({current_customers_sql(as_of, schema)}),
        acct AS (SELECT account_no, customer_id FROM ({current_accounts_sql(as_of, schema)})),
        merch AS (SELECT merchant_id, category_code FROM {schema}.merchant_versions)
        SELECT CAST(date_trunc('month', e.event_date) AS DATE) AS report_month, m.category_code, c.region,
               ROUND(sum(-e.amount_nok), 2) AS value_nok, count(*) AS txn_count
        FROM eff e
        JOIN acct a USING (account_no)
        JOIN cust c USING (customer_id)
        JOIN merch m USING (merchant_id)
        WHERE e.txn_type IN ('CARD_PURCHASE', 'BILL_PAYMENT')
          AND e.event_date >= {date_literal(first_month)}
          AND e.event_date < {date_literal(last_month)} + INTERVAL 1 MONTH
        GROUP BY 1, 2, 3
    """


def knowable_customer_spans_sql(as_of: datetime = FINAL, schema: str = SCHEMA) -> str:
    """Customer versions as a warehouse fed by the extracts can know them by ``as_of``.

    The initial extract delivers only the state at the initial extract date, so
    that state is valid from the beginning of time; every version delivered
    later is valid from its own date. Each span ends where the next begins.
    """
    return f"""
        SELECT customer_id, region, segment, version_no, effective_from,
               coalesce(lead(effective_from) OVER (PARTITION BY customer_id ORDER BY effective_from, version_no),
                        DATE '9999-12-31') AS effective_to
        FROM (
            SELECT c.customer_id, c.region, c.segment, c.version_no,
                   CASE WHEN CAST(c.arrival_ts AS DATE) <= s.initial_extract_date THEN DATE '1900-01-01' ELSE c.valid_from END AS effective_from,
                   row_number() OVER (
                       PARTITION BY c.customer_id, CAST(c.arrival_ts AS DATE) <= s.initial_extract_date
                       ORDER BY c.version_no DESC
                   ) AS rn,
                   CAST(c.arrival_ts AS DATE) <= s.initial_extract_date AS before_initial
            FROM {schema}.customer_versions c
            CROSS JOIN (SELECT initial_extract_date FROM {schema}.scenario) s
            WHERE c.arrival_ts <= {ts_literal(as_of)}
        )
        WHERE NOT before_initial OR rn = 1
    """


def monthly_by_event_region_sql(as_of: datetime, start: date, end: date, schema: str = SCHEMA) -> str:
    """Monthly spend and count by merchant category and the region the customer had on the booking date."""
    return f"""
        WITH eff AS ({effective_transactions_sql(as_of, schema)}),
        cust AS ({knowable_customer_spans_sql(as_of, schema)}),
        acct AS (SELECT account_no, customer_id FROM ({current_accounts_sql(as_of, schema)})),
        merch AS (SELECT merchant_id, category_code FROM {schema}.merchant_versions)
        SELECT CAST(date_trunc('month', e.event_date) AS DATE) AS report_month, m.category_code, c.region,
               ROUND(sum(-e.amount_nok), 2) AS value_nok, count(*) AS txn_count
        FROM eff e
        JOIN acct a USING (account_no)
        JOIN cust c ON c.customer_id = a.customer_id AND e.event_date >= c.effective_from AND e.event_date < c.effective_to
        JOIN merch m USING (merchant_id)
        WHERE e.txn_type IN ('CARD_PURCHASE', 'BILL_PAYMENT')
          AND e.event_date >= {date_literal(start)} AND e.event_date <= {date_literal(end)}
        GROUP BY 1, 2, 3
    """


def fx_daily_sql(as_of: datetime, start: date, end: date, schema: str = SCHEMA) -> str:
    """Daily spend in foreign currencies, in the original currency and in NOK."""
    return f"""
        WITH eff AS ({effective_transactions_sql(as_of, schema)})
        SELECT event_date, currency, ROUND(sum(-amount), 2) AS amount_ccy, ROUND(sum(-amount_nok), 2) AS amount_nok,
               count(*) AS txn_count
        FROM eff
        WHERE currency <> 'NOK' AND event_date BETWEEN {date_literal(start)} AND {date_literal(end)}
        GROUP BY 1, 2
    """


# --- Convenience wrappers --------------------------------------------------

def kpi_daily_balance(con: duckdb.DuckDBPyConnection, as_of: datetime, start: date, end: date) -> pd.DataFrame:
    return con.execute(daily_balance_sql(as_of, start, end) + " ORDER BY 1, 2").fetchdf()


def kpi_monthly_category_region(con: duckdb.DuckDBPyConnection, as_of: datetime, first_month: date, last_month: date) -> pd.DataFrame:
    return con.execute(monthly_category_region_sql(as_of, first_month, last_month) + " ORDER BY 1, 2, 3").fetchdf()


def kpi_fx_daily(con: duckdb.DuckDBPyConnection, as_of: datetime, start: date, end: date) -> pd.DataFrame:
    return con.execute(fx_daily_sql(as_of, start, end) + " ORDER BY 1, 2").fetchdf()


def lateness_summary(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """How many first versions arrived how many days after their booking date."""
    return con.execute(
        f"""
        SELECT arrival_lag_days AS lag_days, count(*) AS rows,
               count(*) * 1.0 / sum(count(*)) OVER () AS share
        FROM {SCHEMA}.transaction_versions WHERE version_no = 1 AND change_type = 'NEW'
        GROUP BY 1 ORDER BY 1
        """
    ).fetchdf()


def correction_summary(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(
        f"SELECT change_type, count(*) AS rows FROM {SCHEMA}.transaction_versions GROUP BY 1 ORDER BY 1"
    ).fetchdf()
