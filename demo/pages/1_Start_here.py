"""Start here: how to use the demo, the question, the short answer and the headline chart."""

from __future__ import annotations

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from common import (
    DEVELOPER, DEVELOPER_URL, KPI_LABELS, PAGES, REPO_URL, SCENARIO_LABELS, STRATEGY_COLORS, STRATEGY_ORDER, STRATEGY_SYMBOLS,
    choice, cost_axis, family, footer, header, label, load, manifest, missing, pct, show,
)

header(1, "Late Data Lab")
st.markdown(
    "A small retail bank whose records sometimes arrive days late or get corrected afterwards. The same thirty days "
    "are loaded into a data warehouse with ten different loading strategies, and after every day the reported numbers "
    "are compared with the truth. This demo shows what goes wrong, which strategy fixes it, and what that costs."
)

st.subheader("How to use this demo")
steps = [
    (":material/menu:", "1. Pick a page",
     "Use the menu on the left, or the Next button at the bottom of each page. On a phone, open the menu with the "
     "arrow button in the top-left corner."),
    (":material/tune:", "2. Change the controls",
     "Buttons, drop-down lists and sliders update the charts right away. Nothing you do changes the data, so try "
     "everything. The small question marks explain each control."),
    (":material/ads_click:", "3. Explore the charts",
     "Hover for the exact numbers. Click a name in a legend to hide or show it, drag to zoom in, and double-click "
     "to zoom back out."),
]
for column, (icon, heading, text) in zip(st.columns(len(steps), border=True), steps):
    with column:
        st.markdown(f"**{icon} {heading}**")
        st.markdown(text)
st.caption(
    "Every other page starts with cards that say what it shows, how to read it and what to try. "
    "A first tour takes about five minutes."
)

with st.expander("Words used in this demo", icon=":material/dictionary:"):
    st.markdown(
        "- **Late record:** a transaction that reaches the bank's core system one or more days after its booking date.\n"
        "- **Correction:** a change to a transaction that was already delivered, either a new amount or a reversal plus a rebooked row.\n"
        "- **Replay:** thirty days of daily extracts, loaded one day at a time, the way a nightly pipeline runs.\n"
        "- **KPI cell:** one reported number, such as one account's balance on one day, or one category's spend in one region and month.\n"
        "- **Truth as of a day:** what the core system knew at the end of that day. No warehouse can do better than this.\n"
        "- **Final truth:** what the core system knows once every late row and correction has arrived.\n"
        "- **Full refresh:** rebuild every table from all the data, every day.\n"
        "- **Append-only:** add the rows that are new since the last run and never look back.\n"
        "- **Merge and delete+insert:** reprocess the last few days and replace what changed.\n"
        "- **Lookback window W:** how many days back a merge or delete+insert reprocesses.\n"
        "- **Credits:** the unit in which Snowflake bills compute."
    )

m = manifest()
summary = load("summary")

st.subheader("The question")
st.markdown(
    "How much do late-arriving and corrected records distort warehouse KPIs, and which incremental loading "
    "strategy (full refresh, append-only, merge, delete+insert) restores correctness at what cost?"
)

if summary is None or summary.empty:
    missing("summary")
    st.stop()


def best_incremental(scenario: str):
    """The cheapest incremental strategy that ends exactly right, or the most accurate one when none does."""
    cost_col, _ = cost_axis(summary, scenario, m)
    s = summary[summary["scenario"] == scenario].set_index("run_key")
    others = s.drop(index="full_refresh", errors="ignore")
    if others.empty:
        return None, False
    exact = others[others["final_wrong_share"] == 0].sort_values(cost_col)
    if not exact.empty:
        return exact.iloc[0], True
    return others.sort_values(["mean_wrong_share", cost_col]).iloc[0], False


def short_answer(scenario: str) -> str:
    cost_col, _ = cost_axis(summary, scenario, m)
    s = summary[summary["scenario"] == scenario].set_index("run_key")
    if s.empty:
        return ""
    naive = s.loc["append_only", "mean_wrong_share"] if "append_only" in s.index else None
    full_cost = s.loc["full_refresh", cost_col] if "full_refresh" in s.index else None
    best, exact = best_incremental(scenario)
    parts = []
    if naive is not None:
        parts.append(f"with append-only loading {pct(naive)} of the KPI cells are wrong on an average day")
    if best is not None and full_cost:
        if exact:
            parts.append(f"{label(best.name)} ends every day exactly right at {best[cost_col] / full_cost:.0%} of the cost of a full refresh")
        else:
            parts.append(
                f"the most accurate incremental strategy is {label(best.name)}, which leaves {pct(best['mean_wrong_share'])} of cells wrong "
                f"on an average day at {best[cost_col] / full_cost:.0%} of the cost of a full refresh; only the full refresh is exact"
            )
    if not parts:
        return ""
    return f"In the {SCENARIO_LABELS.get(scenario, scenario)} scenario, " + "; ".join(parts) + "."


st.subheader("The short answer")
scenarios = [name for name in SCENARIO_LABELS if (summary["scenario"] == name).any()] or list(summary["scenario"].unique())
scenario = choice("Scenario", scenarios, format_func=lambda name: SCENARIO_LABELS.get(name, name),
                  help="Two settings of the data: in the mild one most records arrive on time, in the heavy one many arrive late.")
