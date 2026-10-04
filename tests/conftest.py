import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_outside_keys(monkeypatch):
    """Tests never reach the real outside providers, and each starts with a clean per-minute log."""
    import providers
    monkeypatch.delenv("CEREBRAS_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    providers._recent.clear()
    providers._cool.clear()
