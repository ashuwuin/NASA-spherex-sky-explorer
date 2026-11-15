"""Warm real SPHEREx data for the five Explore presets, also usable as a CLI."""
import asyncio
import logging

from moc_index import BENCHMARK_CATALOG
from spherex_archive import bundled_records, cutout, discover, spectral_samples

logger = logging.getLogger(__name__)


async def warm_target(name, target, measurements=True):
    ra, dec = target['ra'], target['dec']
    records = bundled_records(ra, dec) or await discover(ra, dec)
    if not records:
        return {'target': name, 'images': 0, 'status': 'no observations'}
    first = records[0]
    matching = [r for r in records if (r['minUm'], r['maxUm']) == (first['minUm'], first['maxUm'])]
    chosen = {r['url']: r for r in (matching[0], matching[len(matching)//2], matching[-1])}
    images = await asyncio.gather(*(cutout(r['url'], ra, dec, 5.0) for r in chosen.values()), return_exceptions=True)
    count = sum(not isinstance(value, BaseException) for value in images)
    result = {'target': name, 'images': count, 'requested': len(chosen), 'status': 'ready' if count == len(chosen) else 'partial'}
    if measurements:
        try:
            values = await spectral_samples(ra, dec)
            result['samples'] = len(values['samples'])
        except Exception as exc:
            result['samples'] = 'unavailable'
            logger.warning('SPHEREx measurements unavailable for %s: %s', name, type(exc).__name__)
    return result


async def warm_presets():
    semaphore = asyncio.Semaphore(2)
    async def warm(name, target):
        async with semaphore:
            try:
                result = await warm_target(name, target)
                logger.info('SPHEREx cache warm: %s', result)
                return result
            except Exception as exc:
                logger.warning('SPHEREx cache warm failed for %s: %s', name, type(exc).__name__)
                return {'target': name, 'status': 'archive unavailable', 'images': 0}
    return await asyncio.gather(*(warm(name, target) for name, target in list(BENCHMARK_CATALOG.items())[:5]))


async def maintain_presets():
    """Retry unavailable presets without delaying application startup."""
    while True:
        await warm_presets()
        await asyncio.sleep(300)


if __name__ == '__main__':
    import json
    print(json.dumps(asyncio.run(warm_presets()), indent=2))
