# Oracle VM: persistent SPHEREx cache update

The public Cloudflare URL forwards requests to the Oracle VM. Changes on a developer's computer take effect publicly only after the VM's backend is updated. Cloudflare itself does not retrieve astronomy data or populate this cache.

## Docker Compose

Apply the updated source files to the existing repository on the VM. Keep its existing Compose project name and port settings. Run from that repository directory:

```bash
docker compose build backend
docker compose run --rm --no-deps --user root backend chown -R 1000:1000 /home/user/.cache/spherex
docker compose up -d backend
docker compose restart frontend
docker compose exec -T backend python warm_spherex_cache.py
```

If the existing deployment uses `docker compose -p NAME`, use that same `-p NAME` on every command. The `spherex-cache` volume in `compose.yaml` stores successful archive results across container replacement. The permission command initializes that specific volume for the non-root application user, including volumes created by an older image.

Warming reports each target's image count and sample availability. Failed retrievals remain unavailable. Background warming starts without delaying the API and retries every five minutes. Avoid running concurrent manual warming commands. Keep the cache volume when restarting the application.

If supplied with `spherex-verified-cache.tar.gz`, copy it to the VM and import the already retrieved real observations into the same volume:

```bash
docker compose exec -T backend tar -xz -C /home/user/.cache/spherex < spherex-verified-cache.tar.gz
docker compose restart backend
```

The supplied seed cache contains the two verified Barnard-field previews and three retrieved R Draconis previews with their source metadata. The remaining three fields still need a first successful IRSA retrieval. This cache archive contains only successful archive responses, without environment files or credentials.

## Direct Uvicorn / systemd deployment

Set these variables for the existing backend service:

```text
SPHEREX_CACHE_DIR=/var/lib/spherex/cache
SPHEREX_PREWARM=true
```

Create that directory with ownership matching the existing service user, update the backend source, and restart the existing service. Use that service's Python environment to run `backend/warm_spherex_cache.py` manually if desired. Retain the same directory between releases.

## Cache behavior

- All coordinates can reuse successful discovery, processed preview and native measurement responses from disk.
- Discovery refreshes after one day; previews after 30 days. Saved results remain available during background refresh or archive outages.
- Cached native measurements retain their source dates and incomplete coverage.
- The five presets warm automatically at a five-arcminute field of view. Other coordinates populate the cache when requested.
- The exact documented Barnard field includes two verified real SPHEREx previews available immediately.
- The cache is bounded at 256 MB and evicts older entries when necessary.

A first successful retrieval is required for observations that have never been cached. An empty cache cannot bypass an archive outage. Different exact coordinates or fields of view use separate image entries.

## Verify on the VM

```bash
docker compose exec -T backend python warm_spherex_cache.py
docker compose restart backend
docker compose exec -T backend python -c "import archive_cache; print(archive_cache.store().cache_dir); print(len(archive_cache.store()._disk_index))"
```

Open the public Explore page after restarting the backend. Check each target, actual observation dates and source. Saved results should load promptly once warming succeeds. The existing Cloudflare tunnel can keep forwarding to the same application port.
