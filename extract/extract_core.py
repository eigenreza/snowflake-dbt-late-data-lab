"""Watermark extract from the on-prem core system to the legacy file format.

Each run covers one business day. It reads every record whose ``updated_at``
is later than the stored watermark and not later than the end of that day,
writes one legacy file per entity into ``landing/<scenario>/<YYYYMMDD>/``,
and only then records the run and moves the watermark forward.

Rerunning a day reuses the window recorded for it and produces the same
files, byte for byte, as long as the core system has not changed since.

Usage:
    python -m extract.extract_core --day 2024-11-30 [--scenario mild]
    python -m extract.extract_core --next
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from common import legacy_format as legacy
from common.paths import Paths, day_folder

CONTROL_TABLE = "etl_control.extract_runs"
KEYS = {
    "customers": "customer_id",
    "accounts": "account_no",
    "cards": "card_id",
    "merchants": "merchant_id",
    "transactions": "txn_id",
}
EPOCH = datetime(1900, 1, 1)
DEFAULT_DUPLICATE_RATE = 0.002

Writer = Callable[[Path, pd.DataFrame], int]


@dataclass
class ExtractResult:
    day: date
    watermark_from: datetime
    watermark_to: datetime
    rerun: bool
    files: dict[str, Path] = field(default_factory=dict)
    rows: dict[str, int] = field(default_factory=dict)

    @property
    def folder(self) -> Path:
        return next(iter(self.files.values())).parent


def end_of_day(day: date) -> datetime:
    return datetime.combine(day, time(23, 59, 59, 999999))


def run(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> None:
    """Execute a statement and consume its result so the connection is free for the next one."""
    con.execute(sql, params or []).fetchall()


def ensure_control_table(con: duckdb.DuckDBPyConnection) -> None:
    run(con, "CREATE SCHEMA IF NOT EXISTS etl_control")
    run(
        con,
        f"""
        CREATE TABLE IF NOT EXISTS {CONTROL_TABLE} (
            run_day         DATE PRIMARY KEY,
            watermark_from  TIMESTAMP NOT NULL,
            watermark_to    TIMESTAMP NOT NULL,
            status          VARCHAR NOT NULL,
            started_at      TIMESTAMP NOT NULL,
            finished_at     TIMESTAMP,
            row_counts      VARCHAR
        )
        """,
    )


def first_row(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> tuple | None:
    rows = con.execute(sql, params or []).fetchall()
    return rows[0] if rows else None


def last_completed_run(con: duckdb.DuckDBPyConnection) -> tuple[date, datetime] | None:
    row = first_row(
        con, f"SELECT run_day, watermark_to FROM {CONTROL_TABLE} WHERE status = 'DONE' ORDER BY run_day DESC LIMIT 1"
    )
    if row is None:
        return None
    return row[0], row[1]


def plan_window(con: duckdb.DuckDBPyConnection, day: date) -> tuple[datetime, datetime, bool]:
    """Decide the (from, to] window for a day, reusing the recorded one on a rerun."""
    existing = first_row(
        con, f"SELECT watermark_from, watermark_to, status FROM {CONTROL_TABLE} WHERE run_day = ?", [day]
    )
    if existing is not None:
        return existing[0], existing[1], existing[2] == "DONE"

    incomplete = first_row(con, f"SELECT run_day FROM {CONTROL_TABLE} WHERE status <> 'DONE' ORDER BY run_day LIMIT 1")
    if incomplete is not None:
        raise RuntimeError(f"the run for {incomplete[0]} did not finish; rerun it before extracting {day}")

    last = last_completed_run(con)
    if last is None:
        return EPOCH, end_of_day(day), False
    last_day, watermark = last
    if day <= last_day:
        raise RuntimeError(f"{day} is not after the last completed run ({last_day})")
    return watermark, end_of_day(day), False


def read_changes(con: duckdb.DuckDBPyConnection, entity: str, low: datetime, high: datetime) -> pd.DataFrame:
    cols = ", ".join(c.source for c in legacy.LAYOUTS[entity])
    return con.execute(
        f"SELECT {cols} FROM core.{entity} WHERE updated_at > ? AND updated_at <= ? ORDER BY updated_at, {KEYS[entity]}",
        [low, high],
    ).fetchdf()


def noise_seed(day: date, entity: str) -> int:
    return int(day.strftime("%Y%m%d")) * 10 + list(legacy.LAYOUTS).index(entity)


def run_extract(
    core_db: Path,
    landing_dir: Path,
    day: date,
    duplicate_rate: float = DEFAULT_DUPLICATE_RATE,
    writer: Writer = legacy.write_legacy_file,
) -> ExtractResult:
    con = duckdb.connect(str(core_db))
    try:
        ensure_control_table(con)
        low, high, rerun = plan_window(con, day)
        started = datetime.now().replace(microsecond=0)
        run(
            con,
            f"""
            INSERT INTO {CONTROL_TABLE} (run_day, watermark_from, watermark_to, status, started_at)
            VALUES (?, ?, ?, 'RUNNING', ?)
            ON CONFLICT (run_day) DO UPDATE SET status = 'RUNNING', started_at = excluded.started_at,
                finished_at = NULL, row_counts = NULL
            """,
            [day, low, high, started],
        )
        result = ExtractResult(day, low, high, rerun)
        folder = day_folder(landing_dir, day)
        folder.mkdir(parents=True, exist_ok=True)
        for entity in legacy.LAYOUTS:
            changes = read_changes(con, entity, low, high)
            formatted = legacy.format_frame(changes, entity)
            noisy = legacy.add_noise(formatted, entity, noise_seed(day, entity), duplicate_rate)
            path = folder / legacy.file_name(entity, day)
            result.rows[entity] = writer(path, noisy)
            result.files[entity] = path
        # Every file is on disk: now the watermark may move.
        run(
            con,
            f"UPDATE {CONTROL_TABLE} SET status = 'DONE', finished_at = ?, row_counts = ? WHERE run_day = ?",
            [datetime.now().replace(microsecond=0), json.dumps(result.rows), day],
        )
        return result
    finally:
        con.close()


def next_day(core_db: Path) -> date:
    con = duckdb.connect(str(core_db), read_only=True)
    try:
        if not first_row(con, "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'etl_control'")[0]:
            raise SystemExit("no extract has run yet; pass --day for the initial extract")
        last = last_completed_run(con)
    finally:
        con.close()
    if last is None:
        raise SystemExit("no completed extract yet; pass --day")
    return last[0] + timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="mild")
    parser.add_argument("--day", type=date.fromisoformat, help="extract day (YYYY-MM-DD)")
    parser.add_argument("--next", action="store_true", help="the day after the last completed run")
    parser.add_argument("--root", type=Path, default=None, help="repository root (tests only)")
    args = parser.parse_args(argv)
    if bool(args.day) == args.next:
        parser.error("give exactly one of --day or --next")

    paths = Paths(args.scenario) if args.root is None else Paths(args.scenario, args.root)
    day = args.day or next_day(paths.core_db)
    result = run_extract(paths.core_db, paths.landing_dir, day)
    note = " (rerun)" if result.rerun else ""
    print(f"extract {day}{note}: window ({result.watermark_from}, {result.watermark_to}] -> {result.folder}")
    for entity, rows in result.rows.items():
        print(f"  {result.files[entity].name:<22} {rows:>9} lines")
    return 0


if __name__ == "__main__":
    sys.exit(main())
