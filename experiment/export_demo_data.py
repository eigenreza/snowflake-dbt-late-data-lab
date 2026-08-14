"""Export what the demo needs as small Parquet files in demo/data.

Sources: the experiment results (one or more result folders), the Snowflake
cost collection when present, the full-refresh warehouse of the reference
scenario for the bank report, the truth files for the lateness figures, and
a dbt test run for the data-quality page. The demo reads only this folder.

Usage:
    python -m experiment.export_demo_data [--results experiment/results/dev ...] [--scenario mild]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from common.dbt_runner import PROJECT_DIR, run_dbt
from common.paths import REPO_ROOT, Paths
from experiment.config import KPIS, RESULTS_DIR, STRATEGY_GRID, run_key, strategy_label
from experiment.run import summarize
from experiment.strategy_run import warehouse_path
from generator import truth
from generator.scenarios import get_scenario

DEMO_DATA = REPO_ROOT / "demo" / "data"


def _concat(dirs: list[Path], name: str) -> pd.DataFrame:
    frames = [pd.read_parquet(d / f"{name}.parquet") for d in dirs if (d / f"{name}.parquet").exists()]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _write(out: Path, name: str, frame: pd.DataFrame) -> None:
    frame.to_parquet(out / f"{name}.parquet", index=False)
    print(f"  {name:<28} {len(frame):>8} rows")


def export_results(dirs: list[Path], snowflake_dir: Path, out: Path) -> tuple[pd.DataFrame, dict]:
    errors = _concat(dirs, "kpi_errors")
    costs = _concat(dirs, "run_costs")
    summary = summarize(errors, costs)
    cost_basis = {
        "column": "incremental_model_seconds",
        "label": "execution time of the three strategy-dependent models on DuckDB (seconds)",
        "engine": "duckdb",
    }

    query_costs = snowflake_dir / "query_costs.parquet"
    if query_costs.exists() and not summary.empty:
        sf = pd.read_parquet(query_costs)
        sf = sf[sf["cycle"] > 0].groupby(["scenario", "run_key"], as_index=False).agg(
            snowflake_execution_seconds=("execution_seconds", "sum"),
            snowflake_resumes=("resumes", "sum"),
            credits_execution=("credits_execution", "sum"),
            credits_with_minimum=("credits_with_minimum", "sum"),
        )
        summary = summary.merge(sf, on=["scenario", "run_key"], how="left")
        if summary["credits_with_minimum"].notna().any():
            cost_basis = {"column": "credits_with_minimum", "label": "estimated Snowflake credits (execution time plus the 60-second minimum per resume)", "engine": "snowflake"}
        metering = snowflake_dir / "warehouse_metering.parquet"
        if metering.exists():
            shutil.copy(metering, out / "warehouse_metering.parquet")
            print("  warehouse_metering           copied")

    order = {run_key(s, w): i for i, (s, w) in enumerate(STRATEGY_GRID)}
    summary["order"] = summary["run_key"].map(order)
    summary = summary.sort_values(["scenario", "order"]).drop(columns="order")
    _write(out, "summary", summary)
    _write(out, "kpi_errors", errors)
    _write(out, "lateness_floor", _concat(dirs, "lateness_floor"))
    _write(out, "cycles", _concat(dirs, "cycles"))
    totals = costs[costs["node"] == "cycle_total"].drop(columns=["resource_type", "status", "rows_affected"], errors="ignore")
    _write(out, "run_costs_cycles", totals)
    models = costs[costs["node"] != "cycle_total"].groupby(["scenario", "run_key", "node", "resource_type"], as_index=False).agg(
        execution_seconds=("execution_seconds", "sum"), cycles=("cycle", "nunique")
    )
    _write(out, "run_costs_models", models)

    categories = pd.read_csv(PROJECT_DIR / "seeds" / "merchant_categories.csv", dtype=str)
    monthly = _concat(dirs, "values_monthly_category_region")
    if not monthly.empty:
        monthly["report_month"] = pd.to_datetime(monthly["report_month"]).dt.date
        keep = monthly["report_month"] >= date(2024, 11, 1)
        drift = monthly[keep].groupby(["scenario", "run_key", "cycle", "cycle_day", "report_month", "category_code"], as_index=False).agg(
            value_nok=("value_nok", "sum"), txn_count=("txn_count", "sum")
        )
        drift = drift.merge(categories, on="category_code", how="left")
        _write(out, "drift_category", drift)

    balances = _concat(dirs, "values_daily_balance")
    if not balances.empty:
        balances["balance_date"] = pd.to_datetime(balances["balance_date"]).dt.date
        on_cycle_day = balances[balances["balance_date"] == balances["cycle_day"]]
        _write(out, "drift_account_cycles", on_cycle_day.drop(columns=["balance_date"]))
        last = balances.groupby("scenario")["cycle"].transform("max")
        _write(out, "drift_account_final", balances[balances["cycle"] == last])

    fx = _concat(dirs, "values_fx_daily")
    if not fx.empty:
        fx_drift = fx.groupby(["scenario", "run_key", "cycle", "cycle_day", "currency"], as_index=False).agg(
            amount_nok=("amount_nok", "sum"), amount_ccy=("amount_ccy", "sum"), txn_count=("txn_count", "sum")
        )
        _write(out, "drift_fx", fx_drift)

    for name in ("reconciliation_mild_nightly", "reconciliation_mild_full", "reconciliation_heavy_nightly", "reconciliation_heavy_full"):
        for d in dirs:
            if (d / f"{name}.json").exists():
                shutil.copy(d / f"{name}.json", out / f"{name}.json")
    return summary, cost_basis


def export_lateness(scenarios: list[str], out: Path) -> dict:
    rows, corrections, facts = [], [], {}
    for name in scenarios:
        paths = Paths(name)
        if not paths.truth_db.exists():
            continue
        con = truth.connect(paths.truth_db, read_only=True)
        try:
            arrival = truth.lateness_summary(con)
            arrival.insert(0, "kind", "arrival")
            corr = con.execute(
                f"""
                SELECT 'correction' AS kind, arrival_lag_days AS lag_days, count(*) AS rows,
                       count(*) * 1.0 / sum(count(*)) OVER () AS share
                FROM {truth.SCHEMA}.transaction_versions WHERE change_type IN ('UPDATE', 'REVERSAL')
                GROUP BY 1, 2 ORDER BY 2
                """
            ).fetchdf()
            kinds = truth.correction_summary(con)
            scn = truth.read_scenario(con)
            counts = con.execute(
                f"""
                SELECT count(*) FILTER (WHERE change_type = 'NEW') AS booked,
                       count(*) AS versions,
                       count(*) FILTER (WHERE change_type = 'NEW' AND arrival_lag_days > 0) * 1.0
                           / count(*) FILTER (WHERE change_type = 'NEW') AS late_share,
                       count(*) FILTER (WHERE change_type IN ('UPDATE', 'REVERSAL')) * 1.0
                           / count(*) FILTER (WHERE change_type = 'NEW') AS correction_share,
                       (SELECT count(DISTINCT customer_id) FROM {truth.SCHEMA}.customer_versions) AS customers,
                       (SELECT count(DISTINCT account_no) FROM {truth.SCHEMA}.account_versions) AS accounts,
                       (SELECT count(*) FROM {truth.SCHEMA}.merchant_versions) AS merchants,
                       (SELECT count(*) FROM {truth.SCHEMA}.customer_versions WHERE version_no > 1) AS customer_changes
                FROM {truth.SCHEMA}.transaction_versions
                """
            ).fetchdf().iloc[0]
        finally:
            con.close()
        for frame in (arrival, corr):
            frame.insert(0, "scenario", name)
            rows.append(frame)
        kinds.insert(0, "scenario", name)
        corrections.append(kinds)
        facts[name] = dict(
            seed=int(scn["seed"]), period_start=scn["period_start"].isoformat(), initial_extract_date=scn["initial_extract_date"].isoformat(),
            last_day=scn["last_day"].isoformat(), replay_days=int(scn["replay_days"]),
            customers=int(counts["customers"]), accounts=int(counts["accounts"]), merchants=int(counts["merchants"]),
            transactions_booked=int(counts["booked"]), transaction_versions=int(counts["versions"]),
            late_share=float(counts["late_share"]), correction_share=float(counts["correction_share"]),
            customer_changes=int(counts["customer_changes"]),
        )
    if rows:
        _write(out, "lateness", pd.concat(rows, ignore_index=True))
        _write(out, "corrections", pd.concat(corrections, ignore_index=True))
    return facts


def export_bank(scenario: str, out: Path) -> dict:
    """Aggregates of the star schema for the bank report, from the full-refresh warehouse of the scenario."""
    scn = get_scenario(scenario)
    warehouse = warehouse_path(Paths(scenario), scn, "full_refresh", 0)
    if not warehouse.exists():
        print(f"  bank report skipped: {warehouse} not found")
        return {}
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        q = lambda sql: con.execute(sql).fetchdf()
        monthly = q(
            """
            SELECT d.month_start, c.region, c.area, c.segment,
                   coalesce(m.category_group, f.txn_type) AS category_group,
                   coalesce(m.category_name, f.txn_type) AS category_name,
                   f.txn_type, f.currency,
                   sum(f.amount_nok) AS net_nok,
                   sum(CASE WHEN f.amount_nok < 0 THEN -f.amount_nok ELSE 0 END) AS spend_nok,
                   sum(CASE WHEN f.amount_nok > 0 THEN f.amount_nok ELSE 0 END) AS inflow_nok,
                   sum(f.amount) AS amount_ccy,
                   count(*) AS txn_count
            FROM marts.fct_transactions f
            JOIN marts.dim_date d ON d.date_day = f.event_date
            JOIN marts.dim_customer c ON c.customer_id = f.customer_id AND c.is_current
            LEFT JOIN marts.dim_merchant m ON m.merchant_id = f.merchant_id
            GROUP BY ALL
            """
        )
        customers_monthly = q(
            """
            SELECT d.month_start, c.region, c.segment, count(DISTINCT f.customer_id) AS active_customers
            FROM marts.fct_transactions f
            JOIN marts.dim_date d ON d.date_day = f.event_date
            JOIN marts.dim_customer c ON c.customer_id = f.customer_id AND c.is_current
            GROUP BY ALL
            """
        )
        balances = q(
            """
            WITH month_end AS (
                SELECT month_start, max(date_day) AS last_day FROM marts.dim_date
                WHERE date_day <= (SELECT max(balance_date) FROM marts.fct_account_balance_daily)
                GROUP BY 1
            )
            SELECT e.month_start, c.region, c.segment, a.account_type,
                   sum(b.balance_nok) AS balance_nok, avg(b.balance_nok) AS avg_balance_nok, count(*) AS accounts
            FROM marts.fct_account_balance_daily b
            JOIN month_end e ON e.last_day = b.balance_date
            JOIN marts.dim_account a ON a.account_no = b.account_no
            JOIN marts.dim_customer c ON c.customer_id = a.customer_id AND c.is_current
            GROUP BY ALL
            """
        )
        daily = q(
            """
            SELECT f.event_date, c.region,
                   sum(CASE WHEN f.amount_nok < 0 THEN -f.amount_nok ELSE 0 END) AS spend_nok,
                   sum(CASE WHEN f.amount_nok > 0 THEN f.amount_nok ELSE 0 END) AS inflow_nok,
                   count(*) AS txn_count
            FROM marts.fct_transactions f
            JOIN marts.dim_customer c ON c.customer_id = f.customer_id AND c.is_current
            WHERE f.event_date >= (SELECT max(event_date) FROM marts.fct_transactions) - INTERVAL 120 DAY
            GROUP BY ALL
            """
        )
        customers = q(
            """
            SELECT customer_id, customer_no, full_name, segment, region, area, street, postal_code, city, customer_since,
                   valid_from, valid_to, is_current, version_no, version_started_at
            FROM marts.dim_customer ORDER BY customer_id, version_no
            """
        )
        fx_rates = q("SELECT rate_date, currency, rate_nok, is_published FROM staging.int_fx_rates_daily WHERE rate_date >= DATE '2023-01-01' ORDER BY 1, 2")
        truth_monthly = None
        facts = q(
            """
            SELECT (SELECT count(*) FROM marts.fct_transactions) AS fact_rows,
                   (SELECT max(event_date) FROM marts.fct_transactions) AS as_of,
                   (SELECT count(*) FROM marts.fct_account_balance_daily) AS balance_rows,
                   (SELECT count(*) FROM marts.dim_customer) AS customer_versions,
                   (SELECT count(*) FROM marts.dim_customer WHERE version_no > 1) AS customer_changes_seen
            """
        ).iloc[0]
    finally:
        con.close()
    for frame in (monthly, customers_monthly, balances):
        frame["month_start"] = pd.to_datetime(frame["month_start"]).dt.date
    daily["event_date"] = pd.to_datetime(daily["event_date"]).dt.date
    for col in ("customer_since", "valid_from", "valid_to"):
        customers[col] = pd.to_datetime(customers[col]).dt.date
    fx_rates["rate_date"] = pd.to_datetime(fx_rates["rate_date"]).dt.date
    _write(out, "bank_monthly", monthly)
    _write(out, "bank_customers_monthly", customers_monthly)
    _write(out, "bank_balances_monthly", balances)
    _write(out, "bank_daily", daily)
    _write(out, "customers", customers)
    _write(out, "fx_rates", fx_rates)

    # Truth of the monthly report at the final cycle, for the Power BI KPI-error measures.
    tpaths = Paths(scenario)
    if tpaths.truth_db.exists():
        tcon = truth.connect(tpaths.truth_db, read_only=True)
        try:
            as_of = truth.datetime.combine(pd.Timestamp(facts["as_of"]).date(), truth.datetime.max.time())
            truth_monthly = truth.kpi_monthly_category_region(tcon, as_of, date(2023, 1, 1), pd.Timestamp(facts["as_of"]).date().replace(day=1))
        finally:
            tcon.close()
        truth_monthly["report_month"] = pd.to_datetime(truth_monthly["report_month"]).dt.date
        _write(out, "truth_monthly", truth_monthly)

    return dict(
        fact_rows=int(facts["fact_rows"]), as_of=pd.Timestamp(facts["as_of"]).date().isoformat(),
        balance_rows=int(facts["balance_rows"]), customer_versions=int(facts["customer_versions"]),
        customer_changes_seen=int(facts["customer_changes_seen"]),
    )


def export_dbt_tests(scenario: str, out: Path) -> dict:
    scn = get_scenario(scenario)
    warehouse = warehouse_path(Paths(scenario), scn, "full_refresh", 0)
    if not warehouse.exists():
        return {}
    nodes = run_dbt(["test"], duckdb_path=warehouse, work_tag="export", check=False)
    frame = pd.DataFrame([dict(test=n.name, status=n.status, execution_seconds=n.execution_time, message=n.message) for n in nodes if n.resource_type == "test"])
    _write(out, "dbt_tests", frame)
    return dict(tests=len(frame), passed=int((frame["status"] == "pass").sum()) if len(frame) else 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--results", type=Path, nargs="+", default=[RESULTS_DIR / "dev"])
    parser.add_argument("--snowflake-results", type=Path, default=RESULTS_DIR / "snowflake")
    parser.add_argument("--scenario", default="mild", help="scenario whose warehouse feeds the bank report")
    parser.add_argument("--out", type=Path, default=DEMO_DATA)
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    print(f"exporting to {args.out}")
    summary, cost_basis = export_results(args.results, args.snowflake_results, args.out)
    scenarios = sorted(summary["scenario"].unique()) if not summary.empty else ["mild", "heavy"]
    facts = export_lateness(scenarios, args.out)
    bank = export_bank(args.scenario, args.out)
    tests = export_dbt_tests(args.scenario, args.out)

    manifest = dict(
        scenarios=facts,
        bank_scenario=args.scenario,
        bank=bank,
        dbt_tests=tests,
        cost_basis=cost_basis,
        kpis=list(KPIS),
        strategies=[dict(run_key=run_key(s, w), strategy=s, lookback_days=w, label=strategy_label(s, w)) for s, w in STRATEGY_GRID],
        tolerance_nok=0.005,
        repo_url="https://github.com/eigenreza/snowflake-dbt-late-data-lab",
    )
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    total = sum(p.stat().st_size for p in args.out.glob("*")) / 1e6
    print(f"demo data: {total:.1f} MB in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
