"""Scenario parameters: how big the bank is and how late and messy its data arrives."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta


@dataclass(frozen=True)
class Scenario:
    name: str
    seed: int
    n_customers: int
    period_start: date
    initial_extract_date: date
    replay_days: int
    same_day_share: float
    late_tail_mean_days: float
    late_max_days: int
    correction_rate: float
    correction_lag_mean_days: float
    correction_max_lag_days: int
    update_share: float
    customer_changes_per_month: float
    foreign_share: float
    duplicate_rate: float

    @property
    def last_day(self) -> date:
        """Last replay day; the last extract covers changes up to and including it."""
        return self.initial_extract_date + timedelta(days=self.replay_days)

    @property
    def replay_dates(self) -> list[date]:
        return [self.initial_extract_date + timedelta(days=i) for i in range(1, self.replay_days + 1)]

    def with_size(self, n_customers: int | None = None, replay_days: int | None = None) -> "Scenario":
        changes = {}
        if n_customers is not None:
            changes["n_customers"] = n_customers
        if replay_days is not None:
            changes["replay_days"] = replay_days
        return replace(self, **changes) if changes else self


_BASE = dict(
    n_customers=3000,
    period_start=date(2023, 1, 1),
    initial_extract_date=date(2024, 11, 30),
    replay_days=30,
    update_share=0.7,
    customer_changes_per_month=0.015,
    foreign_share=0.06,
    duplicate_rate=0.002,
)

SCENARIOS: dict[str, Scenario] = {
    "mild": Scenario(
        name="mild",
        seed=20231,
        same_day_share=0.88,
        late_tail_mean_days=2.0,
        late_max_days=14,
        correction_rate=0.01,
        correction_lag_mean_days=3.0,
        correction_max_lag_days=45,
        **_BASE,
    ),
    "heavy": Scenario(
        name="heavy",
        seed=20232,
        same_day_share=0.60,
        late_tail_mean_days=5.0,
        late_max_days=30,
        correction_rate=0.04,
        correction_lag_mean_days=6.0,
        correction_max_lag_days=45,
        **_BASE,
    ),
}


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]
    except KeyError:
        raise SystemExit(f"unknown scenario {name!r}; choose from {', '.join(SCENARIOS)}") from None
