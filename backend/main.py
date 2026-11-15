import asyncio
import io
import re
import uuid
import os
from contextlib import asynccontextmanager
from typing import List, Optional

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from cache import BoundedLRUCache, JobStore
from moc_index import (
    check_spherex_moc,
    generate_cutout_product,
    UNWISE_RELEASES,
    generate_spectrum_vector,
    query_irsa_tap,
    resolve_target_query,
)
from wcs_pipeline import (
    build_wcs_headers,
    dequantize_uint16,
    psf_matched_diff,
    quantize_to_uint16,
)

CACHE_KEY_RE = re.compile(r"^[a-f0-9]{24}$")


def validate_cache_key(key: str) -> str:
    if not CACHE_KEY_RE.match(key):
        raise HTTPException(status_code=400, detail="Invalid cache key format.")
    return key


@asynccontextmanager
async def lifespan(app):
    task = None
    if os.environ.get('SPHEREX_PREWARM', 'false').lower() == 'true':
        from warm_spherex_cache import maintain_presets
        task = asyncio.create_task(maintain_presets())
    try:
        yield
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


app = FastAPI(title="SPHEREx Sky Explorer Proxy API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=[
        "X-Raster-Dimensions",
        "X-Raster-Bitpix",
        "X-BSCALE",
        "X-BZERO",
        "X-BUNIT",
        "X-WCS-CRVAL",
        "X-WCS-CRPIX",
        "X-WCS-CDELT",
        "X-WCS-CTYPE",
        "X-Observation-Epoch",
        "X-Survey-Provenance",
        "X-Band-Index",
        "X-Band-Wavelength-Um",
        "X-Channel-Count",
        "X-Diff-Confidence",
        "X-WCS-Reprojected",
        "X-Data-Kind",
        "X-Source-Release",
        "X-Source-URL",
    ],
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

raster_cache = BoundedLRUCache()
job_store = JobStore()


class CutoutJobRequest(BaseModel):
    ra: float = Field(..., ge=0.0, le=360.0)
    dec: float = Field(..., ge=-90.0, le=90.0)
    fov_arcmin: float = Field(5.0, gt=0.1, le=15.0)
    bands: List[int] = Field(default_factory=lambda: [1, 42, 102])
    epochs: Optional[List[str]] = None

    @field_validator("bands")
    @classmethod
    def validate_bands(cls, v: List[int]) -> List[int]:
        if not v:
            raise ValueError("bands list cannot be empty")
        for b in v:
            if not (0 <= b <= 102):
                raise ValueError(f"band index {b} out of range [0, 102]")
        return list(dict.fromkeys(v))


class DiffRequest(BaseModel):
    raster_key_a: str = Field(..., pattern="^[a-f0-9]{24}$")
    raster_key_b: str = Field(..., pattern="^[a-f0-9]{24}$")
    kernel: str = Field("alard_lupton", pattern="^(alard_lupton|gaussian)$")


@app.get("/api/health")
async def health():
    return {"status": "ok", "cache_items": len(raster_cache._disk_index)}


@app.get("/api/coverage")
async def coverage(ra: float = Query(..., ge=0.0, le=360.0), dec: float = Query(..., ge=-90.0, le=90.0)):
    return check_spherex_moc(ra, dec)


@app.get("/api/resolve")
async def resolve(q: str = Query(..., min_length=1)):
    result = await resolve_target_query(q)
    if not result:
        raise HTTPException(status_code=404, detail="Target or coordinates could not be resolved.")
    return result


@app.get("/api/spectrum")
async def spectrum(
    ra: float = Query(..., ge=0.0, le=360.0),
    dec: float = Query(..., ge=-90.0, le=90.0),
    radius_arcsec: float = Query(6.2, gt=0.5, le=60.0),
    center_ra: Optional[float] = Query(None, ge=0.0, le=360.0),
    center_dec: Optional[float] = Query(None, ge=-90.0, le=90.0),
    fov_arcmin: float = Query(5.0, gt=0.1, le=15.0),
):
    raw_bytes = generate_spectrum_vector(
        ra, dec, radius_arcsec, center_ra=center_ra, center_dec=center_dec, fov_arcmin=fov_arcmin
    )
    return Response(
        content=raw_bytes,
        media_type="application/octet-stream",
        headers={
            "X-Survey-Provenance": "Synthetic-Demo",
            "X-Data-Kind": "simulated",
            "X-BUNIT": "arbitrary",
            "X-Channel-Count": "102",
        },
    )