cost_col, cost_label = cost_axis(summary, scenario, m)
rows = summary[summary["scenario"] == scenario].set_index("run_key")
best, exact = best_incremental(scenario)
tiles = st.columns(4)
if "append_only" in rows.index:
    tiles[0].metric("Wrong with append-only", pct(rows.loc["append_only", "mean_wrong_share"]), border=True,
                    help="Share of reported numbers that differ from the truth on an average day when the warehouse only adds new rows.")
if best is not None:
    tiles[1].metric(f"Wrong with {label(best.name)}", pct(best["mean_wrong_share"]), border=True,
                    help="The incremental strategy with the fewest wrong numbers on an average day.")
if "full_refresh" in rows.index:
    tiles[2].metric("Wrong with a full refresh", pct(rows.loc["full_refresh", "mean_wrong_share"]), border=True,
                    help="Rebuilding everything every day is always right. It is the reference for cost.")
    if best is not None and rows.loc["full_refresh", cost_col]:
        tiles[3].metric(f"Cost of {label(best.name)}", f"{best[cost_col] / rows.loc['full_refresh', cost_col]:.0%}", border=True,
                        help=f"Compared with a full refresh. Cost measure: {cost_label}.")
answer = short_answer(scenario)
if answer:
    st.markdown(answer)
with st.expander("Read the longer answer", icon=":material/article:"):
    st.markdown(
        "A full refresh is always right, and on DuckDB always the most expensive. Append-only is cheap and wrong. "
        "A merge or delete+insert with a lookback window of a few days is right once the window covers how late the data "
        "arrives, and the work it does grows with the window. On an X-Small Snowflake warehouse at this data size the ten "
        "strategies cost about the same credits, because the fixed cost per statement outweighs the rows read; the chart below "
        "uses credits where the grid ran on Snowflake and DuckDB seconds otherwise. The strategy race page has the details, "
        "the other cost measures and the error over time."
    )

st.subheader("Correctness versus cost")
st.markdown(
    "Lower is better on both axes: left means cheap, bottom means correct. The strategy you want sits in the "
    "bottom-left corner."
)
plot = summary[summary["scenario"] == scenario]
fig = go.Figure()
for key in STRATEGY_ORDER:
    row = plot[plot["run_key"] == key]
    if row.empty:
        continue
    fig.add_scatter(
        x=row[cost_col], y=row["mean_wrong_share"] * 100, mode="markers", name=label(key),
        marker=dict(color=STRATEGY_COLORS[key], size=14, symbol=STRATEGY_SYMBOLS[family(key)]),
        hovertemplate=f"{label(key)}<br>cost %{{x:,.2f}}<br>wrong cells %{{y:.2f}}%<extra></extra>",
    )
fig.update_layout(
    xaxis=dict(title=cost_label, rangemode="tozero"),
    yaxis=dict(title="KPI cells wrong, average over the replay (%)", rangemode="tozero"),
)
show(fig, 440)
st.caption(
    "Each point is one strategy over the whole replay. Wrong cells are counted over three KPIs: daily account balances, "
    "monthly value by merchant category and region, and daily foreign-currency totals. A cell is wrong when it differs "
    "from the truth by more than half a cent or is missing."
)

st.subheader("What is real and what is synthetic")
col1, col2 = st.columns(2)
with col1:
    st.markdown(
        "**Synthetic:** the bank. Customers, accounts, cards, merchants and every transaction come from a seeded generator, "
        "including which records arrive late and which get corrected. No real person or company is in the data. "
        "Region names are real Norwegian counties."
    )
    facts = m.get("scenarios", {})
    if facts:
        lines = []
        for name, f in facts.items():
            lines.append(
                f"- {SCENARIO_LABELS.get(name, name)}: {f['customers']:,} customers, {f['accounts']:,} accounts, "
                f"{f['transactions_booked']:,} transactions; {f['late_share']:.1%} arrive late, {f['correction_share']:.1%} are corrected later."
            )
        st.markdown("\n".join(lines))
with col2:
    st.markdown(
        "**Real:** the exchange rates. EUR, USD and SEK against NOK come from Norges Bank's open data, one rate per business "
        "day, carried forward over weekends and holidays. Foreign-currency transactions are converted with them."
    )
    fx = load("fx_rates")
    if fx is not None:
        fig = px.line(fx, x="rate_date", y="rate_nok", color="currency", labels=dict(rate_date="", rate_nok="NOK per unit", currency=""))
        show(fig, 260)

st.caption(f"KPIs measured: {', '.join(KPI_LABELS.values())}.")

st.subheader("Where to go next")
for column, (script, title, icon, _section, question) in zip(st.columns(len(PAGES) - 1, border=True), PAGES[1:]):
    with column:
        st.markdown(f"**{icon} {title}**")
        st.markdown(question)
        st.page_link(script, label="Open this page", icon=":material/arrow_forward:")

st.subheader("About this project")
st.markdown(
    f"Late Data Lab was designed and developed by [{DEVELOPER}]({DEVELOPER_URL}). The source code, the documentation and "
    f"the steps to run the whole pipeline on a laptop are in the [GitHub repository]({REPO_URL})."
)
footer(1)
