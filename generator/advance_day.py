"""Move the core system forward by one day: apply everything that arrived that day.

Usage:
    python -m generator.advance_day --day 2024-12-01 [--scenario mild]
    python -m generator.advance_day --next
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from common.paths import Paths
from generator import core_db, truth


def applied_days(paths: Paths) -> list[date]:
    con = truth.connect(paths.truth_db, read_only=True)
    try:
        return [r[0] for r in con.execute("SELECT day FROM ground_truth.applied_days ORDER BY day").fetchall()]
    finally:
        con.close()


def scenario_dates(paths: Paths) -> tuple[date, date]:
    con = truth.connect(paths.truth_db, read_only=True)
    try:
        row = truth.read_scenario(con)
    finally:
        con.close()
    return row["initial_extract_date"], row["last_day"]


def next_day(paths: Paths) -> date:
    initial, _ = scenario_dates(paths)
    done = applied_days(paths)
    return (max(done) if done else initial) + timedelta(days=1)


def advance(paths: Paths, day: date) -> dict[str, int]:
    initial, last = scenario_dates(paths)
    if not initial < day <= last:
        raise SystemExit(f"{day} is outside the replay window ({initial + timedelta(days=1)} to {last})")
    done = applied_days(paths)
    expected = (max(done) if done else initial) + timedelta(days=1)
    if day in done:
        pass  # applying a day again is harmless: the upsert is idempotent
    elif day != expected:
        raise SystemExit(f"days must be applied in order; next is {expected}")
    counts = core_db.apply_day(paths.core_db, paths.truth_db, day)
    con = truth.connect(paths.truth_db)
    try:
        truth.run(
            con,
            "INSERT INTO ground_truth.applied_days VALUES (?, ?) ON CONFLICT (day) DO UPDATE SET applied_at = excluded.applied_at",
            [day, datetime.now().replace(microsecond=0)],
        )
    finally:
        con.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="mild")
    parser.add_argument("--day", type=date.fromisoformat)
    parser.add_argument("--next", action="store_true", help="the day after the last applied one")
    parser.add_argument("--root", type=Path, default=None, help="repository root (tests only)")
    args = parser.parse_args(argv)
    if bool(args.day) == args.next:
        parser.error("give exactly one of --day or --next")

    paths = Paths(args.scenario) if args.root is None else Paths(args.scenario, args.root)
    day = args.day or next_day(paths)
    counts = advance(paths, day)
    print(f"core system advanced to {day}: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
