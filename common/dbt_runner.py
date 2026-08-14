"""Run dbt from Python, in process, and read back what each node did.

The experiment harness, the reconciliation and the tests all go through
this module so that the DuckDB file and the query tag are chosen in one
place: ``duckdb_path`` is passed to the profile as LDL_DUCKDB_PATH and
``query_tag`` as DBT_QUERY_TAG.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from common.paths import REPO_ROOT

PROJECT_DIR = REPO_ROOT / "transform"


class DbtError(RuntimeError):
    pass


@dataclass(frozen=True)
class NodeResult:
    unique_id: str
    name: str
    resource_type: str
    status: str
    execution_time: float
    rows_affected: int | None
    message: str | None


@contextmanager
def _environment(overrides: dict[str, str | None]):
    saved = {k: os.environ.get(k) for k in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def run_dbt(
    args: list[str],
    *,
    target: str = "dev",
    duckdb_path: Path | None = None,
    query_tag: str | None = None,
    vars: dict | None = None,
    quiet: bool = True,
    check: bool = True,
    work_tag: str | None = None,
) -> list[NodeResult]:
    """Invoke a dbt command and return one result per executed node.

    ``work_tag`` gives the invocation its own target and log folders under the
    project, so several processes can run dbt on the project at the same time.
    """
    from dbt.cli.main import dbtRunner

    full = list(args) + ["--project-dir", str(PROJECT_DIR), "--target", target]
    if work_tag:
        full += ["--target-path", str(PROJECT_DIR / "target" / work_tag), "--log-path", str(PROJECT_DIR / "logs" / work_tag)]
    if vars:
        rendered = ", ".join(f"{k}: {v}" for k, v in vars.items() if v is not None)
        full += ["--vars", "{" + rendered + "}"]
    if quiet:
        full += ["--quiet"]
    overrides = {
        "LDL_DUCKDB_PATH": str(duckdb_path) if duckdb_path else None,
        "DBT_QUERY_TAG": query_tag,
    }
    with _environment(overrides):
        result = dbtRunner().invoke(full)

    nodes = node_results(result)
    if check and not result.success:
        failed = [f"{n.name} ({n.status}): {n.message}" for n in nodes if n.status not in ("success", "pass", "skipped")]
        detail = "; ".join(failed) or (str(result.exception) if result.exception else "unknown error")
        raise DbtError(f"dbt {' '.join(args)} failed: {detail}")
    return nodes


def node_results(result) -> list[NodeResult]:
    run_results = getattr(result, "result", None)
    rows = getattr(run_results, "results", None)
    if not rows:
        return []
    out = []
    for r in rows:
        node = r.node
        response = getattr(r, "adapter_response", None) or {}
        rows_affected = response.get("rows_affected") if isinstance(response, dict) else None
        out.append(
            NodeResult(
                unique_id=node.unique_id,
                name=node.name,
                resource_type=str(node.resource_type).split(".")[-1].lower(),
                status=str(r.status).split(".")[-1].lower(),
                execution_time=float(r.execution_time or 0.0),
                rows_affected=int(rows_affected) if rows_affected is not None else None,
                message=r.message,
            )
        )
    return out
