import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import io
import numpy as np
import pytest
from astropy.io import fits
from spherex_archive import valid_url, decode_image


def test_only_irsa_spherex_dr1_fits_urls():
    assert valid_url('https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/example.fits')
    for url in ['http://localhost/a.fits', 'https://irsa.ipac.caltech.edu.evil.com/ibe/data/spherex/dr1/level2/a.fits', 'https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/../a.fits', 'https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/a.fits?x=1']:
        assert not valid_url(url)


def test_bundled_investigation_preserves_verified_inputs_and_real_coverage():
    import json
    import spherex_archive as archive
    frontend = json.loads(archive.BUNDLED_EVIDENCE.read_text())
    records = archive.bundled_records(frontend['ra'], frontend['dec'])
    assert [item['url'] for item in records] == [item['url'] for item in frontend['inputs']]
    assert [item['date'] for item in records] == [item['date'] for item in frontend['inputs']]
    assert archive.bundled_records(frontend['ra'] + .01, frontend['dec']) == []

    preview = archive.bundled_preview(
        records[0]['url'], frontend['ra'], frontend['dec'], frontend['fov_arcmin'])
    pixels = np.asarray(preview['pixels'], dtype=float)
    assert preview['previewAvailability'] == 'bundled-verified'
    assert preview['fitsSha256'] == frontend['inputs'][0]['fits_sha256']
    assert preview['unit'].replace(' ', '') == 'MJy/sr'
    assert pixels.shape == (128, 128)
    assert np.isfinite(pixels).sum() > 2_000
    assert np.isnan(pixels).any()
    assert archive.bundled_preview(
        records[0]['url'], frontend['ra'] + .01, frontend['dec'], frontend['fov_arcmin']) is None


def test_native_pixels_units_and_missing_values_preserved():
    image = fits.ImageHDU(np.array([[1., np.nan], [-2., 4.]]), name='IMAGE')
    image.header['BUNIT'] = 'MJy / sr'
    stream = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), image]).writeto(stream)
    result = decode_image(stream.getvalue())
    assert result['pixels'] == [[1., None], [-2., 4.]]
    assert result['unit'] == 'MJy / sr'
    assert result['width'] == result['height'] == 2


def test_missing_science_extension_rejected():
    stream = io.BytesIO()
    fits.PrimaryHDU(np.zeros((2, 2))).writeto(stream)
    with pytest.raises(KeyError):
        decode_image(stream.getvalue())


def test_common_grid_preserves_empty_coverage_and_sky_center():
    from wcs_pipeline import build_tan_wcs
    image = fits.ImageHDU(np.ones((9, 9)), header=build_tan_wcs(10, 20, 9, 9, 6).to_header(), name='IMAGE')
    stream = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), image]).writeto(stream)
    aligned = decode_image(stream.getvalue(), 10, 20, 5)
    assert aligned['width'] == aligned['height'] == 128
    assert aligned['pixels'][64][64] == pytest.approx(1)
    assert aligned['pixels'][0][0] is None
    assert 'bilinear' in aligned['processing']


def test_archive_cache_coalesces_requests_and_does_not_cache_failures():
    import asyncio
    from spherex_archive import cached_archive
    async def scenario():
        calls = []
        @cached_archive(2, 60)
        async def fetch(key):
            calls.append(key)
            await asyncio.sleep(.001)
            if key == 'bad':
                raise ValueError('unavailable')
            return {'key': key}
        results = await asyncio.gather(fetch('a'), fetch('a'))
        assert results == [{'key': 'a'}, {'key': 'a'}]
        assert calls == ['a']
        assert await fetch('a') == {'key': 'a'}
        assert calls == ['a']
        for _ in range(2):
            with pytest.raises(ValueError):
                await fetch('bad')
        assert calls == ['a', 'bad', 'bad']
        await fetch('b')
        await fetch('c')
        await fetch('a')
        assert calls.count('a') == 2
    asyncio.run(scenario())


def test_native_spectral_sample_rejects_flagged_pixel():
    from spherex_archive import extract_sample
    from wcs_pipeline import build_tan_wcs
    header = build_tan_wcs(10, 20, 3, 3, 6).to_header()
    image = fits.ImageHDU(np.ones((3, 3)), header=header, name='IMAGE')
    stream = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), image, fits.ImageHDU(np.ones((3, 3), dtype=np.int32), name='FLAGS'), fits.ImageHDU(np.ones((3, 3)), name='VARIANCE')]).writeto(stream)
    # Reject quality flags before attempting to interpret a spectral WCS.
    assert extract_sample(stream.getvalue(), 10, 20) is None


