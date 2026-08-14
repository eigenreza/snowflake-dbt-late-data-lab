"""Run the old nightly reporting job against the core system.

The scheduler used to substitute ``&RUN_DATE`` and ``&FROM_MONTH`` into the
script and execute it statement by statement. This runner does the same.
By default the job rebuilds the current month; ``--full`` rebuilds everything,
which is how the report table was first populated.

Usage:
    python -m legacy.run_nightly_report --run-date 2024-12-15 [--scenario mild] [--full]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import duckdb

from common.paths import Paths

SCRIPT = Path(__file__).with_name("nightly_report.sql")
REPORT_TABLE = "report.monthly_category_region"


def month_start(day: date) -> date:
    return day.replace(day=1)


def render(run_date: date, from_month: date) -> str:
    text = SCRIPT.read_text(encoding="utf-8")
    return text.replace("&RUN_DATE", run_date.isoformat()).replace("&FROM_MONTH", from_month.isoformat())


def statements(sql: str) -> list[str]:
    out = []
    for raw in sql.split(";"):
        lines = [line for line in raw.splitlines() if not line.strip().startswith("--")]
        stmt = "\n".join(lines).strip()
        if stmt:
            out.append(stmt)
    return out


def run_nightly_report(core_db: Path, run_date: date, full: bool = False) -> int:
    """Execute the job and return the number of report rows afterwards."""
    from_month = date(1900, 1, 1) if full else month_start(run_date)
    con = duckdb.connect(str(core_db))
    try:
        for stmt in statements(render(run_date, from_month)):
            con.execute(stmt).fetchall()
        return con.execute(f"SELECT count(*) FROM {REPORT_TABLE}").fetchall()[0][0]
    finally:
        con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="mild")
    parser.add_argument("--run-date", type=date.fromisoformat, required=True)
    parser.add_argument("--full", action="store_true", help="rebuild every month instead of the current one")
    parser.add_argument("--root", type=Path, default=None, help="repository root (tests only)")
    args = parser.parse_args(argv)

    paths = Paths(args.scenario) if args.root is None else Paths(args.scenario, args.root)
    rows = run_nightly_report(paths.core_db, args.run_date, args.full)
    scope = "all months" if args.full else f"the month from {month_start(args.run_date)}"
    print(f"nightly report run for {args.run_date} ({scope}): {rows} rows in {REPORT_TABLE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
