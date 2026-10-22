"""Prepare the existing generation fleet from EIA-860M (Aug 2026) + EIA-860 (2025 annual).

Output: data/interim/generators.parquet  operating generators, CONUS
        data/interim/plants.parquet      plant-level aggregation with grid voltage & NERC region
        data/interim/planned_generators.parquet
"""
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, RAW, log  # noqa: E402

TECH = {
    "Nuclear": "Nuclear",
    "Conventional Steam Coal": "Coal", "Coal Integrated Gasification Combined Cycle": "Coal",
    "Petroleum Coke": "Coal",
    "Natural Gas Fired Combined Cycle": "Gas CC",
    "Natural Gas Fired Combustion Turbine": "Gas CT/other", "Natural Gas Steam Turbine": "Gas CT/other",
    "Natural Gas Internal Combustion Engine": "Gas CT/other", "Other Natural Gas": "Gas CT/other",
    "Other Gases": "Gas CT/other", "Natural Gas with Compressed Air Storage": "Gas CT/other",
    "Petroleum Liquids": "Oil",
    "Conventional Hydroelectric": "Hydro", "Hydroelectric Pumped Storage": "Pumped storage",
    "Onshore Wind Turbine": "Wind", "Offshore Wind Turbine": "Offshore wind",
    "Solar Photovoltaic": "Solar", "Solar Thermal without Energy Storage": "Solar",
    "Solar Thermal with Energy Storage": "Solar",
    "Batteries": "Battery", "Flywheels": "Battery",
    "Geothermal": "Geothermal",
}  # everything else (landfill gas, wood, MSW, other) -> "Biomass/other"

# Share of capacity producing at a summer-peak evening hour (screening assumption)
PEAK_AVAIL = {"Nuclear": 0.95, "Coal": 0.85, "Gas CC": 0.90, "Gas CT/other": 0.85, "Oil": 0.50,
              "Hydro": 0.60, "Pumped storage": 0.50, "Wind": 0.25, "Offshore wind": 0.35,
              "Solar": 0.50, "Battery": 0.60, "Geothermal": 0.90, "Biomass/other": 0.70}
DISPATCHABLE = ["Coal", "Gas CC", "Gas CT/other", "Oil"]
RENEWABLE = ["Wind", "Offshore wind", "Solar"]

NERC_TO_IC = {"WECC": "Western", "TRE": "ERCOT", "SERC": "Eastern", "RFC": "Eastern",
              "MRO": "Eastern", "NPCC": "Eastern", "SPP": "Eastern"}
CONUS_EXCL = {"AK", "HI", "PR", "VI", "GU"}


