from __future__ import annotations

import os
import pytest


@pytest.fixture(autouse=True)
def isolated_app_data(tmp_path, monkeypatch):
    """Each test gets an isolated SQLite/config directory."""
    monkeypatch.setenv("OPEDTAGGER_DATA_DIR", str(tmp_path / "appdata"))
    monkeypatch.delenv("OPEDTAGGER_CONFIG_PATH", raising=False)
    yield
