"""The watermark extract: windows, reruns, failure handling and the legacy files it writes."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import duckdb
import pandas as pd
import pytest

from common import legacy_format as legacy
from extract import extract_core
from generator import core_db
from generator.advance_day import advance


def _query(db: Path, sql: str, params: list | None = None) -> pd.DataFrame:
    con = duckdb.connect(str(db), read_only=True)
    try:
        return con.execute(sql, params or []).fetchdf()
    finally:
        con.close()


def _file_bytes(result: extract_core.ExtractResult) -> dict[str, bytes]:
    return {entity: path.read_bytes() for entity, path in result.files.items()}


def test_initial_extract_covers_the_whole_core_system(fresh_paths, small_scenario):
    t0 = small_scenario.initial_extract_date
    result = extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, t0, duplicate_rate=0.0)
    assert not result.rerun
    assert result.watermark_from == extract_core.EPOCH
    assert result.watermark_to == extract_core.end_of_day(t0)
    for entity in legacy.ENTITIES:
        core_rows = _query(fresh_paths.core_db, f"SELECT count(*) AS n FROM core.{entity}")["n"].iloc[0]
        assert result.rows[entity] == core_rows
        frame = legacy.read_legacy_file(result.files[entity])
        assert len(frame) == core_rows
        assert list(frame.columns) == [c.legacy for c in legacy.LAYOUTS[entity]]
    runs = _query(fresh_paths.core_db, f"SELECT * FROM {extract_core.CONTROL_TABLE}")
    assert len(runs) == 1 and runs["status"].iloc[0] == "DONE"


def test_incremental_extract_takes_only_the_days_changes(fresh_paths, small_scenario):
    t0 = small_scenario.initial_extract_date
    day1 = t0 + timedelta(days=1)
    extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, t0, duplicate_rate=0.0)
    advance(fresh_paths, day1)
    result = extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, day1, duplicate_rate=0.0)
    assert result.watermark_from == extract_core.end_of_day(t0)
    expected = _query(
        fresh_paths.core_db,
        "SELECT txn_id FROM core.transactions WHERE updated_at > ? AND updated_at <= ? ORDER BY updated_at, txn_id",
        [result.watermark_from, result.watermark_to],
    )
    frame = legacy.read_legacy_file(result.files["transactions"])
    assert frame["TXN_ID"].astype(int).tolist() == expected["txn_id"].tolist()
    assert len(frame) > 0
    # Late rows carry an old booking date and a new update timestamp.
    booked = frame["BOOK_DT"].map(legacy.parse_date)
    assert (booked < day1).any()
    assert all(legacy.parse_timestamp(ts).date() == day1 for ts in frame["UPD_TS"])


def test_rerun_of_a_day_gives_identical_files(fresh_paths, small_scenario):
    t0 = small_scenario.initial_extract_date
    day1 = t0 + timedelta(days=1)
    extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, t0)
    advance(fresh_paths, day1)
    first = extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, day1)
    first_bytes = _file_bytes(first)
    again = extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, day1)
    assert again.rerun
    assert (again.watermark_from, again.watermark_to) == (first.watermark_from, first.watermark_to)
    assert _file_bytes(again) == first_bytes
    runs = _query(fresh_paths.core_db, f"SELECT run_day, status FROM {extract_core.CONTROL_TABLE} ORDER BY run_day")
    assert runs["run_day"].dt.date.tolist() == [t0, day1]
    assert (runs["status"] == "DONE").all()


def test_watermark_moves_only_after_every_file_is_written(fresh_paths, small_scenario):
    t0 = small_scenario.initial_extract_date
    day1 = t0 + timedelta(days=1)
    day2 = t0 + timedelta(days=2)
    extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, t0)
    advance(fresh_paths, day1)

    written: list[str] = []

    def failing_writer(path: Path, frame: pd.DataFrame) -> int:
        if len(written) == 2:
            raise OSError("disk full")
        written.append(path.name)
        return legacy.write_legacy_file(path, frame)

    with pytest.raises(OSError):
        extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, day1, writer=failing_writer)

    runs = _query(fresh_paths.core_db, f"SELECT run_day, status FROM {extract_core.CONTROL_TABLE} ORDER BY run_day")
    assert dict(zip(runs["run_day"].dt.date, runs["status"])) == {t0: "DONE", day1: "RUNNING"}
    # The next day refuses to run while the failed one is open, and --next still points at the failed day.
    advance(fresh_paths, day2)
    with pytest.raises(RuntimeError):
        extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, day2)
    assert extract_core.next_day(fresh_paths.core_db) == day1
    # Rerunning the failed day completes it with the window it was given the first time.
    result = extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, day1)
    assert result.watermark_from == extract_core.end_of_day(t0)
    assert result.watermark_to == extract_core.end_of_day(day1)
    assert all(path.exists() for path in result.files.values())
    assert extract_core.next_day(fresh_paths.core_db) == day2


def test_days_cannot_go_backwards(fresh_paths, small_scenario):
    t0 = small_scenario.initial_extract_date
    extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, t0)
    with pytest.raises(RuntimeError):
        extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, t0 - timedelta(days=1))


def test_legacy_quirks_are_present_and_deterministic(fresh_paths, small_scenario):
    t0 = small_scenario.initial_extract_date
    result = extract_core.run_extract(fresh_paths.core_db, fresh_paths.landing_dir, t0, duplicate_rate=0.01)
    raw = result.files["transactions"].read_bytes().decode(legacy.ENCODING)
    frame = legacy.read_legacy_file(result.files["transactions"])
    core_rows = _query(fresh_paths.core_db, "SELECT count(*) AS n FROM core.transactions")["n"].iloc[0]
    assert len(frame) > core_rows, "duplicates were not written"
    assert frame["TXN_ID"].nunique() == core_rows
    assert len({v for v in frame["CCY"]}) > len({v.upper() for v in frame["CCY"]}), "no case noise"
    assert "\r\n" in raw and raw.endswith(f"{legacy.TRAILER_PREFIX};{len(frame)}\r\n")
    assert all("," in v for v in frame["AMT"]) and not any("." in v for v in frame["AMT"])
    customers = legacy.read_legacy_file(result.files["customers"])
    assert customers["LAST_NM"].str.contains("ø|æ|å", case=False).any(), "Latin-1 characters did not survive"
