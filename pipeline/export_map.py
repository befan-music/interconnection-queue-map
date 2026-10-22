"""Export the model to compact JSON for the web map (map/data/*.json).

Geometry is polyline-encoded (precision 1e-4 deg ~ 11 m) and attributes are stored column-wise,
which keeps the whole national dataset small enough to load in a browser.
"""
import json
import sys
from datetime import date
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import MANIFEST, MAP_DATA, PROCESSED, RAW, log  # noqa: E402

PREC = 1e4


def encode(coords):
    """Google polyline algorithm on (lon, lat) pairs, precision 1e-4."""
    out, plat, plon = [], 0, 0
    for lon, lat in coords:
        ilat, ilon = int(round(lat * PREC)), int(round(lon * PREC))
        for v in (ilat - plat, ilon - plon):
            v = ~(v << 1) if v < 0 else (v << 1)
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1F)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        plat, plon = ilat, ilon
    return "".join(out)


def geom_parts(geom):
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type in ("LineString", "LinearRing"):
        return [encode(geom.coords)]
    if geom.geom_type == "Polygon":
        return [encode(geom.exterior.coords)] + [encode(r.coords) for r in geom.interiors]
    if geom.geom_type.startswith("Multi") or geom.geom_type == "GeometryCollection":
        return [p for g in geom.geoms for p in geom_parts(g)]
    return []


def col(s, nd=0):
    s = pd.Series(s)
    if s.dtype.kind in "fi":
        v = s.astype(float).round(nd)
        return [None if pd.isna(x) else (int(x) if nd == 0 else float(x)) for x in v]
    return [None if pd.isna(x) else str(x) for x in s]


def dump(name, obj):
    p = MAP_DATA / name
    p.write_text(json.dumps(obj, separators=(",", ":")), encoding="utf-8")
    log(f"  {name}: {p.stat().st_size / 1e6:.2f} MB")


