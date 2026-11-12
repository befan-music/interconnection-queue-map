"""Load LBNL interconnection cost studies into one table and place each project on a bus.

Sources: LBNL "Generator Interconnection Costs to the Transmission System" cleaned data files
(MISO, PJM, SPP, NYISO, ISO-NE in real 2022 $; non-ISO balancing authorities in 2024 $).
All costs are converted to 2024 $/kW with the CPI-U annual average ratio (2024 / 2022).
Location: ISO projects are joined to the located LBNL queue history by queue ID; the non-ISO file
gives the point-of-interconnection coordinates, snapped to the nearest bus within 15 km.
Output: data/interim/cost_projects.parquet
"""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, RAW, log  # noqa: E402
from normalize_queues import norm_id  # noqa: E402

COSTS = RAW / "costs"
CPI_2022, CPI_2024 = 292.655, 313.689      # BLS CPI-U, U.S. city average, annual averages
TO_2024 = CPI_2024 / CPI_2022
BA_REGION = {"PAC": "West", "BPA": "West", "DEP": "Southeast", "DEC": "Southeast", "DEF": "Southeast"}


def _iso(region, file, cols, scale=TO_2024):
    d = pd.read_excel(COSTS / file, sheet_name="data")
    g = lambda c: d[cols[c]] if cols.get(c) in d else pd.Series(np.nan, index=d.index)  # noqa: E731
    return pd.DataFrame({
        "region": region, "q_id": g("id").map(norm_id), "state": g("state").astype("string"),
        "county": g("county").astype("string"), "status": g("status").astype("string"),
        "fuel": g("fuel").astype("string"), "service": g("service").astype("string"),
        "mw": pd.to_numeric(g("mw"), errors="coerce"),
        "q_date": pd.to_datetime(g("q_date"), errors="coerce", format="mixed"),
        "study_date": pd.to_datetime(g("study_date"), errors="coerce", format="mixed"),
        "cod_date": pd.to_datetime(g("cod"), errors="coerce", format="mixed"),
        "poi_kw": pd.to_numeric(g("poi"), errors="coerce") * scale,
        "net_kw": pd.to_numeric(g("net"), errors="coerce") * scale,
        "total_kw": pd.to_numeric(g("total"), errors="coerce") * scale,
        "lat": np.nan, "lon": np.nan})


def load_costs():
    usd22 = {"poi": "$2022 POI Cost/kW", "net": "$2022 Network Cost/kW", "total": "$2022 Total Cost/kW"}
    base = {"id": "Project #", "state": "State", "status": "Request Status", "fuel": "Fuel", "service": "Service Type",
            "mw": "Nameplate MW", "q_date": "Queue Date", "study_date": "Study Date"}
    parts = [
        _iso("MISO", "miso_costs_2021_clean_data.xlsx",
             {**base, "cod": "Done Date", "poi": "Real POI/kW", "net": "Real Network/kW", "total": "Real Total/kW"}),
        _iso("PJM", "pjm_costs_2022_clean_data.xlsx", {**base, **usd22, "cod": "Actual In Service Date"}),
        _iso("SPP", "spp_costs_2023_clean_data.xlsx", {**base, **usd22, "cod": "In-Service Date"}),
        _iso("NYISO", "nyiso_2022_final_data_cleaned_publication_vfinal.xlsx",
             {**base, **usd22, "id": "Queue ID", "county": "County", "fuel": "Resource Type"}),
        _iso("ISO-NE", "isone_interconnection_cost_data_publication_vfinal.xlsx",
             {**base, **usd22, "id": "Queue ID 1", "county": "County", "fuel": "Resource Type"}),
    ]
    b = pd.read_excel(COSTS / "ba_costs_2024_clean_data.xlsx", sheet_name="Project Cost Data")
    b = b.sort_values("Study Date").drop_duplicates("Project #", keep="last")     # latest study per project
    parts.append(pd.DataFrame({
        "region": b.BA.map(BA_REGION), "q_id": b["Project #"].map(norm_id), "state": b.State.astype("string"),
        "county": b.County.astype("string"), "status": b["Request Status"].astype("string"),
        "fuel": b.Fuel.astype("string"), "service": b["Service Type"].astype("string"),
        "mw": pd.to_numeric(b["Nameplate MW"], errors="coerce"),
        "q_date": pd.to_datetime(b["Queue Date"], errors="coerce"),
        "study_date": pd.to_datetime(b["Study Date"], errors="coerce"), "cod_date": pd.NaT,
        "poi_kw": pd.to_numeric(b["$2024 POI Cost/kW"], errors="coerce"),
        "net_kw": pd.to_numeric(b["$2024 Network Cost/kW"], errors="coerce"),
        "total_kw": pd.to_numeric(b["$2024 Total Cost/kW"], errors="coerce"),
        "lat": pd.to_numeric(b["Latitude of POI"], errors="coerce"),
        "lon": pd.to_numeric(b["Longitude of POI"], errors="coerce"), "ba": b.BA}))
    c = pd.concat(parts, ignore_index=True)
    c = c[c.total_kw.notna() & (c.mw > 0)]
    c["study_year"] = c.study_date.dt.year
    c["status"] = c.status.str.title()
    return c


def main():
    c = load_costs()
    nodes = gpd.read_parquet(INTERIM / "nodes.parquet")
    hist = pd.read_parquet(INTERIM / "lbnl_located.parquet")

    # ISO projects: join to the located LBNL history by region + queue ID
    h = hist[hist.node >= 0].drop_duplicates(["region", "q_id"])[["region", "q_id", "node", "match_method"]]
    c = c.merge(h, on=["region", "q_id"], how="left")
    # non-ISO projects: snap POI coordinates to the nearest bus within 15 km
    has_xy = c.lat.between(24, 50) & c.lon.between(-125, -66)
    pts = gpd.GeoSeries(gpd.points_from_xy(c.loc[has_xy, "lon"], c.loc[has_xy, "lat"]), crs=4326).to_crs(5070)
    xy = np.c_[pts.x, pts.y]
    finite = np.isfinite(xy).all(axis=1)
    d = np.full(len(xy), np.inf)
    i = np.zeros(len(xy), dtype=int)
    d[finite], i[finite] = cKDTree(nodes[["x", "y"]].values).query(xy[finite])
    near = d <= 15e3
    idx = c.index[has_xy][near]
    c.loc[idx, "node"] = nodes.node.values[i[near]]
    c.loc[idx, "match_method"] = "POI coordinates"
    c["node"] = c.node.fillna(-1).astype(int)
    c.to_parquet(INTERIM / "cost_projects.parquet", index=False)

    loc = c[c.node >= 0]
    log(f"cost studies: {len(c):,} projects, {len(loc):,} placed on a bus ({len(loc) / len(c):.0%})")
    print(c.groupby("region").agg(n=("q_id", "size"), placed=("node", lambda s: (s >= 0).mean()),
                                  recent=("study_year", lambda s: (s >= 2018).mean()),
                                  med_total=("total_kw", "median"), med_net=("net_kw", "median")).round(2).to_string())


if __name__ == "__main__":
    main()
