"""Inside the warehouse: schema, customer history, access, cost, tests and the reconciliation."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from common import STRATEGY_COLORS, footer, label, load, load_json, manifest, page, show

page(
    5,
    "Inside the warehouse",
    "The star schema the reports run on, one customer's history as the warehouse keeps it, who may see what, what the "
    "Snowflake warehouses cost, whether the data tests pass, and whether the rewritten report agrees with the old one.",
    "Each block stands on its own. The reconciliation at the bottom is the migration check: a full rebuild of the old "
    "job must match the new model to the cent, and the differences in the nightly comparison explain what the old job got wrong.",
    "Pick a customer in the history block to see how a move to another region is kept as a new version, and open "
    "'All tests' to see every data-quality test that ran.",
)

m = manifest()

st.subheader("Star schema")
st.graphviz_chart(
    """
    digraph G {
        rankdir=LR; node [shape=box, style="rounded,filled", fillcolor="#f4f6f8", fontname="Helvetica", fontsize=11];
        fct_transactions [fillcolor="#dbe9f4", label="fct_transactions\\none row per transaction"];
        fct_balance [fillcolor="#dbe9f4", label="fct_account_balance_daily\\naccount x day"];
        mart [fillcolor="#e5f2e5", label="mart_monthly_category_region\\nmonth x category x region"];
        dim_customer [label="dim_customer\\nSCD type 2"]; dim_account; dim_card; dim_merchant; dim_date; dim_currency;
        dim_customer -> fct_transactions [label="customer_sk"]; dim_account -> fct_transactions; dim_card -> fct_transactions;
        dim_merchant -> fct_transactions; dim_date -> fct_transactions; dim_currency -> fct_transactions;
        dim_account -> fct_balance; dim_date -> fct_balance; dim_customer -> dim_account [label="current version"];
        fct_transactions -> mart [style=dashed];
    }
    """
)

st.subheader("A customer's history (SCD type 2)")
customers = load("customers")
if customers is not None:
    changed = customers[customers["version_no"] > 1]["customer_id"].unique()
    if len(changed):
        pick = st.selectbox("Customer", sorted(changed), format_func=lambda c: f"{customers[customers['customer_id'] == c]['customer_no'].iloc[0]}  {customers[customers['customer_id'] == c]['full_name'].iloc[0]}",
                            help="Only customers with more than one version are listed.")
        h = customers[customers["customer_id"] == pick].sort_values("version_no").copy()
        h["from"] = pd.to_datetime(h["valid_from"]).clip(lower=pd.Timestamp("2022-12-01"))
        h["to"] = pd.to_datetime(h["valid_to"]).clip(upper=pd.Timestamp("2025-01-31"))
        h["version"] = "version " + h["version_no"].astype(str)
        h["label"] = h["region"] + ", " + h["segment"] + ", " + h["city"]
        detail = [c for c in ("street", "postal_code") if c in h.columns]
        fig = px.timeline(h, x_start="from", x_end="to", y="version", color="region", text="label", hover_data=detail)
        fig.update_yaxes(autorange="reversed")
        fig.update_layout(title="Versions and the dates they applied (the first version starts at the beginning of time)")
        show(fig, 260 + 30 * len(h))
        shown = [c for c in ("version_no", "valid_from", "valid_to", "is_current", "region", "segment", "street", "postal_code", "city") if c in h.columns]
        st.dataframe(h[shown], hide_index=True, width="stretch")
    bank = m.get("bank", {})
    if bank:
        st.caption(f"{bank.get('customer_changes_seen', 0):,} customer changes were captured by the snapshot during the replay.")

st.subheader("Roles and access")
st.markdown(
    """
| Role | Reads | Writes | Warehouse |
|---|---|---|---|
| LOADER | landed files | RAW | LOAD_WH |
| TRANSFORMER | RAW | STAGING, MARTS | TRANSFORM_WH |
| REPORTER | MARTS | nothing | TRANSFORM_WH |
| REPORTER_VESTLAND, REPORTER_OSLO | MARTS, one region (row access policy) | nothing | TRANSFORM_WH |

