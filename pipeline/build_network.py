"""Build the transmission network graph from HIFLD line geometry.

Nodes  = substations / junctions, found by snapping line endpoints together (and to OSM substations).
Edges  = HIFLD line segments with screening-level electrical parameters by voltage class.
Output = data/interim/nodes.parquet, data/interim/lines.parquet (GeoParquet, EPSG:4326)
"""
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from shapely.geometry import MultiLineString, Point
from shapely.ops import linemerge

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, RAW, log  # noqa: E402

CRS_M = 5070          # CONUS Albers, metres
SNAP_M = 300          # line endpoints closer than this are the same bus
OSM_MERGE_M = 500     # endpoint clusters this close to one OSM transmission substation are merged
CONUS = (-125.0, 24.0, -66.5, 49.6)

# Typical single-circuit overhead parameters by nominal class (screening values; refs in README).
#   kv_hi: upper bound of the class, x: series reactance [ohm/km], amps: continuous thermal rating,
#   sil: surge impedance loading [MW] at nominal voltage
LINE_CLASSES = pd.DataFrame([
    (105, 0.48, 650, 15), (125, 0.48, 800, 35), (150, 0.48, 900, 50), (190, 0.48, 1000, 70),
    (300, 0.48, 1200, 135), (400, 0.37, 2000, 390), (600, 0.33, 3000, 900), (2000, 0.27, 3500, 2200),
], columns=["kv_hi", "x_ohm_km", "amps", "sil_mw_nom"])
NOMINAL = np.array([100, 115, 138, 161, 230, 345, 500, 765])
# St. Clair curve: loadability as a multiple of SIL versus line length (miles)
STCLAIR_MI = [0, 50, 100, 150, 200, 300, 400, 600]
STCLAIR_X = [10.0, 3.0, 2.0, 1.6, 1.3, 1.0, 0.8, 0.6]

GENERIC = re.compile(r"^(UNKNOWN|TAP|NOT AVAILABLE|NA$|N/A)")


def endpoints(geom):
    if isinstance(geom, MultiLineString):
        merged = linemerge(geom)
        geom = merged if merged.geom_type == "LineString" else geom
    if geom.geom_type == "MultiLineString":
        parts = list(geom.geoms)
        return parts[0].coords[0], parts[-1].coords[-1]
    return geom.coords[0], geom.coords[-1]


def load_osm():
    files = sorted(glob.glob(str(RAW / "osm" / "substations_US-*.json")))
    rows = []
    for f in files:
        for el in json.load(open(f, encoding="utf-8"))["elements"]:
            c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
            if c.get("lat") is None:
                continue
            t = el.get("tags", {})
            volts = [float(v) / 1000 for v in re.findall(r"\d+", t.get("voltage", "")) if float(v) >= 1000]
            rows.append({"osm_id": f"{el['type'][0]}{el['id']}", "lon": c["lon"], "lat": c["lat"],
                         "osm_name": t.get("name"), "osm_operator": t.get("operator"),
                         "osm_kv_max": max(volts) if volts else np.nan,
                         "osm_substation": t.get("substation")})
    if not rows:
        return None
    o = pd.DataFrame(rows)
    o = gpd.GeoDataFrame(o, geometry=gpd.points_from_xy(o.lon, o.lat), crs=4326).to_crs(CRS_M)
    log(f"OSM substations loaded: {len(o):,} from {len(files)} state files")
    return o