def test_source_flag_is_informational_and_variance_is_preserved(monkeypatch):
    import spherex_archive
    from types import SimpleNamespace
    from wcs_pipeline import build_tan_wcs
    real_wcs = spherex_archive.WCS
    def wcs(header, *args, **kwargs):
        if kwargs.get('key') == 'W':
            return SimpleNamespace(sip=None, all_pix2world=lambda pixels, origin: np.array([[3.05, .08]]))
        return real_wcs(header)
    monkeypatch.setattr(spherex_archive, 'WCS', wcs)
    image = fits.ImageHDU(np.full((3, 3), 4.), header=build_tan_wcs(10, 20, 3, 3, 6).to_header(), name='IMAGE')
    image.header['BUNIT'] = 'MJy / sr'
    stream = io.BytesIO()
    fits.HDUList([fits.PrimaryHDU(), image, fits.ImageHDU(np.full((3, 3), 1 << 21, dtype=np.int32), name='FLAGS'), fits.ImageHDU(np.full((3, 3), .25), name='VARIANCE')]).writeto(stream)
    sample = spherex_archive.extract_sample(stream.getvalue(), 10, 20)
    assert sample['flux'] == 4
    assert sample['sigma'] == .5
    assert sample['wavelength'] == 3.05
    assert sample['flags'] == 1 << 21


def test_sampling_distinguishes_archive_failure_from_rejected_measurements(monkeypatch):
    import asyncio
    import spherex_archive as archive
    record = {'url': 'https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/test.fits', 'minUm': 1, 'mjd': 60000, 'date': '2025-08-15'}
    async def discover(ra, dec):
        return [record]
    async def failed_download(*args):
        raise RuntimeError('archive offline')
    async def successful_download(*args):
        return b'fixture'
    monkeypatch.setattr(archive, 'discover', discover)
    monkeypatch.setattr(archive, 'download_fits', failed_download)
    with pytest.raises(RuntimeError, match='cutout requests failed'):
        asyncio.run(archive.spectral_samples(1.234, 2.345))
    monkeypatch.setattr(archive, 'download_fits', successful_download)
    monkeypatch.setattr(archive, 'extract_sample', lambda *args: {'wavelength': 3.05, 'bandwidth': .08, 'flux': -2., 'sigma': .5, 'unit': 'MJy/sr', 'flags': 0})
    result = asyncio.run(archive.spectral_samples(1.234, 2.345))
    assert result['samples'][0]['flux'] == -2.
    assert result['samples'][0]['sigma'] == .5
    assert result['unavailable'] == 0
    monkeypatch.setattr(archive, 'extract_sample', lambda *args: None)
    result = asyncio.run(archive.spectral_samples(1.235, 2.345))
    assert result['samples'] == []
    assert result['attempted'] == result['rejected'] == 1
    assert result['unavailable'] == 0


def test_progressive_samples_arrive_before_slowest_download(monkeypatch):
    import asyncio
    import spherex_archive as archive
    async def scenario():
        release = asyncio.Event()
        calls = []
        async def discover(*args):
            return [{'url': name, 'minUm': i, 'mjd': i, 'date': '2025-08-15'} for i,name in enumerate(['fast','slow'])]
        async def download(url,*args):
            calls.append(url)
            if url == 'slow':
                await release.wait()
            return b'fixture'
        monkeypatch.setattr(archive,'discover',discover)
        monkeypatch.setattr(archive,'download_fits',download)
        monkeypatch.setattr(archive,'extract_sample',lambda *args: {'wavelength':2.,'flux':1.,'sigma':.1,'bandwidth':.05,'unit':'MJy/sr'})
        updates=archive.spectral_sample_updates(2.123,3.234)
        assert not (await anext(updates))['complete']
        partial=await anext(updates)
        assert len(partial['samples']) == 1
        assert not partial['complete'] and not release.is_set()
        release.set()
        final=await anext(updates)
        assert final['complete'] and len(final['samples']) == 2
        await updates.aclose()
        cached=[v async for v in archive.spectral_sample_updates(2.123,3.234)]
        assert len(cached)==1 and cached[0]['complete']
        assert len(calls)==2
    asyncio.run(scenario())