@app.get("/api/tap/query")
async def tap_query(
    ra: float = Query(..., ge=0.0, le=360.0),
    dec: float = Query(..., ge=-90.0, le=90.0),
    radius_arcsec: float = Query(12.4, gt=0.5, le=360.0),
    table: str = Query("allwise_p3as_psd", pattern="^[a-zA-Z0-9_]+$"),
):
    """
    Live NASA/IPAC IRSA TAP Query Hook for cross-matching infrared catalog sources in supported public tables.
    """
    return await query_irsa_tap(ra=ra, dec=dec, radius_arcsec=radius_arcsec, table=table)


def raster_manifest(cache_key, headers):
    return {
        "cache_key": cache_key,
        "epoch": headers.get("X-Observation-Epoch", "unknown"),
        "release": headers.get("X-Source-Release", "unknown"),
        "data_kind": headers.get("X-Data-Kind", "unknown"),
        "band_index": int(headers["X-Band-Index"]),
        "raster_url": f"/api/data/raster/{cache_key}",
    }


async def _process_cutout_job(
    job_id: str,
    ra: float,
    dec: float,
    fov_arcmin: float,
    bands: List[int],
    epochs: List[str],
    provenance: str,
) -> None:
    completed = []
    size = 512
    pixel_scale = (fov_arcmin * 60.0) / size

    try:
        for epoch in epochs:
            e_idx = UNWISE_RELEASES.index(epoch)
            for band_idx in bands:
                cache_key = BoundedLRUCache.make_key(
                    ra, dec, fov_arcmin, epoch, band_idx, provenance, "observed"
                )
                entry = raster_cache.get(cache_key)
                if not entry or entry[1].get("X-Data-Kind") != "observed":
                    product = await asyncio.to_thread(
                        generate_cutout_product,
                        ra,
                        dec,
                        e_idx,
                        band_idx,
                        size,
                        epoch,
                        fov_arcmin,
                    )
                    is_reproj = product.is_reprojected
                    clean_flux = np.nan_to_num(product.pixels, nan=0.0).astype(np.float32)
                    raw_u16, bscale, bzero = await asyncio.to_thread(quantize_to_uint16, clean_flux)
                    headers = build_wcs_headers(
                        width=size,
                        height=size,
                        bscale=bscale,
                        bzero=bzero,
                        ra=ra,
                        dec=dec,
                        pixel_scale_arcsec=pixel_scale,
                        epoch=product.epoch,
                        provenance=product.provenance,
                        band_index=product.band,
                        wavelength_um=product.wavelength_um,
                        is_reprojected=is_reproj,
                    )
                    headers.update({
                        "X-Data-Kind": product.data_kind,
                        "X-Source-Release": product.release,
                        "X-Source-URL": product.source_url,
                        "X-BUNIT": product.bunit,
                    })
                    # Product kind is part of identity: retries cannot overwrite older demo downloads.
                    cache_key = BoundedLRUCache.make_key(
                        ra, dec, fov_arcmin, epoch, band_idx, provenance, product.data_kind
                    )
                    raster_cache.put(cache_key, raw_u16, headers)

                stored = raster_cache.get(cache_key)
                completed.append(raster_manifest(cache_key, stored[1]))
                job_store.update(job_id, status="partial", completed_rasters=list(completed))

        job_store.update(job_id, status="ready", completed_rasters=completed)
    except Exception as exc:
        job_store.update(job_id, status="failed", error=str(exc))


@app.post("/api/jobs/cutouts")
async def create_cutout_job(req: CutoutJobRequest):
    provenance = "unwise-products-v3"  # Invalidate legacy falsely labelled cache entries.
    epochs = list(dict.fromkeys(req.epochs or UNWISE_RELEASES[:3]))
    if any(epoch not in UNWISE_RELEASES for epoch in epochs):
        raise HTTPException(status_code=422, detail="epochs must be archive releases: allwise, neo2, neo5, neo7")

    all_cached = []
    missing = False
    for epoch in epochs:
        for band_idx in req.bands:
            cache_key = BoundedLRUCache.make_key(
                req.ra, req.dec, req.fov_arcmin, epoch, band_idx, provenance, "observed"
            )
            entry = raster_cache.get(cache_key)
            if entry and entry[1].get("X-Data-Kind") == "observed":
                all_cached.append(raster_manifest(cache_key, entry[1]))
            else:
                missing = True

    if not missing:
        return JSONResponse(
            status_code=200,
            content={
                "status": "ready",
                "provenance": "per-raster metadata",
                "raster_urls": [item["raster_url"] for item in all_cached],
                "rasters": all_cached,
            },
        )

    job_id = f"job_{uuid.uuid4().hex[:8]}"
    job_store.create(job_id, {"total_requested": len(epochs) * len(req.bands)})
    asyncio.create_task(
        _process_cutout_job(job_id, req.ra, req.dec, req.fov_arcmin, req.bands, epochs, provenance)
    )
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job_id,
            "status": "pending",
            "poll_interval_ms": 350,
            "estimated_wait_ms": 1200,
            "status_url": f"/api/jobs/{job_id}",
        },
    )


