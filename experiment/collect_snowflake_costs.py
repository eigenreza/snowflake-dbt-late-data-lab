"""Collect Snowflake query history for the experiment and estimate credits.

Every experiment query carries a tag ``ldl:<scenario>:<run key>:c<cycle>``.
Execution time per tag comes from the account's query history; credits are
estimated as execution time times the warehouse's credit rate, plus the
60-second minimum that Snowflake bills on every resume. Warehouse metering
totals are collected as well; they lag by up to a few hours, so this script
is meant to be run again later for the final figures.

The result files keep a relative clock: seconds since the first tagged query
started, and whole hours since the hour of that query for the metering rows.
The resume rule only needs the gaps between queries, so nothing is lost.

The warehouse-scoped history function returns at most 10,000 queries per call,
so the window is read in slices that are split further when a slice fills up.

Usage:
    python -m experiment.collect_snowflake_costs [--hours 72] [--since YYYY-MM-DDTHH:MM:SS] [--until ...]

``--since`` and ``--until`` are UTC timestamps and replace the ``--hours`` window; use
``--since`` to leave out earlier trial runs that carried the same query tags, and
``--skip START/END`` (repeatable) to leave out an aborted run inside the window whose
tags were run again.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from experiment.config import CREDITS_PER_HOUR, MIN_BILLED_SECONDS, RESULTS_DIR
from load.load_snowflake import connect

TAG_PREFIX = "ldl:"
RESULT_LIMIT = 10000  # the most one call of the history function returns


def _frame(cur) -> pd.DataFrame:
    return pd.DataFrame(cur.fetchall(), columns=[d[0].lower() for d in cur.description])


def _ltz(moment: datetime) -> str:
    """A UTC moment as a Snowflake TIMESTAMP_LTZ expression."""
    return f"TO_TIMESTAMP_TZ('{moment.astimezone(timezone.utc):%Y-%m-%d %H:%M:%S} +00:00')::TIMESTAMP_LTZ"


def _history_slice(cur, warehouse: str, start: datetime, end: datetime) -> pd.DataFrame:
    cur.execute(
        f"""
        SELECT query_id, query_tag, warehouse_name, warehouse_size, start_time, end_time,
               execution_time / 1000.0 AS execution_seconds,
               total_elapsed_time / 1000.0 AS elapsed_seconds,
               bytes_scanned, rows_produced, query_type
        FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY_BY_WAREHOUSE(
                WAREHOUSE_NAME => '{warehouse}',
                END_TIME_RANGE_START => {_ltz(start)},
                END_TIME_RANGE_END => {_ltz(end)},
                RESULT_LIMIT => {RESULT_LIMIT}))
        """
    )
    return _frame(cur)


def _history_slices(cur, warehouse: str, start: datetime, end: datetime, step: timedelta) -> list[pd.DataFrame]:
    """Read the window in slices; a slice that fills the result limit is read again in halves."""
    pieces = []
    cursor = start
    while cursor < end:
        stop = min(cursor + step, end)
        frame = _history_slice(cur, warehouse, cursor, stop)
        if len(frame) >= RESULT_LIMIT and step > timedelta(minutes=2):
            pieces.extend(_history_slices(cur, warehouse, cursor, stop, step / 2))
        else:
            pieces.append(frame)
        cursor = stop
    return pieces


def query_history(since: datetime, until: datetime | None = None, step_hours: float = 1.0) -> pd.DataFrame:
    """One row per experiment query (tag ``ldl:<scenario>:<run key>:c<cycle>``) on the two warehouses."""
    until = until or datetime.now(timezone.utc)
    con = connect(role="TRANSFORMER", warehouse="TRANSFORM_WH", query_tag="ldl:collect")
    try:
        cur = con.cursor()
        pieces = []
        for warehouse in ("TRANSFORM_WH", "LOAD_WH"):
            pieces.extend(_history_slices(cur, warehouse, since, until, timedelta(hours=step_hours)))
    finally:
        con.close()
    pieces = [p for p in pieces if not p.empty]
    if not pieces:
        return pd.DataFrame()
    history = pd.concat(pieces, ignore_index=True).drop_duplicates("query_id")
    for col in ("execution_seconds", "elapsed_seconds", "bytes_scanned", "rows_produced"):
        history[col] = pd.to_numeric(history[col], errors="coerce").astype(float)  # the driver returns Decimal
    tags = history["query_tag"].fillna("")
    history = history[tags.str.startswith(TAG_PREFIX) & (tags.str.count(":") == 3)].copy()
    if history.empty:
        return history
    parts = history["query_tag"].str.split(":", expand=True)
    history["scenario"] = parts[1]
    history["run_key"] = parts[2]
    history["cycle"] = parts[3].str.lstrip("c").astype(int)
    return history.sort_values("start_time").reset_index(drop=True)


def relative_clock(history: pd.DataFrame) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Replace the wall-clock times with seconds since the first tagged query started; returns the frame and that moment."""
    origin = history["start_time"].min()
    out = history.copy()
    out["start_seconds"] = (out["start_time"] - origin).dt.total_seconds()
    out["end_seconds"] = (out["end_time"] - origin).dt.total_seconds()
    return out.drop(columns=["start_time", "end_time"]), origin


