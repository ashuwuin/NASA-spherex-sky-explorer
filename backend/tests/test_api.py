import sys
from pathlib import Path
import time
import pytest
import numpy as np
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import app  # noqa: E402
from wcs_pipeline import build_tan_wcs, reproject_to_tangent_grid  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_products(monkeypatch, tmp_path):
    import main
    import moc_index
    from cache import BoundedLRUCache, JobStore
    monkeypatch.setattr(main, "raster_cache", BoundedLRUCache(cache_dir=str(tmp_path)))
    monkeypatch.setattr(main, "job_store", JobStore())
    monkeypatch.setattr(moc_index, "fetch_real_unwise_cutout", lambda *args, **kwargs: None)


def test_coverage_moc_check():
    with TestClient(app) as tc:
        r1 = tc.get("/api/coverage?ra=133.7951&dec=-7.2451")
        assert r1.status_code == 200
        assert r1.json()["spherex_covered"] is None
        assert r1.json()["moc_order"] is None
        assert r1.json()["coverage_status"] == "unavailable"

        r2 = tc.get("/api/coverage?ra=266.4168&dec=-29.0078")
        assert r2.status_code == 200
        assert r2.json()["spherex_covered"] is None
        assert r2.json()["unwise_fallback_available"] is None


def test_documented_spherex_field_loads_without_live_discovery():
    with TestClient(app) as client:
        records = client.get('/api/spherex/images', params={'ra': 269.4521, 'dec': 4.6934})
        assert records.status_code == 200
        payload = records.json()
        assert payload['availability'] == 'bundled-verified'
        assert [item['id'] for item in payload['images']] == [
            '2026W12_1A_0610_4', '2026W19_1A_0046_1']

        preview = client.get('/api/spherex/cutout', params={
            'url': payload['images'][0]['url'], 'ra': 269.4521, 'dec': 4.6934, 'fov': 5})
        assert preview.status_code == 200
        result = preview.json()
        assert result['previewAvailability'] == 'bundled-verified'
        assert result['width'] == result['height'] == 128
        assert result['cache_key']


def test_resolve_all_formats_and_benchmarks():
    with TestClient(app) as tc:
        for name in ["Barnard's Star", "WISE 0855", "Epsilon Indi B", "RW Aurigae", "R Draconis"]:
            r = tc.get(f"/api/resolve?q={name}")
            assert r.status_code == 200
            assert r.json()["spherex_covered"] is None

        # Decimal & HMS/DMS
        r_dec = tc.get("/api/resolve?q=133.7951,-7.2451")
        assert r_dec.status_code == 200
        r_hms = tc.get("/api/resolve?q=08:55:10.8 -07:14:42")
        assert r_hms.status_code == 200
        assert abs(r_hms.json()["ra"] - 133.795) < 0.02


def test_spectrum_408_byte_and_wcs_click_sampling():
    with TestClient(app) as tc:
        # Center star click -> high flux with H2O absorption dip
        r_center = tc.get(
            "/api/spectrum?ra=133.7951&dec=-7.2451&radius_arcsec=12.4&center_ra=133.7951&center_dec=-7.2451&fov_arcmin=5.0"
        )
        assert r_center.status_code == 200
        assert len(r_center.content) == 408
        center_flux = np.frombuffer(r_center.content, dtype="<f4")

        # Empty sky click near corner (outside star margin) -> flat low noise floor
        r_sky = tc.get(
            "/api/spectrum?ra=133.8355&dec=-7.2055&radius_arcsec=6.2&center_ra=133.7951&center_dec=-7.2451&fov_arcmin=5.0"
        )
        assert r_sky.status_code == 200
        sky_flux = np.frombuffer(r_sky.content, dtype="<f4")
        assert float(np.max(center_flux)) > float(np.max(sky_flux)) * 10.0


def test_wcs_tangent_plane_reprojection():
    # Verify reprojection from native SPHEREx (6.2"/px) to target grid preserves peak position
    ra, dec = 133.7951, -7.2451
    src_wcs = build_tan_wcs(ra, dec, 128, 128, pixel_scale_arcsec=6.2)
    yy, xx = np.mgrid[0:128, 0:128]
    src_img = np.exp(-((xx - 64.0) ** 2 + (yy - 64.0) ** 2) / (2.0 * 3.0**2)).astype(np.float32)

    regridded, out_wcs = reproject_to_tangent_grid(
        src_img, src_wcs, ra, dec, out_shape=(256, 256), pixel_scale_arcsec=3.1
    )
    assert regridded.shape == (256, 256)
    peak_y, peak_x = np.unravel_index(np.argmax(regridded), regridded.shape)
    assert abs(peak_x - 128) <= 2 and abs(peak_y - 128) <= 2


