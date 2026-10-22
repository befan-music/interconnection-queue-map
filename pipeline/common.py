"""Shared paths, HTTP helpers and provenance tracking for the pipeline."""
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"
PROCESSED = ROOT / "data" / "processed"
MAP_DATA = ROOT / "map" / "data"
MANIFEST = RAW / "manifest.json"

for _p in (RAW, INTERIM, PROCESSED, MAP_DATA):
    _p.mkdir(parents=True, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept": "*/*"})


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def record(source, path, url, note=""):
    """Append/replace a provenance entry for a downloaded file."""
    path = Path(path)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}
    manifest[str(path.relative_to(ROOT)).replace("\\", "/")] = {
        "source": source,
        "url": url,
        "retrieved_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "note": note,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def get(url, method="GET", retries=4, timeout=180, **kw):
    for attempt in range(retries):
        try:
            r = SESSION.request(method, url, timeout=timeout, **kw)
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise
            wait = 5 * (attempt + 1)
            log(f"  retry {attempt + 1} for {url} in {wait}s ({e})")
            time.sleep(wait)


def download(source, url, dest, method="GET", note="", **kw):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = get(url, method=method, **kw)
    dest.write_bytes(r.content)
    record(source, dest, url, note)
    log(f"  saved {dest.relative_to(ROOT)} ({len(r.content) / 1e6:.1f} MB)")
    return dest


def arcgis_layer_to_geojson(source, layer_url, dest, where="1=1", out_fields="*",
                            page=2000, max_offset=None, note=""):
    """Page through an ArcGIS FeatureServer layer and write one GeoJSON file (EPSG:4326)."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    count = get(f"{layer_url}/query", params={"where": where, "returnCountOnly": "true",
                                              "f": "json"}).json()["count"]
    feats, offset = [], 0
    while offset < count:
        params = {"where": where, "outFields": out_fields, "outSR": 4326, "f": "geojson",
                  "resultOffset": offset, "resultRecordCount": page,
                  "orderByFields": "OBJECTID"}
        if max_offset:
            params["maxAllowableOffset"] = max_offset
        batch = get(f"{layer_url}/query", params=params).json().get("features", [])
        if not batch:
            break
        feats.extend(batch)
        offset += len(batch)
        log(f"  {source}: {offset}/{count}")
    dest.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    record(source, dest, layer_url, note or f"{len(feats)} features")
    return dest


def add_pipeline_to_path():
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
