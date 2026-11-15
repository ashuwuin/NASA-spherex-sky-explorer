"""Export small, shared-stretch teaching previews from the verified real FITS pair.
Run verify_matched_investigation.py first. No extra imaging dependency required.
"""
import hashlib
import json
import struct
import zlib
from pathlib import Path
import numpy as np
from spherex_archive import decode_image


def chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind+data) & 0xffffffff)


def export():
    root = Path(__file__).resolve().parents[1]
    evidence = json.loads((root/'frontend/src/data/investigation.json').read_text())
    images = []
    preview_payload = {}
    for item in evidence['inputs']:
        path = root/'backend/cache/investigation'/f"{evidence['fov_arcmin']}arcmin-{item['url'].rsplit('/', 1)[-1]}"
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != item['fits_sha256']:
            raise ValueError('FITS input no longer matches recorded evidence.')
        decoded = decode_image(content, evidence['ra'], evidence['dec'], evidence['fov_arcmin'])
        image = np.asarray(decoded['pixels'], dtype=np.float32)
        spectral = np.asarray(decoded['spectral'], dtype=np.float32)
        images.append(image)
        prefix = item['id']
        preview_payload[f'{prefix}__pixels'] = image
        preview_payload[f'{prefix}__spectral'] = spectral
        preview_payload[f'{prefix}__wcs'] = np.asarray(decoded['wcs'])
        preview_payload[f'{prefix}__extensions'] = np.asarray(decoded['extensions'])
    values = np.concatenate([image[np.isfinite(image)] for image in images])
    low, high = np.percentile(values, [2, 99.5])
    output = root/'frontend/public/images/learning'
    output.mkdir(parents=True, exist_ok=True)
    for name, image in zip(('spherex-march.png', 'spherex-may.png'), images):
        valid = np.isfinite(image)
        signal = np.maximum(0, (np.nan_to_num(image, nan=low)-low)/(high-low))
        gray = np.uint8(255*np.clip(np.arcsinh(8*signal)/np.arcsinh(8), 0, 1))
        rgb = np.repeat(gray[..., None], 3, axis=2)
        rgb[~valid] = [27, 35, 49]
        height, width = image.shape
        scanlines = b''.join(b'\0'+row.tobytes() for row in rgb)
        header = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
        (output/name).write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', header)+chunk(b'IDAT', zlib.compress(scanlines))+chunk(b'IEND', b''))
        print(output/name)
    backend_output = root/'backend/data'
    backend_output.mkdir(parents=True, exist_ok=True)
    manifest = {key: evidence[key] for key in ('id', 'ra', 'dec', 'fov_arcmin')}
    fields = ('id', 'url', 'mjd', 'date', 'endDate', 'minUm', 'maxUm', 'fits_sha256')
    manifest['inputs'] = [{key: item[key] for key in fields} for item in evidence['inputs']]
    (backend_output/'matched_investigation.json').write_text(json.dumps(manifest, indent=2)+'\n')
    preview_file = backend_output/'matched_investigation_previews.npz'
    np.savez_compressed(preview_file, **preview_payload)
    print(preview_file)


if __name__ == '__main__':
    export()