@app.get("/api/jobs/{job_id}")
async def get_job_status(job_id: str):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired.")
    return job


@app.get("/api/data/raster/{cache_key}")
async def get_raster(cache_key: str):
    validate_cache_key(cache_key)
    entry = raster_cache.get(cache_key)
    if not entry:
        raise HTTPException(status_code=404, detail="Raster not found in cache.")
    raw_bytes, headers = entry
    return Response(content=raw_bytes, media_type="application/octet-stream", headers=headers)


@app.post("/api/diff")
async def compute_diff(req: DiffRequest):
    validate_cache_key(req.raster_key_a)
    validate_cache_key(req.raster_key_b)
    entry_a = raster_cache.get(req.raster_key_a)
    entry_b = raster_cache.get(req.raster_key_b)
    if not entry_a or not entry_b:
        raise HTTPException(status_code=404, detail="One or both rasters not found in cache.")

    bytes_a, hdr_a = entry_a
    bytes_b, hdr_b = entry_b
    if any(hdr_a.get(field) != hdr_b.get(field) for field in ("X-BUNIT", "X-Band-Index", "X-Data-Kind", "X-SPHEREx-Detector")):
        raise HTTPException(status_code=400, detail="Difference inputs must share units, band and observed/simulated kind.")
    w_a, h_a = (int(x) for x in hdr_a["X-Raster-Dimensions"].split(","))
    w_b, h_b = (int(x) for x in hdr_b["X-Raster-Dimensions"].split(","))
    if (w_a, h_a) != (w_b, h_b):
        raise HTTPException(status_code=400, detail="Raster dimensions must match for differencing.")
    if hdr_a.get('X-SPHEREx-Detector') is not None:
        checks = []
        for header in (hdr_a, hdr_b):
            center_ra, center_dec = map(float, header['X-WCS-CRVAL'].split(','))
            fov = abs(float(header['X-WCS-CDELT'].split(',')[0])) * w_a * 60
            checks.append(await spherex_cutout(header['X-Source-URL'], center_ra, center_dec, fov))
        a, b = checks
        if not all(v.get('qualityApplied') and v.get('spectral') for v in checks):
            raise HTTPException(status_code=400, detail='SPHEREx pixel wavelengths and quality must be verified before subtraction.')
        sa, sb = np.asarray(a['spectral'], dtype=float), np.asarray(b['spectral'], dtype=float)
        pa, pb = np.asarray(a['pixels'], dtype=float), np.asarray(b['pixels'], dtype=float)
        overlap = np.isfinite(pa) & np.isfinite(pb)
        if sa.shape != sb.shape or overlap.sum() < 64:
            raise HTTPException(status_code=400, detail='Insufficient comparable sky coverage.')
        tolerance = .1 * np.minimum(sa[..., 1], sb[..., 1])
        valid = np.isfinite(sa).all(axis=-1) & np.isfinite(sb).all(axis=-1) & (tolerance > 0) & (np.abs(sa[...,0]-sb[...,0]) <= tolerance) & (np.abs(sa[...,1]-sb[...,1]) <= tolerance)
        if not valid[overlap].all():
            raise HTTPException(status_code=400, detail='SPHEREx pixel bandpasses differ; use visual comparison instead.')

    w, h = w_a, h_a
    img_a = dequantize_uint16(bytes_a, (h, w), float(hdr_a["X-BSCALE"]), float(hdr_a["X-BZERO"]))
    img_b = dequantize_uint16(bytes_b, (h, w), float(hdr_b["X-BSCALE"]), float(hdr_b["X-BZERO"]))
    if hdr_a.get('X-SPHEREx-Detector') is not None:
        from scipy.ndimage import map_coordinates
        yy, xx = np.mgrid[:h, :w]
        coords = [yy * (pa.shape[0]-1)/(h-1), xx * (pa.shape[1]-1)/(w-1)]
        common = map_coordinates(overlap.astype(float), coords, order=1) > .999
        img_a[~common] = np.nan
        img_b[~common] = np.nan

    diff_img, confidence = await asyncio.to_thread(psf_matched_diff, img_a, img_b, req.kernel)
    diff_img = np.nan_to_num(diff_img, nan=0.0)
    raw_u16, bscale, bzero = await asyncio.to_thread(quantize_to_uint16, diff_img)

    ra, dec = (float(x) for x in hdr_a["X-WCS-CRVAL"].split(","))
    cdelt = abs(float(hdr_a["X-WCS-CDELT"].split(",")[0])) * 3600.0
    is_reproj = (
        hdr_a.get("X-WCS-Reprojected") == "true"
        or "reproject" in hdr_a.get("X-Survey-Provenance", "").lower()
        or "unwise" in hdr_a.get("X-Survey-Provenance", "").lower()
    )
    headers = build_wcs_headers(
        width=w,
        height=h,
        bscale=bscale,
        bzero=bzero,
        ra=ra,
        dec=dec,
        pixel_scale_arcsec=cdelt,
        epoch=f"{hdr_a['X-Observation-Epoch']}..{hdr_b['X-Observation-Epoch']}",
        provenance=f"{hdr_a['X-Survey-Provenance']}-PSF-Diff",
        band_index=int(hdr_a["X-Band-Index"]),
        wavelength_um=float(hdr_a["X-Band-Wavelength-Um"]),
        is_reprojected=is_reproj,
    )
    headers["X-BUNIT"] = hdr_a.get("X-BUNIT", "unknown")
    headers["X-Data-Kind"] = (
        "observed" if all(h.get("X-Data-Kind") == "observed" for h in (hdr_a, hdr_b)) else "simulated"
    )
    headers["X-Source-Release"] = f"{hdr_a.get('X-Source-Release', 'unknown')}..{hdr_b.get('X-Source-Release', 'unknown')}"
    headers["X-Source-URL"] = hdr_a.get("X-Source-URL", "")
    headers["X-Diff-Confidence"] = f"{confidence:.4f}"
    return Response(content=raw_u16, media_type="application/octet-stream", headers=headers)


