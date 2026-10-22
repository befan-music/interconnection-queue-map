"""Locate every active queue project on a network bus.

1. Geocode the project's county (Census) -> centroid.
2. Fuzzy-match the POI name ("Lamont 115 kV", "Helm-Kerman 70 kV Line", "59903 Bearkat 345kV")
   against substation names (HIFLD endpoint labels + OSM) within 150 km of that county,
   rewarding voltage agreement and proximity.
3. Fall back to the nearest bus of suitable voltage to the county centroid.
Output: data/interim/queue_located.parquet
"""
import re
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from rapidfuzz import fuzz, process
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, RAW, log  # noqa: E402

CRS_M = 5070
NAME_RADIUS_KM = 150
MIN_SCORE = 87

STOP = (r"\b(SUBSTATION|SUBSTA|SUBST|SUB|SWITCHYARD|SWITCHING|STATION|STA|SWYD|SS|BUS|POI|TAP|TAPS|"
        r"LINE|LN|KV|NEW|PROPOSED|EXISTING|CONCEPTUAL|THE|AT|INTO|ON|SWITCH|YARD|TRANSMISSION|CIRCUIT|CKT|"
        r"GEN|INTERCONNECTION|POINT|OF|AND|LOOP|IN|OUT|NO|FUTURE|EXPANSION|SECTION|SIDE|HIGH|LOW|DOUBLE|"
        r"SINGLE|CIRCUITS|EVERSOURCE|NSTAR|NATIONAL|GRID|AEP|ONCOR|CENTERPOINT|TNMP|LCRA|AEN|SHARYLAND)\b")


def clean(s):
    if not isinstance(s, str):
        return ""
    s = s.upper()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\d{2,3}(\.\d+)?\s*-?\s*KV", " ", s)
    s = re.sub(r"^\s*\d{3,6}\s+", " ", s)
    s = re.sub(r"#\s*\d+", " ", s)
    s = re.sub(r"[^A-Z0-9 \-/]", " ", s)
    s = re.sub(STOP, " ", s)
    return " ".join(s.split())


def poi_variants(poi):
    """Full name plus line endpoints for 'A-B line' style POIs."""
    full = clean(poi)
    out = [(full.replace("-", " ").replace("/", " "), "name")]
    if isinstance(poi, str) and re.search(r"[-/]| TO ", poi.upper()):
        for part in re.split(r"\s*[-/]\s*|\s+TO\s+", full):
            part = part.strip()
            if len(part) >= 3 and part != out[0][0]:
                out.append((part, "line endpoint"))
    return [v for v in out if len(v[0]) >= 3]


def norm_county(s):
    s = str(s).lower().split(",")[0].split("/")[0].split(";")[0].split(" and ")[0]
    s = re.sub(r"\b(county|parish|borough|census area|municipality|city and)\b", "", s)
    s = s.replace("saint ", "st ").replace("st. ", "st ").replace(".", "").replace("'", "")
    return re.sub(r"\s+", " ", s).strip()


