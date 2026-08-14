"""The rewritten report against the old job: the nightly window loses late rows; a full rebuild matches."""

from __future__ import annotations

import shutil

import pytest

from common.dbt_runner import run_dbt
from common.paths import Paths
from extract import extract_core
from generator.advance_day import advance
from legacy import run_nightly_report as nightly
from load import load_duckdb
from reconcile import reconcile as rec


@pytest.fixture(scope="module")
def replayed(built_root, tmp_path_factory, small_scenario):
    """A private bank replayed through every day, with the old job run nightly and the warehouse built."""
    src = Paths("mild", built_root)
    root = tmp_path_factory.mktemp("replay")
    paths = Paths("mild", root)
    paths.core_db.parent.mkdir(parents=True, exist_ok=True)
    paths.truth_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src.core_db, paths.core_db)
    shutil.copy(src.truth_db, paths.truth_db)

    t0 = small_scenario.initial_extract_date
    extract_core.run_extract(paths.core_db, paths.landing_dir, t0)
    nightly.run_nightly_report(paths.core_db, t0, full=True)
    for day in small_scenario.replay_dates:
        advance(paths, day)
        extract_core.run_extract(paths.core_db, paths.landing_dir, day)
        nightly.run_nightly_report(paths.core_db, day)
    load_duckdb.load_all(paths.warehouse_db, paths.landing_dir, paths.fx_cache)
    run_dbt(["build", "--full-refresh"], duckdb_path=paths.warehouse_db, vars={"as_of_date": small_scenario.last_day.isoformat()})
    return paths


def test_nightly_window_misses_late_rows_for_earlier_months(replayed, small_scenario):
    """Runs first: the fixture leaves the report table as the nightly job maintained it."""
    result = rec.reconcile(replayed, "dev", "nightly", small_scenario.last_day)
    assert not result.matches
    cutoff = nightly.month_start(small_scenario.last_day)
    assert result.differing_months, "expected differences in earlier months"
    assert all(m < cutoff.isoformat() for m in result.differing_months)
    # The current month was rebuilt every night, so it agrees exactly.
    assert [k for k in result.differing_keys if k["report_month"] >= cutoff.isoformat()] == []
    # Late rows reach the mart but never the old report, so the old count is the smaller one.
    assert result.count_sum_old < result.count_sum_new


def test_full_rebuild_of_the_old_job_matches_the_rewrite(replayed, small_scenario):
    result = rec.reconcile(replayed, "dev", "full", small_scenario.last_day)
    assert result.matches, result.summary()
    assert result.old_rows == result.new_rows > 0
    assert result.keys_only_in_old == result.keys_only_in_new == 0
    assert result.value_sum_old == pytest.approx(result.value_sum_new, abs=0.01)
    assert result.count_sum_old == result.count_sum_new


def test_result_is_written_as_json(replayed, small_scenario, tmp_path):
    result = rec.reconcile(replayed, "dev", "full", small_scenario.last_day)
    path = rec.write_result(result, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert '"matches": true' in text and '"mode": "full"' in text