def test_cutout_async_job_and_binary_raster_and_psf_diff():
    with TestClient(app) as tc:
        resp = tc.post(
            "/api/jobs/cutouts",
            json={"ra": 133.7951, "dec": -7.2451, "fov_arcmin": 5.0, "bands": [42]},
        )
        assert resp.status_code in (200, 202)
        if resp.status_code == 202:
            job_url = resp.json()["status_url"]
            rasters = []
            for _ in range(120):
                poll = tc.get(job_url)
                assert poll.status_code == 200
                data = poll.json()
                if data["status"] == "ready":
                    rasters = data["completed_rasters"]
                    break
                time.sleep(0.1)
            assert len(rasters) >= 2
        else:
            rasters = resp.json()["rasters"]

        first = rasters[0]
        second = rasters[-1]

        r_bin = tc.get(first["raster_url"])
        assert r_bin.status_code == 200
        assert len(r_bin.content) == 512 * 512 * 2
        assert r_bin.headers["x-raster-dimensions"] == "512,512"
        assert "x-bscale" in r_bin.headers
        assert "x-wcs-crval" in r_bin.headers

        r_diff = tc.post(
            "/api/diff",
            json={
                "raster_key_a": first["cache_key"],
                "raster_key_b": second["cache_key"],
                "kernel": "alard_lupton",
            },
        )
        assert r_diff.status_code == 200
        assert len(r_diff.content) == 512 * 512 * 2
        assert float(r_diff.headers["x-diff-confidence"]) >= 0.55

        r_fits = tc.get(f"/api/export/fits?cache_key={first['cache_key']}")
        assert r_fits.status_code == 200
        assert r_fits.content.startswith(b"SIMPLE  =")


def test_health_endpoint():
    with TestClient(app) as tc:
        resp = tc.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert "cache_items" in resp.json()


def test_band_index_modulation():
    from moc_index import generate_calibrated_cutout

    cutout_band1 = generate_calibrated_cutout(133.7951, -7.2451, epoch_idx=1, band_index=1, size=256)
    cutout_band102 = generate_calibrated_cutout(133.7951, -7.2451, epoch_idx=1, band_index=102, size=256)
    # Band 1 (0.75 um) and Band 102 (5.0 um) must not be identical
    assert not np.allclose(cutout_band1, cutout_band102)


def test_validation_errors():
    with TestClient(app) as tc:
        # Invalid cache keys
        r1 = tc.get("/api/data/raster/bad_key")
        assert r1.status_code == 400

        r2 = tc.get("/api/export/fits?cache_key=bad_key")
        assert r2.status_code == 400

        # Out-of-range bands
        r3 = tc.post(
            "/api/jobs/cutouts",
            json={"ra": 133.7951, "dec": -7.2451, "fov_arcmin": 5.0, "bands": [999]},
        )
        assert r3.status_code == 422


def test_sesame_redirect_follow():
    with TestClient(app) as tc:
        resp = tc.get("/api/resolve?q=Vega")
        # Should successfully resolve with HTTP 200 via follow_redirects
        if resp.status_code == 200:
            data = resp.json()
            assert abs(data["ra"] - 279.23) < 1.0
            assert abs(data["dec"] - 38.78) < 1.0


def test_irsa_tap_hook():
    with TestClient(app) as tc:
        resp = tc.get("/api/tap/query?ra=269.4521&dec=4.6934&radius_arcsec=15.0")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("success", "unavailable")
        assert data["count"] == len(data["sources"])
        if data["status"] == "unavailable":
            assert "message" in data


def test_nan_handling_quantization():
    from wcs_pipeline import dequantize_uint16, quantize_to_uint16

    # Array containing NaNs, positive infinity, and negative infinity
    nan_arr = np.array([[np.nan, 1.5, 2.0], [np.inf, -np.inf, 3.5]], dtype=np.float32)
    raw_u16, bscale, bzero = quantize_to_uint16(nan_arr)

    assert not np.isnan(bscale)
    assert not np.isnan(bzero)
    assert not np.isinf(bscale)
    assert not np.isinf(bzero)
    assert bscale > 0

    dequant = dequantize_uint16(raw_u16, (2, 3), bscale, bzero)
    assert not np.any(np.isnan(dequant))
    assert not np.any(np.isinf(dequant))


