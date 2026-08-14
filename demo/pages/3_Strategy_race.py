"""Strategy race: correctness versus cost, and the error over the replay cycles."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from common import (
    KPI_LABELS, SCENARIO_LABELS, STRATEGY_COLORS, STRATEGY_ORDER, STRATEGY_SYMBOLS,
    choice, controls, cost_options, family, footer, label, load, manifest, missing, page, pct, show,
)

page(
    3,
    "Strategy race",
    "Every loading strategy ran through the same replay: thirty daily extracts, one dbt build per day, and after each day "
    "the three KPIs were compared with the truth as of that day. Cost is what the warehouse had to do to get there.",
    "Filter by strategy family and lookback window. Bottom-left is the winner. The second chart shows how the error "
    "develops day by day; the dashed line is the lateness floor, the error that no strategy can remove because the data "
    "has not arrived yet.",
    "Remove the full refresh from the strategy family filter so the other points spread out, then change the cost "
    "measure and see whether the ranking changes. The green box at the bottom of the page sums up what the current selection shows.",
)

m = manifest()
summary = load("summary")
errors = load("kpi_errors")
floor = load("lateness_floor")
if summary is None or errors is None:
    missing("summary")
    st.stop()

box = controls()
col1, col2, col3 = box.columns([1.2, 2, 1])
with col1:
    scenario = choice("Scenario", list(SCENARIO_LABELS), format_func=lambda s: SCENARIO_LABELS[s],
                      help="Mild: most rows arrive the same day. Heavy: four in ten are late, some by weeks.")
with col2:
    families = st.multiselect("Strategy family", ["full_refresh", "append_only", "merge", "delete_insert"],
                              default=["full_refresh", "append_only", "merge", "delete_insert"],
                              format_func=lambda f: {"full_refresh": "full refresh", "append_only": "append-only", "merge": "merge", "delete_insert": "delete+insert"}[f])
with col3:
    lookbacks = st.multiselect("Lookback window W (days)", [0, 1, 3, 7], default=[0, 1, 3, 7],
                               help="How many days before the newest loaded booking date a merge or delete+insert goes back.")

keys = [k for k in STRATEGY_ORDER if family(k) in families and (family(k) in ("full_refresh", "append_only") or int(k.split("_w")[1]) in lookbacks)]
s = summary[(summary["scenario"] == scenario) & summary["run_key"].isin(keys)].copy()
options = cost_options(summary, scenario, m)
if len(options) > 1:
    cost_col, cost_label = box.selectbox(
        "Cost measure", options, format_func=lambda o: o[1],
        help="Snowflake credits are estimated from the tagged execution time at 1.35 credits per hour; DuckDB seconds are the "
             "execution time of the three strategy-dependent models; rows read does not depend on the engine.",
    )
else:
    cost_col, cost_label = options[0]

st.subheader("Correctness versus cost")
fig = go.Figure()
for key in keys:
    row = s[s["run_key"] == key]
    if row.empty:
        continue
    fig.add_scatter(
        x=row[cost_col], y=row["mean_wrong_share"] * 100, mode="markers+text" if len(keys) <= 4 else "markers", name=label(key), text=[label(key)],
        textposition="top center", marker=dict(color=STRATEGY_COLORS[key], size=14, symbol=STRATEGY_SYMBOLS[family(key)]),
        hovertemplate=f"{label(key)}<br>cost %{{x:,.1f}}<br>wrong cells %{{y:.2f}}%<extra></extra>",
    )
fig.update_layout(xaxis=dict(title=cost_label, rangemode="tozero"), yaxis=dict(title="KPI cells wrong, average over the replay (%)", rangemode="tozero"), showlegend=True)
show(fig, 460)

st.subheader("Error over the replay")
kpi = st.selectbox("KPI", list(KPI_LABELS), format_func=lambda k: KPI_LABELS[k])
e = errors[(errors["scenario"] == scenario) & (errors["kpi"] == kpi) & errors["run_key"].isin(keys)]
fig = go.Figure()
for key in keys:
    k = e[e["run_key"] == key].sort_values("cycle")
    if k.empty:
        continue
    fig.add_scatter(x=k["cycle_day"], y=k["wrong_share"] * 100, name=label(key), mode="lines+markers",
                    line=dict(color=STRATEGY_COLORS[key]), marker=dict(symbol=STRATEGY_SYMBOLS[family(key)], size=7))
if floor is not None:
    f = floor[(floor["scenario"] == scenario) & (floor["kpi"] == kpi)].sort_values("cycle")
    if not f.empty:
        fig.add_scatter(x=f["cycle_day"], y=f["wrong_share"] * 100, name="lateness floor (final truth vs as-of truth)", mode="lines",
                        line=dict(color="#888", dash="dash"))
fig.update_layout(xaxis_title="replay day", yaxis_title="cells wrong (%)")
show(fig, 420)

st.subheader("Results")
table = s.copy()
table["Strategy"] = table["run_key"].map(label)
cols = {
    "Strategy": "Strategy",
    "mean_wrong_share": "Wrong cells, average",
    "final_wrong_share": "Wrong cells, last day",
    "daily_balance__mean_mae_nok": "Balance error, mean abs (NOK)",
    "monthly_category_region__mean_mae_nok": "Monthly cell error, mean abs (NOK)",
    "credits_with_minimum": "Snowflake credits, estimated",
    "incremental_model_seconds": "Strategy-dependent models on DuckDB (seconds)",
    "dbt_execution_seconds": "All dbt models on DuckDB (seconds)",
    "rows_in_window_total": "Rows re-read over the replay",
}
shown = table[[c for c in cols if c in table]].rename(columns=cols).dropna(axis=1, how="all")
for c in ("Wrong cells, average", "Wrong cells, last day"):
    if c in shown:
        shown[c] = shown[c].map(lambda v: pct(v))
st.dataframe(shown, hide_index=True, width="stretch")

st.subheader("What this means")
full = s[s["run_key"] == "full_refresh"]
naive = s[s["run_key"] == "append_only"]
exact = s[(s["final_wrong_share"] == 0) & (s["run_key"] != "full_refresh")].sort_values(cost_col)
lines = []
if not naive.empty:
    lines.append(f"Append-only leaves {pct(naive['mean_wrong_share'].iloc[0])} of cells wrong on an average day and never recovers: a late row is lost for good.")
if not exact.empty and not full.empty:
    best = exact.iloc[0]
    ratio = best[cost_col] / full[cost_col].iloc[0] if full[cost_col].iloc[0] else float("nan")
    lines.append(f"{label(best['run_key']).capitalize()} is the cheapest strategy that ends every day exactly right, at {ratio:.0%} of the cost of a full refresh.")
elif not full.empty:
    others = s[s["run_key"] != "full_refresh"]
    if not others.empty:
        best = others.sort_values(["mean_wrong_share", cost_col]).iloc[0]
        ratio = best[cost_col] / full[cost_col].iloc[0] if full[cost_col].iloc[0] else float("nan")
        lines.append(
            f"No incremental strategy is exact here: the best, {label(best['run_key'])}, still leaves {pct(best['mean_wrong_share'])} of cells wrong on an average day "
            f"({pct(best['final_wrong_share'])} on the last day) at {ratio:.0%} of the cost of a full refresh. Rows that arrive later than the window, "
            f"and corrections to older rows, are never picked up, and a missed row keeps an account's balance wrong for every later day."
        )
windowed = s[s["run_key"].str.contains("_w")]
if not windowed.empty:
    worst_w0 = windowed[windowed["run_key"].str.endswith("_w0")]
    if not worst_w0.empty:
        lines.append(f"With no lookback (W=0) a merge or delete+insert still misses {pct(worst_w0['mean_wrong_share'].max())} of cells: it fixes same-day corrections and duplicates, not lateness.")
    if cost_col == "credits_with_minimum" and s[cost_col].min() > 0:
        spread = s[cost_col].max() / s[cost_col].min()
        lines.append(
            f"On Snowflake the strategies shown cost within a factor of {spread:.1f} of each other: on an X-Small warehouse at this data size "
            "the fixed cost per statement outweighs the rows read, so the window changes correctness, not credits."
        )
    else:
        lines.append("Cost grows with the window because every day re-reads W days of history; the error drops once W covers most of the arrival lag.")
if lines:
    st.success("\n\n".join(f"- {line}" for line in lines))
st.caption(
    "Cost on DuckDB is the execution time of the three models whose work depends on the strategy (the transaction fact, "
    "the balance snapshot and the monthly report); the dimensions are rebuilt the same way for every strategy and are left out. "
    "On Snowflake it is the credit estimate from the query history (execution time of the tagged dbt queries at 1.35 credits per hour, "
    "plus the 60-second minimum per resume), shown for the scenario whose grid ran there. Rows read is the number of staged rows the "
    "strategy's cycles had to read. None of them includes producing and loading the files, which is the same for every strategy."
)

footer(3)
