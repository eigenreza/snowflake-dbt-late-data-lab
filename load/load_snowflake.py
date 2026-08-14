"""Load landed legacy files and the FX cache into Snowflake RAW with PUT and COPY INTO.

Runs as the LOADER role on LOAD_WH with key-pair authentication. Each day's
files are uploaded to a folder of the internal stage and copied into the RAW
tables with ON_ERROR = CONTINUE, so the trailer line of every legacy file is
rejected and counted rather than failing the load. A file that Snowflake has
already loaded is skipped; ``--force`` deletes its rows and loads it again;
``--reset`` empties RAW and the stages first, which is how a second scenario
replaces the first (its files carry the same names).

Environment:
    SNOWFLAKE_ACCOUNT, SNOWFLAKE_USER, SNOWFLAKE_PRIVATE_KEY_PATH,
    SNOWFLAKE_PRIVATE_KEY_PASSPHRASE (optional), SNOWFLAKE_DATABASE (default LATE_DATA_LAB)

Usage:
    python -m load.load_snowflake [--scenario mild] [--day 2024-12-01] [--force]
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime
from pathlib import Path

from dotenv import load_dotenv

from common import legacy_format as legacy
from common.paths import Paths
from load import common

LEGACY_STAGE = "RAW.LEGACY_STAGE"
FX_STAGE = "RAW.FX_STAGE"
LEGACY_FORMAT = "RAW.LEGACY_CSV"
FX_FORMAT = "RAW.FX_CSV"


def private_key_bytes(path: Path, passphrase: str | None) -> bytes:
    from cryptography.hazmat.primitives import serialization

    key = serialization.load_pem_private_key(path.read_bytes(), password=passphrase.encode() if passphrase else None)
    return key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def connect(role: str = "LOADER", warehouse: str = "LOAD_WH", query_tag: str = "late_data_lab:load"):
    """Open a key-pair authenticated connection from environment variables (and .env if present)."""
    import snowflake.connector

    load_dotenv()
    missing = [v for v in ("SNOWFLAKE_ACCOUNT", "SNOWFLAKE_USER", "SNOWFLAKE_PRIVATE_KEY_PATH") if not os.getenv(v)]
    if missing:
        raise SystemExit(f"missing environment variables: {', '.join(missing)}")
    database = os.getenv("SNOWFLAKE_DATABASE", "LATE_DATA_LAB")
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        private_key=private_key_bytes(Path(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]), os.getenv("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE")),
        role=role,
        warehouse=warehouse,
        database=database,
        schema="RAW",
        session_parameters={"QUERY_TAG": query_tag},
    )


def already_loaded(cur, file_name: str, digest: str) -> bool:
    cur.execute(
        f"SELECT 1 FROM RAW.{common.AUDIT_TABLE} WHERE file_name = %s AND file_hash = %s AND status = 'DONE'",
        (file_name, digest),
    )
    return cur.fetchone() is not None


def record(cur, rec: common.LoadRecord) -> None:
    cur.execute(f"DELETE FROM RAW.{common.AUDIT_TABLE} WHERE file_name = %s", (rec.file_name,))
    cur.execute(
        f"INSERT INTO RAW.{common.AUDIT_TABLE} VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        tuple(rec.as_row()),
    )


def put(cur, path: Path, stage_folder: str) -> None:
    """Upload a file to the stage unless the same file name is already there (the landed files never change)."""
    cur.execute(f"LIST @{stage_folder}/{path.name}.gz")
    if cur.fetchall():
        return
    cur.execute(f"PUT 'file://{path.as_posix()}' @{stage_folder} AUTO_COMPRESS = TRUE OVERWRITE = TRUE")
    cur.fetchall()


def reset_raw(cur) -> None:
    """Empty every RAW table, the load audit and both stages, so a replay can start from day one.

    TRUNCATE also clears Snowflake's load history for the tables, so the next
    COPY INTO of a file that was loaded before is not skipped. The stages are
    emptied because ``put`` trusts file names: two replays of different
    scenarios land files of the same names with different contents, and a
    reset must not let the next load copy the old ones.
    """
    for entity in legacy.LAYOUTS:
        cur.execute(f"TRUNCATE TABLE IF EXISTS RAW.{common.raw_table(entity).upper()}")
    cur.execute(f"TRUNCATE TABLE IF EXISTS RAW.{common.FX_TABLE.upper()}")
    cur.execute(f"TRUNCATE TABLE IF EXISTS RAW.{common.AUDIT_TABLE.upper()}")
    for stage in (LEGACY_STAGE, FX_STAGE):
        cur.execute(f"REMOVE @{stage}")
        cur.fetchall()


def copy_result(cur) -> dict:
    columns = [d[0].lower() for d in cur.description]
    row = cur.fetchone()
    return dict(zip(columns, row)) if row else {}


def load_file(cur, landed: common.LandedFile, force: bool = False) -> common.LoadRecord:
    digest = common.file_hash(landed.path)
    now = datetime.now().replace(microsecond=0)
    declared = common.trailer_count(landed.path)
    if not force and already_loaded(cur, landed.name, digest):
        cur.execute(f"SELECT rows_loaded, rows_rejected FROM RAW.{common.AUDIT_TABLE} WHERE file_name = %s", (landed.name,))
        rows = cur.fetchone()
        return common.LoadRecord(landed.name, landed.entity, landed.extract_day, digest, declared, rows[0], rows[1], "SKIPPED", None, now)

    table = f"RAW.{common.raw_table(landed.entity).upper()}"
    folder = f"{LEGACY_STAGE}/{landed.extract_day:%Y%m%d}"
    columns = [c.legacy for c in legacy.LAYOUTS[landed.entity]]
    positions = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
    if force:
        cur.execute(f"DELETE FROM {table} WHERE _file_name = %s", (landed.name,))
    put(cur, landed.path, folder)
    cur.execute(
        f"""
        COPY INTO {table} ({", ".join(columns)}, _file_name, _extract_day, _loaded_at)
        FROM (
            SELECT {positions}, %s, TO_DATE(%s, 'YYYY-MM-DD'), CURRENT_TIMESTAMP()
            FROM @{folder}/{landed.name}.gz
        )
        FILE_FORMAT = (FORMAT_NAME = {LEGACY_FORMAT})
        ON_ERROR = CONTINUE
        FORCE = {"TRUE" if force else "FALSE"}
        """,
        (landed.name, landed.extract_day.isoformat()),
    )
    result = copy_result(cur)
    status = str(result.get("status", "")).upper()
    loaded = int(result.get("rows_loaded") or 0)
    rejected = int(result.get("errors_seen") or 0)
    message = result.get("first_error")
    if status in ("LOADED", "PARTIALLY_LOADED"):
        # COPY with a transformation does not enforce the column count, so the trailer line
        # arrives as a row with the marker in the first column: remove it and count it as rejected.
        cur.execute(f"DELETE FROM {table} WHERE _file_name = %s AND {columns[0]} = %s", (landed.name, legacy.TRAILER_PREFIX))
        removed = int(cur.rowcount or 0)
        loaded -= removed
        rejected += removed
    if status == "LOAD_SKIPPED":
        rec = common.LoadRecord(landed.name, landed.entity, landed.extract_day, digest, declared, 0, 0, "SKIPPED", "already in Snowflake load history", now)
    elif status in ("LOADED", "PARTIALLY_LOADED"):
        final = "DONE" if declared is None or loaded == declared else "COUNT_MISMATCH"
        rec = common.LoadRecord(landed.name, landed.entity, landed.extract_day, digest, declared, loaded, rejected, final, message, now)
    else:
        rec = common.LoadRecord(landed.name, landed.entity, landed.extract_day, digest, declared, loaded, rejected, "FAILED", message or status, now)
    record(cur, rec)
    return rec


def load_fx(cur, cache: Path) -> common.LoadRecord:
    digest = common.file_hash(cache)
    now = datetime.now().replace(microsecond=0)
    cur.execute(f"DELETE FROM RAW.{common.FX_TABLE.upper()} WHERE _file_name = %s", (cache.name,))
    put(cur, cache, FX_STAGE)
    cur.execute(
        f"""
        COPY INTO RAW.{common.FX_TABLE.upper()} (rate_date, currency, rate_nok, _file_name, _extract_day, _loaded_at)
        FROM (SELECT $1, $2, $3, %s, CURRENT_DATE(), CURRENT_TIMESTAMP() FROM @{FX_STAGE}/{cache.name}.gz)
        FILE_FORMAT = (FORMAT_NAME = {FX_FORMAT})
        ON_ERROR = ABORT_STATEMENT
        FORCE = TRUE
        """,
        (cache.name,),
    )
    result = copy_result(cur)
    loaded = int(result.get("rows_loaded") or 0)
    rec = common.LoadRecord(cache.name, "fx_rates", date.today(), digest, None, loaded, 0, "DONE", None, now)
    record(cur, rec)
    return rec


def load_all(
    landing_dir: Path, fx_cache: Path, day: date | None = None, force: bool = False, include_fx: bool | None = None,
    reset: bool = False,
) -> list[common.LoadRecord]:
    if include_fx is None:
        include_fx = day is None
    con = connect()
    try:
        cur = con.cursor()
        if reset:
            reset_raw(cur)
        records = [load_fx(cur, fx_cache)] if include_fx and fx_cache.exists() else []
        for landed in common.discover(landing_dir, day):
            records.append(load_file(cur, landed, force))
        con.commit()
        return records
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    from load.load_duckdb import print_records

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="mild")
    parser.add_argument("--day", type=date.fromisoformat, default=None, help="load only this extract day")
    parser.add_argument("--force", action="store_true", help="reload files that were already loaded")
    parser.add_argument("--reset", action="store_true", help="empty RAW and the stages first, then load")
    args = parser.parse_args(argv)

    paths = Paths(args.scenario)
    records = load_all(paths.landing_dir, paths.fx_cache, args.day, args.force, reset=args.reset)
    print(f"RAW in Snowflake ({os.getenv('SNOWFLAKE_DATABASE', 'LATE_DATA_LAB')})")
    print_records(records)
    return 1 if any(r.status == "FAILED" for r in records) else 0


if __name__ == "__main__":
    sys.exit(main())
