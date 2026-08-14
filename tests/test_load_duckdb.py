"""The DuckDB loader: RAW rows, the audit table, rejected trailers and idempotent reloads."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd

from common import legacy_format as legacy
from extract import extract_core
from generator.advance_day import advance
from load import common, load_duckdb


def _query(db, sql, params=None) -> pd.DataFrame:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql, params or []).fetchdf()
    finally:
        con.close()


def _land_two_days(paths, scn):
    t0 = scn.initial_extract_date
    day1 = t0 + timedelta(days=1)
    extract_core.run_extract(paths.core_db, paths.landing_dir, t0, duplicate_rate=0.01)
    advance(paths, day1)
    extract_core.run_extract(paths.core_db, paths.landing_dir, day1, duplicate_rate=0.01)
    return t0, day1


def test_discover_orders_files_by_day(fresh_paths, small_scenario):
    t0, day1 = _land_two_days(fresh_paths, small_scenario)
    found = common.discover(fresh_paths.landing_dir)
    assert [f.extract_day for f in found] == [t0] * 5 + [day1] * 5
    assert {f.entity for f in found} == set(legacy.ENTITIES)
    assert [f.extract_day for f in common.discover(fresh_paths.landing_dir, day1)] == [day1] * 5


def test_load_keeps_every_delivered_row_and_rejects_the_trailer(fresh_paths, small_scenario):
    t0, day1 = _land_two_days(fresh_paths, small_scenario)
    records = load_duckdb.load_all(fresh_paths.warehouse_db, fresh_paths.landing_dir, fresh_paths.fx_cache)
    by_file = {r.file_name: r for r in records}
    assert all(r.status == "DONE" for r in records), [(r.file_name, r.status, r.error_message) for r in records]

    txn_t0 = by_file[legacy.file_name("transactions", t0)]
    declared = common.trailer_count(fresh_paths.landing_dir / t0.strftime("%Y%m%d") / txn_t0.file_name)
    assert txn_t0.rows_loaded == declared
    assert txn_t0.rows_rejected == 1, "the trailer line should be the only rejected row"

    raw = _query(fresh_paths.warehouse_db, "SELECT _file_name, count(*) AS n, count(DISTINCT TXN_ID) AS ids FROM raw.transactions GROUP BY 1")
    rows = raw.set_index("_file_name")
    assert rows.loc[txn_t0.file_name, "n"] == declared
    assert rows.loc[txn_t0.file_name, "ids"] < declared, "duplicates must survive into RAW"

    audit = _query(fresh_paths.warehouse_db, "SELECT * FROM raw.load_audit ORDER BY extract_day, file_name")
    assert len(audit) == 11  # five entities for two days, plus the FX cache
    assert (audit["status"] == "DONE").all()
    fx = _query(fresh_paths.warehouse_db, "SELECT count(*) AS n FROM raw.fx_rates")["n"].iloc[0]
    assert fx == by_file["fx_rates_nok.csv"].rows_loaded > 1000

    # Values arrive untouched: decimal commas, DDMMYYYY dates, mixed case.
    sample = _query(fresh_paths.warehouse_db, "SELECT AMT, BOOK_DT, CCY FROM raw.transactions LIMIT 2000")
    assert sample["AMT"].str.contains(",").all()
    assert sample["BOOK_DT"].str.len().eq(8).all()
    assert sample["CCY"].nunique() > sample["CCY"].str.upper().nunique()


def test_reload_is_skipped_unless_forced_and_never_duplicates(fresh_paths, small_scenario):
    _land_two_days(fresh_paths, small_scenario)
    first = load_duckdb.load_all(fresh_paths.warehouse_db, fresh_paths.landing_dir, fresh_paths.fx_cache)
    counts = _query(fresh_paths.warehouse_db, "SELECT count(*) AS n FROM raw.transactions")["n"].iloc[0]

    second = load_duckdb.load_all(fresh_paths.warehouse_db, fresh_paths.landing_dir, fresh_paths.fx_cache)
    assert all(r.status == "SKIPPED" for r in second)
    assert _query(fresh_paths.warehouse_db, "SELECT count(*) AS n FROM raw.transactions")["n"].iloc[0] == counts

    forced = load_duckdb.load_all(fresh_paths.warehouse_db, fresh_paths.landing_dir, fresh_paths.fx_cache, force=True)
    assert all(r.status == "DONE" for r in forced)
    assert _query(fresh_paths.warehouse_db, "SELECT count(*) AS n FROM raw.transactions")["n"].iloc[0] == counts
    assert [r.rows_loaded for r in forced] == [r.rows_loaded for r in first]
    audit = _query(fresh_paths.warehouse_db, "SELECT count(*) AS n FROM raw.load_audit")["n"].iloc[0]
    assert audit == len(first)


def test_changed_file_is_reloaded(fresh_paths, small_scenario):
    t0, day1 = _land_two_days(fresh_paths, small_scenario)
    load_duckdb.load_all(fresh_paths.warehouse_db, fresh_paths.landing_dir, fresh_paths.fx_cache)
    # A rerun of the extract with a different duplicate rate changes the file content.
    extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, day1, duplicate_rate=0.05)
    records = load_duckdb.load_all(fresh_paths.warehouse_db, fresh_paths.landing_dir, fresh_paths.fx_cache, day=day1)
    statuses = {r.file_name: r.status for r in records}
    assert statuses[legacy.file_name("transactions", day1)] == "DONE"
    assert statuses[legacy.file_name("customers", day1)] in ("DONE", "SKIPPED")


def test_raw_ddl_matches_the_snowflake_script():
    script = (Path(__file__).resolve().parents[1] / "snowflake" / "03_raw_objects.sql").read_text(encoding="utf-8").upper()
    for entity in legacy.ENTITIES:
        for col in legacy.LAYOUTS[entity]:
            assert f"{col.legacy} VARCHAR" in script, f"{entity}.{col.legacy} missing from 03_raw_objects.sql"
    for col in common.LOAD_COLUMNS:
        assert col.upper() in script
