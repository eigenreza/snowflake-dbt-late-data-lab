"""Fetch daily EUR, USD and SEK rates against NOK from Norges Bank.

The result is cached as a small CSV in ``data/`` and committed, so the
pipeline, the tests and the demo never need the network. With ``--offline``
or when the API cannot be reached, the cached file is used as it is.

Usage:
    python -m extract.extract_fx [--start 2022-12-15] [--end 2024-12-31] [--offline]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd
import requests

from common import fx
from common.paths import Paths

DEFAULT_START = date(2022, 12, 15)
DEFAULT_END = date(2024, 12, 31)


def fetch(start: date, end: date, timeout: int = 60) -> pd.DataFrame:
    response = requests.get(fx.api_url(start, end), timeout=timeout)
    response.raise_for_status()
    return fx.parse_norges_bank_csv(response.text)


def refresh_cache(cache: Path, start: date, end: date, offline: bool = False) -> tuple[pd.DataFrame, str]:
    """Return the rates and a short note on where they came from."""
    existing = fx.read_cache(cache) if cache.exists() else None
    if offline:
        if existing is None:
            raise SystemExit(f"offline and no cache at {cache}")
        return existing, "cache (offline)"
    try:
        fresh = fetch(start, end)
    except (requests.RequestException, ValueError) as exc:
        if existing is None:
            raise SystemExit(f"could not fetch rates and no cache exists: {exc}") from exc
        return existing, f"cache (fetch failed: {exc.__class__.__name__})"
    merged = fresh if existing is None else (
        pd.concat([existing, fresh]).drop_duplicates(["rate_date", "currency"], keep="last")
    )
    fx.write_cache(merged, cache)
    return fx.read_cache(cache), "Norges Bank API"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START)
    parser.add_argument("--end", type=date.fromisoformat, default=DEFAULT_END)
    parser.add_argument("--offline", action="store_true", help="use the cached file without calling the API")
    parser.add_argument("--cache", type=Path, default=Paths().fx_cache)
    args = parser.parse_args(argv)

    rates, source = refresh_cache(args.cache, args.start, args.end, args.offline)
    summary = rates.groupby("currency")["rate_date"].agg(["min", "max", "count"])
    print(f"FX rates from {source}: {len(rates)} rows in {args.cache}")
    print(summary.to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
