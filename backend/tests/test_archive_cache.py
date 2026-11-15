import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import archive_cache
from spherex_archive import cached_archive


def test_persistent_preview_survives_new_worker_and_archive_failure(monkeypatch, tmp_path):
    monkeypatch.setenv('SPHEREX_CACHE_DIR', str(tmp_path))
    args = ('https://irsa.ipac.caltech.edu/observed.fits', 133.7951, -7.2451, 5.)
    value = {'pixels': [[1., None]], 'unit': 'MJy/sr', 'source': args[0]}
    archive_cache.write('test-preview', args, value)
    archive_cache._store.cache_clear()
    calls = []
    @cached_archive(2, 3600, persistent='test-preview')
    async def unavailable(*args):
        calls.append(args)
        raise RuntimeError('archive offline')
    assert asyncio.run(unavailable(*args)) == value
    assert calls == []
    with pytest.raises(RuntimeError):
        asyncio.run(unavailable(args[0], 133.7952, -7.2451, 5.))
    assert len(calls) == 1


def test_stale_discovery_returns_immediately_and_failed_refresh_keeps_cache(monkeypatch, tmp_path):
    monkeypatch.setenv('SPHEREX_CACHE_DIR', str(tmp_path))
    value = [{'url': 'real.fits', 'date': '2026-03-19'}]
    archive_cache.write('test-discovery', (10., 20.), value)
    @cached_archive(2, -1, persistent='test-discovery')
    async def unavailable(*args):
        raise RuntimeError('offline')
    async def scenario():
        assert await unavailable(10., 20.) == value
        await asyncio.sleep(.01)
        assert archive_cache.read('test-discovery', (10., 20.))[1] == value
    asyncio.run(scenario())


def test_cache_does_not_write_nonfinite_values(monkeypatch, tmp_path):
    monkeypatch.setenv('SPHEREX_CACHE_DIR', str(tmp_path))
    with pytest.raises(ValueError):
        archive_cache.write('test', (1,), {'flux': float('nan')})
    assert archive_cache.read('test', (1,)) is None
