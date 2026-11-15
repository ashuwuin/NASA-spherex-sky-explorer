# Submission evidence and remaining checks

## 1. Real SPHEREx investigation

### Matched pair: did the field fade?

Open **Try a real investigation** in Explore, or use:
`/explorer?source=spherex&ra=269.4521&dec=4.6934&fov=5&mode=side-by-side&name=Matched%20infrared%20field&investigation=matched-infrared-field`.
The link pins two exact archive records; unavailable inputs are never silently replaced.

For submission reliability, the repository also includes compact 128×128 previews derived from these exact hash-verified FITS inputs. Their source URLs, dates, units, missing coverage and evidence hashes remain attached. They are used only for this documented field and are not synthetic observations; other sky positions still require the live archive.

| Input | UTC start (FITS) | Observation ID | Calibrated wavelength at field centre |
| --- | --- | --- | --- |
| A | 2026-03-19 09:09:23.051 | 2026W12_1A_0610_4 | 2.124950 µm |
| B | 2026-05-04 03:11:42.741 | 2026W19_1A_0046_1 | 2.124196 µm |

The fixed 5-arcmin field has 2,259/2,393 and 2,349/2,382 usable native pixels. On the common display grid 16,318 of 16,384 pixels overlap; these are interpolated display pixels, **not** independent measurements. Every overlapping location passes the 10%-of-bandwidth check using both the preview lookup and full calibrated CWAVE/CBAND arrays. Maximum calibrated wavelength-centre difference: 0.000981 µm. The same detector/release and MJy/sr units are used. Band-centre/width matching is a compatibility screen, not proof of identical full response curves. The wider field shows more actual structure than the earlier 1-arcmin crop. An asinh display stretch reveals faint detail without adding resolution. Blink and comparison modes share contrast limits.

The native field median falls from 0.247577 to 0.210704 MJy/sr, a change of −0.036873. The supplied ZODI model changes by −0.046159 MJy/sr. Medians of IMAGE minus ZODI are 0.087501 and 0.096965 MJy/sr: the smaller remaining difference is +0.009465. These medians are calculated independently; a median of differences need not equal a difference of medians. The recorded interpretation is rechecked against the downloaded values before regenerating evidence; its consistency guard is descriptive, not a statistical variability threshold.

**Conclusion:** most of the field-median change is consistent with changing zodiacal foreground light. This is a real, reproducible foreground investigation, not evidence that a star faded or moved. ZODI is a supplied model, not independent confirmation. No variability significance, chemical detection or motion measurement is claimed. The viewer retains foreground light; its normalized Difference mode is a display aid, not the native MJy/sr calculation quoted here.

Alignment uses the same north-up TAN sky grid and explicit TAN-SIP archive astrometry. Numerical WCS round-trip errors are below 0.00001 display pixel; this does not validate absolute astrometry. There is no empirical star registration, PSF matching, source photometry or systematic-error propagation. Field medians include unresolved sources and background, not an isolated stellar flux. The coordinates are a fixed reference field, not an epoch-corrected location of Barnard’s Star. Those limits are appropriate to the foreground question and preclude a motion claim.

Recorded FITS/calibration hashes, inputs, native counts, method and limits are in [the UI’s evidence file](../frontend/src/data/investigation.json). Reproduce without new dependencies:

```bash
backend/.venv/bin/python backend/verify_matched_investigation.py
```

Raw FITS/calibration downloads are cached under ignored `backend/cache/investigation/`; delete that directory to repeat retrieval. Retrieval errors stop verification. Calibration uses the archive’s [CWAVE/CBAND recommendation and ZODI definition](https://caltech-ipac.github.io/spherex-archive-documentation/spherex-data-products/). Explore and the Changing Sky mission share the same evidence and exact-input link.

### Rejected control: the original first/last pair

Target: Barnard's Star field, ICRS RA 269.4521°, Dec +4.6934°, 5 arcmin.
Open `/explorer?ra=269.4521&dec=4.6934&source=spherex&mode=side-by-side`.

The live IRSA discovery query returned these detector-3 observations:

| Input | UTC start | Observation ID | Detector range |
| --- | --- | --- | --- |
| A | 2025-08-15 20:45:11.376 | 2025W33_2C_0321_1 | 1.6301–2.4346 µm |
| B | 2026-05-05 01:59:28.781 | 2026W19_1A_0403_1 | 1.6301–2.4346 µm |

Reproducible live verification is saved in [the evidence JSON](evidence/barnards-star.json). Both native archive cutouts downloaded successfully with quality masking enabled. Image A has 14,890 valid pixels and Image B has 14,790 on the common 128×128 sky grid. Both are measured in MJy/sr, and their pixel SHA-256 hashes differ. They share 13,680 valid pixels.

Only 27.42% of the overlapping grid pixels pass the bandpass gate; the median central-wavelength difference is 0.011805 µm. Consequently, this pair is suitable for a cautious visual investigation but **fails the subtraction gate**. A residual cannot be interpreted as motion. Astropy also reports inconsistent SIP header suffix metadata in the archive input; independent astrometric validation remains necessary for research measurements.

Reproduce from the repository root:

```bash
backend/.venv/bin/python backend/verify_spherex_investigation.py --output docs/evidence/barnards-star.json
```

Archive availability can vary; the verifier records retrieval failures instead of substituting simulated images. The original FITS source URLs, dates, processing notes and pixel hashes are included in the JSON.

Conclusion: two real dated images are available for visual study. A measurable wavelength-response mismatch explains why brightness differences are inconclusive. Background, processing, PSF and missing coverage are additional alternatives. No motion or planetary discovery is established.

## 2. Comparison safeguards

Preview quality flags and invalid variances are masked. Both images use the same TAN sky grid. Missing interpolation support remains empty. Frontend and PSF API reject unavailable quality/spectral lookup, insufficient shared coverage, differing detectors and mismatched pixel bandpasses.

The visualization gate requires wavelength centers and bandwidths to agree within 10% of the smaller bandwidth at every valid overlapping grid pixel, with at least 64 overlapping pixels. This is a conservative implementation check, not a scientifically validated variability threshold. WCS-WAVE is a visualization lookup; rigorous analysis requires calibration products, uncertainty propagation, background/source separation and validated PSF treatment. A passing gate does not prove motion.

## 3–4. Inputs and conclusions

Explore shows Image A/B dates, bands or detector ranges and releases. “Record a conclusion” collects observations and alternative explanations. Save retains these notes with actual displayed source URLs, dates, bands and processing metadata. The journal exposes input sources and exports the saved record. Saved records contain metadata, not permanent image files.

## 5. Beginner study — pending real responses

Run individually with five beginners; do not coach their first attempt. No results have been collected or invented.

Ask each participant to select SPHEREx, identify the two input dates, use Blink and Difference when allowed, explain one apparent change, identify whether the spectrum is measured or simulated, and save a tentative conclusion with an alternative explanation.

For P1–P5, record: task completion without help; image source correctly identified; spectrum simulation correctly identified; residual interpreted cautiously; successful save; confusing wording; one suggested improvement. Obtain participant consent and avoid collecting names or personal information. Fix repeated misunderstandings, then retest the affected tasks. Automated tests do not replace this study.

## 6. Publication

The supplied Cloudflare URL returned HTTP 200 and a healthy `/api/health`. Its JavaScript asset was `index-BuRco3EA.js`, an older build than the local implementation. The Cloudflare origin process is not present in this workspace. Publishing to GitHub alone does not update an independently running tunnel origin. Verify the current deployed asset and repeat the investigation after the hosting origin is updated. A trycloudflare URL is temporary and depends on its origin process remaining available.
