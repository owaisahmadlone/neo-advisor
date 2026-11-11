from __future__ import annotations

from pathlib import Path

import pytest

from neo.domain.fixtures import FixtureSpec, state_from_registry
from neo.domain.registry import Registry
from neo.domain.state import NetworkState

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def registry() -> Registry:
    return Registry.load(ROOT / "configs" / "reference")


@pytest.fixture(scope="session")
def state(registry: Registry) -> NetworkState:
    return state_from_registry(registry)


@pytest.fixture(scope="session")
def fixture_state(registry: Registry):
    def make(name: str) -> NetworkState:
        return state_from_registry(registry, FixtureSpec.load(ROOT / "configs" / "fixtures" / f"{name}.yaml"))

    return make