One service user runs the loader and dbt with key-pair authentication. The regional policies are attached to the
customer dimension, the facts and the monthly report after every build.
"""
)

st.subheader("Warehouses and credits")
metering = load("warehouse_metering")
if metering is not None and not metering.empty:
    per = metering.groupby("warehouse_name", as_index=False)["credits_used"].sum()
    per = per[per["credits_used"] > 0.01]
    fig = px.bar(per, x="warehouse_name", y="credits_used", labels=dict(warehouse_name="", credits_used="credits"))
    fig.update_layout(title="Credits metered per warehouse over both cost grids")
    show(fig, 300)
    st.caption(
        "Two extra-small warehouses, auto-suspend after 60 seconds, under a resource monitor that notifies at 50 and 80 "
        "percent of its quota and suspends at 100. The loader runs on LOAD_WH, dbt and the scoring on TRANSFORM_WH; the "
        "figures are what Snowflake billed, compute and cloud services, over the days the grid ran."
    )
else:
    st.markdown(
        "Two extra-small warehouses, `LOAD_WH` for loading and `TRANSFORM_WH` for dbt and the experiment, each with "
        "auto-suspend after 60 seconds and auto-resume, under a resource monitor with a credit quota that notifies at "
        "50 and 80 percent and suspends at 100. Credit figures per warehouse appear here once the Snowflake cost grid has been collected."
    )
costs = load("run_costs_models")
if costs is not None and not costs.empty:
    scen = "mild" if (costs["scenario"] == "mild").any() else costs["scenario"].iloc[0]
    heavy_nodes = ["fct_transactions", "fct_account_balance_daily", "mart_monthly_category_region", "snap_customers", "dim_customer", "int_fx_rates_daily", "dim_date"]
    sub = costs[(costs["scenario"] == scen) & costs["run_key"].isin(["full_refresh", "merge_w3", "append_only"]) & costs["node"].isin(heavy_nodes)].copy()
    sub["strategy"] = sub["run_key"].map(label)
    fig = px.bar(sub, x="node", y="execution_seconds", color="strategy", barmode="group",
                 color_discrete_map={label(k): STRATEGY_COLORS[k] for k in ["full_refresh", "merge_w3", "append_only"]},
                 labels=dict(node="", execution_seconds="seconds over the replay", strategy=""))
    fig.update_layout(title=f"Where the time goes on DuckDB ({scen} scenario, three strategies, heaviest models)")
    show(fig, 340)

st.subheader("Data-quality tests")
tests = load("dbt_tests")
if tests is not None and not tests.empty:
    counts = tests["status"].value_counts()
    c1, c2, c3 = st.columns(3)
    c1.metric("Tests", len(tests), border=True)
    c2.metric("Passed", int(counts.get("pass", 0)), border=True)
    c3.metric("Failed or warned", int(len(tests) - counts.get("pass", 0)), border=True)
    with st.expander("All tests"):
        st.dataframe(tests[["test", "status", "execution_seconds"]].sort_values("test"), hide_index=True, width="stretch")
else:
    st.markdown("Test results appear here once the export has run.")

st.subheader("Old report versus the rewrite")
full = load_json("reconciliation_mild_full")
nightly = load_json("reconciliation_mild_nightly")
if full:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Full rebuild: rows old / new", f"{full['old_rows']:,} / {full['new_rows']:,}", border=True)
    c2.metric("Value difference (NOK)", f"{full['value_sum_old'] - full['value_sum_new']:,.2f}", border=True)
    c3.metric("Keys that differ", full["keys_with_value_difference"] + full["keys_only_in_old"] + full["keys_only_in_new"], border=True)
    c4.metric("Verdict", "match" if full["matches"] else "differences", border=True)
    st.markdown("When the old job rebuilds every month, it agrees with the dbt model to the cent. The rewrite reproduces the old logic.")
if nightly:
    totals = nightly.get("month_totals", [])
    missing_rows = nightly["count_sum_new"] - nightly["count_sum_old"]
    missing_value = nightly["value_sum_new"] - nightly["value_sum_old"]
    short_months = ", ".join(pd.Timestamp(m["report_month"]).strftime("%B %Y") for m in totals) or "the months before the replay"
    other_months = len([m for m in nightly["differing_months"] if m not in {t["report_month"] for t in totals}])
    st.markdown(
        f"Run the old job the way it ran every night, rebuilding only the current month, and it drifts in two ways. "
        f"First, **{missing_rows:,} transactions ({missing_value:,.0f} NOK) are missing from {short_months}**: they were booked in that month "
        f"but reached the core system in December, after the month had last been rebuilt. Second, the report keys differ in "
        f"**{other_months} earlier months whose totals agree**: a few customers moved to another region during December, and the rewrite "
        f"attributes their whole history to the current region while the old report keeps the region each month had when it was last rebuilt. "
        f"In all, {nightly['keys_with_value_difference']:,} of {nightly['keys_in_both']:,} keys differ. The dbt model is the consistent one."
    )
    if nightly["differing_keys"]:
        with st.expander("Largest differences"):
            st.dataframe(pd.DataFrame(nightly["differing_keys"]).head(25), hide_index=True, width="stretch")
if not full and not nightly:
    st.markdown("The reconciliation result appears here once the export has run.")

footer(5)
