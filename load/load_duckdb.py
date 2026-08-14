"""Load landed legacy files and the FX cache into the RAW schema of the DuckDB warehouse.

This is the local counterpart of ``load_snowflake.py``: the same files, the
same RAW layout and the same audit table, read straight from the landing
folder instead of an internal stage. A file that was already loaded with the
same content is skipped; ``--force`` reloads it, replacing its rows.

Usage:
    python -m load.load_duckdb [--scenario mild] [--day 2024-12-01] [--force]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

import duckdb

from common import legacy_format as legacy
from common.paths import Paths
from load import common


def run(con: duckdb.DuckDBPyConnection, sql: str, params: list | None = None) -> list[tuple]:
    return con.execute(sql, params or []).fetchall()


def ensure_raw_schema(con: duckdb.DuckDBPyConnection) -> None:
    run(con, f"CREATE SCHEMA IF NOT EXISTS {common.RAW_SCHEMA}")
    for entity in legacy.ENTITIES:
        run(con, common.raw_table_ddl(entity))
    run(con, common.fx_table_ddl())
    run(con, common.audit_table_ddl())


def already_loaded(con: duckdb.DuckDBPyConnection, file_name: str, digest: str) -> bool:
    rows = run(
        con,
        f"SELECT 1 FROM {common.RAW_SCHEMA}.{common.AUDIT_TABLE} WHERE file_name = ? AND file_hash = ? AND status = 'DONE'",
        [file_name, digest],
    )
    return bool(rows)


def last_reject_scan(con: duckdb.DuckDBPyConnection) -> int:
    """Highest scan id in DuckDB's reject tables, or -1 before the first rejecting read."""
    try:
        return run(con, "SELECT coalesce(max(scan_id), -1) FROM reject_scans")[0][0]
    except duckdb.Error:
        return -1


def record(con: duckdb.DuckDBPyConnection, rec: common.LoadRecord) -> None:
    run(con, f"DELETE FROM {common.RAW_SCHEMA}.{common.AUDIT_TABLE} WHERE file_name = ?", [rec.file_name])
    run(
        con,
        f"INSERT INTO {common.RAW_SCHEMA}.{common.AUDIT_TABLE} VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rec.as_row(),
    )


def load_file(con: duckdb.DuckDBPyConnection, landed: common.LandedFile, force: bool = False) -> common.LoadRecord:
    digest = common.file_hash(landed.path)
    now = datetime.now().replace(microsecond=0)
    declared = common.trailer_count(landed.path)
    if not force and already_loaded(con, landed.name, digest):
        rows = run(con, f"SELECT rows_loaded, rows_rejected FROM {common.RAW_SCHEMA}.{common.AUDIT_TABLE} WHERE file_name = ?", [landed.name])[0]
        return common.LoadRecord(landed.name, landed.entity, landed.extract_day, digest, declared, rows[0], rows[1], "SKIPPED", None, now)

    table = f"{common.RAW_SCHEMA}.{common.raw_table(landed.entity)}"
    columns = ", ".join(c.legacy for c in legacy.LAYOUTS[landed.entity])
    try:
        previous_scan = last_reject_scan(con)
        run(con, "BEGIN")
        run(con, f"DELETE FROM {table} WHERE _file_name = ?", [landed.name])
        # The trailer line has two fields instead of a full record; it is rejected and counted.
        run(
            con,
            f"""
            INSERT INTO {table} ({columns}, _file_name, _extract_day, _loaded_at)
            SELECT {columns}, ?, ?, ?
            FROM read_csv(?, delim = ';', header = true, all_varchar = true, encoding = 'latin-1',
                          ignore_errors = true, store_rejects = true, nullstr = '', quote = '"')
            """,
            [landed.name, landed.extract_day, now, landed.path.as_posix()],
        )
        loaded = run(con, f"SELECT count(*) FROM {table} WHERE _file_name = ?", [landed.name])[0][0]
        rejected = run(
            con,
            "SELECT count(*) FROM (SELECT DISTINCT scan_id, line FROM reject_errors WHERE scan_id > ?)",
            [previous_scan],
        )[0][0]
        first_error = run(con, "SELECT error_message FROM reject_errors WHERE scan_id > ? LIMIT 1", [previous_scan])
        run(con, "COMMIT")
    except duckdb.Error as exc:
        run(con, "ROLLBACK")
        rec = common.LoadRecord(landed.name, landed.entity, landed.extract_day, digest, declared, 0, 0, "FAILED", str(exc), now)
        record(con, rec)
        return rec

    status = "DONE" if declared is None or loaded == declared else "COUNT_MISMATCH"
    message = first_error[0][0] if first_error else None
    rec = common.LoadRecord(landed.name, landed.entity, landed.extract_day, digest, declared, loaded, rejected, status, message, now)
    record(con, rec)
    return rec