def main():
    log("loading HIFLD lines")
    g = gpd.read_file(RAW / "hifld" / "transmission_lines.geojson")
    b = g.geometry.bounds
    cx, cy = (b.minx + b.maxx) / 2, (b.miny + b.maxy) / 2
    g = g[cx.between(CONUS[0], CONUS[2]) & cy.between(CONUS[1], CONUS[3])]
    g = g[~g.STATUS.isin(["INACTIVE", "PROPOSED"]) & g.VOLT_CLASS.ne("DC") & g.geometry.notna()]
    g = g.to_crs(CRS_M).reset_index(drop=True)
    log(f"  {len(g):,} AC line segments in CONUS")

    ends = np.array([endpoints(geom) for geom in g.geometry])            # (n, 2, 2)
    pts = ends.reshape(-1, 2)                                            # 2i = start, 2i+1 = end
    pairs = cKDTree(pts).query_pairs(SNAP_M, output_type="ndarray")
    n = len(pts)

    # optional OSM anchors: merge clusters that sit inside the same transmission substation
    osm = None if "--no-osm" in sys.argv else load_osm()
    extra_pairs = []
    if osm is not None:
        tx = osm[(osm.osm_kv_max >= 100) | osm.osm_substation.eq("transmission")]
        d, j = cKDTree(np.c_[tx.geometry.x, tx.geometry.y]).query(pts, distance_upper_bound=OSM_MERGE_M)
        hit = np.isfinite(d)
        # connect every endpoint to a virtual vertex per OSM substation (index n + j)
        extra_pairs = np.c_[np.nonzero(hit)[0], n + j[hit]]
    allp = np.vstack([pairs, extra_pairs]) if len(extra_pairs) else pairs
    size = n + (len(osm) if osm is not None else 0)
    adj = coo_matrix((np.ones(len(allp)), (allp[:, 0], allp[:, 1])), shape=(size, size))
    _, lab = connected_components(adj, directed=False)
    _, node_of_pt = np.unique(lab[:n], return_inverse=True)
    g["from_node"], g["to_node"] = node_of_pt[0::2], node_of_pt[1::2]
    n_nodes = node_of_pt.max() + 1
    log(f"  {n_nodes:,} buses after snapping (radius {SNAP_M} m)")

    # ---- node geometry & voltage ----------------------------------------------------------
    nodes = pd.DataFrame({"node": node_of_pt, "x": pts[:, 0], "y": pts[:, 1]}).groupby("node").mean()
    kv_long = pd.concat([g[["from_node", "VOLTAGE"]].set_axis(["node", "kv"], axis=1),
                         g[["to_node", "VOLTAGE"]].set_axis(["node", "kv"], axis=1)])
    nodes["kv_max"] = kv_long.groupby("node").kv.max()
    nodes["kv_levels"] = kv_long.groupby("node").kv.apply(lambda s: "/".join(str(int(v)) for v in sorted(set(s), reverse=True)))
    nodes["n_terminations"] = kv_long.groupby("node").size()

    # ---- node names from SUB_1/SUB_2 -------------------------------------------------------
    s1 = g.SUB_1.fillna("").str.strip().str.upper()
    s2 = g.SUB_2.fillna("").str.strip().str.upper()
    cnt = [Counter() for _ in range(n_nodes)]
    for a, b, l1, l2 in zip(g.from_node, g.to_node, s1, s2):
        for nd in (a, b):
            cnt[nd][l1] += 1
            cnt[nd][l2] += 1
    label = [None] * n_nodes
    for nd, cn in enumerate(cnt):
        top = cn.most_common(2)
        if top and top[0][1] >= 2 and (len(top) == 1 or top[0][1] > top[1][1]) and top[0][0]:
            label[nd] = top[0][0]
    # SUB_1 is usually the geometry start: check, then propagate along lines
    both = [(label[a], label[b], l1, l2) for a, b, l1, l2 in zip(g.from_node, g.to_node, s1, s2)
            if label[a] and label[b] and {label[a], label[b]} == {l1, l2} and l1 != l2]
    order_ok = np.mean([la == l1 for la, _, l1, _ in both]) if both else 0
    log(f"  SUB_1 at geometry start for {order_ok:.0%} of fully-resolved lines")
    for _ in range(3):
        for a, b, l1, l2 in zip(g.from_node, g.to_node, s1, s2):
            if label[a] and not label[b]:
                label[b] = l2 if label[a] == l1 else (l1 if label[a] == l2 else None)
            elif label[b] and not label[a]:
                label[a] = l1 if label[b] == l2 else (l2 if label[b] == l1 else None)
    if order_ok > 0.9:
        for a, b, l1, l2 in zip(g.from_node, g.to_node, s1, s2):
            label[a] = label[a] or l1
            label[b] = label[b] or l2
    nodes["hifld_label"] = label
    lab_s = nodes.hifld_label.fillna("")
    nodes["node_type"] = np.select(
        [lab_s.str.startswith("TAP"), lab_s.eq("") | lab_s.str.match(GENERIC.pattern)],
        ["tap", "substation (unnamed)"], "substation")

    # ---- OSM enrichment ----------------------------------------------------------------------
    if osm is not None:
        d, j = cKDTree(np.c_[osm.geometry.x, osm.geometry.y]).query(nodes[["x", "y"]].values,
                                                                     distance_upper_bound=800)
        ok = np.isfinite(d)
        for col in ("osm_id", "osm_name", "osm_operator", "osm_kv_max"):
            nodes[col] = None
            nodes.loc[ok, col] = osm[col].values[j[ok]]
        nodes["osm_dist_m"] = np.where(ok, d, np.nan)
        log(f"  {ok.mean():.0%} of buses matched to an OSM substation within 800 m")
    else:
        for col in ("osm_id", "osm_name", "osm_operator", "osm_kv_max", "osm_dist_m"):
            nodes[col] = None

    def display(r):
        if r.node_type == "substation":
            return r.hifld_label.title()
        if isinstance(r.osm_name, str) and r.osm_name:
            return r.osm_name
        return "Line tap" if r.node_type == "tap" else "Unnamed substation"
    nodes["name"] = nodes.apply(display, axis=1)
    nodes.loc[nodes.node_type.ne("substation") & nodes.osm_name.notna(), "node_type"] = "substation"

    # ---- line electrical parameters ---------------------------------------------------------
    g = g[g.from_node != g.to_node].copy()
    g["length_km"] = np.maximum(g.length / 1000, 0.5)
    kv = g.VOLTAGE.astype(float)
    cls = LINE_CLASSES.iloc[np.searchsorted(LINE_CLASSES.kv_hi.values, kv.values)].reset_index(drop=True)
    cls.index = g.index
    nominal = NOMINAL[np.abs(np.log(kv.values[:, None] / NOMINAL[None, :])).argmin(axis=1)]
    g["kv"] = kv
    g["x_pu"] = cls.x_ohm_km * g.length_km / (kv ** 2 / 100.0)               # 100 MVA base
    g["thermal_mva"] = np.sqrt(3) * kv * cls.amps / 1000
    g["sil_mw"] = cls.sil_mw_nom * (kv / nominal) ** 2
    g["stclair_mw"] = g.sil_mw * np.interp(g.length_km / 1.609, STCLAIR_MI, STCLAIR_X)
    g["limit_mw"] = np.minimum(g.thermal_mva, g.stclair_mw)
    g["limit_basis"] = np.where(g.thermal_mva <= g.stclair_mw, "thermal", "stability (St. Clair)")

    # ---- interconnection labels & seam cuts ---------------------------------------------------
    # The three US interconnections meet only through DC ties, but snapping merges the AC lines
    # on both sides of each back-to-back converter. Label every bus by its balancing-authority
    # polygon (whose interconnection = majority NERC region of the EIA plants inside it), fall
    # back to the nearest plants, then cut any line that joins two interconnections.
    pts_ll = gpd.GeoSeries(gpd.points_from_xy(nodes.x, nodes.y), crs=CRS_M).to_crs(4326)
    nodes["lon"], nodes["lat"] = pts_ll.x.values, pts_ll.y.values
    plants = gpd.read_parquet(INTERIM / "plants.parquet").dropna(subset=["interconnection"])
    ca = gpd.read_file(RAW / "hifld" / "control_areas.geojson")
    ca["geometry"] = ca.geometry.make_valid()
    pj = gpd.sjoin(plants[["interconnection", "geometry"]], ca[["ID", "geometry"]], predicate="within")
    ca_ic = pj.groupby("ID").interconnection.agg(lambda s: s.mode().iat[0] if len(s) >= 3 else None)
    ca["ic"] = ca.ID.map(ca_ic)
    nj = gpd.sjoin(gpd.GeoDataFrame(geometry=pts_ll.values, crs=4326), ca.dropna(subset=["ic"])[["ic", "geometry"]],
                   predicate="within")
    poly_ic = nj.groupby(level=0).ic.agg(lambda s: s.iat[0] if s.nunique() == 1 else None)
    pxy = plants.to_crs(CRS_M)
    _, nn = cKDTree(np.c_[pxy.geometry.x, pxy.geometry.y]).query(nodes[["x", "y"]].values, k=9)
    knn_ic = pd.DataFrame(plants.interconnection.to_numpy(dtype=object)[nn]).mode(axis=1)[0]
    nodes["ic_label"] = poly_ic.reindex(range(n_nodes)).fillna(knn_ic).values

    seam = g.from_node.map(nodes.ic_label).values != g.to_node.map(nodes.ic_label).values
    cut = g[seam]
    log(f"  cutting {seam.sum()} seam lines between interconnections "
        f"(kV: {cut.kv.value_counts().to_dict()})")
    g = g[~seam].copy()

    adj = coo_matrix((np.ones(len(g)), (g.from_node, g.to_node)), shape=(n_nodes, n_nodes))
    ncomp, comp = connected_components(adj, directed=False)
    nodes["component"] = comp
    sizes = pd.Series(comp).value_counts()
    nodes["component_size"] = nodes.component.map(sizes)
    ic = {}
    for cid, sz in sizes.items():
        if sz < 200:
            break
        m = nodes.component.eq(cid)
        ic[cid] = nodes.loc[m, "ic_label"].mode().iat[0]
        log(f"  component {cid}: {sz:,} buses -> {ic[cid]} "
            f"(label purity {nodes.loc[m, 'ic_label'].eq(ic[cid]).mean():.1%})")
    nodes["interconnection"] = nodes.component.map(ic).fillna("island")
    nodes["modeled"] = nodes.component.isin(ic)
    log(f"  {ncomp:,} components; {nodes.modeled.mean():.1%} of buses in modeled networks")

    # ---- state & county for each bus --------------------------------------------------------
    county = gpd.read_file(next((RAW / "census").glob("cb_*_us_county_500k.zip")))[["GEOID", "NAME", "STUSPS", "geometry"]]
    pts_gdf = gpd.GeoDataFrame(nodes[[]], geometry=pts_ll.values, crs=4326)
    j = gpd.sjoin(pts_gdf, county.to_crs(4326), how="left", predicate="within")
    j = j[~j.index.duplicated()]
    nodes["state"] = j.STUSPS
    nodes["county_fips"] = j.GEOID
    nodes["county"] = j.NAME

    nodes.index.name = "node"
    nodes = gpd.GeoDataFrame(nodes.reset_index(), geometry=pts_ll.values, crs=4326)
    nodes.to_parquet(INTERIM / "nodes.parquet")

    keep = ["ID", "OWNER", "STATUS", "SUB_1", "SUB_2", "kv", "VOLT_CLASS", "TYPE", "length_km", "x_pu",
            "thermal_mva", "sil_mw", "stclair_mw", "limit_mw", "limit_basis", "from_node", "to_node",
            "SOURCEDATE", "VAL_DATE", "geometry"]
    lines = g[keep].rename(columns=str.lower).to_crs(4326)
    lines.to_parquet(INTERIM / "lines.parquet")
    log(f"saved {len(nodes):,} buses and {len(lines):,} lines")
    print(nodes.node_type.value_counts().to_string())
    print(nodes.interconnection.value_counts().to_string())
    print(lines.groupby(pd.cut(lines.kv, [0, 105, 125, 150, 190, 300, 400, 600, 2000])).agg(
        n=("kv", "size"), km=("length_km", "sum"), limit_med=("limit_mw", "median")).round(0).to_string())


if __name__ == "__main__":
    main()
