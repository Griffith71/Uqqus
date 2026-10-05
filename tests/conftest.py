import pytest

from legacy_harness import build_legacy


@pytest.fixture
def legacy(monkeypatch):
    return build_legacy(monkeypatch)
