"""Draw the static figures for the README and the docs from the exported demo data.

Usage:
    python -m experiment.plot_results [--data demo/data] [--out docs/img]
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from common.paths import REPO_ROOT  # noqa: E402

ORDER = ["full_refresh", "append_only", "merge_w0", "merge_w1", "merge_w3", "merge_w7",
         "delete_insert_w0", "delete_insert_w1", "delete_insert_w3", "delete_insert_w7"]
LABELS = {
    "full_refresh": "full refresh", "append_only": "append-only",
    "merge_w0": "merge W=0", "merge_w1": "merge W=1", "merge_w3": "merge W=3", "merge_w7": "merge W=7",
    "delete_insert_w0": "delete+insert W=0", "delete_insert_w1": "delete+insert W=1",
    "delete_insert_w3": "delete+insert W=3", "delete_insert_w7": "delete+insert W=7",
}
COLORS = {
    "full_refresh": "#4a4a4a", "append_only": "#c0392b",
    "merge_w0": "#9ecae1", "merge_w1": "#6baed6", "merge_w3": "#3182bd", "merge_w7": "#08519c",
    "delete_insert_w0": "#a1d99b", "delete_insert_w1": "#74c476", "delete_insert_w3": "#31a354", "delete_insert_w7": "#006d2c",
}
MARKERS = {"full_refresh": "s", "append_only": "X", "merge": "o", "delete_insert": "D"}
SCENARIOS = {"mild": "mild lateness", "heavy": "heavy lateness"}


def family(key: str) -> str:
    return key.split("_w")[0]


DUCKDB_COST = ("incremental_model_seconds", "execution time of the three strategy-dependent models on DuckDB (seconds)")


def cost_bases(summary: pd.DataFrame, basis: dict) -> list[tuple[str, str]]:
    """The cost measures to draw, one row each: Snowflake credits where a grid ran there, then the DuckDB seconds."""
    bases = []
    col = basis.get("column")
    if col and col != DUCKDB_COST[0] and col in summary and summary[col].notna().any():
        bases.append((col, basis.get("label", col)))
    if DUCKDB_COST[0] in summary and summary[DUCKDB_COST[0]].notna().any():
        bases.append(DUCKDB_COST)
    else:
        bases.append(("dbt_execution_seconds", "dbt execution time on DuckDB (seconds)"))
    return bases


def strategy_race(summary: pd.DataFrame, basis: dict, out: Path) -> None:
    scenarios = [s for s in SCENARIOS if s in set(summary["scenario"])]
    bases = cost_bases(summary, basis)
    fig, axes = plt.subplots(len(bases), len(scenarios), figsize=(5.2 * len(scenarios), 4.3 * len(bases) + 0.6),
                             sharey=True, squeeze=False)
    for r, (row, (cost_col, cost_label)) in enumerate(zip(axes, bases)):
        plotted: list[float] = []
        for ax, scenario in zip(row, scenarios):
            s = summary[summary["scenario"] == scenario].set_index("run_key")
            for key in ORDER:
                if key not in s.index or pd.isna(s.loc[key, cost_col]):
                    continue
                plotted.append(float(s.loc[key, cost_col]))
                ax.scatter(s.loc[key, cost_col], s.loc[key, "mean_wrong_share"] * 100, s=90, color=COLORS[key],
                           marker=MARKERS[family(key)], label=LABELS[key], zorder=3, edgecolor="white", linewidth=0.6)
            if r == 0:
                ax.set_title(SCENARIOS[scenario])
            ax.set_xlabel("\n".join(textwrap.wrap(cost_label, 52)), fontsize=9)
            ax.grid(True, alpha=0.3)
            ax.set_axisbelow(True)
        for ax in row:  # one scale per cost measure, from zero
            ax.set_xlim(0, max(plotted) * 1.08 if plotted else 1)
        row[0].set_ylabel("KPI cells wrong, average over the replay (%)")
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=5, frameon=False, fontsize=8.5, bbox_to_anchor=(0.5, -0.01))
    fig.suptitle("Correctness versus cost of the loading strategies", fontsize=12)
    fig.tight_layout(rect=(0, 0.14 if len(bases) == 1 else 0.07, 1, 0.96))
    fig.savefig(out / "strategy_race.png", dpi=160)
    plt.close(fig)


def error_over_cycles(errors: pd.DataFrame, floor: pd.DataFrame, kpi: str, out: Path, name: str, title: str) -> None:
    scenarios = [s for s in SCENARIOS if s in set(errors["scenario"])]
    fig, axes = plt.subplots(1, len(scenarios), figsize=(5.2 * len(scenarios), 4.2), sharey=True, squeeze=False)
    for ax, scenario in zip(axes[0], scenarios):
        e = errors[(errors["scenario"] == scenario) & (errors["kpi"] == kpi) & (errors["cycle"] > 0)]
        for key in ORDER:
            k = e[e["run_key"] == key].sort_values("cycle")
            if k.empty:
                continue
            ax.plot(k["cycle"], k["wrong_share"] * 100, color=COLORS[key], label=LABELS[key],
                    marker=MARKERS[family(key)], markersize=3.5, linewidth=1.4)
        f = floor[(floor["scenario"] == scenario) & (floor["kpi"] == kpi) & (floor["cycle"] > 0)].sort_values("cycle")
        if not f.empty:
            ax.plot(f["cycle"], f["wrong_share"] * 100, color="#888", linestyle="--", label="lateness floor")
        ax.set_title(SCENARIOS[scenario])
        ax.set_xlabel("replay day")
        ax.grid(True, alpha=0.3)
    axes[0][0].set_ylabel("cells wrong (%)")
    handles, labels = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=6, frameon=False, fontsize=8, bbox_to_anchor=(0.5, -0.02))
    fig.suptitle(title, fontsize=12)
    fig.tight_layout(rect=(0, 0.12, 1, 0.95))
    fig.savefig(out / name, dpi=160)
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=REPO_ROOT / "demo" / "data")
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "docs" / "img")
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    summary = pd.read_parquet(args.data / "summary.parquet")
    errors = pd.read_parquet(args.data / "kpi_errors.parquet")
    floor = pd.read_parquet(args.data / "lateness_floor.parquet")
    manifest = json.loads((args.data / "manifest.json").read_text(encoding="utf-8"))
    strategy_race(summary, manifest.get("cost_basis", {}), args.out)
    error_over_cycles(errors, floor, "daily_balance", args.out, "error_daily_balance.png", "Daily account balances: share of wrong cells after each replay day")
    error_over_cycles(errors, floor, "monthly_category_region", args.out, "error_monthly.png", "Monthly value by category and region: share of wrong cells after each replay day")
    print(f"figures written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