def main():
    q = pd.read_parquet(INTERIM / "queue_projects.parquet")
    nodes = gpd.read_parquet(INTERIM / "nodes.parquet")

    county = gpd.read_file(next((RAW / "census").glob("cb_*_us_county_500k.zip")))
    county = county.to_crs(CRS_M)
    county["ctr_x"], county["ctr_y"] = county.geometry.centroid.x, county.geometry.centroid.y
    county["key"] = county.STUSPS + "|" + county.NAME.map(norm_county)
    county = pd.DataFrame(county.drop(columns="geometry"))
    by_key = county.drop_duplicates("key").set_index("key")
    by_fips = county.set_index(county.GEOID.astype(int))

    # county centroid per project: FIPS (LBNL) first, then state|county name
    fips = pd.to_numeric(q.get("fips"), errors="coerce")
    cx = fips.map(by_fips.ctr_x)
    cy = fips.map(by_fips.ctr_y)
    key = q.state.fillna("") + "|" + q.county.map(norm_county)
    cx = cx.fillna(key.map(by_key.ctr_x))
    cy = cy.fillna(key.map(by_key.ctr_y))
    q["county_located"] = cx.notna()
    q["cx"], q["cy"] = cx, cy
    log(f"county geocoded: {q.county_located.mean():.1%} of {len(q):,} projects")

    # bus name index
    nodes["nm_hifld"] = np.where(nodes.node_type.eq("substation"), nodes.hifld_label.map(clean), "")
    nodes["nm_osm"] = nodes.osm_name.map(clean) if "osm_name" in nodes else ""
    named = nodes[(nodes.nm_hifld.str.len() >= 3) | (nodes.nm_osm.str.len() >= 3)]
    tree_named = cKDTree(named[["x", "y"]].values)
    tree_all = cKDTree(nodes[["x", "y"]].values)
    kv_sets = nodes.kv_levels.astype(str).str.split("/").map(lambda l: {float(v) for v in l if v})

    st_centroid = county.groupby("STUSPS")[["ctr_x", "ctr_y"]].mean()
    res = []
    for r in q.itertuples():
        x, y = r.cx, r.cy
        if pd.isna(x) and isinstance(r.state, str) and r.state in st_centroid.index:
            x, y = st_centroid.loc[r.state]
            radius = 450e3
        else:
            radius = NAME_RADIUS_KM * 1e3
        best = None
        if not pd.isna(x):
            idx = tree_named.query_ball_point([x, y], radius)
            if idx:
                cand = named.iloc[idx]
                names_h, names_o = cand.nm_hifld.tolist(), cand.nm_osm.tolist()
                for variant, how in poi_variants(r.poi_name):
                    s = np.maximum(process.cdist([variant], names_h, scorer=fuzz.ratio)[0],
                                   process.cdist([variant], names_o, scorer=fuzz.ratio)[0])
                    if s.max() < MIN_SCORE:
                        continue
                    d_km = np.hypot(cand.x.values - x, cand.y.values - y) / 1e3
                    kv_ok = np.zeros(len(cand), dtype=bool)
                    if not pd.isna(r.poi_kv):
                        for k in np.nonzero(s >= MIN_SCORE)[0]:
                            kv_ok[k] = any(abs(v - r.poi_kv) <= 0.1 * r.poi_kv for v in kv_sets.iat[cand.index[k]])
                    total = s + 6 * kv_ok - d_km / 25 - (3 if how != "name" else 0)
                    i = int(np.argmax(np.where(s >= MIN_SCORE, total, -1e9)))
                    if best is None or total[i] > best[0]:
                        best = (total[i], cand.index[i], how, float(s[i]), float(d_km[i]))
        if best:
            res.append((r.uid, best[1], f"POI {best[2]} match", best[3], best[4]))
            continue
        if pd.isna(x):
            res.append((r.uid, -1, "unlocated", np.nan, np.nan))
            continue
        # fallback: nearest bus with a plausible voltage
        min_kv = 0.9 * r.poi_kv if not pd.isna(r.poi_kv) else (200 if r.mw >= 300 else 100)
        d, idx = tree_all.query([x, y], k=40)
        ok = [i for i in idx if nodes.kv_max.iat[i] >= min_kv]
        i = ok[0] if ok else idx[0]
        res.append((r.uid, int(nodes.index[i]), "county proximity",
                    np.nan, float(np.hypot(nodes.x.iat[i] - x, nodes.y.iat[i] - y) / 1e3)))

    m = pd.DataFrame(res, columns=["uid", "bus_idx", "match_method", "match_score", "match_dist_km"])
    out = q.merge(m, on="uid")
    ok = out.bus_idx >= 0
    out["node"] = np.where(ok, nodes.node.reindex(out.bus_idx.where(ok, 0)).values, -1)
    out["bus_name"] = np.where(ok, nodes.name.reindex(out.bus_idx.where(ok, 0)).values, None)
    out["lon"] = np.where(ok, nodes.lon.reindex(out.bus_idx.where(ok, 0)).values, np.nan)
    out["lat"] = np.where(ok, nodes.lat.reindex(out.bus_idx.where(ok, 0)).values, np.nan)
    out = out.drop(columns=["bus_idx", "cx", "cy"])
    out.to_parquet(INTERIM / "queue_located.parquet", index=False)
    summary = out.groupby("match_method").agg(n=("uid", "size"), gw=("mw", lambda s: s.sum() / 1e3)).round(1)
    print(summary.to_string())
    print(out[out.match_method.str.startswith("POI")].sample(12, random_state=3)[
              ["iso", "poi_name", "bus_name", "match_score", "match_dist_km"]].to_string())


if __name__ == "__main__":
    main()
