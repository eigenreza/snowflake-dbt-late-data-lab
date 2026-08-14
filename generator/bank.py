"""Customers, their change history, accounts, cards and merchants.

Every entity is produced as a table of *versions*: one row per state of a
record, with the timestamp at which that state reached the core system. The
core system shows the latest version that has arrived; the ground truth keeps
them all.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from . import reference as ref
from .scenarios import Scenario

SECONDS_PER_DAY = 86400


def weighted_choice(rng: np.random.Generator, items: list, weights: list[float], n: int) -> np.ndarray:
    p = np.asarray(weights, dtype=float)
    return rng.choice(np.asarray(items, dtype=object), size=n, p=p / p.sum())


def random_times(rng: np.random.Generator, n: int, start_hour: int = 6, end_hour: int = 22) -> np.ndarray:
    """Seconds after midnight during the bank's posting hours."""
    return rng.integers(start_hour * 3600, end_hour * 3600, size=n)


def to_timestamps(days: pd.Series | np.ndarray, seconds: np.ndarray) -> np.ndarray:
    """Combine calendar days with seconds after midnight; positional, so safe to assign to any frame."""
    base = pd.to_datetime(pd.Series(np.asarray(days, dtype=object))).dt.floor("D")
    return (base + pd.to_timedelta(seconds, unit="s")).astype("datetime64[us]").to_numpy()


def random_dates(rng: np.random.Generator, start: date, end: date, n: int) -> np.ndarray:
    span = (end - start).days
    offsets = rng.integers(0, span + 1, size=n)
    return np.array([start + timedelta(days=int(o)) for o in offsets], dtype=object)


@dataclass
class BankEntities:
    customer_versions: pd.DataFrame
    account_versions: pd.DataFrame
    card_versions: pd.DataFrame
    merchant_versions: pd.DataFrame
    declined_attempts: pd.DataFrame | None = None

    @property
    def customers_initial(self) -> pd.DataFrame:
        return self.customer_versions[self.customer_versions["version_no"] == 1]

    @property
    def accounts_initial(self) -> pd.DataFrame:
        return self.account_versions[self.account_versions["version_no"] == 1]


def _postal_code(rng: np.random.Generator, region: str) -> str:
    base = 1000 + ref.REGIONS.index(next(r for r in ref.REGIONS if r[0] == region)) * 600
    return f"{base + int(rng.integers(0, 600)):04d}"


def _address(rng: np.random.Generator, region: str) -> tuple[str, str, str]:
    street = f"{rng.choice(ref.STREETS)} {int(rng.integers(1, 180))}"
    city = str(rng.choice(ref.CITIES[region]))
    return street, _postal_code(rng, region), city


def make_customers(scn: Scenario, rng: np.random.Generator) -> pd.DataFrame:
    """Customer versions: the state at onboarding plus every later change."""
    n = scn.n_customers
    ids = np.arange(1, n + 1)
    regions = weighted_choice(rng, [r for r, _ in ref.REGIONS], [w for _, w in ref.REGIONS], n)
    segments = weighted_choice(rng, [s for s, _ in ref.SEGMENTS], [w for _, w in ref.SEGMENTS], n)
    birth = random_dates(rng, date(1940, 1, 1), date(2006, 12, 31), n)

    before = rng.random(n) < 0.92
    since = np.empty(n, dtype=object)
    since[before] = [scn.period_start - timedelta(days=int(d)) for d in rng.integers(1, 3000, size=before.sum())]
    since[~before] = random_dates(rng, scn.period_start, scn.last_day, int((~before).sum()))

    rows = []
    for i in range(n):
        street, postal, city = _address(rng, regions[i])
        rows.append(
            dict(
                customer_id=int(ids[i]),
                version_no=1,
                valid_from=since[i],
                customer_no=f"C{100000 + int(ids[i])}",
                first_name=str(rng.choice(ref.FIRST_NAMES)),
                last_name=str(rng.choice(ref.LAST_NAMES)),
                birth_date=birth[i],
                segment=segments[i],
                region=regions[i],
                street=street,
                postal_code=postal,
                city=city,
                customer_since=since[i],
            )
        )

    # Later changes: moves, address changes and segment changes.
    versions = list(rows)
    region_names = [r for r, _ in ref.REGIONS]
    region_weights = [w for _, w in ref.REGIONS]
    segment_names = [s for s, _ in ref.SEGMENTS]
    for i in range(n):
        window_start = max(since[i], scn.period_start)
        months = max((scn.last_day - window_start).days, 0) / 30.4
        k = int(rng.poisson(scn.customer_changes_per_month * months))
        if k == 0:
            continue
        change_days = sorted(random_dates(rng, window_start + timedelta(days=1), scn.last_day, k))
        current = dict(rows[i])
        for v, day in enumerate(change_days, start=2):
            current = dict(current)
            kind = rng.choice(["move", "address", "segment"], p=[0.40, 0.35, 0.25])
            if kind == "move":
                new_region = current["region"]
                while new_region == current["region"]:
                    new_region = weighted_choice(rng, region_names, region_weights, 1)[0]
                current["region"] = new_region
                current["street"], current["postal_code"], current["city"] = _address(rng, new_region)
            elif kind == "address":
                current["street"], current["postal_code"], current["city"] = _address(rng, current["region"])
            else:
                new_segment = current["segment"]
                while new_segment == current["segment"]:
                    new_segment = str(rng.choice(segment_names))
                current["segment"] = new_segment
            current["version_no"] = v
            current["valid_from"] = day
            versions.append(current)

    frame = pd.DataFrame(versions)
    frame["arrival_ts"] = to_timestamps(frame["valid_from"], random_times(rng, len(frame), 8, 17))
    frame = frame.sort_values(["customer_id", "version_no"]).reset_index(drop=True)
    return frame


