"""Shared fixtures for Bottom Flow Divergence Screener tests."""

import json
import os
import sys

import pytest

# Make the scripts/ modules importable (mirrors swing-short-screener convention).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample.json")


@pytest.fixture
def fixture_path() -> str:
    return FIXTURE_PATH


@pytest.fixture
def fixture_rows() -> list:
    with open(FIXTURE_PATH, encoding="utf-8") as fh:
        return json.load(fh)["rows"]


def row_by_symbol(rows: list, symbol: str) -> dict:
    for row in rows:
        if row["symbol"].split(":")[-1] == symbol:
            return row
    raise KeyError(symbol)