def load_fx(con: duckdb.DuckDBPyConnection, cache: Path, force: bool = False) -> common.LoadRecord:
    digest = common.file_hash(cache)
    now = datetime.now().replace(microsecond=0)
    if not force and already_loaded(con, cache.name, digest):
        rows = run(con, f"SELECT rows_loaded FROM {common.RAW_SCHEMA}.{common.AUDIT_TABLE} WHERE file_name = ?", [cache.name])[0]
        return common.LoadRecord(cache.name, "fx_rates", date.today(), digest, None, rows[0], 0, "SKIPPED", None, now)
    table = f"{common.RAW_SCHEMA}.{common.FX_TABLE}"
    run(con, f"DELETE FROM {table} WHERE _file_name = ?", [cache.name])
    run(
        con,
        f"""
        INSERT INTO {table}
        SELECT rate_date, currency, rate_nok, ?, ?, ?
        FROM read_csv(?, delim = ',', header = true, all_varchar = true)
        """,
        [cache.name, date.today(), now, cache.as_posix()],
    )
    loaded = run(con, f"SELECT count(*) FROM {table} WHERE _file_name = ?", [cache.name])[0][0]
    rec = common.LoadRecord(cache.name, "fx_rates", date.today(), digest, None, loaded, 0, "DONE", None, now)
    record(con, rec)
    return rec


def load_all(
    warehouse_db: Path, landing_dir: Path, fx_cache: Path, day: date | None = None, force: bool = False, include_fx: bool = True
) -> list[common.LoadRecord]:
    warehouse_db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(warehouse_db))
    try:
        ensure_raw_schema(con)
        records = [load_fx(con, fx_cache, force)] if include_fx and fx_cache.exists() else []
        for landed in common.discover(landing_dir, day):
            records.append(load_file(con, landed, force))
        return records
    finally:
        con.close()


def print_records(records: list[common.LoadRecord]) -> None:
    print(f"{'file':<24} {'day':<10} {'loaded':>9} {'rejected':>8}  status")
    for rec in records:
        print(f"{rec.file_name:<24} {rec.extract_day!s:<10} {rec.rows_loaded:>9} {rec.rows_rejected:>8}  {rec.status}"
              + (f"  {rec.error_message}" if rec.status == "FAILED" else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="mild")
    parser.add_argument("--day", type=date.fromisoformat, default=None, help="load only this extract day")
    parser.add_argument("--force", action="store_true", help="reload files that were already loaded")
    parser.add_argument("--warehouse", type=Path, default=None, help="DuckDB file to load into (default from scenario)")
    parser.add_argument("--root", type=Path, default=None, help="repository root (tests only)")
    args = parser.parse_args(argv)

    paths = Paths(args.scenario) if args.root is None else Paths(args.scenario, args.root)
    warehouse = args.warehouse or paths.warehouse_db
    records = load_all(warehouse, paths.landing_dir, paths.fx_cache, args.day, args.force)
    print(f"RAW in {warehouse}")
    print_records(records)
    return 1 if any(r.status == "FAILED" for r in records) else 0


if __name__ == "__main__":
    sys.exit(main())