def make_accounts(scn: Scenario, rng: np.random.Generator, customers: pd.DataFrame) -> pd.DataFrame:
    """Account versions: one at opening, and one more when an account closes."""
    n_cust = len(customers)
    n_accounts = 1 + rng.binomial(2, 0.35, size=n_cust)
    cust_idx = np.repeat(np.arange(n_cust), n_accounts)
    ordinal = np.concatenate([np.arange(k) for k in n_accounts])
    total = len(cust_idx)

    since = customers["customer_since"].to_numpy()
    segment = customers["segment"].to_numpy()
    is_primary = ordinal == 0
    acc_type = np.where(is_primary, "CURRENT", np.where(rng.random(total) < 0.70, "SAVINGS", "CURRENT"))

    open_date = np.empty(total, dtype=object)
    for j in range(total):
        c = cust_idx[j]
        if is_primary[j]:
            open_date[j] = since[c]
        else:
            latest = scn.last_day
            span = max((latest - since[c]).days, 0)
            open_date[j] = since[c] + timedelta(days=int(rng.integers(0, span + 1)))

    closes = (~is_primary) & (rng.random(total) < 0.06)
    close_date = np.full(total, None, dtype=object)
    for j in np.flatnonzero(closes):
        candidate = open_date[j] + timedelta(days=int(rng.integers(90, 1500)))
        if candidate <= scn.last_day:
            close_date[j] = candidate

    pb_boost = np.where(segment[cust_idx] == "Private Banking", 5.0, 1.0)
    median = np.where(acc_type == "CURRENT", 15000.0, 80000.0) * pb_boost
    opening_balance = np.round(rng.lognormal(np.log(median), 1.0), 2)

    # Overdraft limit: none on savings accounts; on current accounts it depends on the segment.
    credit_limit = np.zeros(total)
    for seg, (values, weights) in ref.CREDIT_LIMITS_BY_SEGMENT.items():
        mask = (acc_type == "CURRENT") & (segment[cust_idx] == seg)
        if mask.any():
            credit_limit[mask] = rng.choice(values, size=int(mask.sum()), p=weights)

    account_id = np.arange(1, total + 1)
    base = pd.DataFrame(
        dict(
            account_no=[f"9999{aid:07d}" for aid in account_id],
            customer_id=customers["customer_id"].to_numpy()[cust_idx],
            account_type=acc_type,
            open_date=open_date,
            close_date=None,
            status="OPEN",
            opening_balance=opening_balance,
            credit_limit=credit_limit,
            is_primary=is_primary,
            segment=segment[cust_idx],
        )
    )
    base["version_no"] = 1
    base["arrival_ts"] = to_timestamps(base["open_date"], random_times(rng, total, 8, 17))

    closed = base[close_date != None].copy()  # noqa: E711  (object array comparison)
    closed["close_date"] = close_date[close_date != None]  # noqa: E711
    closed["status"] = "CLOSED"
    closed["version_no"] = 2
    closed["arrival_ts"] = to_timestamps(closed["close_date"], random_times(rng, len(closed), 8, 17))

    frame = pd.concat([base, closed], ignore_index=True)
    frame = frame.sort_values(["account_no", "version_no"]).reset_index(drop=True)
    return frame


