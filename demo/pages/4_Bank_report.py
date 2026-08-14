"""Bank report: KPI cards and charts from the star schema, with simulated row-level security."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from common import INK, SOFT_COLORS, controls, footer, load, manifest, missing, page, show

page(
    4,
    "Bank report",
    "The kind of report the star schema is built for: spend, volume, customers and balances by region, merchant "
    "category and segment, with foreign-currency totals converted to NOK. The numbers come from the full-refresh "
    "warehouse at the end of the replay.",
    "The 'view as' selector does what row-level security does in Power BI and the row access policy does in "
    "Snowflake: a regional manager only sees their own region. Everything on the page follows the selection.",
    "Change 'View as' to a regional manager and watch every number and chart shrink to that one region. "
    "Then move the two ends of the month slider to change the reporting period.",
)

m = manifest()
monthly = load("bank_monthly")
customers_monthly = load("bank_customers_monthly")
balances = load("bank_balances_monthly")
daily = load("bank_daily")
if monthly is None:
    missing("bank_monthly")
    st.stop()

regions = sorted(monthly["region"].unique())
roles = {"All regions (REPORTER)": None} | {f"{r} regional manager (REPORTER_{r.upper().replace(' ', '_')})": r for r in regions}
box = controls()
col1, col2 = box.columns([1, 2])
with col1:
    role = st.selectbox("View as", list(roles), help="Mirrors the Power BI roles and the Snowflake row access policy: a regional role sees one region.")
with col2:
    months = sorted(monthly["month_start"].unique())
    start, end = st.select_slider("Months", months, value=(months[max(0, len(months) - 12)], months[-1]),
                                  format_func=lambda d: pd.Timestamp(d).strftime("%b %Y"), help="Reporting period.")
region = roles[role]


def scoped(frame: pd.DataFrame, date_col: str = "month_start") -> pd.DataFrame:
    out = frame[(frame[date_col] >= start) & (frame[date_col] <= end)]
    return out[out["region"] == region] if region else out


mo = scoped(monthly)
cu = scoped(customers_monthly) if customers_monthly is not None else None
ba = scoped(balances) if balances is not None else None

spend = mo["spend_nok"].sum()
volume = mo["txn_count"].sum()
foreign = mo[mo["currency"] != "NOK"]["spend_nok"].sum()
active = cu.groupby("month_start")["active_customers"].sum().mean() if cu is not None and not cu.empty else float("nan")
avg_balance = ba[ba["month_start"] == ba["month_start"].max()]["avg_balance_nok"].mean() if ba is not None and not ba.empty else float("nan")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Spend (M NOK)", f"{spend / 1e6:,.1f}", border=True, help="Card purchases, bills, cash and fees, in millions of NOK.")
c2.metric("Transactions", f"{volume:,.0f}", border=True, help="Number of transactions in the period shown.")
c3.metric("Active customers", f"{active:,.0f}" if active == active else "n/a", border=True, help="Average number of customers per month with at least one transaction.")
c4.metric("Avg. balance (NOK)", f"{avg_balance:,.0f}" if avg_balance == avg_balance else "n/a", border=True, help="Average end-of-month balance per account in the last month shown.")
c5.metric("Foreign share", f"{foreign / spend:.1%}" if spend else "n/a", border=True, help="Share of spend booked in EUR, USD or SEK, after conversion to NOK.")

col1, col2 = st.columns(2)
with col1:
    by_month = mo.groupby("month_start", as_index=False)[["spend_nok", "inflow_nok"]].sum()
    fig = px.line(by_month, x="month_start", y=["spend_nok", "inflow_nok"], labels=dict(month_start="", value="NOK", variable=""))
    fig.for_each_trace(lambda t: t.update(name={"spend_nok": "spend", "inflow_nok": "inflow"}.get(t.name, t.name)))
    fig.update_layout(title="Spend and inflow by month")
    show(fig, 340)
with col2:
    by_region = mo.groupby("region", as_index=False)["spend_nok"].sum().sort_values("spend_nok")
    fig = px.bar(by_region, x="spend_nok", y="region", orientation="h", labels=dict(spend_nok="NOK", region=""))
    fig.update_layout(title="Spend by region")
    show(fig, 340)

col1, col2 = st.columns(2)
with col1:
    by_group = mo[mo["txn_type"].isin(["CARD_PURCHASE", "BILL_PAYMENT"])].groupby(["category_group", "category_name"], as_index=False)["spend_nok"].sum()
    fig = px.treemap(by_group, path=["category_group", "category_name"], values="spend_nok",
                     color="category_group", color_discrete_sequence=SOFT_COLORS)
    fig.update_traces(marker=dict(line=dict(color="#ffffff", width=2)), root_color="#ffffff", textfont=dict(color=INK),
                      hovertemplate="%{label}<br>%{value:,.0f} NOK<extra></extra>")
    fig.update_layout(title="Spend by merchant category")
    show(fig, 380)
with col2:
    by_segment = mo.groupby(["month_start", "segment"], as_index=False)["spend_nok"].sum()
    fig = px.area(by_segment, x="month_start", y="spend_nok", color="segment", labels=dict(month_start="", spend_nok="NOK", segment=""))
    fig.update_layout(title="Spend by customer segment")
    show(fig, 380)

st.subheader("Foreign-currency transactions")
fx = mo[mo["currency"] != "NOK"].groupby(["month_start", "currency"], as_index=False).agg(spend_nok=("spend_nok", "sum"), amount_ccy=("amount_ccy", "sum"), txn_count=("txn_count", "sum"))
fx["amount_ccy"] = -fx["amount_ccy"]
col1, col2 = st.columns(2)
with col1:
    fig = px.bar(fx, x="month_start", y="spend_nok", color="currency", labels=dict(month_start="", spend_nok="NOK", currency=""))
    fig.update_layout(title="Converted to NOK at the booking date's rate")
    show(fig, 320)
with col2:
    tbl = fx.groupby("currency", as_index=False).agg(transactions=("txn_count", "sum"), amount_in_currency=("amount_ccy", "sum"), amount_nok=("spend_nok", "sum"))
    tbl["average rate used"] = tbl["amount_nok"] / tbl["amount_in_currency"]
    st.dataframe(tbl.round(2), hide_index=True, width="stretch")

if ba is not None and not ba.empty:
    st.subheader("Balances")
    by_type = ba.groupby(["month_start", "account_type"], as_index=False)["balance_nok"].sum()
    fig = px.line(by_type, x="month_start", y="balance_nok", color="account_type", labels=dict(month_start="", balance_nok="NOK at month end", account_type=""))
    fig.update_layout(title="Total end-of-month balance by account type")
    show(fig, 320)

if daily is not None:
    d = daily[daily["region"] == region] if region else daily
    d = d.groupby("event_date", as_index=False)[["spend_nok", "txn_count"]].sum()
    fig = px.bar(d, x="event_date", y="spend_nok", labels=dict(event_date="", spend_nok="NOK"))
    fig.update_layout(title="Daily spend, last four months of the replay")
    show(fig, 280)

bank = m.get("bank", {})
if bank:
    st.caption(f"Warehouse state as of {bank.get('as_of')}: {bank.get('fact_rows', 0):,} transactions, {bank.get('balance_rows', 0):,} daily balance rows, {bank.get('customer_versions', 0):,} customer versions.")

footer(4)
