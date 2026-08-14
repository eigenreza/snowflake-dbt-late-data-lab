"""Shared fixtures: a small bank built once per session, copied fresh for tests that change it."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from common import fx
from common.paths import Paths
from generator.build_core_bank import build, generate
from generator.scenarios import Scenario, get_scenario


@pytest.fixture(scope="session")
def small_scenario() -> Scenario:
    return get_scenario("mild").with_size(n_customers=120, replay_days=5)


@pytest.fixture(scope="session")
def fx_published():
    return fx.read_cache(Paths().fx_cache)


@pytest.fixture(scope="session")
def bank_data(small_scenario, fx_published):
    """Entities, transaction versions and the daily FX calendar, in memory."""
    return generate(small_scenario, fx_published)


@pytest.fixture(scope="session")
def built_root(tmp_path_factory, small_scenario) -> Path:
    root = tmp_path_factory.mktemp("bank")
    build(small_scenario, Paths("mild", root), verbose=False)
    return root


@pytest.fixture(scope="session")
def built_paths(built_root) -> Paths:
    """Read-only view of the built bank. Tests that change it use ``fresh_paths``."""
    return Paths("mild", built_root)


@pytest.fixture
def fresh_paths(built_root, tmp_path) -> Paths:
    """A private copy of the built bank for tests that advance days or run extracts."""
    src = Paths("mild", built_root)
    dst = Paths("mild", tmp_path)
    dst.core_db.parent.mkdir(parents=True, exist_ok=True)
    dst.truth_db.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src.core_db, dst.core_db)
    shutil.copy(src.truth_db, dst.truth_db)
    return dst
