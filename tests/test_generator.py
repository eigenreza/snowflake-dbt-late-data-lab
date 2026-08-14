"""The synthetic bank: determinism, integrity, lateness and corrections."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

from common import fx
from generator import core_db, truth
from generator.build_core_bank import generate
from generator.ledger import CORRECTABLE_TYPES, NEVER_LATE_TYPES, sample_arrival_lag, sample_correction_lag


def test_same_seed_gives_same_bank(small_scenario, fx_published):
    a_entities, a_txn, _ = generate(small_scenario, fx_published)
    b_entities, b_txn, _ = generate(small_scenario, fx_published)
    pd.testing.assert_frame_equal(a_txn, b_txn)
    pd.testing.assert_frame_equal(a_entities.customer_versions, b_entities.customer_versions)
    pd.testing.assert_frame_equal(a_entities.account_versions, b_entities.account_versions)


def test_referential_integrity(bank_data, small_scenario):
    entities, txn, _ = bank_data
    accounts = entities.accounts_initial
    assert txn["account_no"].isin(accounts["account_no"]).all()
    merchants = entities.merchant_versions["merchant_id"]
    with_merchant = txn["merchant_id"].notna()
    assert txn.loc[with_merchant, "merchant_id"].isin(merchants).all()
    cards = entities.card_versions.set_index("card_id")["account_no"]
    with_card = txn["card_id"].notna()
    assert (txn.loc[with_card, "card_id"].map(cards) == txn.loc[with_card, "account_no"]).all()
    assert txn["event_date"].min() >= small_scenario.period_start
    assert txn["event_date"].max() <= small_scenario.last_day


def test_no_transaction_before_account_opened(bank_data):
    entities, txn, _ = bank_data
    opened = entities.accounts_initial.set_index("account_no")["open_date"]
    assert (txn["event_date"] >= txn["account_no"].map(opened)).all()


def test_card_purchases_use_an_issued_card(bank_data):
    entities, txn, _ = bank_data
    issued = entities.card_versions.set_index("card_id")["issued_date"]
    purchases = txn[txn["txn_type"] == "CARD_PURCHASE"]
    assert purchases["card_id"].notna().all()
    assert (purchases["event_date"] >= purchases["card_id"].map(issued)).all()


def test_version_structure(bank_data):
    _, txn, _ = bank_data
    new = txn[txn["change_type"] == "NEW"]
    assert (new["version_no"] == 1).all()
    assert new["txn_id"].is_unique
    updates = txn[txn["change_type"] == "UPDATE"]
    assert (updates["version_no"] == 2).all()
    assert updates["txn_id"].isin(new["txn_id"]).all()
    parent_arrival = new.set_index("txn_id")["arrival_ts"]
    assert (updates["arrival_ts"] > updates["txn_id"].map(parent_arrival)).all()
    parent_amount = new.set_index("txn_id")["amount"]
    assert (updates["amount"] != updates["txn_id"].map(parent_amount)).all()

    reversals = txn[txn["change_type"] == "REVERSAL"]
    assert reversals["reversal_of"].isin(new["txn_id"]).all()
    assert np.allclose(reversals["amount"], -reversals["reversal_of"].map(parent_amount))
    assert (reversals["event_date"] == reversals["reversal_of"].map(new.set_index("txn_id")["event_date"])).all()
    rebooks = txn[txn["change_type"] == "REBOOK"]
    assert len(rebooks) == len(reversals)
    assert rebooks["origin_txn_id"].isin(new["txn_id"]).all()


def test_correction_share_matches_scenario(bank_data, small_scenario):
    _, txn, _ = bank_data
    correctable = txn[(txn["change_type"] == "NEW") & txn["txn_type"].isin(CORRECTABLE_TYPES)]
    corrected = txn["change_type"].isin(["UPDATE", "REVERSAL"]).sum()
    share = corrected / len(correctable)
    assert abs(share - small_scenario.correction_rate) < 0.004
    assert not txn[txn["txn_type"] == "TRANSFER_IN"]["change_type"].isin(["UPDATE", "REVERSAL", "REBOOK"]).any()


def test_lateness_distribution(bank_data, small_scenario):
    _, txn, _ = bank_data
    new = txn[(txn["change_type"] == "NEW") & ~txn["txn_type"].isin(NEVER_LATE_TYPES)]
    same_day = (new["arrival_lag_days"] == 0).mean()
    assert abs(same_day - small_scenario.same_day_share) < 0.02
    assert new["arrival_lag_days"].max() <= small_scenario.late_max_days
    late = new.loc[new["arrival_lag_days"] > 0, "arrival_lag_days"]
    assert abs(late.mean() - small_scenario.late_tail_mean_days) < 0.5
    never_late = txn[txn["txn_type"].isin(NEVER_LATE_TYPES)]
    assert (never_late["arrival_lag_days"] == 0).all()


def test_no_balance_below_credit_limit(built_paths, bank_data, small_scenario):
    """Declines keep every account at or above minus its overdraft limit, in the final ledger and as of any day."""
    entities, txn, _ = bank_data
    accounts = entities.accounts_initial.set_index("account_no")
    assert (accounts.loc[accounts["account_type"] == "SAVINGS", "credit_limit"] == 0).all()
    students = accounts[accounts["segment"] == "Student"]
    assert students["credit_limit"].max() <= 2000
    assert entities.declined_attempts is not None and len(entities.declined_attempts) > 0
    assert (entities.declined_attempts["amount"] < 0).all(), "only debits are declined"

    con = truth.connect(built_paths.truth_db, read_only=True)
    try:
        for as_of in (truth.FINAL, core_db.end_of_day(small_scenario.initial_extract_date)):
            balances = truth.kpi_daily_balance(con, as_of, small_scenario.period_start, small_scenario.last_day)
            limit = balances["account_no"].map(accounts["credit_limit"]).astype(float)
            assert (balances["balance_nok"].astype(float) + limit >= -0.005).all()
    finally:
        con.close()


def test_lag_samplers_respect_bounds(small_scenario):
    rng = np.random.default_rng(1)
    lag = sample_arrival_lag(rng, 50_000, small_scenario)
    assert lag.min() == 0 and lag.max() <= small_scenario.late_max_days
    corr = sample_correction_lag(rng, 50_000, small_scenario)
    assert corr.min() >= 1 and corr.max() <= small_scenario.correction_max_lag_days


def test_foreign_currency_amounts(bank_data, small_scenario):
    _, txn, fx_daily = bank_data
    purchases = txn[(txn["change_type"] == "NEW") & (txn["txn_type"] == "CARD_PURCHASE")]
    foreign = purchases[purchases["currency"] != "NOK"]
    assert set(foreign["currency"]) <= set(fx.CURRENCIES)
    assert abs(len(foreign) / len(purchases) - small_scenario.foreign_share) < 0.015
    rates = fx_daily.set_index(["rate_date", "currency"])["rate_nok"]
    expected = np.round(foreign["amount"].to_numpy() * rates.loc[list(zip(foreign["event_date"], foreign["currency"]))].to_numpy(), 2)
    # DECIMAL rounding in the generator and float rounding here can differ by one cent on exact ties.
    assert np.allclose(foreign["amount_nok"].to_numpy(), expected, atol=0.011)
    domestic = purchases[purchases["currency"] == "NOK"]
    assert (domestic["amount_nok"] == domestic["amount"]).all()


def test_customer_history_is_consistent(bank_data):
    entities, _, _ = bank_data
    versions = entities.customer_versions.sort_values(["customer_id", "version_no"])
    per_customer = versions.groupby("customer_id")
    assert (per_customer["version_no"].agg(lambda s: list(s) == list(range(1, len(s) + 1)))).all()
    assert (per_customer["valid_from"].agg(lambda s: s.is_monotonic_increasing)).all()
    assert versions["version_no"].max() >= 2, "no customer ever changed"
    changed = versions[versions["version_no"] > 1]
    previous = versions.groupby("customer_id").shift(1).loc[changed.index]
    differs = (
        (changed["region"] != previous["region"])
        | (changed["segment"] != previous["segment"])
        | (changed["street"] != previous["street"])
    )
    assert differs.all()


def test_core_state_at_initial_extract(built_paths, bank_data, small_scenario):
    _, txn, _ = bank_data
    t0 = core_db.end_of_day(small_scenario.initial_extract_date)
    arrived = txn[txn["arrival_ts"] <= t0]
    latest = arrived.sort_values("version_no").groupby("txn_id").tail(1).set_index("txn_id")

    con = core_db.connect(built_paths.core_db, read_only=True)
    try:
        core = con.execute("SELECT txn_id, amount, created_at, updated_at FROM core.transactions").fetchdf().set_index("txn_id")
    finally:
        con.close()
    assert len(core) == len(latest)
    assert np.allclose(core["amount"].astype(float).sort_index(), latest["amount"].astype(float).sort_index())
    updated = latest[latest["version_no"] == 2]
    assert len(updated) > 0
    assert (core.loc[updated.index, "updated_at"] > core.loc[updated.index, "created_at"]).all()
    # Corrections that arrive after the initial extract are not in the core system yet.
    pending = txn[(txn["change_type"] == "UPDATE") & (txn["arrival_ts"] > t0) & txn["txn_id"].isin(core.index)]
    original = txn[(txn["change_type"] == "NEW") & txn["txn_id"].isin(pending["txn_id"])].set_index("txn_id")["amount"]
    assert np.allclose(core.loc[original.index, "amount"].astype(float), original.astype(float))


def test_advance_day_applies_that_days_arrivals(fresh_paths, bank_data, small_scenario):
    from generator.advance_day import advance

    _, txn, _ = bank_data
    day = small_scenario.initial_extract_date + timedelta(days=1)
    counts = advance(fresh_paths, day)
    arrivals = txn[(txn["arrival_ts"] >= core_db.start_of_day(day)) & (txn["arrival_ts"] <= core_db.end_of_day(day))]
    assert counts["transactions"] == arrivals["txn_id"].nunique()

    con = core_db.connect(fresh_paths.core_db, read_only=True)
    try:
        changed = con.execute(
            "SELECT txn_id, amount FROM core.transactions WHERE updated_at >= ? AND updated_at <= ?",
            [core_db.start_of_day(day), core_db.end_of_day(day)],
        ).fetchdf().set_index("txn_id")
    finally:
        con.close()
    expected = arrivals.sort_values("version_no").groupby("txn_id").tail(1).set_index("txn_id")["amount"]
    assert np.allclose(changed["amount"].astype(float).sort_index(), expected.astype(float).sort_index())

    # Applying the same day again changes nothing; skipping a day is refused.
    assert advance(fresh_paths, day) == counts
    with pytest.raises(SystemExit):
        advance(fresh_paths, day + timedelta(days=2))


def test_truth_summaries(built_paths):
    con = truth.connect(built_paths.truth_db, read_only=True)
    try:
        lateness = truth.lateness_summary(con)
        corrections = truth.correction_summary(con)
    finally:
        con.close()
    assert abs(lateness["share"].sum() - 1.0) < 1e-9
    assert lateness.loc[lateness["lag_days"] == 0, "share"].iloc[0] > 0.8
    assert set(corrections["change_type"]) == {"NEW", "UPDATE", "REVERSAL", "REBOOK"}
