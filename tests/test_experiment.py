"""The experiment harness: scoring arithmetic, the lateness floor and a tiny end-to-end grid."""

from __future__ import annotations

import shutil

import duckdb
import pytest

from common.paths import Paths
from experiment import kpis, replay
from experiment.config import KPIS, run_key
from experiment.run import run_experiment, summarize
from experiment.strategy_run import run_strategy


def test_score_counts_missing_extra_and_different_cells():
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE t AS SELECT * FROM (VALUES
            (DATE '2024-11-01', '5411', 'Oslo', 100.00, 3),
            (DATE '2024-11-01', '5812', 'Oslo', 50.00, 2),
            (DATE '2024-11-01', '5411', 'Troms', 10.00, 1),
            (DATE '2024-12-01', '5411', 'Oslo', 200.00, 4)
        ) v(report_month, category_code, region, value_nok, txn_count)
        """
    ).fetchall()
    con.execute(
        """
        CREATE TABLE w AS SELECT * FROM (VALUES
            (DATE '2024-11-01', '5411', 'Oslo', 100.00, 3),
            (DATE '2024-11-01', '5812', 'Oslo', 40.00, 2),
            (DATE '2024-11-01', '5411', 'Troms', 10.00, 2),
            (DATE '2024-12-01', '5651', 'Oslo', 5.00, 1)
        ) v(report_month, category_code, region, value_nok, txn_count)
        """
    ).fetchall()
    result = kpis.score(con, "monthly_category_region", "SELECT * FROM t", "SELECT * FROM w")
    assert result["cells"] == 5
    assert result["wrong_cells"] == 4          # value off, count off, missing, extra
    assert result["missing_in_warehouse"] == 1
    assert result["extra_in_warehouse"] == 1
    assert result["mae_nok"] == pytest.approx((0 + 10 + 0 + 200 + 5) / 5)
    assert result["max_abs_error_nok"] == pytest.approx(200.0)
    assert result["wrong_share"] == pytest.approx(0.8)


def test_identical_kpis_score_zero():
    con = duckdb.connect()
    con.execute("CREATE TABLE t AS SELECT DATE '2024-12-01' AS event_date, 'EUR' AS currency, 12.5 AS amount_nok, 1 AS txn_count").fetchall()
    result = kpis.score(con, "fx_daily", "SELECT * FROM t", "SELECT * FROM t")
    assert result["wrong_cells"] == 0 and result["mae_nok"] == 0.0 and result["cells"] == 1


@pytest.fixture(scope="module")
def replayed_small(built_root, tmp_path_factory, small_scenario):
    scn = small_scenario.with_size(replay_days=3)
    root = tmp_path_factory.mktemp("experiment")
    paths = Paths("mild", root)
    replay.replay_scenario(scn, paths, log=lambda m: None)
    return scn, paths


def test_replay_lands_every_day_and_is_detected_as_ready(replayed_small):
    scn, paths = replayed_small
    assert replay.replay_is_ready(scn, paths)
    stats = replay.cycle_stats(scn, paths)
    assert stats["cycle"].tolist() == [1, 2, 3]
    assert (stats["late_rows"] > 0).all()
    assert (stats["late_share"] < 1).all()


def test_lateness_floor_grows_as_rows_are_still_to_come(replayed_small):
    scn, paths = replayed_small
    floor = replay.lateness_floor(scn, paths)
    assert set(floor["kpi"]) == set(KPIS)
    balance = floor[floor["kpi"] == "daily_balance"].sort_values("cycle")
    assert (balance["wrong_share"] > 0).all(), "rows arrive after every cycle, so the as-of truth is never final"


def test_full_refresh_is_exact_and_append_only_drifts(replayed_small):
    scn, paths = replayed_small
    exact = run_strategy(scn, paths, "full_refresh", 0, "dev", log=lambda m: None)
    assert all(e["wrong_cells"] == 0 for e in exact.errors), [e for e in exact.errors if e["wrong_cells"]]
    assert {c["node"] for c in exact.costs} >= {"fct_transactions", "fct_account_balance_daily", "mart_monthly_category_region", "cycle_total"}

    naive = run_strategy(scn, paths, "append_only", 0, "dev", log=lambda m: None)
    initial = [e for e in naive.errors if e["cycle"] == 0]
    assert all(e["wrong_cells"] == 0 for e in initial), "the initial full build is the same for every strategy"
    later = [e for e in naive.errors if e["cycle"] > 0 and e["kpi"] == "daily_balance"]
    assert any(e["wrong_cells"] > 0 for e in later)
    totals = [c for c in naive.costs if c["node"] == "cycle_total" and c["cycle"] > 0]
    assert all(c["rows_in_window"] > 0 for c in totals)


def test_run_experiment_writes_results_and_resumes(replayed_small, tmp_path):
    scn, paths = replayed_small
    out = tmp_path / "results"
    grid = [("full_refresh", 0), ("merge", 1)]
    summary = run_experiment("dev", [scn], grid, out, cycles=2, root=paths.root, log=lambda m: None)
    assert sorted(summary["run_key"]) == ["full_refresh", "merge_w1"]
    assert (out / "kpi_errors.parquet").exists() and (out / "run_costs.parquet").exists()
    assert (out / "reconciliation_mild_full.json").exists()
    full = summary[summary["run_key"] == "full_refresh"].iloc[0]
    assert full["mean_wrong_share"] == 0.0
    again = run_experiment("dev", [scn], grid, out, cycles=2, resume=True, root=paths.root, log=lambda m: None)
    assert len(again) == 2


def test_run_key_and_summary_shapes():
    assert run_key("merge", 3) == "merge_w3" and run_key("full_refresh", 0) == "full_refresh"
    import pandas as pd

    assert summarize(pd.DataFrame(), pd.DataFrame()).empty
