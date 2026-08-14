"""Replay a scenario: rebuild the bank, extract every day, run the old job every night.

The replay is independent of the loading strategy, so it is done once per
scenario and every strategy run loads the same landed files. The old nightly
job runs after each day's extract, exactly as it would in production, which
is what the nightly-mode reconciliation later compares with the rewrite.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from common import legacy_format as legacy
from common.paths import Paths, day_folder
from experiment import kpis
from experiment.config import KPIS, eval_window
from extract import extract_core
from generator import core_db, truth
from generator.advance_day import advance, applied_days
from generator.build_core_bank import build
from generator.scenarios import Scenario
from legacy.run_nightly_report import run_nightly_report


def replay_is_ready(scn: Scenario, paths: Paths) -> bool:
    """True when the core system, the truth and every day's files are already in place."""
    if not (paths.core_db.exists() and paths.truth_db.exists()):
        return False
    try:
        con = truth.connect(paths.truth_db, read_only=True)
        try:
            row = truth.read_scenario(con)
        finally:
            con.close()
    except duckdb.Error:
        return False
    if row["n_customers"] != scn.n_customers or row["replay_days"] != scn.replay_days or row["seed"] != scn.seed:
        return False
    if applied_days(paths) != scn.replay_dates:
        return False
    for day in [scn.initial_extract_date] + scn.replay_dates:
        folder = day_folder(paths.landing_dir, day)
        if not all((folder / legacy.file_name(entity, day)).exists() for entity in legacy.ENTITIES):
            return False
    return True


def replay_scenario(scn: Scenario, paths: Paths, log: Callable[[str], None] = print) -> None:
    """Rebuild the bank and produce every day's extract, running the old job nightly."""
    log(f"[{scn.name}] building the bank ({scn.n_customers} customers, {scn.replay_days} replay days)")
    build(scn, paths, verbose=False)
    if paths.landing_dir.exists():
        for folder in paths.landing_dir.iterdir():
            for file in folder.glob("*"):
                file.unlink()
            folder.rmdir()
    t0 = scn.initial_extract_date
    result = extract_core.run_extract(paths.core_db, paths.landing_dir, t0, duplicate_rate=scn.duplicate_rate)
    run_nightly_report(paths.core_db, t0, full=True)
    log(f"[{scn.name}] initial extract {t0}: {result.rows['transactions']} transaction lines")
    for day in scn.replay_dates:
        advance(paths, day)
        result = extract_core.run_extract(paths.core_db, paths.landing_dir, day, duplicate_rate=scn.duplicate_rate)
        run_nightly_report(paths.core_db, day)
        log(f"[{scn.name}] day {day}: {result.rows['transactions']} transaction lines")


def cycle_stats(scn: Scenario, paths: Paths) -> pd.DataFrame:
    """What arrived in the core system on each replay day, from the truth."""
    con = truth.connect(paths.truth_db, read_only=True)
    try:
        frame = con.execute(
            f"""
            SELECT CAST(arrival_ts AS DATE) AS cycle_day,
                   count(*) AS rows_arrived,
                   sum(CASE WHEN change_type = 'NEW' AND event_date < CAST(arrival_ts AS DATE) THEN 1 ELSE 0 END) AS late_rows,
                   sum(CASE WHEN change_type <> 'NEW' THEN 1 ELSE 0 END) AS correction_rows,
                   sum(CASE WHEN change_type = 'NEW' AND event_date < CAST(arrival_ts AS DATE) THEN 1 ELSE 0 END) * 1.0 / count(*) AS late_share
            FROM {truth.SCHEMA}.transaction_versions
            WHERE CAST(arrival_ts AS DATE) > ? AND CAST(arrival_ts AS DATE) <= ?
            GROUP BY 1 ORDER BY 1
            """,
            [scn.initial_extract_date, scn.last_day],
        ).fetchdf()
    finally:
        con.close()
    frame["cycle_day"] = pd.to_datetime(frame["cycle_day"]).dt.date
    frame.insert(0, "cycle", frame["cycle_day"].map({d: i for i, d in enumerate(scn.replay_dates, start=1)}))
    frame.insert(0, "scenario", scn.name)
    return frame


def truth_values(scn: Scenario, paths: Paths, cycles: list[int] | None = None) -> dict[str, pd.DataFrame]:
    """Per cycle and KPI: the truth cells as of that day, plus the final truth under run_key 'truth_final'."""
    from experiment.config import VALUE_ACCOUNT_FILTER, values_window

    days = [scn.initial_extract_date] + scn.replay_dates
    out: dict[str, list[pd.DataFrame]] = {k: [] for k in KPIS}
    for cycle, day in enumerate(days):
        if cycles is not None and cycle not in cycles:
            continue
        start, end = values_window(scn.initial_extract_date, scn.period_start, day)
        for kind in KPIS:
            account_filter = VALUE_ACCOUNT_FILTER if kind == "daily_balance" else ""
            for key, as_of in (("truth", core_db.end_of_day(day)), ("truth_final", truth.FINAL)):
                frame = kpis.truth_values(paths.truth_db, kind, as_of, start, end, account_filter)
                frame.insert(0, "cycle_day", day)
                frame.insert(0, "cycle", cycle)
                frame.insert(0, "run_key", key)
                frame.insert(0, "scenario", scn.name)
                out[kind].append(frame)
    return {k: pd.concat(v, ignore_index=True) if v else pd.DataFrame() for k, v in out.items()}


def lateness_floor(scn: Scenario, paths: Paths, cycles: list[int] | None = None) -> pd.DataFrame:
    """Per cycle and KPI: the error of the as-of truth against the final truth."""
    days = [scn.initial_extract_date] + scn.replay_dates
    rows = []
    for cycle, day in enumerate(days):
        if cycles is not None and cycle not in cycles:
            continue
        start, end = eval_window(scn.initial_extract_date, scn.period_start, day)
        for kind in KPIS:
            result = kpis.score_truth_against_final(paths.truth_db, kind, core_db.end_of_day(day), start, end)
            rows.append(dict(scenario=scn.name, cycle=cycle, cycle_day=day, **result))
    return pd.DataFrame(rows)
