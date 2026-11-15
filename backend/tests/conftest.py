import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def isolated_archive_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('SPHEREX_CACHE_DIR', str(tmp_path / 'archive-cache'))
    monkeypatch.setenv('SPHEREX_PREWARM', 'false')
