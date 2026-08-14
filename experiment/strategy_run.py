"""Run one loading strategy through every replay cycle and score it.

A strategy run owns its warehouse: on DuckDB a file of its own, on Snowflake a
schema suffix on the shared database (RAW is shared, since the landed files
are the same for every strategy). Each cycle loads that day's files, runs the
whole dbt graph with the strategy's variables and the cycle's query tag,
records what every node did, and scores the three KPIs against the truth as
of that day.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

import duckdb
import pandas as pd

from common.dbt_runner import DbtError, NodeResult, run_dbt
from common.paths import Paths
from experiment import kpis
from experiment.config import (
    BALANCE_SAMPLE_FILTER, KPIS, VALUE_ACCOUNT_FILTER, eval_window, query_tag, run_key, values_window,
)
from generator import core_db
from generator.scenarios import Scenario
from load import load_duckdb


@dataclass
class StrategyResults:
    errors: list[dict] = field(default_factory=list)
    costs: list[dict] = field(default_factory=list)
    values: dict[str, list[pd.DataFrame]] = field(default_factory=lambda: {k: [] for k in KPIS})


def warehouse_path(paths: Paths, scn: Scenario, strategy: str, lookback: int) -> Path:
    return paths.root / "data" / "experiment" / scn.name / f"{run_key(strategy, lookback)}.duckdb"


def _remove(path: Path) -> None:
    for candidate in (path, path.with_suffix(path.suffix + ".wal")):
        if candidate.exists():
            candidate.unlink()


def _window_rows_sql(strategy: str, lookback: int, day: date, staging_schema: str) -> str:
    """Rows of staging the strategy had to look at in this cycle."""
    if strategy == "full_refresh":
        predicate = "1 = 1"
    elif strategy == "append_only":
        predicate = f"event_date >= DATE '{day.isoformat()}'"
    else:
        predicate = f"event_date >= DATE '{day.isoformat()}' - INTERVAL '{lookback}' DAY"
    return f"SELECT count(*) FROM {staging_schema}.stg_transactions WHERE {predicate}"


def _duckdb_scalar(path: Path, sql: str):
    con = duckdb.connect(str(path))
    try:
        return con.execute(sql).fetchall()[0][0]
    finally:
        con.close()


def _cost_rows(nodes: list[NodeResult], base: dict) -> list[dict]:
    rows = []
    for n in nodes:
        if n.resource_type not in ("model", "snapshot", "seed"):
            continue
        rows.append(dict(**base, node=n.name, resource_type=n.resource_type, status=n.status,
                         execution_seconds=n.execution_time, rows_affected=n.rows_affected))
    return rows


def run_strategy(
    scn: Scenario,
    paths: Paths,
    strategy: str,
    lookback: int,
    target: str = "dev",
    cycles: int | None = None,
    log: Callable[[str], None] = print,
) -> StrategyResults:
    key = run_key(strategy, lookback)
    days = [scn.initial_extract_date] + scn.replay_dates
    if cycles is not None:
        days = days[: cycles + 1]
    results = StrategyResults()

    if target == "dev":
        warehouse = warehouse_path(paths, scn, strategy, lookback)
        _remove(warehouse)
        warehouse.parent.mkdir(parents=True, exist_ok=True)
        schema_suffix, staging_schema, marts_schema = "", "staging", "marts"
    else:
        warehouse = None
        schema_suffix = key
        staging_schema, marts_schema = f"staging__{key}", f"marts__{key}"

    for cycle, day in enumerate(days):
        tag = query_tag(scn.name, strategy, lookback, cycle)
        t0 = time.perf_counter()
        if target == "dev":
            records = load_duckdb.load_all(warehouse, paths.landing_dir, paths.fx_cache, day=day, include_fx=(cycle == 0))
        else:
            from load import load_snowflake

            # RAW is shared on Snowflake, so every strategy starts from an empty RAW and loads day by day;
            # its own STAGING and MARTS schemas are dropped too, so the snapshot starts from scratch.
            if cycle == 0:
                _drop_snowflake_schemas(staging_schema, marts_schema)
            records = load_snowflake.load_all(
                paths.landing_dir, paths.fx_cache, day=day, include_fx=(cycle == 0), reset=(cycle == 0)
            )
        failed = [r for r in records if r.status == "FAILED"]
        if failed:
            raise RuntimeError(f"load failed for {failed[0].file_name}: {failed[0].error_message}")
        t1 = time.perf_counter()

        args = ["build", "--exclude", "resource_type:test"]
        if cycle == 0 or strategy == "full_refresh":
            args.append("--full-refresh")
        dbt_kwargs = dict(
            target=target,
            duckdb_path=warehouse,
            query_tag=tag,
            vars=dict(load_strategy=strategy, lookback_days=lookback, as_of_date=day.isoformat(),
                      query_tag=tag, schema_suffix=schema_suffix or "''"),
            work_tag=scn.name,
        )
        try:
            nodes = run_dbt(args, **dbt_kwargs)
        except DbtError as exc:
            # A transient file error on the dbt artifacts is worth one retry; a model error is not.
            if "Errno" not in str(exc):
                raise
            log(f"[{scn.name}] {key} cycle {cycle}: dbt failed ({exc}); retrying once")
            time.sleep(2)
            nodes = run_dbt(args, **dbt_kwargs)
        t2 = time.perf_counter()

        base = dict(scenario=scn.name, strategy=strategy, lookback_days=lookback, run_key=key, cycle=cycle,
                    cycle_day=day, query_tag=tag, target=target)
        results.costs.extend(_cost_rows(nodes, base))
        if target == "dev":
            rows_in_window = _duckdb_scalar(warehouse, _window_rows_sql(strategy, lookback, day, staging_schema))
            fact_rows = _duckdb_scalar(warehouse, f"SELECT count(*) FROM {marts_schema}.fct_transactions")
        else:
            rows_in_window, fact_rows = _snowflake_counts(strategy, lookback, day, staging_schema, marts_schema)
        results.costs.append(dict(**base, node="cycle_total", resource_type="cycle", status="success",
                                  execution_seconds=sum(n.execution_time for n in nodes if n.resource_type in ("model", "snapshot", "seed")),
                                  rows_affected=None, wall_load_seconds=t1 - t0, wall_dbt_seconds=t2 - t1,
                                  rows_in_window=rows_in_window, fact_rows=fact_rows))

        start, end = eval_window(scn.initial_extract_date, scn.period_start, day)
        vstart, vend = values_window(scn.initial_extract_date, scn.period_start, day)
        as_of = core_db.end_of_day(day)
        for kind in KPIS:
            value_filter = VALUE_ACCOUNT_FILTER if kind == "daily_balance" else ""
            if target == "dev":
                scored = kpis.score_local_warehouse(warehouse, paths.truth_db, kind, as_of, start, end, marts_schema)
                values = kpis.local_values(warehouse, kind, marts_schema, vstart, vend, value_filter)
            else:
                sample_filter = BALANCE_SAMPLE_FILTER if kind == "daily_balance" else ""
                frame = _snowflake_kpi(kind, marts_schema, start, end, sample_filter)
                scored = kpis.score_frame(paths.truth_db, kind, as_of, start, end, frame, sample_filter)
                values = kpis._dated(_snowflake_kpi(kind, marts_schema, vstart, vend, value_filter), kind)
            results.errors.append(dict(**base, **scored))
            values.insert(0, "cycle_day", day)
            values.insert(0, "cycle", cycle)
            values.insert(0, "run_key", key)
            values.insert(0, "scenario", scn.name)
            results.values[kind].append(values)
        t3 = time.perf_counter()
        worst = max(e["wrong_share"] for e in results.errors[-len(KPIS):])
        log(f"[{scn.name}] {key:<18} cycle {cycle:>2} {day}: load {t1 - t0:4.1f}s dbt {t2 - t1:5.1f}s score {t3 - t2:4.1f}s"
            f"  worst wrong share {worst:6.2%}")
    if target != "dev":
        close_scoring_connection()
    return results


_SCORING_CONNECTION = None


def _snowflake_cursor(query_tag_value: str = "ldl:score"):
    """One scoring connection per process, opened on first use; the queries it runs are tagged ldl:score."""
    global _SCORING_CONNECTION
    if _SCORING_CONNECTION is None or _SCORING_CONNECTION.is_closed():
        from load.load_snowflake import connect

        _SCORING_CONNECTION = connect(role="TRANSFORMER", warehouse="TRANSFORM_WH", query_tag=query_tag_value)
    return _SCORING_CONNECTION, _SCORING_CONNECTION.cursor()


def close_scoring_connection() -> None:
    global _SCORING_CONNECTION
    if _SCORING_CONNECTION is not None:
        try:
            _SCORING_CONNECTION.close()
        finally:
            _SCORING_CONNECTION = None


def _drop_snowflake_schemas(*schemas: str) -> None:
    """Remove a strategy's own schemas on the shared database so its run starts from nothing."""
    _, cur = _snowflake_cursor()
    for schema in schemas:
        cur.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")


def _snowflake_kpi(kind: str, marts_schema: str, start: date, end: date, account_filter: str = "") -> pd.DataFrame:
    _, cur = _snowflake_cursor()
    cur.execute(kpis.warehouse_kpi_sql(kind, marts_schema, start, end, account_filter))
    return pd.DataFrame(cur.fetchall(), columns=[d[0].lower() for d in cur.description])


def _snowflake_counts(strategy: str, lookback: int, day: date, staging_schema: str, marts_schema: str) -> tuple[int, int]:
    _, cur = _snowflake_cursor()
    cur.execute(_window_rows_sql(strategy, lookback, day, staging_schema).replace(f"INTERVAL '{lookback}' DAY", f"INTERVAL '{lookback} DAY'"))
    rows_in_window = cur.fetchone()[0]
    cur.execute(f"SELECT count(*) FROM {marts_schema}.fct_transactions")
    fact_rows = cur.fetchone()[0]
    return int(rows_in_window), int(fact_rows)