def main():
    m = RAW / "eia860m" / sorted(p.name for p in (RAW / "eia860m").glob("*.xlsx"))[-1]
    log(f"reading {m.name}")
    op = pd.read_excel(m, sheet_name="Operating", header=2).dropna(subset=["Plant ID"])
    pl = pd.read_excel(m, sheet_name="Planned", header=2).dropna(subset=["Plant ID"])
    plant860 = pd.read_excel(next((RAW / "eia860").glob("eia860*/2___Plant_Y*.xlsx")), header=1)

    for d in (op, pl):
        d["Plant ID"] = d["Plant ID"].astype(int)
        d["tech"] = d.Technology.map(TECH).fillna("Biomass/other")
    op = op[op.Status.str[:4].isin(["(OP)", "(SB)"]) & ~op["Plant State"].isin(CONUS_EXCL)]
    pl = pl[~pl["Plant State"].isin(CONUS_EXCL)]
    op["mw"] = pd.to_numeric(op["Net Summer Capacity (MW)"], errors="coerce").fillna(
        pd.to_numeric(op["Nameplate Capacity (MW)"], errors="coerce")).clip(lower=0)
    pl["mw"] = pd.to_numeric(pl["Nameplate Capacity (MW)"], errors="coerce")

    meta = plant860.rename(columns={"Plant Code": "plant_id", "NERC Region": "nerc",
                                    "Grid Voltage (kV)": "grid_kv", "Grid Voltage 2 (kV)": "grid_kv2",
                                    "Transmission or Distribution System Owner": "tdso",
                                    "Balancing Authority Code": "ba_860"})
    meta = meta[["plant_id", "nerc", "grid_kv", "grid_kv2", "tdso", "ba_860"]].astype(
        {"nerc": "string", "tdso": "string", "ba_860": "string"})
    for c in ("grid_kv", "grid_kv2"):
        meta[c] = pd.to_numeric(meta[c], errors="coerce")

    gens = op.rename(columns={"Plant ID": "plant_id", "Plant Name": "plant_name", "Plant State": "state",
                              "County": "county", "Balancing Authority Code": "ba", "Technology": "technology",
                              "Operating Year": "op_year", "Planned Retirement Year": "retire_year",
                              "Latitude": "lat", "Longitude": "lon", "Entity Name": "owner",
                              "Generator ID": "gen_id"})
    gens = gens[["plant_id", "gen_id", "plant_name", "owner", "state", "county", "ba", "technology", "tech",
                 "mw", "op_year", "retire_year", "lat", "lon"]]
    for c in ("op_year", "retire_year", "lat", "lon"):
        gens[c] = pd.to_numeric(gens[c], errors="coerce")
    for c in ("gen_id", "plant_name", "owner", "state", "county", "ba", "technology"):
        gens[c] = gens[c].astype("string")
    gens["peak_avail"] = gens.tech.map(PEAK_AVAIL)
    gens.to_parquet(INTERIM / "generators.parquet", index=False)

    cap = gens.pivot_table(index="plant_id", columns="tech", values="mw", aggfunc="sum", fill_value=0)
    first = gens.groupby("plant_id").first()[["plant_name", "owner", "state", "county", "ba", "lat", "lon"]]
    plants = first.join(cap).reset_index().merge(meta, on="plant_id", how="left")
    plants["mw_total"] = cap.sum(axis=1).reindex(plants.plant_id).values
    plants["main_tech"] = cap.idxmax(axis=1).reindex(plants.plant_id).values
    plants["ba"] = plants.ba.fillna(plants.ba_860)
    # NERC region for plants new since the 2025 annual: majority NERC of the same BA
    ba_nerc = plants.dropna(subset=["nerc"]).groupby("ba").nerc.agg(lambda s: s.mode().iat[0])
    plants["nerc"] = plants.nerc.fillna(plants.ba.map(ba_nerc))
    plants["interconnection"] = plants.nerc.map(NERC_TO_IC)
    plants = gpd.GeoDataFrame(plants, geometry=gpd.points_from_xy(plants.lon, plants.lat), crs=4326)
    plants = plants[plants.lat.between(24, 50) & plants.lon.between(-125, -66)]
    plants.to_parquet(INTERIM / "plants.parquet")

    pl = pl.rename(columns={"Plant ID": "plant_id", "Plant Name": "plant_name", "Plant State": "state",
                            "Balancing Authority Code": "ba", "Planned Operation Year": "year",
                            "Latitude": "lat", "Longitude": "lon", "Status": "status"})
    pl[["plant_id", "plant_name", "state", "ba", "Technology", "tech", "mw", "year", "status", "lat", "lon"]] \
        .to_parquet(INTERIM / "planned_generators.parquet", index=False)

    log(f"operating: {len(gens):,} generators / {len(plants):,} plants / {gens.mw.sum() / 1e3:,.0f} GW")
    log(f"planned (860M): {len(pl):,} generators / {pl.mw.sum() / 1e3:,.0f} GW")
    print(gens.groupby("tech").mw.sum().div(1e3).round(1).sort_values(ascending=False).to_string())
    print(plants.interconnection.value_counts(dropna=False).to_string())


if __name__ == "__main__":
    main()