@app.get("/api/export/fits")
async def export_fits(cache_key: str):
    validate_cache_key(cache_key)
    entry = raster_cache.get(cache_key)
    if not entry:
        raise HTTPException(status_code=404, detail="Raster not found in cache.")
    raw_bytes, hdr = entry
    w, h = (int(x) for x in hdr["X-Raster-Dimensions"].split(","))
    flux = dequantize_uint16(raw_bytes, (h, w), float(hdr["X-BSCALE"]), float(hdr["X-BZERO"]))
    flux = np.nan_to_num(flux, nan=0.0)

    # Do not flip WCS-reprojected data that is already in standard FITS row-major orientation
    is_reprojected = (
        hdr.get("X-WCS-Reprojected") == "true"
        or "reproject" in hdr.get("X-Survey-Provenance", "").lower()
        or "unwise" in hdr.get("X-Survey-Provenance", "").lower()
    )
    flux_fits = flux if is_reprojected else np.flipud(flux)

    ra, dec = (float(x) for x in hdr["X-WCS-CRVAL"].split(","))
    cdelt_x, cdelt_y = (float(x) for x in hdr["X-WCS-CDELT"].split(","))

    wcs = WCS(naxis=2)
    wcs.wcs.crpix = [w / 2.0 + 0.5, h / 2.0 + 0.5]
    wcs.wcs.cdelt = [cdelt_x, cdelt_y]
    wcs.wcs.crval = [ra, dec]
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]

    hdu = fits.PrimaryHDU(data=flux_fits.astype(np.float32), header=wcs.to_header())
    hdu.header["SURVEY"] = hdr["X-Survey-Provenance"]
    hdu.header["EPOCH"] = hdr["X-Observation-Epoch"]
    hdu.header["BANDIDX"] = int(hdr["X-Band-Index"])
    hdu.header["WAVELEN"] = float(hdr["X-Band-Wavelength-Um"])
    hdu.header["BUNIT"] = hdr.get("X-BUNIT", "unknown")
    hdu.header["DATKIND"] = hdr.get("X-Data-Kind", "unknown")
    hdu.header["RELEASE"] = hdr.get("X-Source-Release", "unknown")
    hdu.header["SRCURL"] = hdr.get("X-Source-URL", "")
    hdu.header.add_history("Display product: regridded, percentile-clipped and uint16 quantized.")
    hdu.header.add_history("Not an original archive FITS or a calibrated SPHEREx observation.")

    buf = io.BytesIO()
    hdu.writeto(buf)
    return Response(
        content=buf.getvalue(),
        media_type="application/fits",
        headers={"Content-Disposition": f'attachment; filename="sky_{cache_key}.fits"'},
    )