def relative_hours(metering: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
    """Replace the metering hours with whole hours since the hour of the first tagged query."""
    first_hour = origin.floor("h")
    out = metering.copy()
    for source, target in (("start_time", "hour_start"), ("end_time", "hour_end")):
        out[target] = ((out[source] - first_hour).dt.total_seconds() / 3600.0).round().astype(int)
    columns = ["warehouse_name", "hour_start", "hour_end"] + [c for c in metering.columns if c.startswith("credits")]
    return out[columns]


def resumes_from_gaps(history: pd.DataFrame, auto_suspend_seconds: int = 60) -> pd.DataFrame:
    """Count warehouse resumes per tag: a query that starts after the warehouse sat idle long enough to suspend."""
    rows = []
    for warehouse, group in history.sort_values("start_seconds").groupby("warehouse_name"):
        previous_end = None
        for _, q in group.iterrows():
            resumed = previous_end is None or q["start_seconds"] - previous_end > auto_suspend_seconds
            rows.append(dict(query_id=q["query_id"], resumed=bool(resumed)))
            previous_end = max(previous_end, q["end_seconds"]) if previous_end is not None else q["end_seconds"]
    flags = pd.DataFrame(rows)
    return history.merge(flags, on="query_id", how="left")


def per_tag(history: pd.DataFrame) -> pd.DataFrame:
    """Execution time, resumes and the credit estimate for each scenario, strategy and cycle."""
    with_resumes = resumes_from_gaps(history)
    grouped = with_resumes.groupby(["scenario", "run_key", "cycle", "warehouse_name", "warehouse_size"], as_index=False).agg(
        queries=("query_id", "count"),
        execution_seconds=("execution_seconds", "sum"),
        elapsed_seconds=("elapsed_seconds", "sum"),
        bytes_scanned=("bytes_scanned", "sum"),
        resumes=("resumed", "sum"),
        first_start_seconds=("start_seconds", "min"),
        last_end_seconds=("end_seconds", "max"),
    )
    rate = grouped["warehouse_size"].map(lambda s: CREDITS_PER_HOUR.get(str(s), 1.0))
    grouped["credits_execution"] = grouped["execution_seconds"] / 3600.0 * rate
    billed = grouped["execution_seconds"] + grouped["resumes"].clip(lower=0) * MIN_BILLED_SECONDS
    grouped["credits_with_minimum"] = billed / 3600.0 * rate
    return grouped


def warehouse_metering(since: datetime) -> pd.DataFrame:
    """Credits billed per hour and warehouse since the moment, from the account usage share (lags by up to three hours)."""
    con = connect(role="TRANSFORMER", warehouse="TRANSFORM_WH", query_tag="ldl:collect")
    try:
        cur = con.cursor()
        cur.execute(
            f"""
            SELECT warehouse_name, start_time, end_time, credits_used, credits_used_compute, credits_used_cloud_services
            FROM SNOWFLAKE.ACCOUNT_USAGE.WAREHOUSE_METERING_HISTORY
            WHERE end_time > {_ltz(since)}
            ORDER BY 1, 2
            """
        )
        return _frame(cur)
    finally:
        con.close()


def _utc(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)


def _window(value: str) -> tuple[datetime, datetime]:
    start, end = (_utc(part) for part in value.split("/", 1))
    return start, end


def without_windows(history: pd.DataFrame, windows: list[tuple[datetime, datetime]]) -> pd.DataFrame:
    """Drop the queries that started inside any of the windows."""
    for start, end in windows:
        inside = (history["start_time"] >= start) & (history["start_time"] < end)
        history = history[~inside]
    return history


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=int, default=72, help="look back this many hours (ignored with --since)")
    parser.add_argument("--since", type=_utc, default=None, help="UTC start of the window, ISO form without a zone")
    parser.add_argument("--until", type=_utc, default=None, help="UTC end of the window (default now)")
    parser.add_argument("--skip", type=_window, action="append", default=[], metavar="START/END",
                        help="leave out the queries started in this UTC window (an aborted run); repeatable")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "snowflake")
    args = parser.parse_args(argv)

    args.out.mkdir(parents=True, exist_ok=True)
    since = args.since or datetime.now(timezone.utc) - timedelta(hours=args.hours)
    history = without_windows(query_history(since, args.until), args.skip)
    if history.empty:
        print("no tagged queries found in the window")
        return 1
    history, origin = relative_clock(history)
    history.to_parquet(args.out / "query_history.parquet", index=False)
    tags = per_tag(history)
    tags.to_parquet(args.out / "query_costs.parquet", index=False)
    totals = tags.groupby(["scenario", "run_key"], as_index=False)[["execution_seconds", "resumes", "credits_execution", "credits_with_minimum"]].sum()
    print(totals.to_string(index=False, float_format=lambda v: f"{v:,.3f}"))
    try:
        metering = warehouse_metering(since)
        credit_columns = ["credits_used", "credits_used_compute", "credits_used_cloud_services"]
        for col in credit_columns:
            metering[col] = pd.to_numeric(metering[col], errors="coerce").astype(float)
        metering = relative_hours(metering, origin)
        metering.to_parquet(args.out / "warehouse_metering.parquet", index=False)
        print(metering.groupby("warehouse_name")[credit_columns].sum().to_string(float_format=lambda v: f"{v:,.3f}"))
    except Exception as exc:  # the account usage share lags and needs a grant; keep the query costs regardless
        print(f"warehouse metering not collected: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
