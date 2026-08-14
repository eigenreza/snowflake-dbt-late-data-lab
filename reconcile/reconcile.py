"""Compare the old nightly report with the dbt model that replaces it.

Two comparisons are made, each on the same data:

* ``full``: the old job is run with a full rebuild on the core system as it
  stands, and compared with ``mart_monthly_category_region``. This proves the
  rewrite reproduces the old logic.
* ``nightly``: the report table as the old job maintained it night after
  night during the replay is compared with the mart. This shows what the old
  job's nightly window did to late rows.

Row counts, sums of value and count per key, and every differing key are
reported, and the result is written as JSON for the docs and the demo.

Usage:
    python -m reconcile.reconcile [--target dev|snowflake] [--mode full|nightly|both] [--run-date YYYY-MM-DD]

Run it after the latest extract day has been loaded and built, and before the
core system is advanced again, so both sides see the same data.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd

from common.paths import Paths
from extract.extract_core import CONTROL_TABLE
from legacy.run_nightly_report import REPORT_TABLE, run_nightly_report

KEY = ["report_month", "category_code", "region"]
TOLERANCE = 0.005
NIGHTLY_COPY = REPORT_TABLE + "_nightly"


@dataclass
class Reconciliation:
    mode: str
    run_date: str
    old_rows: int
    new_rows: int
    keys_in_both: int
    keys_only_in_old: int
    keys_only_in_new: int
    value_sum_old: float
    value_sum_new: float
    count_sum_old: int
    count_sum_new: int
    keys_with_value_difference: int
    keys_with_count_difference: int
    max_abs_value_difference: float
    matches: bool
    differing_keys: list[dict] = field(default_factory=list)
    differing_months: list[str] = field(default_factory=list)
    month_totals: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"reconciliation ({self.mode}) at {self.run_date}: {'MATCH' if self.matches else 'DIFFERENCES FOUND'}",
            f"  rows            old {self.old_rows:>8}   new {self.new_rows:>8}",
            f"  value_nok sum   old {self.value_sum_old:>16,.2f}   new {self.value_sum_new:>16,.2f}",
            f"  txn_count sum   old {self.count_sum_old:>8}   new {self.count_sum_new:>8}",
            f"  keys: {self.keys_in_both} in both, {self.keys_only_in_old} only old, {self.keys_only_in_new} only new, "
            f"{self.keys_with_value_difference} with a value difference (max {self.max_abs_value_difference:,.2f})",
        ]
        if self.month_totals:
            lines.append("  months whose totals differ (new minus old):")
            for m in self.month_totals:
                lines.append(f"    {m['report_month']}: {m['count_new'] - m['count_old']:+d} transactions, {m['value_new'] - m['value_old']:+,.2f} NOK")
        if self.differing_months:
            other = [m for m in self.differing_months if m not in {t["report_month"] for t in self.month_totals}]
            if other:
                lines.append(f"  months where only the split between keys differs: {len(other)} ({other[0]} to {other[-1]})")
        return "\n".join(lines)


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    out.columns = [c.lower() for c in out.columns]
    out["report_month"] = pd.to_datetime(out["report_month"]).dt.date
    out["category_code"] = out["category_code"].astype(str)
    out["value_nok"] = out["value_nok"].astype(float)
    out["txn_count"] = out["txn_count"].astype(int)
    return out[KEY + ["value_nok", "txn_count"]].sort_values(KEY).reset_index(drop=True)


def read_legacy_report(core_db: Path, table: str = REPORT_TABLE) -> pd.DataFrame:
    con = duckdb.connect(str(core_db), read_only=True)
    try:
        return _normalize(con.execute(f"SELECT report_month, category_code, region, value_nok, txn_count FROM {table}").fetchdf())
    finally:
        con.close()


def snapshot_nightly(core_db: Path) -> None:
    """Keep the report table as the nightly job left it, before a full rebuild overwrites it."""
    con = duckdb.connect(str(core_db))
    try:
        con.execute(f"CREATE OR REPLACE TABLE {NIGHTLY_COPY} AS SELECT * FROM {REPORT_TABLE}").fetchall()
    finally:
        con.close()


def has_nightly_copy(core_db: Path) -> bool:
    con = duckdb.connect(str(core_db), read_only=True)
    try:
        schema, table = NIGHTLY_COPY.split(".")
        rows = con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?", [schema, table]
        ).fetchall()
        return bool(rows[0][0])
    finally:
        con.close()


def read_mart(target: str, warehouse_db: Path | None = None) -> pd.DataFrame:
    sql = "SELECT report_month, category_code, region, value_nok, txn_count FROM marts.mart_monthly_category_region"
    if target == "snowflake":
        from load.load_snowflake import connect

        con = connect(role="TRANSFORMER", warehouse="TRANSFORM_WH", query_tag="late_data_lab:reconcile")
        try:
            cur = con.cursor()
            cur.execute(sql)
            frame = pd.DataFrame(cur.fetchall(), columns=[d[0] for d in cur.description])
        finally:
            con.close()
        return _normalize(frame)
    con = duckdb.connect(str(warehouse_db))
    try:
        return _normalize(con.execute(sql).fetchdf())
    finally:
        con.close()


def last_extract_day(core_db: Path) -> date:
    con = duckdb.connect(str(core_db), read_only=True)
    try:
        rows = con.execute(f"SELECT max(run_day) FROM {CONTROL_TABLE} WHERE status = 'DONE'").fetchall()
    finally:
        con.close()
    if not rows or rows[0][0] is None:
        raise SystemExit("no completed extract; run the extract and the load first")
    return rows[0][0]


def compare(old: pd.DataFrame, new: pd.DataFrame, mode: str, run_date: date, max_listed: int = 200) -> Reconciliation:
    merged = old.merge(new, on=KEY, how="outer", suffixes=("_old", "_new"), indicator=True)
    both = merged["_merge"] == "both"
    value_diff = (merged["value_nok_old"].fillna(0) - merged["value_nok_new"].fillna(0)).abs()
    count_diff = (merged["txn_count_old"].fillna(0) - merged["txn_count_new"].fillna(0)).abs()
    differing = merged[(~both) | (value_diff > TOLERANCE) | (count_diff > 0)].copy()
    differing["value_difference"] = (differing["value_nok_old"].fillna(0) - differing["value_nok_new"].fillna(0)).round(2)
    differing["count_difference"] = (differing["txn_count_old"].fillna(0) - differing["txn_count_new"].fillna(0)).astype(int)
    differing = differing.sort_values("value_difference", key=lambda s: s.abs(), ascending=False)

    listed = []
    for _, row in differing.head(max_listed).iterrows():
        listed.append(
            dict(
                report_month=row["report_month"].isoformat(),
                category_code=row["category_code"],
                region=row["region"],
                value_old=None if pd.isna(row["value_nok_old"]) else round(float(row["value_nok_old"]), 2),
                value_new=None if pd.isna(row["value_nok_new"]) else round(float(row["value_nok_new"]), 2),
                count_old=None if pd.isna(row["txn_count_old"]) else int(row["txn_count_old"]),
                count_new=None if pd.isna(row["txn_count_new"]) else int(row["txn_count_new"]),
                present_in=str(row["_merge"]),
            )
        )
    months = sorted({m.isoformat() for m in differing["report_month"]})
    totals = merged.groupby("report_month", as_index=False).agg(
        value_old=("value_nok_old", "sum"), value_new=("value_nok_new", "sum"),
        count_old=("txn_count_old", "sum"), count_new=("txn_count_new", "sum"),
    )
    month_totals = [
        dict(report_month=r["report_month"].isoformat(), value_old=round(float(r["value_old"]), 2), value_new=round(float(r["value_new"]), 2),
             count_old=int(r["count_old"]), count_new=int(r["count_new"]))
        for _, r in totals.iterrows()
        if abs(r["value_old"] - r["value_new"]) > TOLERANCE or r["count_old"] != r["count_new"]
    ]
    return Reconciliation(
        mode=mode,
        run_date=run_date.isoformat(),
        old_rows=len(old),
        new_rows=len(new),
        keys_in_both=int(both.sum()),
        keys_only_in_old=int((merged["_merge"] == "left_only").sum()),
        keys_only_in_new=int((merged["_merge"] == "right_only").sum()),
        value_sum_old=round(float(old["value_nok"].sum()), 2),
        value_sum_new=round(float(new["value_nok"].sum()), 2),
        count_sum_old=int(old["txn_count"].sum()),
        count_sum_new=int(new["txn_count"].sum()),
        keys_with_value_difference=int((value_diff > TOLERANCE).sum()),
        keys_with_count_difference=int((count_diff > 0).sum()),
        max_abs_value_difference=round(float(value_diff.max()) if len(value_diff) else 0.0, 2),
        matches=differing.empty,
        differing_keys=listed,
        differing_months=months,
        month_totals=month_totals,
    )


def reconcile(paths: Paths, target: str, mode: str, run_date: date | None = None, warehouse_db: Path | None = None) -> Reconciliation:
    """Compare the old report with the mart.

    ``nightly`` reads the report table as the nightly job maintained it (the
    preserved copy if a full rebuild has already run); ``full`` first keeps that
    copy, then runs the old job with a full rebuild and compares the result.
    """
    run_date = run_date or last_extract_day(paths.core_db)
    if mode == "full":
        if not has_nightly_copy(paths.core_db):
            snapshot_nightly(paths.core_db)
        run_nightly_report(paths.core_db, run_date, full=True)
        old = read_legacy_report(paths.core_db)
    else:
        old = read_legacy_report(paths.core_db, NIGHTLY_COPY if has_nightly_copy(paths.core_db) else REPORT_TABLE)
    new = read_mart(target, warehouse_db or paths.warehouse_db)
    return compare(old, new, mode, run_date)


def write_result(result: Reconciliation, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"reconciliation_{result.mode}.json"
    path.write_text(json.dumps(asdict(result), indent=2), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", default="mild")
    parser.add_argument("--target", default="dev", choices=["dev", "snowflake"])
    parser.add_argument("--mode", default="both", choices=["full", "nightly", "both"])
    parser.add_argument("--run-date", type=date.fromisoformat, default=None)
    parser.add_argument("--warehouse", type=Path, default=None, help="DuckDB file to read the mart from")
    parser.add_argument("--root", type=Path, default=None, help="repository root (tests only)")
    args = parser.parse_args(argv)

    paths = Paths(args.scenario) if args.root is None else Paths(args.scenario, args.root)
    modes = ["nightly", "full"] if args.mode == "both" else [args.mode]
    all_match = True
    for mode in modes:
        result = reconcile(paths, args.target, mode, args.run_date, args.warehouse)
        path = write_result(result, paths.experiment_dir)
        print(result.summary())
        print(f"  written to {path}")
        if mode == "full":
            all_match = all_match and result.matches
    return 0 if all_match else 1


if __name__ == "__main__":
    sys.exit(main())
