"""Norges Bank rates: parsing, the committed cache and carrying rates over weekends."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from common import fx
from common.paths import Paths
from generator.scenarios import SCENARIOS

SAMPLE = """FREQ;Frequency;BASE_CUR;Base Currency;QUOTE_CUR;Quote Currency;TENOR;Tenor;DECIMALS;CALCULATED;UNIT_MULT;Unit Multiplier;COLLECTION;Collection Indicator;TIME_PERIOD;OBS_VALUE
B;Business;USD;US dollar;NOK;Norwegian krone;SP;Spot;4;false;0;Units;C;ECB concertation time 14:15 CET;2024-01-05;10.2
B;Business;USD;US dollar;NOK;Norwegian krone;SP;Spot;4;false;0;Units;C;ECB concertation time 14:15 CET;2024-01-08;10.4
B;Business;SEK;Swedish krona;NOK;Norwegian krone;SP;Spot;4;false;2;Hundreds;C;ECB concertation time 14:15 CET;2024-01-05;101.5
B;Business;SEK;Swedish krona;NOK;Norwegian krone;SP;Spot;4;false;2;Hundreds;C;ECB concertation time 14:15 CET;2024-01-08;102.0
"""


def test_parse_scales_by_unit_multiplier():
    rates = fx.parse_norges_bank_csv(SAMPLE)
    sek = rates[rates["currency"] == "SEK"].set_index("rate_date")["rate_nok"]
    usd = rates[rates["currency"] == "USD"].set_index("rate_date")["rate_nok"]
    assert sek[date(2024, 1, 5)] == pytest.approx(1.015)
    assert usd[date(2024, 1, 8)] == pytest.approx(10.4)
    assert list(rates.columns) == fx.CACHE_COLUMNS


def test_parse_rejects_unexpected_layout():
    with pytest.raises(ValueError):
        fx.parse_norges_bank_csv("a;b;c\n1;2;3\n")


def test_fill_calendar_carries_last_rate_over_the_weekend():
    rates = fx.parse_norges_bank_csv(SAMPLE)
    daily = fx.fill_calendar(rates, date(2024, 1, 5), date(2024, 1, 9))
    usd = daily[daily["currency"] == "USD"].set_index("rate_date")
    assert usd.index.tolist() == [date(2024, 1, d) for d in range(5, 10)]
    assert usd.loc[date(2024, 1, 6), "rate_nok"] == pytest.approx(10.2)   # Saturday
    assert usd.loc[date(2024, 1, 7), "rate_nok"] == pytest.approx(10.2)   # Sunday
    assert usd.loc[date(2024, 1, 8), "rate_nok"] == pytest.approx(10.4)   # Monday, published
    assert usd.loc[date(2024, 1, 9), "rate_nok"] == pytest.approx(10.4)   # Tuesday, carried
    assert usd["is_published"].tolist() == [True, False, False, True, False]


def test_fill_calendar_needs_a_rate_before_the_start():
    rates = fx.parse_norges_bank_csv(SAMPLE)
    with pytest.raises(ValueError):
        fx.fill_calendar(rates, date(2024, 1, 1), date(2024, 1, 9))


def test_cache_round_trip(tmp_path):
    rates = fx.parse_norges_bank_csv(SAMPLE)
    path = tmp_path / "fx.csv"
    fx.write_cache(rates, path)
    back = fx.read_cache(path)
    pd.testing.assert_frame_equal(back, rates, check_dtype=False)


def test_committed_cache_covers_every_scenario_period():
    cache = Paths().fx_cache
    assert cache.exists(), "run python -m extract.extract_fx"
    rates = fx.read_cache(cache)
    assert set(rates["currency"]) == set(fx.CURRENCIES)
    for scn in SCENARIOS.values():
        daily = fx.fill_calendar(rates, scn.period_start, scn.last_day)
        assert daily["rate_nok"].notna().all()
        assert len(daily) == len(fx.CURRENCIES) * ((scn.last_day - scn.period_start).days + 1)
