"""Transactions, their arrival lag and their corrections.

The ledger is produced as a table of transaction *versions*. Version 1 of a
transaction arrives in the core system some days after its booking date. A
correction arrives later still, either as a new version of the same row
(``UPDATE``) or as a reversal row plus a rebooked row (``REVERSAL`` and
``REBOOK``), both carrying the original booking date.
"""

from __future__ import annotations

from datetime import date, timedelta

import duckdb
import numpy as np
import pandas as pd

from . import reference as ref
from .bank import BankEntities, random_times, to_timestamps, weighted_choice
from .scenarios import Scenario

FIRST_TXN_ID = 10_000_000
CORRECTABLE_TYPES = ("CARD_PURCHASE", "BILL_PAYMENT", "TRANSFER_OUT")
SYSTEM_TYPES = ("SALARY", "INTEREST", "FEE")
# Credits are never late or corrected, so the balance the warehouse shows for any day is never
# lower than the final one, and an overdraft limit that holds in the final ledger holds everywhere.
NEVER_LATE_TYPES = SYSTEM_TYPES + ("TRANSFER_IN",)

COLUMNS = [
    "txn_id", "version_no", "change_type", "arrival_ts", "arrival_lag_days", "origin_txn_id",
    "account_no", "card_id", "merchant_id", "txn_type", "event_date", "amount", "currency",
    "status", "reversal_of", "amount_nok",
]


def sample_arrival_lag(rng: np.random.Generator, n: int, scn: Scenario) -> np.ndarray:
    """Days between booking and arrival in the core system.

    Most rows arrive the same day. The rest follow a geometric tail with the
    scenario's mean, capped at the scenario's maximum.
    """
    late = rng.random(n) >= scn.same_day_share
    lag = np.zeros(n, dtype=np.int64)
    k = int(late.sum())
    if k:
        tail = rng.geometric(1.0 / scn.late_tail_mean_days, size=k)
        lag[late] = np.minimum(tail, scn.late_max_days)
    return lag


def sample_correction_lag(rng: np.random.Generator, n: int, scn: Scenario) -> np.ndarray:
    """Days between a row's arrival and the arrival of its correction (at least one)."""
    tail = rng.geometric(1.0 / scn.correction_lag_mean_days, size=n)
    return np.minimum(tail, scn.correction_max_lag_days)


def corrected_amount(rng: np.random.Generator, amount: np.ndarray) -> np.ndarray:
    """A corrected amount: a keying error fixed, or a disputed charge adjusted."""
    factor = rng.uniform(0.5, 1.5, size=len(amount))
    factor = np.where(np.abs(factor - 1.0) < 0.05, factor + 0.1, factor)
    return np.round(amount * factor, 2)


