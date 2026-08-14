"""The late data problem: how late, how often corrected, and what a naive load makes of it."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import SCENARIO_LABELS, STRATEGY_COLORS, choice, controls, footer, label, load, manifest, missing, page, show

page(
    2,
    "The late data problem",
    "How many days after their booking date records reach the core system, how often they are corrected afterwards, "
    "and what happens to a single account balance or a single merchant category when the warehouse only appends new rows.",
    "Pick a scenario and an account or category. The orange line is the truth as the core system knew it that day; "
    "the red line is what an append-only warehouse reports. The gap is the late data that never made it in.",
    "Switch the scenario to heavy lateness, scroll down to the drift chart, and compare the append-only line with the "
    "merge line. Then add or remove strategies to see which ones keep up with the truth.",
)

m = manifest()
lateness = load("lateness")
cycles = load("cycles")
scenarios = sorted(SCENARIO_LABELS, key=list(SCENARIO_LABELS).index)
with controls("Choose a scenario"):
    scenario = choice("Scenario", scenarios, format_func=lambda s: SCENARIO_LABELS[s],
                      help="Two settings of the generator: how often records arrive late and how late they are.")

st.subheader("How late is late?")
if lateness is None:
    missing("lateness")
else:
    col1, col2 = st.columns(2)
    sub = lateness[(lateness["scenario"] == scenario)]
    arrival = sub[sub["kind"] == "arrival"]
    correction = sub[sub["kind"] == "correction"]
    with col1:
        fig = px.bar(arrival, x="lag_days", y="share", labels=dict(lag_days="days between booking and arrival", share="share of transactions"))
        fig.update_yaxes(tickformat=".0%")
        fig.update_layout(title="Arrival lag")
        show(fig, 320)
        facts = m.get("scenarios", {}).get(scenario, {})
        if facts:
            st.caption(f"{facts['late_share']:.1%} of transactions arrive at least one day late; {facts['correction_share']:.1%} are corrected afterwards.")
    with col2:
        fig = px.bar(correction, x="lag_days", y="share", labels=dict(lag_days="days between arrival and its correction", share="share of corrections"))
        fig.update_yaxes(tickformat=".0%")
        fig.update_layout(title="Correction lag")
        show(fig, 320)
        st.caption("A correction is either an updated amount on the same transaction or a reversal plus a rebooked row, both with the original booking date.")

if cycles is not None:
    sub = cycles[cycles["scenario"] == scenario]
    fig = go.Figure()
    fig.add_bar(x=sub["cycle_day"], y=sub["rows_arrived"] - sub["late_rows"] - sub["correction_rows"], name="on time", marker_color="#9aa5b1")
    fig.add_bar(x=sub["cycle_day"], y=sub["late_rows"], name="late", marker_color="#e6a100")
    fig.add_bar(x=sub["cycle_day"], y=sub["correction_rows"], name="corrections", marker_color="#c0392b")
    fig.update_layout(barmode="stack", title="What each daily extract delivered", yaxis_title="rows")
    show(fig, 300)
    st.caption("The first day of the month is heavier because housing payments post on the 1st.")

st.subheader("Watch a KPI drift")
drift = controls("Choose what to follow")
with drift:
    kind = choice("KPI", ["Account balance", "Merchant category spend"],
                  help="The balance of one account at the end of each replay day, or the monthly spend of one merchant category as reported after each day.")
strategies = drift.multiselect(
    "Strategies to show", ["append_only", "merge_w1", "merge_w3", "delete_insert_w3", "full_refresh"],
    default=["append_only", "merge_w3", "full_refresh"], format_func=label,
    help="Lines for the warehouse under each strategy. The truth lines are always shown.",
)

if kind == "Account balance":
    frame = load("drift_account_cycles")
    if frame is None:
        missing("drift_account_cycles")
        st.stop()
    sub = frame[frame["scenario"] == scenario]
    accounts = sorted(sub["account_no"].unique())
    account = drift.selectbox("Account", accounts, help="One of the sample accounts kept in the export.")
    s = sub[sub["account_no"] == account]
    fig = go.Figure()
    for key in ["truth_final", "truth"] + strategies:
        k = s[s["run_key"] == key].sort_values("cycle")
        if k.empty:
            continue
        fig.add_scatter(x=k["cycle_day"], y=k["balance_nok"], name=label(key), mode="lines+markers",
                        line=dict(color=STRATEGY_COLORS.get(key, "#888"), dash="dot" if key == "truth_final" else "solid"))
    fig.update_layout(title=f"End-of-day balance of account {account}, as reported after each replay day", yaxis_title="NOK")
    show(fig, 420)
    st.caption(
        "The final truth includes rows that arrive after the replay ends, so even the as-of truth sits below or above it "
        "for a while. A strategy that tracks the as-of truth is doing all a warehouse can do."
    )
else:
    frame = load("drift_category")
    if frame is None:
        missing("drift_category")
        st.stop()
    sub = frame[frame["scenario"] == scenario]
    months = sorted(sub["report_month"].unique())
    month = drift.selectbox("Report month", months, index=len(months) - 2 if len(months) > 1 else 0, format_func=lambda d: pd.Timestamp(d).strftime("%B %Y"),
                         help="November keeps receiving late rows during December; December grows day by day.")
    cats = sub[["category_code", "category_name"]].drop_duplicates().sort_values("category_name")
    category = drift.selectbox("Merchant category", cats["category_code"], format_func=lambda c: cats.set_index("category_code").loc[c, "category_name"])
    s = sub[(sub["report_month"] == month) & (sub["category_code"] == category)]
    fig = go.Figure()
    for key in ["truth_final", "truth"] + strategies:
        k = s[s["run_key"] == key].sort_values("cycle")
        if k.empty:
            continue
        fig.add_scatter(x=k["cycle_day"], y=k["value_nok"], name=label(key), mode="lines+markers",
                        line=dict(color=STRATEGY_COLORS.get(key, "#888"), dash="dot" if key == "truth_final" else "solid"))
    fig.update_layout(title=f"Reported monthly spend, {pd.Timestamp(month).strftime('%B %Y')}, after each replay day", yaxis_title="NOK")
    show(fig, 420)
    st.caption("Append-only never picks up a late November row once December has started, so its November total freezes while the truth keeps rising.")

footer(2)
