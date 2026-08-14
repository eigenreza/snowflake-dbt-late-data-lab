"""The on-prem core banking system as a DuckDB file.

The core system holds the latest state of every record together with
``created_at`` and ``updated_at``. It is built from the ground truth at the
initial extract date and then advanced one day at a time by applying the
versions that arrive that day. Nothing in the core database points back at
the ground truth.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import duckdb
import pandas as pd

from common.legacy_format import LAYOUTS

KEYS = {
    "customers": "customer_id",
    "accounts": "account_no",
    "cards": "card_id",
    "merchants": "merchant_id",
    "transactions": "txn_id",
}

VERSION_TABLES = {
    "customers": "customer_versions",
    "accounts": "account_versions",
    "cards": "card_versions",
    "merchants": "merchant_versions",
    "transactions": "transaction_versions",
}

DDL = {
    "customers": """
        CREATE TABLE core.customers (
            customer_id     INTEGER PRIMARY KEY,
            customer_no     VARCHAR NOT NULL,
            first_name      VARCHAR,
            last_name       VARCHAR,
            birth_date      DATE,
            segment         VARCHAR,
            region          VARCHAR,
            street          VARCHAR,
            postal_code     VARCHAR,
            city            VARCHAR,
            customer_since  DATE,
            created_at      TIMESTAMP NOT NULL,
            updated_at      TIMESTAMP NOT NULL
        )""",
    "accounts": """
        CREATE TABLE core.accounts (
            account_no       VARCHAR PRIMARY KEY,
            customer_id      INTEGER NOT NULL,
            account_type     VARCHAR,
            open_date        DATE,
            close_date       DATE,
            status           VARCHAR,
            opening_balance  DECIMAL(18, 2),
            credit_limit     DECIMAL(18, 2),
            created_at       TIMESTAMP NOT NULL,
            updated_at       TIMESTAMP NOT NULL
        )""",
    "cards": """
        CREATE TABLE core.cards (
            card_id      VARCHAR PRIMARY KEY,
            account_no   VARCHAR NOT NULL,
            masked_pan   VARCHAR,
            issued_date  DATE,
            expiry_date  DATE,
            status       VARCHAR,
            created_at   TIMESTAMP NOT NULL,
            updated_at   TIMESTAMP NOT NULL
        )""",
    "merchants": """
        CREATE TABLE core.merchants (
            merchant_id    INTEGER PRIMARY KEY,
            merchant_name  VARCHAR,
            category_code  VARCHAR,
            category_name  VARCHAR,
            country_code   VARCHAR,
            city           VARCHAR,
            created_at     TIMESTAMP NOT NULL,
            updated_at     TIMESTAMP NOT NULL
        )""",
    "transactions": """
        CREATE TABLE core.transactions (
            txn_id       BIGINT PRIMARY KEY,
            account_no   VARCHAR NOT NULL,
            card_id      VARCHAR,
            merchant_id  INTEGER,
            txn_type     VARCHAR,
            event_date   DATE NOT NULL,
            amount       DECIMAL(18, 2) NOT NULL,
            currency     VARCHAR NOT NULL,
            status       VARCHAR,
            reversal_of  BIGINT,
            created_at   TIMESTAMP NOT NULL,
            updated_at   TIMESTAMP NOT NULL
        )""",
}


def end_of_day(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59, 999999))


def start_of_day(day: date) -> datetime:
    return datetime.combine(day, time(0, 0, 0))


def connect(path: Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(path), read_only=read_only)


def run(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> None:
    """Execute a statement and consume its result so the connection is free for the next one."""
    con.execute(sql, params or []).fetchall()


def scalar(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None):
    """First column of the first row, with the result fully consumed."""
    rows = con.execute(sql, params or []).fetchall()
    return rows[0][0] if rows else None


def create_schema(con: duckdb.DuckDBPyConnection) -> None:
    run(con, "CREATE SCHEMA IF NOT EXISTS core")
    for ddl in DDL.values():
        run(con, ddl)
    run(
        con,
        """
        CREATE TABLE core.fx_rates (
            rate_date  DATE NOT NULL,
            currency   VARCHAR NOT NULL,
            rate_nok   DECIMAL(18, 6) NOT NULL,
            PRIMARY KEY (rate_date, currency)
        )
        """,
    )


def attach_truth(con: duckdb.DuckDBPyConnection, truth_db: Path) -> None:
    run(con, f"ATTACH IF NOT EXISTS '{truth_db.as_posix()}' AS truthdb (READ_ONLY)")


def detach_truth(con: duckdb.DuckDBPyConnection) -> None:
    run(con, "DETACH truthdb")


def data_columns(entity: str) -> list[str]:
    """Columns of the core table except the two audit timestamps."""
    return [c.source for c in LAYOUTS[entity] if c.source not in ("created_at", "updated_at")]


def apply_versions(con: duckdb.DuckDBPyConnection, entity: str, low: datetime, high: datetime) -> int:
    """Upsert the latest version of each record that arrived in (low, high]."""
    key = KEYS[entity]
    cols = data_columns(entity)
    col_list = ", ".join(cols)
    updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != key)
    sql = f"""
        INSERT INTO core.{entity} ({col_list}, created_at, updated_at)
        SELECT {col_list}, first_arrival, arrival_ts
        FROM (
            SELECT *,
                   row_number() OVER (PARTITION BY {key} ORDER BY version_no DESC) AS rn,
                   min(arrival_ts) OVER (PARTITION BY {key}) AS first_arrival
            FROM truthdb.ground_truth.{VERSION_TABLES[entity]}
            WHERE arrival_ts > ? AND arrival_ts <= ?
        )
        WHERE rn = 1
        ON CONFLICT ({key}) DO UPDATE SET {updates}, updated_at = excluded.updated_at
    """
    before = scalar(con, f"SELECT count(*) FROM core.{entity}")
    run(con, sql, [low, high])
    after = scalar(con, f"SELECT count(*) FROM core.{entity}")
    changed = scalar(con, f"SELECT count(*) FROM core.{entity} WHERE updated_at > ? AND updated_at <= ?", [low, high])
    return int(changed) if changed else int(after - before)


def build_initial_state(core_db: Path, truth_db: Path, initial_extract_date: date, fx_published: pd.DataFrame) -> dict[str, int]:
    """Create the core database as it looked at the end of the initial extract date."""
    if core_db.exists():
        core_db.unlink()
    wal = core_db.with_suffix(core_db.suffix + ".wal")
    if wal.exists():
        wal.unlink()
    con = connect(core_db)
    try:
        create_schema(con)
        attach_truth(con, truth_db)
        counts = {}
        for entity in KEYS:
            counts[entity] = apply_versions(con, entity, datetime(1900, 1, 1), end_of_day(initial_extract_date))
        con.register("fx_df", fx_published)
        run(con, "INSERT INTO core.fx_rates SELECT CAST(rate_date AS DATE), currency, rate_nok FROM fx_df ORDER BY 1, 2")
        con.unregister("fx_df")
        detach_truth(con)
    finally:
        con.close()
    return counts


def apply_day(core_db: Path, truth_db: Path, day: date) -> dict[str, int]:
    """Apply everything that arrived in the core system on one day."""
    con = connect(core_db)
    try:
        attach_truth(con, truth_db)
        counts = {}
        for entity in KEYS:
            counts[entity] = apply_versions(con, entity, start_of_day(day) - pd.Timedelta(microseconds=1), end_of_day(day))
        detach_truth(con)
    finally:
        con.close()
    return counts
