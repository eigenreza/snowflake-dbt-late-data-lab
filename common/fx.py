"""Norges Bank exchange rates: parsing, caching and calendar filling.

Norges Bank publishes rates on Norwegian business days only. For a transaction
booked on a weekend or holiday the last published rate is carried forward,
which is the bank's own convention.
"""

from __future__ import annotations

import io
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

CURRENCIES = ("EUR", "USD", "SEK")
API_URL = "https://data.norges-bank.no/api/data/EXR/B.{currencies}.NOK.SP"
CACHE_COLUMNS = ["rate_date", "currency", "rate_nok"]


def api_url(start: date, end: date, currencies: tuple[str, ...] = CURRENCIES) -> str:
    base = API_URL.format(currencies="+".join(currencies))
    return f"{base}?format=csv&startPeriod={start:%Y-%m-%d}&endPeriod={end:%Y-%m-%d}&locale=en"


def parse_norges_bank_csv(text: str) -> pd.DataFrame:
    """Parse the SDMX-style CSV the API returns into rate_date, currency, rate_nok.

    OBS_VALUE is the price in NOK of 10**UNIT_MULT units of the base currency
    (SEK is quoted per 100), so the value is scaled to one unit.
    """
    raw = pd.read_csv(io.StringIO(text), sep=";", dtype=str)
    required = {"BASE_CUR", "QUOTE_CUR", "UNIT_MULT", "TIME_PERIOD", "OBS_VALUE"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"unexpected Norges Bank CSV layout, missing {sorted(missing)}")
    raw = raw[raw["QUOTE_CUR"] == "NOK"]
    out = pd.DataFrame(
        {
            "rate_date": pd.to_datetime(raw["TIME_PERIOD"]).dt.date,
            "currency": raw["BASE_CUR"].str.upper(),
            "rate_nok": raw["OBS_VALUE"].astype(float) / (10.0 ** raw["UNIT_MULT"].astype(int)),
        }
    )
    out = out.dropna().sort_values(["currency", "rate_date"]).reset_index(drop=True)
    return out


def read_cache(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, dtype={"currency": str})
    frame["rate_date"] = pd.to_datetime(frame["rate_date"]).dt.date
    frame["rate_nok"] = frame["rate_nok"].astype(float)
    return frame[CACHE_COLUMNS].sort_values(["currency", "rate_date"]).reset_index(drop=True)


def write_cache(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame[CACHE_COLUMNS].sort_values(["currency", "rate_date"]).to_csv(
        path, index=False, lineterminator="\n", float_format="%.6f"
    )


def fill_calendar(rates: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    """Return one row per calendar day and currency between start and end.

    Days without a published rate take the last published rate before them.
    ``is_published`` tells the two apart. Raises if a currency has no rate on
    or before ``start``, because nothing could be carried forward.
    """
    days = pd.date_range(start, end, freq="D").date
    pieces = []
    for currency, group in rates.groupby("currency"):
        group = group.sort_values("rate_date")
        if group["rate_date"].min() > start:
            raise ValueError(f"{currency}: no published rate on or before {start}")
        published = group.set_index("rate_date")["rate_nok"]
        on_calendar = published.reindex(days)
        filled = published.reindex(sorted(set(published.index) | set(days))).ffill().reindex(days)
        pieces.append(
            pd.DataFrame(
                {
                    "rate_date": days,
                    "currency": currency,
                    "rate_nok": filled.to_numpy(),
                    "is_published": on_calendar.notna().to_numpy(),
                }
            )
        )
    out = pd.concat(pieces, ignore_index=True)
    return out.sort_values(["currency", "rate_date"]).reset_index(drop=True)


def previous_business_window(start: date, margin_days: int = 14) -> date:
    """Start of the fetch window: enough days before the period to have a rate to carry forward."""
    return start - timedelta(days=margin_days)
