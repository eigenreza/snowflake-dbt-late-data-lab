"""Shared pieces of the demo: data loading, colors, labels, page layout and chart styling."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import streamlit as st

DATA_DIR = Path(__file__).resolve().parent / "data"
REPO_URL = "https://github.com/eigenreza/snowflake-dbt-late-data-lab"
DEVELOPER = "Reza Azad Gholami"
DEVELOPER_URL = "https://github.com/eigenreza"
PROJECT_LINE = "A synthetic retail bank, late and corrected records, and what each loading strategy costs to get the numbers right."

# The pages in tour order: script, menu title, menu icon, menu section, and the question the page answers.
PAGES = [
    ("pages/1_Start_here.py", "Start here", ":material/flag:", "Overview",
     "What is this project, and what did it find?"),
    ("pages/2_The_late_data_problem.py", "The late data problem", ":material/schedule:", "The experiment",
     "How late do records arrive, and what does that do to a single number?"),
    ("pages/3_Strategy_race.py", "Strategy race", ":material/leaderboard:", "The experiment",
     "Which loading strategy gets the numbers right, and what does it cost?"),
    ("pages/4_Bank_report.py", "Bank report", ":material/account_balance:", "The warehouse",
     "What does the finished warehouse look like to a business user?"),
    ("pages/5_Inside_the_warehouse.py", "Inside the warehouse", ":material/database:", "The warehouse",
     "How is it built: schema, customer history, access, cost, tests and the migration check?"),
]

STRATEGY_ORDER = [
    "full_refresh", "append_only",
    "merge_w0", "merge_w1", "merge_w3", "merge_w7",
    "delete_insert_w0", "delete_insert_w1", "delete_insert_w3", "delete_insert_w7",
]
STRATEGY_LABELS = {
    "full_refresh": "full refresh",
    "append_only": "append-only",
    "merge_w0": "merge, W=0",
    "merge_w1": "merge, W=1",
    "merge_w3": "merge, W=3",
    "merge_w7": "merge, W=7",
    "delete_insert_w0": "delete+insert, W=0",
    "delete_insert_w1": "delete+insert, W=1",
    "delete_insert_w3": "delete+insert, W=3",
    "delete_insert_w7": "delete+insert, W=7",
    "truth": "truth (as of that day)",
    "truth_final": "final truth",
}
STRATEGY_COLORS = {
    "full_refresh": "#4a4a4a",
    "append_only": "#c0392b",
    "merge_w0": "#9ecae1",
    "merge_w1": "#6baed6",
    "merge_w3": "#3182bd",
    "merge_w7": "#08519c",
    "delete_insert_w0": "#a1d99b",
    "delete_insert_w1": "#74c476",
    "delete_insert_w3": "#31a354",
    "delete_insert_w7": "#006d2c",
    "truth": "#e6a100",
    "truth_final": "#b07d00",
}
STRATEGY_SYMBOLS = {"full_refresh": "square", "append_only": "x", "merge": "circle", "delete_insert": "diamond"}
KPI_LABELS = {
    "daily_balance": "Daily account balance",
    "monthly_category_region": "Monthly value by category and region",
    "fx_daily": "Daily foreign-currency totals in NOK",
}
SCENARIO_LABELS = {"mild": "mild lateness", "heavy": "heavy lateness"}
DUCKDB_COST = ("incremental_model_seconds", "execution time of the three strategy-dependent models on DuckDB (seconds)")

# Chart styling: ink for text, a quiet grid, and a fixed order of series colors for charts that do not
# carry the strategy colors above.
INK = "#1f2933"
MUTED = "#5b6770"
GRID = "#e9edf1"
AXIS = "#c9d1d8"
SURFACE = "#ffffff"
SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
CHART_FONT = '"Source Sans", "Source Sans 3", "Source Sans Pro", -apple-system, "Segoe UI", sans-serif'


def _tint(color: str, amount: float = 0.6) -> str:
    """The color mixed with white, for large filled areas that should stay quiet."""
    red, green, blue = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(c + (255 - c) * amount):02x}" for c in (red, green, blue))


SOFT_COLORS = [_tint(color) for color in SERIES_COLORS]

pio.templates["late_data_lab"] = go.layout.Template(
    layout=dict(
        font=dict(family=CHART_FONT, size=14, color=INK),
        colorway=SERIES_COLORS,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        title=dict(font=dict(size=16, color=INK), x=0, xanchor="left"),
        xaxis=dict(showgrid=False, showline=True, linecolor=AXIS, ticks="outside", tickcolor=AXIS, zeroline=False,
                   tickfont=dict(color=MUTED, size=12), title=dict(font=dict(color=MUTED, size=13)), automargin=True),
        yaxis=dict(showgrid=True, gridcolor=GRID, gridwidth=1, showline=False, ticks="", zeroline=False,
                   tickfont=dict(color=MUTED, size=12), title=dict(font=dict(color=MUTED, size=13)), automargin=True),
        legend=dict(font=dict(color=MUTED, size=13), title=dict(font=dict(color=MUTED, size=13))),
        hoverlabel=dict(bgcolor=SURFACE, bordercolor=AXIS, font=dict(family=CHART_FONT, size=13, color=INK)),
        barcornerradius=4,
        bargap=0.25,
    )
)
pio.templates.default = "simple_white+late_data_lab"

PAGE_STYLE = """
<style>
[data-testid="stMainBlockContainer"] { max-width: 1240px; padding-top: 2.75rem; padding-bottom: 3rem; }
[data-testid="stMetric"] label p { font-size: 0.9rem; }
</style>
"""


@st.cache_data(show_spinner=False)
def load(name: str) -> pd.DataFrame | None:
    path = DATA_DIR / f"{name}.parquet"
    return pd.read_parquet(path) if path.exists() else None


@st.cache_data(show_spinner=False)
def load_json(name: str) -> dict | None:
    path = DATA_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def manifest() -> dict:
    return load_json("manifest") or {}


def label(run_key: str) -> str:
    return STRATEGY_LABELS.get(run_key, run_key)


def family(run_key: str) -> str:
    return run_key.split("_w")[0]


def cost_axis(summary: pd.DataFrame, scenario: str, m: dict) -> tuple[str, str]:
    """Cost column and axis label for one scenario: Snowflake credits where that grid ran, DuckDB seconds otherwise."""
    basis = m.get("cost_basis", {})
    candidates = [(basis.get("column", DUCKDB_COST[0]), basis.get("label", DUCKDB_COST[1])), DUCKDB_COST,
                  ("dbt_execution_seconds", "dbt execution time on DuckDB (seconds)")]
    s = summary[summary["scenario"] == scenario]
    for col, lab in candidates:
        if col in s and s[col].notna().any():
            return col, lab
    return candidates[-1]


def cost_options(summary: pd.DataFrame, scenario: str, m: dict) -> list[tuple[str, str]]:
    """Every cost measure the export holds for a scenario, Snowflake credits first where that grid ran."""
    basis = m.get("cost_basis", {})
    candidates = [(basis.get("column", DUCKDB_COST[0]), basis.get("label", DUCKDB_COST[1])), DUCKDB_COST,
                  ("rows_in_window_total", "staged rows the cycles had to read over the replay")]
    s = summary[summary["scenario"] == scenario]
    options: list[tuple[str, str]] = []
    for col, lab in candidates:
        if col in s and s[col].notna().any() and col not in [c for c, _ in options]:
            options.append((col, lab))
    return options or [cost_axis(summary, scenario, m)]


def sidebar() -> None:
    with st.sidebar:
        st.markdown(f"**Late Data Lab**  \n{PROJECT_LINE}")
        st.caption(
            "Pick a page in the menu above, or follow the Next button at the bottom of each page. "
            "Change a control and the charts follow."
        )
        st.link_button("Source code and documentation", REPO_URL, icon=":material/code:")
        st.markdown(f"Developed by [{DEVELOPER}]({DEVELOPER_URL})")
        st.caption("Built with DuckDB, dbt and Snowflake. The demo reads exported files only.")


def header(step: int, title: str) -> None:
    """The top of every page: styling, the sidebar, the place in the tour and the title."""
    st.markdown(PAGE_STYLE, unsafe_allow_html=True)
    sidebar()
    st.caption(f"Page {step} of {len(PAGES)} | {PAGES[step - 1][3]}")
    st.title(title)


def page(step: int, title: str, what: str, how: str, try_this: str | None = None) -> None:
    """The top of a page with the cards that explain it: what it shows, how to read it, what to try."""
    header(step, title)
    cards = [(":material/visibility:", "What this shows", what), (":material/menu_book:", "How to read it", how)]
    if try_this:
        cards.append((":material/touch_app:", "Try this", try_this))
    for column, (icon, heading, text) in zip(st.columns(len(cards), border=True), cards):
        with column:
            st.markdown(f"**{icon} {heading}**")
            st.markdown(text)


def controls(title: str = "Choose what to show"):
    """A bordered box that groups the controls of a page; use it as ``with controls(): ...``."""
    box = st.container(border=True)
    box.markdown(f"**:material/tune: {title}**")
    return box


def choice(text: str, options: list, **kwargs):
    """Pick exactly one of a few options, shown as a row of buttons."""
    return st.segmented_control(text, options, default=options[0], required=True, **kwargs)


def footer(step: int) -> None:
    """The bottom of a page: the way back and forward through the tour, and who built it."""
    st.divider()
    back, forward = st.columns(2)
    if step > 1:
        script, title = PAGES[step - 2][:2]
        back.page_link(script, label=f"Back: {title}", icon=":material/arrow_back:")
    if step < len(PAGES):
        script, title = PAGES[step][:2]
        forward.page_link(script, label=f"Next: {title}", icon=":material/arrow_forward:")
    st.caption(
        f"Late Data Lab, developed by [{DEVELOPER}]({DEVELOPER_URL}). "
        f"Source code and documentation: [{REPO_URL.removeprefix('https://')}]({REPO_URL})"
    )


def nok(value: float, decimals: int = 0) -> str:
    return f"{value:,.{decimals}f} NOK"


def pct(value: float, decimals: int = 2) -> str:
    return f"{value * 100:.{decimals}f}%"


def missing(name: str) -> None:
    st.warning(f"The file `{name}.parquet` is not in the demo data yet. Run the export step to produce it.")


def tidy(fig: go.Figure, height: int = 420) -> go.Figure:
    """Shared layout: height, margins, legend below the plot, thin lines and ringed markers."""
    has_title = bool(fig.layout.title.text)
    fig.update_layout(
        height=height,
        margin=dict(l=10, r=10, t=48 if has_title else 16, b=10),
        legend=dict(orientation="h", yanchor="top", y=-0.2, x=0),
    )
    line_charts = [t for t in fig.data if t.type == "scatter" and "lines" in (t.mode or "lines")]
    if fig.data and len(line_charts) == len(fig.data):
        fig.update_layout(hovermode="x unified")
    fig.update_traces(selector=dict(type="scatter"), line=dict(width=2), marker=dict(line=dict(color=SURFACE, width=1.5)))
    fig.update_traces(selector=dict(type="bar"), marker=dict(line=dict(color=SURFACE, width=1)))
    return fig


def show(fig: go.Figure, height: int = 420) -> None:
    """Draw a chart with the shared styling and without the toolbar; hover, zoom and the legend still work."""
    st.plotly_chart(tidy(fig, height), width="stretch", theme=None, config={"displayModeBar": False})
