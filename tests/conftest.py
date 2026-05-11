"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from src.config import get_env, get_settings, get_strategy_params


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session")
def env():
    return get_env()


@pytest.fixture(scope="session")
def strategy_params():
    return get_strategy_params()