def main():
    buses = gpd.read_parquet(PROCESSED / "buses.parquet")
    lines = gpd.read_parquet(PROCESSED / "lines.parquet")
    queue = pd.read_parquet(PROCESSED / "queue.parquet")
    plants = gpd.read_parquet(PROCESSED / "plants.parquet")
    summary = json.loads((PROCESSED / "model_summary.json").read_text())

    # ---- lines -----------------------------------------------------------------------------------
    lines = lines[lines.geometry.notna()].copy()
    lines["geometry"] = lines.geometry.simplify(0.002, preserve_topology=False)
    owners = lines.owner.fillna("NOT AVAILABLE").astype(str)
    owner_list = sorted(owners.unique())
    owner_idx = owners.map({o: i for i, o in enumerate(owner_list)})
    dump("lines.json", {
        "id": col(lines.index.to_series()), "kv": col(lines.kv), "own": col(owner_idx), "owners": owner_list,
        "len": col(lines.length_km, 1), "lim": col(lines.limit_eff_mw), "lim0": col(lines.limit_mw),
        "basis": [0 if b == "thermal" else 1 for b in lines.limit_basis],
        "cal": [1 if c is True else 0 for c in lines.calibrated],
        "fP": col(lines.flow_peak_mw), "fL": col(lines.flow_light_mw),
        "ldP": col(lines.loading_peak_pct), "ldL": col(lines.loading_light_pct), "ldQ": col(lines.loading_q_pct),
        "bb": col(lines.binding_buses), "qb": col(lines.queue_mw_behind),
        "s1": col(lines.sub_1.str.title()), "s2": col(lines.sub_2.str.title()),
        "fr": col(lines.from_node), "to": col(lines.to_node),
        "g": [geom_parts(g) for g in lines.geometry],
    })

    # ---- buses ------------------------------------------------------------------------------------
    sig = {"green": 0, "yellow": 1, "red": 2}
    b = buses
    dump("buses.json", {
        "id": col(b.node), "lon": col(b.lon, 4), "lat": col(b.lat, 4), "name": col(b.name),
        "type": [{"substation": 0, "substation (unnamed)": 1, "tap": 2}.get(t, 1) for t in b.node_type],
        "kv": col(b.kv_max), "kvl": col(b.kv_levels), "ic": col(b.interconnection), "st": col(b.state),
        "cty": col(b.county), "mod": [1 if m else 0 for m in b.modeled], "deg": col(b.degree),
        "osm": col(b.osm_name), "own": col(b.osm_operator),
        "load": col(b.load_peak_mw), "gen": col(b.gen_mw), "genS": col(b.gen_solar_mw), "genW": col(b.gen_wind_mw),
        "genB": col(b.gen_battery_mw), "genG": col(b.gen_gas_cc_mw + b.gen_gas_ct_other_mw),
        "genC": col(b.gen_coal_mw), "genN": col(b.gen_nuclear_mw), "genH": col(b.gen_hydro_mw),
        "q": col(b.queue_mw), "qn": col(b.queue_n), "qx": col(b.queue_expected_mw),
        "qE": col(b.queue_early_study_mw), "qL": col(b.queue_late_study_mw), "qI": col(b.queue_ia_executed_mw),
        "qC": col(b.queue_construction_mw), "qS": col(b.queue_storage_mw),
        "n1": col(b.branch_capacity_n1_mw), "qcap": col(b.queue_to_capacity, 2),
        "hG": col(b.headroom_gen_mw), "hGq": col(b.headroom_gen_after_queue_mw),
        "hGp": col(b.hr_gen_peak), "hGl": col(b.hr_gen_light),
        "hL": col(b.headroom_load_mw), "hLq": col(b.headroom_load_after_queue_mw), "hGd": col(b.hr_gen_deliv),
        "bdf": col(b.bind_gen_df_q, 2),
        "sig": [sig.get(s, 3) if m else 3 for s, m in zip(b.gen_signal, b.modeled)],
        "lsig": [sig.get(s, 3) for s in b.load_signal],
        "bG": col(b.bind_gen_line_q), "bL": col(b.bind_load_line),
        "sto": [1 if s else 0 for s in b.storage_opportunity], "stoS": col(b.storage_score, 1),
    })

    # ---- queue projects -----------------------------------------------------------------------------
    q = queue[queue.node >= 0]
    dump("queue.json", {
        "uid": col(q.uid), "iso": col(q.iso), "name": col(q.project_name), "poi": col(q.poi_name),
        "type": col(q.type), "mw": col(q.mw, 1), "phase": col(q.phase), "st": col(q.state), "cty": col(q.county),
        "cod": col(q.cod_year), "qd": col(pd.to_datetime(q.q_date, errors="coerce").dt.year),
        "ent": col(q.entity), "node": col(q.node), "how": col(q.match_method), "src": col(q.source),
    })

    # ---- plants ---------------------------------------------------------------------------------
    p = plants[plants.mw_total >= 5]
    dump("plants.json", {
        "id": col(p.plant_id), "name": col(p.plant_name), "lon": col(p.lon, 4), "lat": col(p.lat, 4),
        "tech": col(p.main_tech), "mw": col(p.mw_total, 1), "kv": col(p.grid_kv), "ba": col(p.ba),
        "node": col(p.bus), "own": col(p.owner),
    })

    # ---- context polygons -------------------------------------------------------------------------
    st = gpd.read_file(next((RAW / "census").glob("cb_*_us_state_20m.zip")))
    st = st[~st.STUSPS.isin(["AK", "HI", "PR"])].to_crs(4326)
    st["geometry"] = st.geometry.simplify(0.01)
    ca = gpd.read_file(RAW / "hifld" / "control_areas.geojson")
    ca = ca[~ca.STATE.isin(["AK", "HI", "NB"])]
    ca["geometry"] = ca.geometry.make_valid().simplify(0.02)
    ca = ca[ca.geometry.notna() & ~ca.geometry.is_empty]
    blm = gpd.read_file(RAW / "blm" / "BLM_lands_open_to_solar_application.geojson")
    blm["geometry"] = blm.geometry.make_valid().simplify(0.01)
    dump("context.json", {
        "states": {"name": col(st.STUSPS), "g": [geom_parts(g) for g in st.geometry]},
        "ba": {"name": col(ca.NAME.str.title()), "peak": col(ca.PEAK_LOAD.where(ca.PEAK_LOAD > 0)),
               "g": [geom_parts(g) for g in ca.geometry]},
        "blm": {"g": [geom_parts(g) for g in blm.geometry]},
    })

    # ---- metadata ----------------------------------------------------------------------------------
    manifest = json.loads(MANIFEST.read_text())
    sources = {}
    for path, m in manifest.items():
        key = m["source"]
        if key.startswith("OpenStreetMap"):
            key = "OpenStreetMap substations (Overpass API)"
        e = sources.setdefault(key, {"source": key, "url": m["url"], "retrieved": m["retrieved_utc"][:10], "files": 0})
        e["files"] += 1
    mb = buses[buses.modeled]
    meta = {
        "built": str(date.today()),
        "summary": summary,
        "counts": {"buses": int(len(buses)), "buses_modeled": int(len(mb)), "lines": int(len(lines)),
                   "line_km": round(float(lines.length_km.sum())), "queue_projects": int(len(queue)),
                   "queue_located": int(len(q)), "queue_gw": round(float(queue.mw.sum()) / 1e3, 1),
                   "plants": int(len(plants)), "gen_gw": round(float(plants.mw_total.sum()) / 1e3, 1)},
        "queue_by_iso": queue.groupby("iso").mw.sum().div(1e3).round(1).to_dict(),
        "queue_by_match": queue.groupby("match_method").size().to_dict(),
        "signals": mb.gen_signal.value_counts().to_dict(),
        "sources": sorted(sources.values(), key=lambda s: s["source"]),
    }
    dump("meta.json", meta)


if __name__ == "__main__":
    main()
