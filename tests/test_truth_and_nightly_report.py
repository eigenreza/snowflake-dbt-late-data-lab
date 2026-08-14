"""Ground-truth KPIs, and the old nightly report compared with them."""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd
import pytest

from generator import core_db, truth
from generator.advance_day import advance
from legacy import run_nightly_report as nightly


def _read_report(core_path) -> pd.DataFrame:
    con = duckdb.connect(str(core_path), read_only=True)
    try:
        return con.execute(
            "SELECT report_month, category_code, region, value_nok, txn_count FROM report.monthly_category_region ORDER BY 1, 2, 3"
        ).fetchdf()
    finally:
        con.close()


def test_daily_balance_is_opening_balance_plus_net_flow(built_paths, bank_data, small_scenario):
    entities, txn, _ = bank_data
    account = entities.accounts_initial.iloc[0]
    con = truth.connect(built_paths.truth_db, read_only=True)
    try:
        balances = truth.kpi_daily_balance(con, truth.FINAL, small_scenario.period_start, small_scenario.last_day)
    finally:
        con.close()
    balances["balance_date"] = pd.to_datetime(balances["balance_date"]).dt.date
    mine = balances[balances["account_no"] == account["account_no"]].set_index("balance_date")["balance_nok"]
    effective = txn.sort_values("version_no").groupby("txn_id").tail(1)
    flows = effective[effective["account_no"] == account["account_no"]]
    expected_last = round(float(account["opening_balance"]) + float(flows["amount_nok"].sum()), 2)
    assert float(mine.iloc[-1]) == pytest.approx(expected_last, abs=0.01)
    day = flows["event_date"].max()
    net_that_day = float(flows.loc[flows["event_date"] == day, "amount_nok"].sum())
    assert float(mine[day]) - float(mine[day - timedelta(days=1)]) == pytest.approx(net_that_day, abs=0.01)


def test_event_date_region_equals_current_region_at_the_initial_extract(built_paths, small_scenario):
    """Before any later customer version arrives, both region rules give the same monthly cells."""
    t0 = core_db.end_of_day(small_scenario.initial_extract_date)
    con = truth.connect(built_paths.truth_db, read_only=True)
    try:
        by_event = con.execute(
            truth.monthly_by_event_region_sql(t0, small_scenario.period_start, small_scenario.initial_extract_date) + " ORDER BY 1, 2, 3"
        ).fetchdf()
        current = truth.kpi_monthly_category_region(con, t0, small_scenario.period_start, small_scenario.initial_extract_date.replace(day=1))
        spans = con.execute(truth.knowable_customer_spans_sql(t0)).fetchdf()
    finally:
        con.close()
    pd.testing.assert_frame_equal(by_event.reset_index(drop=True), current.reset_index(drop=True), check_dtype=False)
    # One span per customer at the initial extract, valid from the beginning of time.
    assert spans["customer_id"].is_unique
    assert (pd.to_datetime(spans["effective_from"]).dt.year == 1900).all()


def test_truth_as_of_a_cycle_ignores_later_arrivals(built_paths, small_scenario):
    t0 = core_db.end_of_day(small_scenario.initial_extract_date)
    con = truth.connect(built_paths.truth_db, read_only=True)
    try:
        at_t0 = truth.kpi_fx_daily(con, t0, small_scenario.period_start, small_scenario.last_day)
        final = truth.kpi_fx_daily(con, truth.FINAL, small_scenario.period_start, small_scenario.last_day)
    finally:
        con.close()
    assert at_t0["txn_count"].sum() < final["txn_count"].sum()
    assert set(at_t0["currency"]) <= {"EUR", "USD", "SEK"}


def test_full_nightly_report_matches_the_ground_truth(fresh_paths, small_scenario):
    """At the initial extract date the core system and the truth agree, so the old job must too."""
    t0 = small_scenario.initial_extract_date
    nightly.run_nightly_report(fresh_paths.core_db, t0, full=True)
    old = _read_report(fresh_paths.core_db)
    con = truth.connect(fresh_paths.truth_db, read_only=True)
    try:
        expected = truth.kpi_monthly_category_region(
            con, core_db.end_of_day(t0), small_scenario.period_start, t0.replace(day=1)
        )
    finally:
        con.close()
    old["report_month"] = pd.to_datetime(old["report_month"]).dt.date
    expected["report_month"] = pd.to_datetime(expected["report_month"]).dt.date
    merged = expected.merge(old, on=["report_month", "category_code", "region"], how="outer", suffixes=("_truth", "_old"), indicator=True)
    assert (merged["_merge"] == "both").all()
    assert np.allclose(merged["value_nok_truth"].astype(float), merged["value_nok_old"].astype(float), atol=0.01)
    assert (merged["txn_count_truth"] == merged["txn_count_old"]).all()


def test_nightly_window_leaves_older_months_untouched(fresh_paths, small_scenario):
    t0 = small_scenario.initial_extract_date
    nightly.run_nightly_report(fresh_paths.core_db, t0, full=True)
    before = _read_report(fresh_paths.core_db)
    for day in small_scenario.replay_dates:
        advance(fresh_paths, day)
        nightly.run_nightly_report(fresh_paths.core_db, day)
    after = _read_report(fresh_paths.core_db)
    cutoff = nightly.month_start(small_scenario.last_day)
    old_months_before = before[before["report_month"] < pd.Timestamp(cutoff)]
    old_months_after = after[after["report_month"] < pd.Timestamp(cutoff)]
    pd.testing.assert_frame_equal(old_months_before.reset_index(drop=True), old_months_after.reset_index(drop=True))
    assert after["txn_count"].sum() > before["txn_count"].sum()


def test_month_start():
    assert nightly.month_start(date(2024, 12, 15)) == date(2024, 12, 1)
    assert nightly.month_start(date(2024, 1, 1)) == date(2024, 1, 1)


def test_script_splits_into_statements():
    stmts = nightly.statements(nightly.render(date(2024, 12, 1), date(2024, 11, 1)))
    assert stmts[0].upper().startswith("CREATE SCHEMA")
    assert any(s.upper().startswith("DELETE FROM") for s in stmts)
    assert all("&RUN_DATE" not in s and "&FROM_MONTH" not in s for s in stmts)
    assert any("DATE '2024-11-01'" in s for s in stmts)