# Isolated real-data endpoints: archive failures never become simulations.
from spherex_archive import bundled_records as bundled_spherex_records, discover as discover_spherex, cutout as spherex_cutout

@app.get('/api/spherex/images')
async def spherex_images(ra: float = Query(..., ge=0, le=360), dec: float = Query(..., ge=-90, le=90)):
    bundled = bundled_spherex_records(ra, dec)
    if bundled:
        return {
            'images': bundled,
            'collection': 'spherex_dr1',
            'source': 'Verified bundled previews from exact NASA/IPAC IRSA SPHEREx DR1 inputs',
            'availability': 'bundled-verified',
        }
    try:
        return {'images': await discover_spherex(ra, dec), 'collection': 'spherex_dr1', 'source': 'NASA/IPAC IRSA SIA2'}
    except Exception as exc:
        raise HTTPException(status_code=502, detail='IRSA discovery unavailable. Retry shortly.') from exc

@app.get('/api/spherex/cutout')
async def real_spherex_cutout(url: str, ra: float = Query(..., ge=0, le=360), dec: float = Query(..., ge=-90, le=90), fov: float = Query(5, ge=1, le=10)):
    try:
        image = await spherex_cutout(url, ra, dec, fov)
        key = raster_cache.make_key('spherex-display', url, ra, dec, fov)
        if not raster_cache.has(key):
            from scipy.ndimage import map_coordinates
            native = np.array([[np.nan if value is None else value for value in row] for row in image['pixels']], dtype=float)
            yy, xx = np.mgrid[:512, :512]
            coords = [yy * (image['height'] - 1) / 511, xx * (image['width'] - 1) / 511]
            support = map_coordinates(np.isfinite(native).astype(float), coords, order=1)
            display = map_coordinates(np.nan_to_num(native), coords, order=1)
            valid_support = support > 0.05
            display = np.where(valid_support, display / np.maximum(support, 1e-6), np.nan)
            raw, scale, zero = quantize_to_uint16(display)
            headers = build_wcs_headers(width=512, height=512, bscale=scale, bzero=zero, ra=ra, dec=dec,
                pixel_scale_arcsec=fov * 60 / 512, epoch='unknown', provenance='SPHEREx-DR1-preview',
                band_index=0, wavelength_um=0, is_reprojected=True)
            detector = re.search(r'D([1-6])_spx', url)
            headers.update({'X-BUNIT': image['unit'], 'X-Data-Kind': 'observed', 'X-Source-Release': 'DR1',
                'X-Source-URL': url, 'X-SPHEREx-Detector': detector.group(1) if detector else 'unknown'})
            raster_cache.put(key, raw, headers)
        return {**image, 'cache_key': key}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail='SPHEREx cutout unavailable; no simulated replacement was generated.') from exc

@app.get('/api/spherex/spectral-samples')
async def real_spectral_samples(ra: float = Query(..., ge=0, le=360), dec: float = Query(..., ge=-90, le=90), stream: bool = False):
    from spherex_archive import spectral_samples
    if stream:
        import json
        from spherex_archive import spectral_sample_updates
        async def events():
            try:
                async for update in spectral_sample_updates(ra, dec):
                    yield 'data: ' + json.dumps(update, allow_nan=False) + '\n\n'
            except Exception:
                yield 'data: ' + json.dumps({'error': 'Measured spectral samples could not be retrieved from IRSA. Please retry.'}) + '\n\n'
        return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    try:
        return await spectral_samples(ra, dec)
    except Exception as exc:
        raise HTTPException(status_code=502, detail='Measured spectral samples could not be retrieved from IRSA.') from exc
