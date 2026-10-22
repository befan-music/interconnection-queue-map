"""Screening-level DC power-flow model of the US transmission system + interconnection headroom.

Inputs : network (build_network), fleet (prepare_eia), located queue (match_queue),
         EIA-930 summer-2026 BA peaks, Census 2020 tract population, HIFLD control areas.
Steps  : 1. every bus gets a balancing authority (BA); BA peak load (EIA-930) is spread over Census
            tracts by population and attached to the nearest bus
         2. EIA-860M generators -> bus (voltage-aware snapping)
         3. two dispatch scenarios (summer peak; light load / high renewables), each BA balanced to
            its own load first, the interconnection-wide residual settled afterwards
         4. DC power flow; line ratings calibrated so today's modeled flows fit (see README)
         5. PTDF headroom: MW injectable (generation) / withdrawable (load) at each bus before a line
            carrying >= DF_MIN of the change reaches its usable limit. New MW are absorbed pro rata by
            the non-nuclear output of the bus's own BA, as in ISO studies. Repeated with the
            phase-weighted active queue added (capped at what each BA can displace). A 5% DF
            deliverability-style figure is kept alongside.
Output : data/processed/{buses,lines,queue,plants}.parquet, data/processed/model_summary.json
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import splu
from scipy.spatial import cKDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, PROCESSED, RAW, log  # noqa: E402
from prepare_eia import DISPATCHABLE, PEAK_AVAIL  # noqa: E402

CRS_M = 5070
DF_MIN = 0.20          # DF cut-off for energy-only (ERIS-style) screening, as in MISO/SPP studies
DF_DELIV = 0.05        # DF cut-off for deliverability-style (NRIS) screening, reported alongside
ALPHA = 0.85           # usable share of rating (proxy for N-1 security margin)
CALIB_UTIL = 0.90      # existing flows may use at most this share of the usable limit
THERMAL_MIN = 0.15     # minimum thermal commitment in the light-load scenario
MIN_SINK_MW = 300.0    # BAs with less flexible output fall back to an interconnection-wide sink
HEADROOM_CAP = 3000.0  # MW, display cap (larger than any single request)
BATCH = 256

SCENARIOS = {
    "peak": {"load_scale": 1.00, "avail": PEAK_AVAIL},
    "light": {"load_scale": 0.55, "avail": {**PEAK_AVAIL, "Solar": 0.85, "Wind": 0.45, "Offshore wind": 0.5,
                                            "Battery": 0.0, "Pumped storage": 0.0, "Hydro": 0.5,
                                            "Biomass/other": 0.6}},
}
# probability that an active request reaches operation (screening assumption informed by LBNL
# completion statistics: ~13-20% overall, much higher once an IA is signed)
PHASE_WEIGHT = {"Early study": 0.10, "Late study": 0.25, "IA executed": 0.55, "Construction": 0.90}
QUEUE_AVAIL = {"peak": {"Solar": 0.5, "Wind": 0.25, "Offshore Wind": 0.35, "Battery": 0.6, "Gas": 0.85,
                        "Nuclear": 0.95, "Hydro": 0.6, "Geothermal": 0.9},
               "light": {"Solar": 0.85, "Wind": 0.45, "Offshore Wind": 0.5, "Battery": 0.0, "Gas": 0.15,
                         "Nuclear": 0.95, "Hydro": 0.5, "Geothermal": 0.9}}


def queue_avail(t, scen):
    parts = str(t).split("+")
    return max(QUEUE_AVAIL[scen].get(p, 0.5) for p in parts)


# ----------------------------------------------------------------------------------------------
def ba_peaks():
    d = pd.read_csv(RAW / "eia930" / "EIA930_BALANCE_Jul_Dec.csv",
                    usecols=["Balancing Authority", "Demand (MW)", "Demand (MW) (Adjusted)"], thousands=",")
    dem = pd.to_numeric(d["Demand (MW) (Adjusted)"], errors="coerce").fillna(
        pd.to_numeric(d["Demand (MW)"], errors="coerce"))
    d = d.assign(dem=dem).dropna(subset=["dem"])
    # robust peak: 3rd-highest hour (drops single-hour telemetry spikes)
    return d.groupby("Balancing Authority").dem.apply(lambda s: s.nlargest(3).min())


def ba_polygons(plants, valid):
    """HIFLD control-area polygons labelled with the EIA BA code most plants inside them report."""
    ca = gpd.read_file(RAW / "hifld" / "control_areas.geojson")
    ca["geometry"] = ca.geometry.make_valid().simplify(0.01)
    ca["area"] = ca.to_crs(CRS_M).area
    pj = gpd.sjoin(plants[["ba", "geometry"]].dropna(), ca[["ID", "geometry"]], predicate="within")
    ca["ba"] = ca.ID.map(pj.groupby("ID").ba.agg(lambda s: s.mode().iat[0]))
    return ca[ca.ba.isin(valid)][["ba", "area", "geometry"]]


def locate_ba(pts, ca, plants, valid):
    """BA for each point: smallest containing polygon, else the BA of the nearest plant."""
    j = gpd.sjoin(pts[["geometry"]], ca, predicate="within", how="left")
    j = j.sort_values("area").loc[lambda x: ~x.index.duplicated()]
    ba = j.ba.reindex(pts.index).astype(object)
    miss = ba.isna().values
    if miss.any():
        pl = plants[plants.ba.isin(valid)].to_crs(CRS_M)
        pm = pts[miss].to_crs(CRS_M)
        _, nn = cKDTree(np.c_[pl.geometry.x, pl.geometry.y]).query(np.c_[pm.geometry.x, pm.geometry.y])
        ba[miss] = pl.ba.astype(object).values[nn]
    return ba


def allocate_load(buses, peaks, ca, plants):
    tr = pd.read_csv(RAW / "census" / "CenPop2020_Mean_TR.txt", encoding="latin-1")
    tr.columns = [c.strip().upper() for c in tr.columns]
    tr = tr[tr.LATITUDE.between(24, 50) & tr.LONGITUDE.between(-125, -66)]
    tr = gpd.GeoDataFrame(tr, geometry=gpd.points_from_xy(tr.LONGITUDE, tr.LATITUDE), crs=4326)
    tr["ba"] = locate_ba(tr, ca, plants, peaks.index)
    pop_ba = tr.groupby("ba").POPULATION.sum()
    tr["load_mw"] = tr.POPULATION * tr.ba.map(peaks) / tr.ba.map(pop_ba)
    trm = tr.to_crs(CRS_M)
    mb = buses[buses.modeled]
    _, nn = cKDTree(mb[["x", "y"]].values).query(np.c_[trm.geometry.x, trm.geometry.y])
    load = pd.Series(tr.load_mw.values).groupby(mb.node.values[nn]).sum()
    used = peaks[peaks.index.isin(tr.ba.unique())]
    log(f"load: {used.sum() / 1e3:,.0f} GW non-coincident BA peak across {len(used)} BAs "
        f"(EIA-930 Jul-Oct {pd.Timestamp.today().year}); allocated over {len(tr):,} tracts")
    return load, used


def assign_plants(buses, plants):
    pxy = plants.to_crs(CRS_M)
    tree = cKDTree(buses[["x", "y"]].values)
    d, idx = tree.query(np.c_[pxy.geometry.x.values, pxy.geometry.y.values], k=15)
    kvsets = buses.kv_levels.astype(str).str.split("/").map(lambda l: [float(v) for v in l if v]).values
    out_bus, out_d = [], []
    for i, gkv in enumerate(plants.grid_kv.values):
        choice = 0
        if not pd.isna(gkv) and gkv >= 60:
            for k in range(15):
                if d[i, k] > 30e3:
                    break
                if any(abs(v - gkv) <= 0.15 * gkv for v in kvsets[idx[i, k]]):
                    choice = k
                    break
        out_bus.append(buses.node.values[idx[i, choice]])
        out_d.append(d[i, choice] / 1e3)
    plants = plants.copy()
    plants["bus"] = out_bus
    plants["bus_dist_km"] = out_d
    return plants


def dispatch(g, load_by_ba, scen):
    """Per-generator output (MW) for one interconnection: each BA meets its own load first, then the
    interconnection residual is settled by thermal plants with room (or by backing down/curtailing)."""
    av = g.tech.map(SCENARIOS[scen]["avail"]).fillna(0.5).values * g.mw.values
    disp = g.tech.isin(DISPATCHABLE).values
    ren = g.tech.isin(["Solar", "Wind", "Offshore wind"]).values
    must = ~disp & ~ren
    floor = THERMAL_MIN if scen == "light" else 0.0
    out = np.where(disp, av * floor, av)
    ba = g.ba.values
    for b_, L_b in load_by_ba.items():
        m = ba == b_
        dm = m & disp
        need = L_b - out[m].sum()
        if need > 0 and av[dm].sum() > 0:
            out[dm] = av[dm] * min(floor + need / av[dm].sum(), 1.0)
    L = sum(load_by_ba.values())
    R = L - out.sum()
    if R > 0:
        spare = av - out
        spare[~disp] = 0
        out += spare * min(1.0, R / max(spare.sum(), 1))
    elif R < 0:
        above = np.where(disp, out - av * floor, 0)
        out -= above * min(1.0, -R / max(above.sum(), 1))
        R2 = L - out.sum()
        if R2 < 0:
            out[ren] *= max(0.0, 1 + R2 / max(out[ren].sum(), 1))
    return out, {"thermal_util": float(out[disp].sum() / max(av[disp].sum(), 1)),
                 "renewable_curtail_factor": float(out[ren].sum() / max(av[ren].sum(), 1)),
                 "shortfall_mw": float(L - out.sum())}


def main():
    buses = gpd.read_parquet(INTERIM / "nodes.parquet")
    lines = gpd.read_parquet(INTERIM / "lines.parquet").reset_index(drop=True)
    plants = gpd.read_parquet(INTERIM / "plants.parquet")
    gens = pd.read_parquet(INTERIM / "generators.parquet")
    queue = pd.read_parquet(INTERIM / "queue_located.parquet")

    peaks = ba_peaks()
    ca = ba_polygons(plants, peaks.index)
    buses["ba"] = locate_ba(buses, ca, plants, peaks.index).values
    load, ba_used = allocate_load(buses, peaks, ca, plants)
    buses["load_peak_mw"] = buses.node.map(load).fillna(0.0)

    plants = assign_plants(buses, plants)
    gens = gens.merge(plants[["plant_id", "bus"]], on="plant_id", how="inner")
    bidx = buses.set_index("node")
    gens["ic"] = gens.bus.map(bidx.interconnection)
    gens["ba"] = gens.bus.map(bidx.ba)          # balance on the BA of the connecting bus
    cap = gens.pivot_table(index="bus", columns="tech", values="mw", aggfunc="sum", fill_value=0)
    buses["gen_mw"] = buses.node.map(cap.sum(axis=1)).fillna(0.0)
    for t in ("Solar", "Wind", "Battery", "Gas CC", "Gas CT/other", "Coal", "Nuclear", "Hydro"):
        buses[f"gen_{t.lower().replace(' ', '_').replace('/', '_')}_mw"] = buses.node.map(cap.get(t)).fillna(0.0)

    # queue per bus
    queue["weight"] = queue.phase.map(PHASE_WEIGHT).fillna(0.1)
    qb = queue[queue.node >= 0]
    buses["queue_mw"] = buses.node.map(qb.groupby("node").mw.sum()).fillna(0.0)
    buses["queue_n"] = buses.node.map(qb.groupby("node").size()).fillna(0).astype(int)
    buses["queue_expected_mw"] = buses.node.map((qb.mw * qb.weight).groupby(qb.node).sum()).fillna(0.0)
    for ph in PHASE_WEIGHT:
        col = "queue_" + ph.lower().replace(" ", "_") + "_mw"
        buses[col] = buses.node.map(qb[qb.phase.eq(ph)].groupby("node").mw.sum()).fillna(0.0)
    buses["queue_storage_mw"] = buses.node.map(
        qb[qb.type.str.contains("Battery", na=False)].groupby("node").mw.sum()).fillna(0.0)

    # substation structural capacity: N-1 sum of incident branch limits
    inc = pd.concat([lines[["from_node", "limit_mw"]].set_axis(["node", "lim"], axis=1),
                     lines[["to_node", "limit_mw"]].set_axis(["node", "lim"], axis=1)])
    g_inc = inc.groupby("node").lim
    n1 = (g_inc.sum() - g_inc.max()).where(g_inc.size() > 1, g_inc.sum())
    buses["branch_capacity_n1_mw"] = buses.node.map(n1).fillna(0.0)
    buses["degree"] = buses.node.map(g_inc.size()).fillna(0).astype(int)

    # ------------------------------------------------------------------------------------------
    for c in ("peak", "light"):
        lines[f"flow_{c}_mw"] = np.nan
        lines[f"flow_{c}_q_mw"] = np.nan
        buses[f"inj_{c}_mw"] = np.nan
        buses[f"qinj_{c}_mw"] = np.nan
    res_cols = ["hr_gen_peak", "hr_gen_light", "hr_gen_peak_q", "hr_gen_light_q", "hr_load_peak",
                "hr_load_peak_q", "hr_gen_deliv"]
    for c in res_cols:
        buses[c] = np.nan
    for c in ("bind_gen_line", "bind_gen_line_q", "bind_load_line"):
        buses[c] = -1
    buses["bind_gen_df_q"] = np.nan
    summary = {"ba_peaks_mw": ba_used.round(0).to_dict(), "interconnections": {}}

    for ic in ("Eastern", "Western", "ERCOT"):
        bm = buses.interconnection.eq(ic).values
        nid = buses.loc[bm, "node"].values
        pos = pd.Series(np.arange(len(nid)), index=nid)
        L = lines[lines.from_node.isin(nid) & lines.to_node.isin(nid)]
        fr, to = pos[L.from_node].values, pos[L.to_node].values
        b = 1.0 / L.x_pu.values
        N, M = len(nid), len(L)
        A = coo_matrix((np.r_[np.ones(M), -np.ones(M)], (np.r_[np.arange(M), np.arange(M)], np.r_[fr, to])),
                       shape=(M, N)).tocsc()
        B = (A.T @ coo_matrix((b, (np.arange(M), np.arange(M)))) @ A).tocsc()

        g_ic = gens[gens.ic.eq(ic)].reset_index(drop=True)
        gb = pos[g_ic.bus].values
        load_vec = buses.loc[bm, "load_peak_mw"].values
        bus_ba = buses.loc[bm, "ba"].values
        slack = int(np.argmax(np.bincount(gb, weights=g_ic.mw.values, minlength=N)))
        keep = np.r_[0:slack, slack + 1:N]
        lu = splu(B[keep][:, keep].tocsc())

        def solve_theta(P):
            th = np.zeros((N,) + P.shape[1:])
            th[keep] = lu.solve(np.ascontiguousarray(P[keep]) / 100.0)
            return th

        def flows(p_mw):
            th = solve_theta(p_mw)
            return (th[fr] - th[to]) * b * 100.0

        qic = queue[queue.node.isin(nid)]
        q_pos = pos[qic.node].values
        info = {"buses": int(N), "lines": int(M), "gen_gw": round(g_ic.mw.sum() / 1e3, 1),
                "peak_load_gw": round(load_vec.sum() / 1e3, 1)}
        base_f, q_f, S, sink_of_bus = {}, {}, {}, {}
        for scen, cfg in SCENARIOS.items():
            ld = load_vec * cfg["load_scale"]
            load_by_ba = pd.Series(ld).groupby(bus_ba).sum().to_dict()
            out, st = dispatch(g_ic, load_by_ba, scen)
            inj = np.bincount(gb, weights=out, minlength=N)
            if st["shortfall_mw"] > 0:      # imports from outside the model: scale load to generation
                ld = ld * inj.sum() / ld.sum()
            p = inj - ld
            p[slack] -= p.sum()
            base_f[scen] = flows(p)

            # sinks: pro-rata non-nuclear output of each BA; interconnection-wide fallback (last column)
            flex = out * (g_ic.tech.values != "Nuclear")
            flex_ba = pd.Series(flex).groupby(g_ic.ba.values).sum()
            sink_bas = [x for x, v in flex_ba.items() if v >= MIN_SINK_MW]
            col_of = {x: k for k, x in enumerate(sink_bas)}
            K = len(sink_bas) + 1
            gcol = np.array([col_of.get(x, K - 1) for x in g_ic.ba.values])
            W = np.zeros((N, K))
            np.add.at(W, (gb, gcol), flex)
            W[:, K - 1] = np.bincount(gb, weights=flex, minlength=N)
            W /= W.sum(axis=0, keepdims=True)
            TS = solve_theta(W)
            S[scen] = ((TS[fr] - TS[to]) * b[:, None] * 100.0).astype(np.float32)  # M x K, MW per MW
            sink_of_bus[scen] = np.array([col_of.get(x, K - 1) for x in bus_ba])
            flex_cap = np.r_[flex_ba.reindex(sink_bas).values, flex.sum()]

            # queue ahead, balanced by its BA's sink and capped at what that BA can displace
            q_mw = (qic.mw * qic.weight * qic.type.map(lambda t: queue_avail(t, scen))).values
            q_col = sink_of_bus[scen][q_pos]
            Qk = np.bincount(q_col, weights=q_mw, minlength=K)
            scale = np.minimum(1.0, flex_cap / np.maximum(Qk, 1e-9))
            q_inj = np.bincount(q_pos, weights=q_mw * scale[q_col], minlength=N)
            q_f[scen] = base_f[scen] + flows(q_inj - W @ (Qk * scale))
            lines.loc[L.index, f"flow_{scen}_mw"] = base_f[scen]
            lines.loc[L.index, f"flow_{scen}_q_mw"] = q_f[scen]
            buses.loc[bm, f"inj_{scen}_mw"] = p
            buses.loc[bm, f"qinj_{scen}_mw"] = q_inj
            info[scen] = {**{k: round(v, 3) for k, v in st.items()}, "load_gw": round(float(ld.sum()) / 1e3, 1),
                          "queue_injection_gw": round(float(q_inj.sum()) / 1e3, 1), "ba_sinks": len(sink_bas)}

        # calibrate ratings so the modeled existing system is feasible
        lim = L.limit_mw.values
        fmax = np.maximum(np.abs(base_f["peak"]), np.abs(base_f["light"]))
        lim_eff = np.maximum(lim, fmax / (CALIB_UTIL * ALPHA))
        lines.loc[L.index, "limit_eff_mw"] = lim_eff
        lines.loc[L.index, "calibrated"] = lim_eff > lim + 1e-6
        info["share_lines_calibrated"] = round(float((lim_eff > lim + 1e-6).mean()), 3)
        U = ALPHA * lim_eff

        out = {c: np.full(N, np.nan) for c in res_cols}
        bind = {k: np.full(N, -1) for k in ("gen", "gen_q", "load")}
        bind_df = np.full(N, np.nan)
        lid = L.index.values
        to_lid = lambda r: np.where(r >= 0, lid[np.maximum(r, 0)], -1)  # noqa: E731

        def margins(D, rows, f, sign, thr):
            """MW addable at each candidate bus (column) before a line with |DF| >= thr reaches U.
            sign=+1 injection, -1 withdrawal. Returns (MW, binding line row, signed DF)."""
            nb = D.shape[1]
            if not len(rows):
                return np.full(nb, np.inf), np.full(nb, -1), np.full(nb, np.nan)
            Ds = D[rows]
            a = np.abs(Ds)
            fs, Us = f[rows][:, None], U[rows][:, None]
            room = np.where(Ds * sign > 0, Us - fs, Us + fs)      # towards the limit the flow is pushed to
            with np.errstate(invalid="ignore", divide="ignore"):
                m = np.where(a >= thr, np.maximum(room, 0) / a, np.inf)
            k = np.argmin(m, axis=0)
            c = np.arange(nb)
            v = m[k, c]
            return v, np.where(np.isfinite(v), rows[k], -1), Ds[k, c] * sign

        for s0 in range(0, N, BATCH):
            cols = np.arange(s0, min(s0 + BATCH, N))
            E = np.zeros((N, len(cols)))
            E[cols, np.arange(len(cols))] = 1.0
            TH = solve_theta(E * 100.0)
            D0 = ((TH[fr] - TH[to]) * b[:, None]).astype(np.float32)        # injection vs slack
            Dp = D0 - S["peak"][:, sink_of_bus["peak"][cols]]                # vs own-BA sink
            Dl = D0 - S["light"][:, sink_of_bus["light"][cols]]
            del D0
            rows_p = np.nonzero((np.abs(Dp) >= DF_MIN).any(axis=1))[0]
            rows_l = np.nonzero((np.abs(Dl) >= DF_MIN).any(axis=1))[0]
            rows_d = np.nonzero((np.abs(Dp) >= DF_DELIV).any(axis=1))[0]

            for sfx, fp, fl in (("", base_f["peak"], base_f["light"]), ("_q", q_f["peak"], q_f["light"])):
                vp, rp, dp = margins(Dp, rows_p, fp, +1, DF_MIN)
                vl, rl, dl = margins(Dl, rows_l, fl, +1, DF_MIN)
                out[f"hr_gen_peak{sfx}"][cols] = vp
                out[f"hr_gen_light{sfx}"][cols] = vl
                worse_light = vl < vp
                bind["gen" + sfx][cols] = to_lid(np.where(worse_light, rl, rp))
                if sfx == "_q":
                    bind_df[cols] = np.where(worse_light, dl, dp)
            v, r, _ = margins(Dp, rows_p, base_f["peak"], -1, DF_MIN)
            out["hr_load_peak"][cols] = v
            bind["load"][cols] = to_lid(r)
            out["hr_load_peak_q"][cols] = margins(Dp, rows_p, q_f["peak"], -1, DF_MIN)[0]
            out["hr_gen_deliv"][cols] = margins(Dp, rows_d, base_f["peak"], +1, DF_DELIV)[0]
        for c in res_cols:
            buses.loc[bm, c] = np.minimum(out[c], HEADROOM_CAP)
        buses.loc[bm, "bind_gen_line"] = bind["gen"]
        buses.loc[bm, "bind_gen_line_q"] = bind["gen_q"]
        buses.loc[bm, "bind_gen_df_q"] = bind_df
        buses.loc[bm, "bind_load_line"] = bind["load"]
        summary["interconnections"][ic] = info
        log(f"{ic}: {N:,} buses, {M:,} lines, gen {info['gen_gw']} GW, peak load {info['peak_load_gw']} GW, "
            f"calibrated lines {info['share_lines_calibrated']:.1%}")
        log(f"   peak: {info['peak']}")
        log(f"   light: {info['light']}")

    # ---- substation outlet limit & no counterflow credit -----------------------------------------
    # A bus cannot export more than its connected lines carry with the largest one out (N-1), less
    # what it already exports. Queued projects' counterflow is not credited (they may withdraw).
    outlet = ALPHA * buses.branch_capacity_n1_mw
    net_q = {}
    for scen in ("peak", "light"):
        p_ = buses[f"inj_{scen}_mw"].fillna(0)
        q_ = buses[f"qinj_{scen}_mw"].fillna(0)
        net_q[scen] = buses[f"hr_gen_{scen}_q"].copy()
        buses[f"hr_gen_{scen}"] = np.minimum(buses[f"hr_gen_{scen}"], (outlet - p_.clip(lower=0)).clip(lower=0))
        buses[f"hr_gen_{scen}_q"] = np.minimum.reduce([buses[f"hr_gen_{scen}_q"], buses[f"hr_gen_{scen}"],
                                                       (outlet - (p_ + q_).clip(lower=0)).clip(lower=0)])
    outlet_binds = buses[["hr_gen_peak_q", "hr_gen_light_q"]].min(axis=1) < pd.concat(net_q, axis=1).min(axis=1) - 1e-6
    buses.loc[outlet_binds & buses.modeled, ["bind_gen_line_q", "bind_gen_df_q"]] = [-2, np.nan]
    buses["outlet_binding"] = outlet_binds & buses.modeled
    o_load = (outlet - (-buses.inj_peak_mw.fillna(0)).clip(lower=0)).clip(lower=0)
    load_outlet = buses.hr_load_peak > o_load
    buses["hr_load_peak"] = np.minimum(buses.hr_load_peak, o_load)
    buses["hr_load_peak_q"] = np.minimum(buses.hr_load_peak_q, buses.hr_load_peak)
    buses.loc[load_outlet & buses.modeled, "bind_load_line"] = -2
    buses["hr_gen_deliv"] = np.minimum(buses.hr_gen_deliv, buses.hr_gen_peak)

    # ---- derived bus metrics ------------------------------------------------------------------
    buses["headroom_gen_mw"] = buses[["hr_gen_peak", "hr_gen_light"]].min(axis=1)
    buses["headroom_gen_after_queue_mw"] = buses[["hr_gen_peak_q", "hr_gen_light_q"]].min(axis=1)
    buses["headroom_load_mw"] = buses.hr_load_peak
    buses["headroom_load_after_queue_mw"] = buses.hr_load_peak_q
    # islands / unmodeled: structural estimate only
    um = ~buses.modeled
    buses.loc[um, "headroom_gen_mw"] = (ALPHA * buses.branch_capacity_n1_mw - buses.gen_mw).clip(lower=0)[um]
    buses.loc[um, "headroom_gen_after_queue_mw"] = (buses.headroom_gen_mw - buses.queue_expected_mw).clip(lower=0)[um]
    buses["queue_to_capacity"] = buses.queue_mw / buses.branch_capacity_n1_mw.replace(0, np.nan)
    buses["queue_to_headroom"] = buses.queue_mw / buses.headroom_gen_mw.clip(lower=1)
    h = buses.headroom_gen_after_queue_mw
    buses["gen_signal"] = np.select([h >= 250, h >= 75], ["green", "yellow"], "red")
    buses.loc[buses.queue_to_capacity > 1.5, "gen_signal"] = "red"
    hl = buses.headroom_load_after_queue_mw
    buses["load_signal"] = np.select([hl >= 250, hl >= 75], ["green", "yellow"], "red")
    buses.loc[um, "load_signal"] = "unmodeled"
    # storage / reconductoring opportunity: congested for generation but renewables stack up behind
    ren_local = buses.gen_solar_mw + buses.gen_wind_mw + 0.3 * buses.queue_mw
    buses["storage_opportunity"] = (buses.headroom_gen_after_queue_mw < 100) & (ren_local >= 150) & buses.modeled
    buses["storage_score"] = (ren_local / buses.headroom_gen_after_queue_mw.clip(lower=25)).where(buses.modeled)

    # ---- line metrics ----------------------------------------------------------------------------
    lines["loading_peak_pct"] = 100 * lines.flow_peak_mw.abs() / (ALPHA * lines.limit_eff_mw)
    lines["loading_light_pct"] = 100 * lines.flow_light_mw.abs() / (ALPHA * lines.limit_eff_mw)
    lines["loading_q_pct"] = 100 * np.maximum(lines.flow_peak_q_mw.abs(), lines.flow_light_q_mw.abs()) / (
        ALPHA * lines.limit_eff_mw)
    lines["loading_max_pct"] = lines[["loading_peak_pct", "loading_light_pct"]].max(axis=1)
    lines["binding_buses"] = lines.index.map(buses[buses.modeled].bind_gen_line_q.value_counts()).fillna(0).astype(int)
    qbus = buses.groupby("bind_gen_line_q").queue_mw.sum()
    lines["queue_mw_behind"] = lines.index.map(qbus).fillna(0.0)

    # ---- queue projects enriched ----------------------------------------------------------------
    bcols = ["node", "name", "kv_levels", "headroom_gen_mw", "headroom_gen_after_queue_mw", "gen_signal",
             "queue_mw", "queue_to_capacity", "interconnection", "ba"]
    queue = queue.merge(buses[bcols].rename(columns={"name": "bus_label", "queue_mw": "bus_queue_mw",
                                                     "interconnection": "ic"}), on="node", how="left")

    PROCESSED.mkdir(exist_ok=True)
    buses.to_parquet(PROCESSED / "buses.parquet")
    lines.to_parquet(PROCESSED / "lines.parquet")
    queue.to_parquet(PROCESSED / "queue.parquet", index=False)
    plants.to_parquet(PROCESSED / "plants.parquet")
    summary["params"] = {"DF_MIN": DF_MIN, "DF_DELIV": DF_DELIV, "ALPHA": ALPHA, "CALIB_UTIL": CALIB_UTIL,
                         "PHASE_WEIGHT": PHASE_WEIGHT,
                         "SCENARIOS": {k: {"load_scale": v["load_scale"]} for k, v in SCENARIOS.items()}}
    m = buses[buses.modeled]
    summary["bus_signal_counts"] = m.gen_signal.value_counts().to_dict()
    summary["median_headroom_gen_mw"] = float(m.headroom_gen_mw.median())
    (PROCESSED / "model_summary.json").write_text(json.dumps(summary, indent=2, default=float))
    log("bus generation signal: " + str(m.gen_signal.value_counts().to_dict()))
    print(m[["headroom_gen_mw", "headroom_gen_after_queue_mw", "headroom_load_mw", "hr_gen_deliv"]].describe(
        percentiles=[.1, .25, .5, .75, .9]).round(0).to_string())


if __name__ == "__main__":
    main()