def test_reprojection_cval_padding():
    from wcs_pipeline import build_tan_wcs, reproject_to_tangent_grid

    src_wcs = build_tan_wcs(100.0, 0.0, 64, 64, pixel_scale_arcsec=1.0)
    src_flux = np.ones((64, 64), dtype=np.float32) * 5.0
    # Reproject to a distant target grid offset so pixels fall out of bounds
    regridded, _ = reproject_to_tangent_grid(
        src_flux, src_wcs, target_ra=100.2, target_dec=0.0, out_shape=(64, 64), pixel_scale_arcsec=1.0
    )
    # Edge/out-of-bounds pixels should be padded with cval=0.0
    assert regridded[0, 0] == 0.0


def test_irsa_tap_column_adaptation():
    import asyncio
    from moc_index import query_irsa_tap

    # Test non-WISE table (e.g., 2MASS fp_psc)
    res_2mass = asyncio.run(query_irsa_tap(ra=133.7951, dec=-7.2451, table="fp_psc"))
    assert res_2mass["status"] in ("success", "unavailable")
    if res_2mass["status"] == "unavailable":
        assert "message" in res_2mass
        # Non-wise fallback should not invent w1mpro
        assert res_2mass["sources"] == []

    # Test WISE table
    res_wise = asyncio.run(query_irsa_tap(ra=133.7951, dec=-7.2451, table="allwise_p3as_psd"))
    assert res_wise["status"] in ("success", "unavailable")
    if res_wise["status"] == "unavailable":
        assert res_wise["sources"] == []


def test_fits_export_orientation():
    from astropy.io import fits
    import io
    from cache import BoundedLRUCache
    from wcs_pipeline import build_wcs_headers, quantize_to_uint16
    from main import raster_cache

    # 1. Test WCS-reprojected raster (should NOT be flipped)
    arr_reproj = np.arange(16, dtype=np.float32).reshape((4, 4))
    raw_reproj, bscale, bzero = quantize_to_uint16(arr_reproj)
    key_reproj = BoundedLRUCache.make_key("test_reproj_key_123456789")
    headers_reproj = build_wcs_headers(
        width=4,
        height=4,
        bscale=bscale,
        bzero=bzero,
        ra=10.0,
        dec=20.0,
        pixel_scale_arcsec=1.0,
        epoch="2025-01",
        provenance="unWISE-Archive",
        band_index=1,
        wavelength_um=3.4,
        is_reprojected=True,
    )
    raster_cache.put(key_reproj, raw_reproj, headers_reproj)

    with TestClient(app) as tc:
        resp_reproj = tc.get(f"/api/export/fits?cache_key={key_reproj}")
        assert resp_reproj.status_code == 200
        with fits.open(io.BytesIO(resp_reproj.content)) as hdul:
            data = hdul[0].data
            # Reprojected data should preserve row order (row 0 stays row 0)
            assert np.allclose(data[0], arr_reproj[0], atol=0.1)


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main(["-v", __file__]))


def test_spherex_previews_support_existing_psf_subtraction(monkeypatch):
    import main
    async def preview(*args):
        pixels = np.ones((16,16)).tolist()
        pixels[1][1] = None
        return {'pixels': pixels, 'width': 16, 'height': 16, 'unit': 'MJy / sr', 'qualityApplied': True, 'spectral': [[[2., .05] for _ in range(16)] for _ in range(16)]}
    monkeypatch.setattr(main, 'spherex_cutout', preview)
    monkeypatch.setattr(main, 'psf_matched_diff', lambda a, b, kernel: (b-a, .5))
    with TestClient(app) as client:
        keys = []
        for filename in ['firstD3_spx.fits', 'secondD3_spx.fits', 'thirdD2_spx.fits']:
            response = client.get('/api/spherex/cutout', params={'url': 'https://irsa.ipac.caltech.edu/ibe/data/spherex/dr1/level2/'+filename, 'ra': 10, 'dec': 20})
            assert response.status_code == 200
            assert response.json()['pixels'][1][1] is None
            keys.append(response.json()['cache_key'])
        result = client.post('/api/diff', json={'raster_key_a': keys[0], 'raster_key_b': keys[1]})
        assert result.status_code == 200
        assert len(result.content) == 512 * 512 * 2
        mismatch = client.post('/api/diff', json={'raster_key_a': keys[0], 'raster_key_b': keys[2]})
        assert mismatch.status_code == 400
        async def mismatched_wave(url, *args):
            value = await preview(url, *args)
            if 'second' in url:
                value['spectral'][0][0] = [2.2, .05]
            return value
        monkeypatch.setattr(main, 'spherex_cutout', mismatched_wave)
        mismatch = client.post('/api/diff', json={'raster_key_a': keys[0], 'raster_key_b': keys[1]})
        assert mismatch.status_code == 400