def make_cards(scn: Scenario, rng: np.random.Generator, accounts: pd.DataFrame) -> pd.DataFrame:
    """One card per current account, a second one for some."""
    current = accounts[(accounts["version_no"] == 1) & (accounts["account_type"] == "CURRENT")]
    n_cards = 1 + (rng.random(len(current)) < 0.15).astype(int)
    acc_idx = np.repeat(np.arange(len(current)), n_cards)
    ordinal = np.concatenate([np.arange(k) for k in n_cards])
    total = len(acc_idx)

    open_date = current["open_date"].to_numpy()[acc_idx]
    issued = np.array(
        [d if o == 0 else d + timedelta(days=int(rng.integers(30, 400))) for d, o in zip(open_date, ordinal)],
        dtype=object,
    )
    issued = np.array([min(d, scn.last_day) for d in issued], dtype=object)
    expiry = np.array([date(d.year + 4, d.month, 1) + timedelta(days=27) for d in issued], dtype=object)
    pan = [f"4{int(rng.integers(10000, 99999))}******{int(rng.integers(1000, 9999))}" for _ in range(total)]

    frame = pd.DataFrame(
        dict(
            card_id=[f"K{i:08d}" for i in range(1, total + 1)],
            account_no=current["account_no"].to_numpy()[acc_idx],
            masked_pan=pan,
            issued_date=issued,
            expiry_date=expiry,
            status="ACTIVE",
        )
    )
    frame["version_no"] = 1
    frame["arrival_ts"] = to_timestamps(frame["issued_date"], random_times(rng, total, 8, 17))
    return frame


def make_merchants(scn: Scenario, rng: np.random.Generator) -> pd.DataFrame:
    """Domestic and foreign merchants per category, with a popularity weight."""
    rows = []
    merchant_id = 1001
    for cat in ref.ALL_CATEGORIES:
        type_words = ref.CATEGORY_TYPE_WORDS[cat.code]
        for rank in range(cat.merchants):
            region = weighted_choice(rng, [r for r, _ in ref.REGIONS], [w for _, w in ref.REGIONS], 1)[0]
            city = str(rng.choice(ref.CITIES[region]))
            name = f"{rng.choice(ref.MERCHANT_WORDS)} {rng.choice(type_words)}"
            if rng.random() < 0.5:
                name = f"{name} {city}"
            rows.append(
                dict(
                    merchant_id=merchant_id,
                    merchant_name=name,
                    category_code=cat.code,
                    category_name=cat.name,
                    country_code="NO",
                    city=city,
                    currency="NOK",
                    popularity=1.0 / (rank + 1) ** 0.8,
                )
            )
            merchant_id += 1
        if cat.foreign_ok:
            n_foreign = max(3, int(round(cat.merchants * 0.12)))
            countries = weighted_choice(
                rng, [c for c, _, _ in ref.FOREIGN_COUNTRIES], [w for _, _, w in ref.FOREIGN_COUNTRIES], n_foreign
            )
            currency_of = {c: ccy for c, ccy, _ in ref.FOREIGN_COUNTRIES}
            for rank, country in enumerate(countries):
                city = str(rng.choice(ref.FOREIGN_CITIES[country]))
                name = f"{rng.choice(ref.FOREIGN_MERCHANT_WORDS)} {rng.choice(type_words)} {city}"
                rows.append(
                    dict(
                        merchant_id=merchant_id,
                        merchant_name=name,
                        category_code=cat.code,
                        category_name=cat.name,
                        country_code=country,
                        city=city,
                        currency=currency_of[country],
                        popularity=1.0 / (rank + 1) ** 0.8,
                    )
                )
                merchant_id += 1

    frame = pd.DataFrame(rows)
    created = random_dates(rng, scn.period_start - timedelta(days=2000), scn.period_start - timedelta(days=30), len(frame))
    frame["version_no"] = 1
    frame["arrival_ts"] = to_timestamps(created, random_times(rng, len(frame), 8, 17))
    return frame


def make_entities(scn: Scenario, rng: np.random.Generator) -> BankEntities:
    customers = make_customers(scn, rng)
    accounts = make_accounts(scn, rng, customers[customers["version_no"] == 1].reset_index(drop=True))
    cards = make_cards(scn, rng, accounts)
    merchants = make_merchants(scn, rng)
    return BankEntities(customers, accounts, cards, merchants)
