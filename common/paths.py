"""Locations of the local files the pipeline reads and writes.

Every path is derived from the scenario name so that two scenarios can live
side by side. The default scenario (``mild``) uses the plain file names from
the project layout; other scenarios get a suffix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = "mild"


@dataclass(frozen=True)
class Paths:
    """File locations for one scenario."""

    scenario: str = DEFAULT_SCENARIO
    root: Path = field(default=REPO_ROOT)

    @property
    def _suffix(self) -> str:
        return "" if self.scenario == DEFAULT_SCENARIO else f"_{self.scenario}"

    @property
    def core_db(self) -> Path:
        """The on-prem core banking system, a DuckDB file."""
        return self.root / "onprem" / f"core_bank{self._suffix}.duckdb"

    @property
    def truth_db(self) -> Path:
        """The generator's ground truth: every record version and when it arrived."""
        return self.root / "data" / f"truth{self._suffix}.duckdb"

    @property
    def landing_dir(self) -> Path:
        """Where the extract job writes the daily legacy files, one folder per day."""
        return self.root / "landing" / self.scenario

    @property
    def warehouse_db(self) -> Path:
        """The DuckDB warehouse used by the dbt ``dev`` target."""
        return self.root / "data" / f"late_data_lab{self._suffix}.duckdb"

    @property
    def fx_cache(self) -> Path:
        """Committed cache of Norges Bank exchange rates; source data, so always in the repository."""
        return REPO_ROOT / "data" / "fx_rates_nok.csv"

    @property
    def experiment_dir(self) -> Path:
        return self.root / "experiment" / "results"

    @property
    def demo_data_dir(self) -> Path:
        return self.root / "demo" / "data"


def day_folder(landing_dir: Path, day) -> Path:
    """Landing folder for one extract day, named YYYYMMDD so it sorts by date."""
    return landing_dir / day.strftime("%Y%m%d")
