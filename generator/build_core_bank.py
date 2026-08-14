"""Build the synthetic bank: ground truth first, then the core system as of the initial extract date.

Usage:
    python -m generator.build_core_bank [--scenario mild|heavy] [--customers N] [--replay-days N]

Needs the FX cache in data/ (run ``python -m extract.extract_fx`` once).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from common import fx
from common.paths import Paths
from generator import core_db, truth
from generator.bank import BankEntities, make_entities
from generator.ledger import make_transactions
from generator.scenarios import Scenario, get_scenario


def generate(scn: Scenario, fx_published: pd.DataFrame) -> tuple[BankEntities, pd.DataFrame, pd.DataFrame]:
    """Produce every entity version and transaction version for a scenario."""
    fx_daily = fx.fill_calendar(fx_published, scn.period_start, scn.last_day + timedelta(days=31))
    rng = np.random.default_rng(scn.seed)
    entities = make_entities(scn, rng)
    transactions = make_transactions(scn, rng, entities, fx_daily)
    return entities, transactions, fx_daily


def build(scn: Scenario, paths: Paths, verbose: bool = True) -> dict[str, int]:
    if not paths.fx_cache.exists():
        raise SystemExit(f"FX cache {paths.fx_cache} is missing; run: python -m extract.extract_fx")
    fx_published = fx.read_cache(paths.fx_cache)

    t0 = time.perf_counter()
    entities, transactions, fx_daily = generate(scn, fx_published)
    t1 = time.perf_counter()
    truth.write_truth_db(paths.truth_db, scn, entities, transactions, fx_daily)
    counts = core_db.build_initial_state(paths.core_db, paths.truth_db, scn.initial_extract_date, fx_published)
    t2 = time.perf_counter()

    if verbose:
        n_new = int((transactions["change_type"] == "NEW").sum())
        print(f"scenario {scn.name} (seed {scn.seed}): {scn.n_customers} customers, "
              f"{len(entities.accounts_initial)} accounts, {len(entities.merchant_versions)} merchants")
        print(f"  transactions: {n_new} booked, {len(transactions)} versions in total")
        late = transactions[(transactions["change_type"] == "NEW") & (transactions["arrival_lag_days"] > 0)]
        declined = len(entities.declined_attempts) if entities.declined_attempts is not None else 0
        print(f"  late (lag > 0 days): {len(late) / n_new:.1%}, corrections: "
              f"{int((transactions['change_type'] != 'NEW').sum())} rows, declined debits: {declined} "
              f"({declined / (declined + n_new):.1%} of attempts)")
        print(f"  core system at {scn.initial_extract_date}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
        print(f"  built in {t1 - t0:.1f}s, written in {t2 - t1:.1f}s -> {paths.core_db}")
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="mild")
    parser.add_argument("--customers", type=int, default=None, help="override the number of customers")
    parser.add_argument("--replay-days", type=int, default=None, help="override the number of replay days")
    parser.add_argument("--root", type=Path, default=None, help="repository root (tests only)")
    args = parser.parse_args(argv)

    scn = get_scenario(args.scenario).with_size(args.customers, args.replay_days)
    paths = Paths(scn.name) if args.root is None else Paths(scn.name, args.root)
    build(scn, paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
