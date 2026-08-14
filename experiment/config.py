"""The experiment grid and the few constants the harness shares."""

from __future__ import annotations

from datetime import date, timedelta

from common.paths import REPO_ROOT

# (strategy, lookback window W in days)
STRATEGY_GRID: list[tuple[str, int]] = [
    ("full_refresh", 0),
    ("append_only", 0),
    ("merge", 0),
    ("merge", 1),
    ("merge", 3),
    ("merge", 7),
    ("delete_insert", 0),
    ("delete_insert", 1),
    ("delete_insert", 3),
    ("delete_insert", 7),
]

KPIS = ("daily_balance", "monthly_category_region", "fx_daily")

# KPI cells are compared for dates from this many days before the initial
# extract up to the cycle day: wide enough to hold every late or corrected row.
EVAL_LOOKBACK_DAYS = 75

# A cell is wrong when it differs by more than half a cent, or is missing.
TOLERANCE_NOK = 0.005

# On Snowflake the balance KPI is scored on one account in ten, chosen by the
# last digit of the account number, so the comparison stays small.
BALANCE_SAMPLE_FILTER = "right(account_no, 1) = '7'"

# Per-cycle KPI values are kept for the demo: every monthly and FX cell, and
# the daily balance of these accounts (account numbers end in the sequence).
VALUE_ACCOUNT_SUFFIXES = ("0007", "0042", "0113", "0256", "0333", "0404", "0512", "0600", "0731", "0888",
                          "1024", "1500", "2048", "2500", "3000", "3333", "4000", "4444", "4800", "5000")
VALUE_ACCOUNT_FILTER = "right(account_no, 4) IN (" + ", ".join(f"'{s}'" for s in VALUE_ACCOUNT_SUFFIXES) + ")"
VALUE_LOOKBACK_DAYS = 20

# Snowflake billing assumptions for the credit estimate.
# Generation 2 warehouses, as created by default on new accounts.
CREDITS_PER_HOUR = {"X-Small": 1.35, "XSMALL": 1.35, "Small": 2.7}
MIN_BILLED_SECONDS = 60

RESULTS_DIR = REPO_ROOT / "experiment" / "results"


def run_key(strategy: str, lookback: int) -> str:
    """Short identifier of a grid point, used in file names, schema names and query tags."""
    return f"{strategy}_w{lookback}" if strategy in ("merge", "delete_insert") else strategy


def strategy_label(strategy: str, lookback: int) -> str:
    names = {
        "full_refresh": "full refresh",
        "append_only": "append-only",
        "merge": f"merge (W={lookback})",
        "delete_insert": f"delete+insert (W={lookback})",
    }
    return names[strategy]


def eval_window(initial_extract_date: date, period_start: date, cycle_day: date) -> tuple[date, date]:
    start = max(initial_extract_date - timedelta(days=EVAL_LOOKBACK_DAYS), period_start)
    return start, cycle_day


def values_window(initial_extract_date: date, period_start: date, cycle_day: date) -> tuple[date, date]:
    start = max(initial_extract_date - timedelta(days=VALUE_LOOKBACK_DAYS), period_start)
    return start, cycle_day


def query_tag(scenario: str, strategy: str, lookback: int, cycle: int) -> str:
    return f"ldl:{scenario}:{run_key(strategy, lookback)}:c{cycle}"