def authorize(frame: pd.DataFrame, amount_nok: np.ndarray, accounts: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    """Book each account's rows in date order and refuse debits that would breach the overdraft limit.

    Returns a boolean mask of the rows that are booked. ``amount_nok`` is the
    NOK value to authorize against, normally the most expensive version of
    each row.
    """
    opening = dict(zip(accounts["account_no"], accounts["opening_balance"].astype(float)))
    limits = dict(zip(accounts["account_no"], accounts["credit_limit"].astype(float)))
    account_codes, account_keys = pd.factorize(frame["account_no"])
    day_numbers = pd.to_datetime(frame["event_date"]).to_numpy().astype("datetime64[D]").astype(np.int64)
    order = np.lexsort((rng.random(len(frame)), day_numbers, account_codes))
    booked = np.ones(len(frame), dtype=bool)
    amounts = amount_nok.tolist()
    codes = account_codes.tolist()
    current_code, running, floor = -1, 0.0, 0.0
    for i in order.tolist():
        code = codes[i]
        if code != current_code:
            current_code = code
            key = account_keys[code]
            running, floor = opening[key], -limits[key]
        amount = amounts[i]
        if amount >= 0 or running + amount >= floor - 1e-9:
            running += amount
        else:
            booked[i] = False
    return booked


def _month_grid(start: np.ndarray, end: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """For each (start, end) window return (row index, year, month) for every calendar month inside it."""
    m0 = np.array([d.year * 12 + d.month - 1 for d in start])
    m1 = np.array([d.year * 12 + d.month - 1 for d in end])
    counts = np.maximum(m1 - m0 + 1, 0)
    idx = np.repeat(np.arange(len(start)), counts)
    within = np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts)
    month_index = m0[idx] + within
    return idx, month_index // 12, month_index % 12 + 1


def _clip_to_window(days: np.ndarray, start: np.ndarray, end: np.ndarray) -> np.ndarray:
    return np.array([max(s, min(d, e)) for d, s, e in zip(days, start, end)], dtype=object)


def _fx_lookup(fx_daily: pd.DataFrame) -> dict[tuple[date, str], float]:
    return {(d, c): r for d, c, r in zip(fx_daily["rate_date"], fx_daily["currency"], fx_daily["rate_nok"])}


def make_transactions(
    scn: Scenario, rng: np.random.Generator, entities: BankEntities, fx_daily: pd.DataFrame
) -> pd.DataFrame:
    """Build the transaction versions for the whole period."""
    accounts = entities.accounts_initial.reset_index(drop=True)
    closes = entities.account_versions[entities.account_versions["version_no"] == 2].set_index("account_no")["close_date"]
    close_date = accounts["account_no"].map(closes)

    start = np.array([max(d, scn.period_start) for d in accounts["open_date"]], dtype=object)
    end = np.array(
        [min(c, scn.last_day) if isinstance(c, date) else scn.last_day for c in close_date], dtype=object
    )
    days = np.array([(e - s).days + 1 for s, e in zip(start, end)])
    active = days > 0
    acc_no = accounts["account_no"].to_numpy()
    acc_type = accounts["account_type"].to_numpy()
    segment = accounts["segment"].to_numpy()
    is_primary = accounts["is_primary"].to_numpy()
    opening_balance = accounts["opening_balance"].to_numpy()

    cards = entities.card_versions
    cards_by_account: dict[str, list[str]] = cards.groupby("account_no")["card_id"].agg(list).to_dict()
    card_issue = dict(zip(cards["card_id"], cards["issued_date"]))

    merchants = entities.merchant_versions
    domestic = merchants[merchants["country_code"] == "NO"]
    foreign = merchants[merchants["country_code"] != "NO"]
    dom_by_cat = {c: g for c, g in domestic.groupby("category_code")}
    for_by_cat = {c: g for c, g in foreign.groupby("category_code")}
    merchant_currency = dict(zip(merchants["merchant_id"], merchants["currency"]))

    pieces: list[pd.DataFrame] = []

    def poisson_events(rate: np.ndarray, mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (account index, event date) for Poisson-distributed events on masked accounts."""
        lam = np.where(mask & active, rate * days, 0.0)
        counts = rng.poisson(lam)
        idx = np.repeat(np.arange(len(accounts)), counts)
        offsets = (rng.random(len(idx)) * days[idx]).astype(int)
        dates = np.array([start[i] + timedelta(days=int(o)) for i, o in zip(idx, offsets)], dtype=object)
        return idx, dates

    # Card purchases.
    card_rate = np.where(
        acc_type == "CURRENT",
        np.where(is_primary, [ref.CARD_RATE_BY_SEGMENT[s] for s in segment], 0.10),
        0.0,
    )
    idx, dates = poisson_events(card_rate, acc_type == "CURRENT")
    n = len(idx)
    cat_objs = ref.CARD_CATEGORIES
    cat_choice = weighted_choice(rng, list(range(len(cat_objs))), [c.share for c in cat_objs], n).astype(int)
    go_foreign = rng.random(n) < scn.foreign_share
    merchant_id = np.empty(n, dtype=np.int64)
    amount = np.empty(n)
    for ci, cat in enumerate(cat_objs):
        sel = cat_choice == ci
        if not sel.any():
            continue
        k = int(sel.sum())
        nok = rng.lognormal(np.log(cat.median_nok), cat.sigma, size=k)
        amount[sel] = -np.round(nok, 2)
        pool_dom = dom_by_cat[cat.code]
        pick = weighted_choice(rng, pool_dom["merchant_id"].tolist(), pool_dom["popularity"].tolist(), k).astype(int)
        if cat.foreign_ok and cat.code in for_by_cat:
            pool_for = for_by_cat[cat.code]
            f_sel = go_foreign[sel]
            if f_sel.any():
                pick[f_sel] = weighted_choice(
                    rng, pool_for["merchant_id"].tolist(), pool_for["popularity"].tolist(), int(f_sel.sum())
                ).astype(int)
        merchant_id[sel] = pick
    currency = np.array([merchant_currency[m] for m in merchant_id], dtype=object)
    card_id = np.empty(n, dtype=object)
    for j in range(n):
        options = cards_by_account[acc_no[idx[j]]]
        chosen = options[int(rng.integers(0, len(options)))] if len(options) > 1 else options[0]
        if card_issue[chosen] > dates[j]:
            chosen = options[0]
        card_id[j] = chosen
    pieces.append(
        pd.DataFrame(
            dict(
                account_no=acc_no[idx], card_id=card_id, merchant_id=merchant_id, txn_type="CARD_PURCHASE",
                event_date=dates, amount=amount, currency=currency,
            )
        )
    )

    # Monthly bills on the main current account: housing, utilities, telecom, insurance.
    primary_mask = (acc_type == "CURRENT") & is_primary & active
    p_idx = np.flatnonzero(primary_mask)
    g_idx, years, months = _month_grid(start[p_idx], end[p_idx])
    g_acc = p_idx[g_idx]
    bill_cats = {c.code: c for c in ref.BILL_CATEGORIES}
    housing_amount = {
        a: round(ref.HOUSING_BY_SEGMENT[s] * rng.uniform(0.85, 1.15), 2) for a, s in zip(acc_no[p_idx], segment[p_idx])
    }
    provider = {}
    for code in bill_cats:
        pool = dom_by_cat[code]
        provider[code] = dict(
            zip(acc_no[p_idx], weighted_choice(rng, pool["merchant_id"].tolist(), pool["popularity"].tolist(), len(p_idx)).astype(int))
        )
    has_insurance = dict(zip(acc_no[p_idx], rng.random(len(p_idx)) < 0.70))
    for code, day_of_month in (("6513", 1), ("4900", 15), ("4814", 20), ("6300", 5)):
        cat = bill_cats[code]
        bill_dates = _clip_to_window(
            np.array([date(y, m, day_of_month) for y, m in zip(years, months)], dtype=object), start[g_acc], end[g_acc]
        )
        accs = acc_no[g_acc]
        if code == "6513":
            amt = -np.array([housing_amount[a] for a in accs])
        else:
            amt = -np.round(rng.lognormal(np.log(cat.median_nok), cat.sigma, size=len(accs)), 2)
        keep = np.ones(len(accs), dtype=bool)
        if code == "6300":
            keep = np.array([has_insurance[a] for a in accs])
        pieces.append(
            pd.DataFrame(
                dict(
                    account_no=accs[keep], card_id=None, merchant_id=[provider[code][a] for a in accs[keep]],
                    txn_type="BILL_PAYMENT", event_date=bill_dates[keep], amount=amt[keep], currency="NOK",
                )
            )
        )

    # Salary on the 15th and the account fee on the 28th.
    salary_base = {a: round(ref.SALARY_BY_SEGMENT[s] * rng.uniform(0.9, 1.1), 2) for a, s in zip(acc_no[p_idx], segment[p_idx])}
    salary_dates = _clip_to_window(np.array([date(y, m, 15) for y, m in zip(years, months)], dtype=object), start[g_acc], end[g_acc])
    salary_amt = np.round(np.array([salary_base[a] for a in acc_no[g_acc]]) * rng.uniform(0.98, 1.02, size=len(g_acc)), 2)
    pieces.append(
        pd.DataFrame(
            dict(account_no=acc_no[g_acc], card_id=None, merchant_id=None, txn_type="SALARY",
                 event_date=salary_dates, amount=salary_amt, currency="NOK")
        )
    )
    fee_mask = segment[g_acc] == "Standard"
    fee_dates = _clip_to_window(np.array([date(y, m, 28) for y, m in zip(years, months)], dtype=object), start[g_acc], end[g_acc])
    pieces.append(
        pd.DataFrame(
            dict(account_no=acc_no[g_acc][fee_mask], card_id=None, merchant_id=None, txn_type="FEE",
                 event_date=fee_dates[fee_mask], amount=-39.0, currency="NOK")
        )
    )

    # Transfers and cash withdrawals.
    for txn_type, rate_current, rate_savings, median, sign in (
        ("TRANSFER_OUT", 0.05, 0.015, 1500.0, -1.0),
        ("TRANSFER_IN", 0.04, 0.03, 2000.0, 1.0),
    ):
        rate = np.where(acc_type == "CURRENT", np.where(is_primary, rate_current, 0.02), rate_savings)
        idx, dates = poisson_events(rate, np.ones(len(accounts), dtype=bool))
        med = np.where(acc_type[idx] == "SAVINGS", median * 2.0, median)
        amt = sign * np.round(rng.lognormal(np.log(med), 1.0), 2)
        pieces.append(
            pd.DataFrame(
                dict(account_no=acc_no[idx], card_id=None, merchant_id=None, txn_type=txn_type,
                     event_date=dates, amount=amt, currency="NOK")
            )
        )
    idx, dates = poisson_events(np.where(acc_type == "CURRENT", 0.02, 0.0), acc_type == "CURRENT")
    atm_amt = -100.0 * rng.integers(2, 21, size=len(idx))
    pieces.append(
        pd.DataFrame(
            dict(account_no=acc_no[idx], card_id=None, merchant_id=None, txn_type="ATM_WITHDRAWAL",
                 event_date=dates, amount=atm_amt.astype(float), currency="NOK")
        )
    )

    # Quarterly interest on savings accounts.
    s_idx = np.flatnonzero((acc_type == "SAVINGS") & active)
    g_idx, years, months = _month_grid(start[s_idx], end[s_idx])
    quarter_end = np.isin(months, [3, 6, 9, 12])
    g_acc = s_idx[g_idx][quarter_end]
    q_dates = np.array(
        [date(y, m, 31 if m in (3, 12) else 30) for y, m in zip(years[quarter_end], months[quarter_end])], dtype=object
    )
    inside = np.array([s <= d <= e for d, s, e in zip(q_dates, start[g_acc], end[g_acc])])
    interest = np.round(opening_balance[g_acc] * 0.008 * rng.uniform(0.8, 1.2, size=len(g_acc)), 2)
    pieces.append(
        pd.DataFrame(
            dict(account_no=acc_no[g_acc][inside], card_id=None, merchant_id=None, txn_type="INTEREST",
                 event_date=q_dates[inside], amount=interest[inside], currency="NOK")
        )
    )

    base = pd.concat(pieces, ignore_index=True)
    base["merchant_id"] = base["merchant_id"].astype("Int64")
    base["status"] = "POSTED"
    base["reversal_of"] = pd.array([None] * len(base), dtype="Int64")

    # Amounts in foreign currency are drawn in NOK terms and converted at the booking date's rate.
    fx = _fx_lookup(fx_daily)
    is_foreign = base["currency"] != "NOK"
    if is_foreign.any():
        rates = np.array(
            [fx[(d, c)] for d, c in zip(base.loc[is_foreign, "event_date"], base.loc[is_foreign, "currency"])]
        )
        base.loc[is_foreign, "amount"] = np.round(base.loc[is_foreign, "amount"].to_numpy() / rates, 2)

    # Corrections are decided before authorization, because a debit is authorized against its
    # most expensive version: no later correction can push an account below its limit.
    n = len(base)
    correctable = base["txn_type"].isin(CORRECTABLE_TYPES).to_numpy()
    corr_mask = correctable & (rng.random(n) < scn.correction_rate)
    final_amount = base["amount"].to_numpy().copy()
    final_amount[corr_mask] = corrected_amount(rng, final_amount[corr_mask])
    as_update = rng.random(n) < scn.update_share

    # Authorization: in booking order, a debit that would take the balance below the account's
    # overdraft limit is declined and never booked. Credits are never declined.
    worst_nok = np.minimum(nok_amounts(base, fx_daily), nok_amounts(base.assign(amount=final_amount), fx_daily))
    booked = authorize(base, worst_nok, accounts, rng)
    entities.declined_attempts = base.loc[~booked, ["account_no", "txn_type", "event_date", "amount", "currency"]].reset_index(drop=True)
    base = base[booked].reset_index(drop=True)
    final_amount, corr_mask, as_update = final_amount[booked], corr_mask[booked], as_update[booked]
    n = len(base)

    # Arrival in the core system. Rows the bank books itself and incoming transfers are never late.
    never_late = base["txn_type"].isin(NEVER_LATE_TYPES).to_numpy()
    lag = sample_arrival_lag(rng, n, scn)
    lag[never_late] = 0
    arrival_day = np.array([d + timedelta(days=int(l)) for d, l in zip(base["event_date"], lag)], dtype=object)
    base["arrival_ts"] = to_timestamps(arrival_day, random_times(rng, n))
    base["arrival_lag_days"] = lag
    base["version_no"] = 1
    base["change_type"] = "NEW"

    # Corrections arrive some days after the row they correct.
    corr = base[corr_mask].copy()
    corr_lag = sample_correction_lag(rng, len(corr), scn)
    corr_day = np.array(
        [pd.Timestamp(t).date() + timedelta(days=int(l)) for t, l in zip(corr["arrival_ts"], corr_lag)], dtype=object
    )
    corr["arrival_ts"] = to_timestamps(corr_day, random_times(rng, len(corr)))
    corr["arrival_lag_days"] = corr_lag
    new_amount = final_amount[corr_mask]
    corr_as_update = as_update[corr_mask]

    base["origin_txn_id"] = pd.array([None] * n, dtype="Int64")
    base["_order"] = np.arange(n)
    corr["_parent_order"] = base.loc[corr_mask, "_order"].to_numpy()

    updates = corr[corr_as_update].copy()
    updates["amount"] = new_amount[corr_as_update]
    updates["version_no"] = 2
    updates["change_type"] = "UPDATE"

    reversals = corr[~corr_as_update].copy()
    reversals["amount"] = -reversals["amount"]
    reversals["change_type"] = "REVERSAL"
    rebooks = corr[~corr_as_update].copy()
    rebooks["amount"] = new_amount[~corr_as_update]
    rebooks["change_type"] = "REBOOK"
    rebooks["arrival_ts"] = rebooks["arrival_ts"] + pd.Timedelta(seconds=1)

    # Ids are allocated in arrival order, the way a sequence in the core system would.
    first_versions = pd.concat([base, reversals, rebooks], ignore_index=True)
    first_versions = first_versions.sort_values(["arrival_ts", "_order"], kind="stable").reset_index(drop=True)
    first_versions["txn_id"] = FIRST_TXN_ID + np.arange(len(first_versions))
    id_of_order = dict(zip(first_versions.loc[first_versions["change_type"] == "NEW", "_order"], first_versions.loc[first_versions["change_type"] == "NEW", "txn_id"]))
    is_corr_row = first_versions["change_type"].isin(["REVERSAL", "REBOOK"])
    parent_ids = first_versions.loc[is_corr_row, "_parent_order"].map(id_of_order).astype("Int64")
    first_versions.loc[is_corr_row, "origin_txn_id"] = parent_ids
    first_versions.loc[first_versions["change_type"] == "REVERSAL", "reversal_of"] = parent_ids[
        first_versions.loc[is_corr_row, "change_type"] == "REVERSAL"
    ]
    updates["txn_id"] = updates["_parent_order"].map(id_of_order).astype("int64")
    updates["origin_txn_id"] = updates["txn_id"].astype("Int64")

    versions = pd.concat([first_versions, updates], ignore_index=True)
    versions = versions.drop(columns=["_order", "_parent_order"])
    versions["txn_id"] = versions["txn_id"].astype("int64")
    versions["merchant_id"] = versions["merchant_id"].astype("Int64")

    versions = versions.sort_values(["txn_id", "version_no"]).reset_index(drop=True)
    versions["amount_nok"] = nok_amounts(versions, fx_daily)
    return versions[COLUMNS]


def nok_amounts(versions: pd.DataFrame, fx_daily: pd.DataFrame) -> np.ndarray:
    """NOK value of every version at the booking date's rate, rounded exactly as the warehouse does.

    The rounding runs in DuckDB on DECIMAL values (half away from zero), the same
    arithmetic the dbt models use, so the ground truth and the warehouse never
    disagree by a rounding cent.
    """
    con = duckdb.connect()
    try:
        con.register("v", versions[["amount", "currency", "event_date"]].assign(pos=np.arange(len(versions))))
        con.register("fx", fx_daily[["rate_date", "currency", "rate_nok"]])
        result = con.execute(
            """
            SELECT cast(round(cast(v.amount AS DECIMAL(18, 2)) * cast(coalesce(f.rate_nok, 1) AS DECIMAL(18, 6)), 2)
                        AS DECIMAL(18, 2)) AS amount_nok
            FROM v
            LEFT JOIN fx f ON f.currency = v.currency AND f.rate_date = v.event_date AND v.currency <> 'NOK'
            ORDER BY v.pos
            """
        ).fetchdf()
    finally:
        con.close()
    return result["amount_nok"].astype(float).to_numpy()
