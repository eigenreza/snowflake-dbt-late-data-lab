"""Run the loading-strategy experiment.

For each scenario: replay the bank (once), then run every grid point through
the replay cycles, scoring the KPIs after each cycle and recording what each
dbt node did. Results are written as Parquet after every strategy, so an
interrupted run keeps what it has and ``--resume`` continues from there.

Usage:
    python -m experiment.run [--target dev|snowflake] [--scenarios mild,heavy]
                             [--strategies merge_w3,full_refresh] [--cycles N] [--customers N] [--resume]

The reconciliation of the old nightly report with its rewrite is run at the
end of each scenario on the full-refresh warehouse.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

from common.paths import Paths
from experiment import replay
from experiment.config import KPIS, RESULTS_DIR, STRATEGY_GRID, run_key, strategy_label
from experiment.strategy_run import run_strategy, warehouse_path
from generator.scenarios import Scenario, get_scenario
from reconcile import reconcile as rec

FILES = ("kpi_errors", "run_costs", "cycles", "lateness_floor")

# The models whose work depends on the strategy; everything else is rebuilt the same way every cycle.
STRATEGY_MODELS = ("fct_transactions", "fct_account_balance_daily", "mart_monthly_category_region")


def _read(out_dir: Path, name: str) -> pd.DataFrame:
    path = out_dir / f"{name}.parquet"
    return pd.read_parquet(path) if path.exists() else pd.DataFrame()


def _write(out_dir: Path, name: str, frame: pd.DataFrame) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(out_dir / f"{name}.parquet", index=False)


def _drop(frame: pd.DataFrame, scenario: str, key: str | None = None) -> pd.DataFrame:
    if frame.empty:
        return frame
    mask = frame["scenario"] == scenario
    if key is not None and "run_key" in frame:
        mask &= frame["run_key"] == key
    return frame[~mask]


def summarize(errors: pd.DataFrame, costs: pd.DataFrame) -> pd.DataFrame:
    """One row per scenario and grid point: average and final error per KPI, and the cost totals."""
    if errors.empty:
        return pd.DataFrame()
    after_initial = errors[errors["cycle"] > 0]
    rows = []
    for (scenario, key), group in after_initial.groupby(["scenario", "run_key"], sort=False):
        strategy = group["strategy"].iloc[0]
        lookback = int(group["lookback_days"].iloc[0])
        row = dict(scenario=scenario, run_key=key, strategy=strategy, lookback_days=lookback,
                   label=strategy_label(strategy, lookback), cycles=int(group["cycle"].max()))
        for kind in KPIS:
            k = group[group["kpi"] == kind].sort_values("cycle")
            row[f"{kind}__mean_wrong_share"] = float(k["wrong_share"].mean())
            row[f"{kind}__final_wrong_share"] = float(k["wrong_share"].iloc[-1])
            row[f"{kind}__mean_mae_nok"] = float(k["mae_nok"].mean())
            row[f"{kind}__final_mae_nok"] = float(k["mae_nok"].iloc[-1])
        row["mean_wrong_share"] = float(sum(row[f"{k}__mean_wrong_share"] for k in KPIS) / len(KPIS))
        row["final_wrong_share"] = float(sum(row[f"{k}__final_wrong_share"] for k in KPIS) / len(KPIS))
        mine = costs[(costs["scenario"] == scenario) & (costs["run_key"] == key) & (costs["cycle"] > 0)]
        totals = mine[mine["node"] == "cycle_total"]
        strategy_models = mine[mine["node"].isin(STRATEGY_MODELS)]
        row["dbt_execution_seconds"] = float(totals["execution_seconds"].sum())
        row["incremental_model_seconds"] = float(strategy_models["execution_seconds"].sum())
        row["dbt_wall_seconds"] = float(totals["wall_dbt_seconds"].sum())
        row["load_wall_seconds"] = float(totals["wall_load_seconds"].sum())
        row["rows_in_window_total"] = int(totals["rows_in_window"].sum())
        rows.append(row)
    return pd.DataFrame(rows)


def run_experiment(
    target: str,
    scenarios: list[Scenario],
    grid: list[tuple[str, int]],
    out_dir: Path,
    cycles: int | None = None,
    resume: bool = False,
    root: Path | None = None,
    log=print,
) -> pd.DataFrame:
    errors, costs = _read(out_dir, "kpi_errors"), _read(out_dir, "run_costs")
    cycle_frames, floors = _read(out_dir, "cycles"), _read(out_dir, "lateness_floor")
    values = {k: _read(out_dir, f"values_{k}") for k in KPIS}
    cycle_list = None if cycles is None else list(range(cycles + 1))

    for scn in scenarios:
        paths = Paths(scn.name) if root is None else Paths(scn.name, root)
        started = time.perf_counter()
        if not replay.replay_is_ready(scn, paths):
            replay.replay_scenario(scn, paths, log)
        else:
            log(f"[{scn.name}] replay already in place, reusing it")
        cycle_frames = pd.concat([_drop(cycle_frames, scn.name), replay.cycle_stats(scn, paths)], ignore_index=True)
        floors = pd.concat([_drop(floors, scn.name), replay.lateness_floor(scn, paths, cycle_list)], ignore_index=True)
        _write(out_dir, "cycles", cycle_frames)
        _write(out_dir, "lateness_floor", floors)
        truth_frames = replay.truth_values(scn, paths, cycle_list)
        for kind in KPIS:
            kept = values[kind]
            if not kept.empty:
                kept = kept[~((kept["scenario"] == scn.name) & kept["run_key"].isin(["truth", "truth_final"]))]
            values[kind] = pd.concat([kept, truth_frames[kind]], ignore_index=True)
            _write(out_dir, f"values_{kind}", values[kind])

        for strategy, lookback in grid:
            key = run_key(strategy, lookback)
            done = (not errors.empty) and ((errors["scenario"] == scn.name) & (errors["run_key"] == key)).any()
            if resume and done:
                log(f"[{scn.name}] {key}: already in results, skipped")
                continue
            result = run_strategy(scn, paths, strategy, lookback, target, cycles, log)
            errors = pd.concat([_drop(errors, scn.name, key), pd.DataFrame(result.errors)], ignore_index=True)
            costs = pd.concat([_drop(costs, scn.name, key), pd.DataFrame(result.costs)], ignore_index=True)
            _write(out_dir, "kpi_errors", errors)
            _write(out_dir, "run_costs", costs)
            for kind in KPIS:
                values[kind] = pd.concat([_drop(values[kind], scn.name, key)] + result.values[kind], ignore_index=True)
                _write(out_dir, f"values_{kind}", values[kind])

        if target == "dev" and any(s == "full_refresh" for s, _ in grid):
            warehouse = warehouse_path(paths, scn, "full_refresh", 0)
            last_day = scn.replay_dates[cycles - 1] if cycles else scn.last_day
            for mode in ("nightly", "full"):
                outcome = rec.reconcile(paths, "dev", mode, last_day, warehouse)
                path = out_dir / f"reconciliation_{scn.name}_{mode}.json"
                path.write_text(__import__("json").dumps(rec.asdict(outcome), indent=2), encoding="utf-8")
                log(outcome.summary())
        log(f"[{scn.name}] done in {(time.perf_counter() - started) / 60:.1f} min")

    summary = summarize(errors, costs)
    _write(out_dir, "summary", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="dev", choices=["dev", "snowflake"])
    parser.add_argument("--scenarios", default="mild,heavy")
    parser.add_argument("--strategies", default=None, help="comma-separated run keys, default: the whole grid")
    parser.add_argument("--cycles", type=int, default=None, help="replay only the first N cycles")
    parser.add_argument("--customers", type=int, default=None, help="smaller bank for quick runs")
    parser.add_argument("--resume", action="store_true", help="skip grid points already in the results")
    parser.add_argument("--out", type=Path, default=None, help="results folder (default experiment/results/<target>)")
    args = parser.parse_args(argv)

    scenarios = [get_scenario(name.strip()).with_size(n_customers=args.customers) for name in args.scenarios.split(",")]
    grid = STRATEGY_GRID
    if args.strategies:
        wanted = {s.strip() for s in args.strategies.split(",")}
        grid = [g for g in STRATEGY_GRID if run_key(*g) in wanted]
        unknown = wanted - {run_key(*g) for g in grid}
        if unknown:
            parser.error(f"unknown strategies: {', '.join(sorted(unknown))}")
    out_dir = args.out or (RESULTS_DIR / args.target)

    def log(message: str) -> None:
        print(f"{datetime.now():%H:%M:%S} {message}", flush=True)

    summary = run_experiment(args.target, scenarios, grid, out_dir, args.cycles, args.resume, log=log)
    if not summary.empty:
        cols = ["scenario", "label", "mean_wrong_share", "final_wrong_share", "incremental_model_seconds", "dbt_execution_seconds", "rows_in_window_total"]
        print(summary[cols].to_string(index=False, float_format=lambda v: f"{v:,.4f}"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
