"""Persistent, bounded cache for successful archive results (standard library only)."""
import gzip
import json
import os
import time
from functools import lru_cache
from pathlib import Path

from cache import BoundedLRUCache


@lru_cache(maxsize=4)
def _store(directory):
    return BoundedLRUCache(cache_dir=directory, max_mem_items=8,
                           high_watermark_bytes=256 * 1024 * 1024,
                           low_watermark_bytes=192 * 1024 * 1024)


def store():
    root = os.environ.get('SPHEREX_CACHE_DIR') or str(Path(__file__).parent / 'cache/spherex')
    return _store(str(Path(root) / 'archive-v1'))


def key(namespace, args):
    return BoundedLRUCache.make_key('archive-v1', namespace, json.dumps(args, separators=(',', ':')))


def read(namespace, args):
    try:
        entry = store().get(key(namespace, args))
    except (ValueError, OSError):
        return None
    if entry is None:
        return None
    payload, metadata = entry
    try:
        value = json.loads(gzip.decompress(payload))
        age = max(0, time.time() - metadata['saved_at'])
        return age, value
    except (ValueError, OSError, KeyError, EOFError):
        return None


def write(namespace, args, value):
    payload = gzip.compress(json.dumps(value, allow_nan=False, separators=(',', ':')).encode())
    store().put(key(namespace, args), payload, {'saved_at': time.time(), 'namespace': namespace})
