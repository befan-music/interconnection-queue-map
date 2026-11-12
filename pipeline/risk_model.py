"""Interconnection risk per bus and project size: expected cost, time to operation, the queue ahead,
and the probability that capacity remains once it resolves.

Cost ($2024/kW) = point-of-interconnection (POI) work + network upgrades
  - POI: MISO MTEP24 exploratory cost of adding one breaker-and-a-half position at the bus voltage.
  - Network: regional median network cost from LBNL cost studies since 2018
      x f(size / modelled headroom)   empirical multiplier: how much more studies assigned when the
                                       project was large relative to our modelled headroom
      x g(voltage class)              empirical multiplier by bus voltage
      x local index                   what studies within ~80 km assigned relative to that
                                       expectation, shrunk towards 1 when few studies are nearby
                                       and bounded to 0.3-3x.
  - ERCOT: network upgrades are funded by transmission utilities (PUCT rule 25.195), so only POI.
Time (years from request to operation)
  = regional median for projects online 2018-2025 x multiplier for the expected network-cost tier
    (empirical: complete MISO/PJM/SPP projects with bigger upgrades took longer).
Queue ahead: each active request at the bus succeeds with its empirical regional/stage odds;
  10,000-draw simulation gives the MW that materialises (P10/P50/P90).
Capacity fit: P(after-queue headroom, with simulated local queue outcome and a log-normal model
  error of sigma = MODEL_SIGMA, is at least the project size).
Risk index (0 = best, 100 = worst) = 100 - weighted score of cost, time and capacity fit.
Output: data/processed/risk.parquet, data/processed/risk_meta.json
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from scipy.special import erf as erf_vec

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, PROCESSED, log  # noqa: E402

SIZES = [50, 100, 200, 300, 500, 1000]           # MW
RECENT = 2018                                     # cost studies from this year on
KERNEL_KM, RADIUS_KM, SHRINK_N = 40, 80, 5
LOCAL_CLIP = (0.3, 3.0)                           # bounds on the local cost index
MODEL_SIGMA = 0.5                                 # log-normal error on modelled headroom (assumption)
N_DRAWS = 10000
WEIGHTS = {"cost": 0.35, "time": 0.30, "fit": 0.35}
COST_ANCHOR = (25, 400)                           # $/kW scoring 100 .. 0
TIME_ANCHOR = (2.5, 8.0)                          # years scoring 100 .. 0
R_BINS = [0, 0.5, 1, 2, 4, np.inf]
R_LABELS = ["<0.5", "0.5-1", "1-2", "2-4", ">4"]
KV_BINS = [0, 150, 250, 400, 2000]
KV_LABELS = ["<=150 kV", "230 kV", "345 kV", "500+ kV"]
COST_TIERS = [-1, 10, 50, 150, np.inf]
COST_TIER_LABELS = ["<$10/kW", "$10-50/kW", "$50-150/kW", ">$150/kW"]
RNG = np.random.default_rng(7)

# MISO Transmission Cost Estimation Guide for MTEP24 (2024 $, incl. 30% contingency + AFUDC)
MISO_KV = [69, 115, 138, 161, 230, 345, 500, 765]
MISO_ADD_POSITION_BAAH = [1.7, 2.0, 2.3, 2.6, 3.0, 4.9, 7.3, 21.9]       # $M, Table 4.2-1
MISO_RECONDUCTOR_MI = [0.33, 0.39, 0.39, 0.39, 0.39, 0.62, 0.83, 1.14]  # $M/mile, Table 4.1-3
MISO_NEW_LINE_MI = [1.8, 2.0, 2.05, 2.1, 2.25, 3.6, 4.5, 5.6]           # $M/mile, Table 4.1-1 (median of states)


def miso(kv, table):
    return table[int(np.argmin(np.abs(np.log(np.asarray(MISO_KV) / max(kv, 60)))))]


def norm_cdf(x):
    return 0.5 * (1 + erf_vec(np.asarray(x) / np.sqrt(2)))


def wmedian(v, w):
    o = np.argsort(v)
    cw = np.cumsum(w[o])
    return v[o][np.searchsorted(cw, cw[-1] / 2)]


def main():
    buses = gpd.read_parquet(PROCESSED / "buses.parquet")
    queue = pd.read_parquet(PROCESSED / "queue.parquet")
    stats = json.loads((PROCESSED / "queue_stats.json").read_text())
    costs = pd.read_parquet(INTERIM / "cost_projects.parquet")
    hist = pd.read_parquet(INTERIM / "lbnl_located.parquet")
    b = buses[buses.modeled].copy()
    bidx = b.set_index("node")

    # ---- bus region (ISO or non-ISO area), from its queue and history, else nearest neighbour --------
    reg = pd.concat([queue[queue.node >= 0][["node", "iso"]].rename(columns={"iso": "region"}),
                     hist[hist.node >= 0][["node", "region"]]])
    b["region"] = b.node.map(reg.groupby("node").region.agg(lambda s: s.mode().iat[0]))
    known = b.region.notna().values
    _, nn = cKDTree(b.loc[known, ["x", "y"]].values).query(b.loc[~known, ["x", "y"]].values)
    b.loc[~known, "region"] = b.loc[known, "region"].values[nn]

    # ---- empirical cost calibration ------------------------------------------------------------------
    c = costs[(costs.node >= 0) & (costs.study_year >= RECENT)].merge(
        bidx[["headroom_gen_mw", "kv_max", "x", "y"]], left_on="node", right_index=True, how="inner")
    c = c[c.net_kw.notna() & (c.net_kw >= 0)]
    reg_med = c.groupby("region").net_kw.median()
    nat_med = float(c.net_kw.median())
    c["rel"] = c.net_kw / c.region.map(reg_med).clip(lower=1)
    c["rbin"] = pd.cut(c.mw / c.headroom_gen_mw.clip(lower=5), R_BINS, labels=R_LABELS)
    c["kvbin"] = pd.cut(c.kv_max, KV_BINS, labels=KV_LABELS)
    f_r = c.groupby("rbin", observed=True).rel.median()
    g_kv = (c.rel / c.rbin.map(f_r).astype(float)).groupby(c.kvbin, observed=False).median().fillna(1.0)
    g_kv = g_kv / float(np.median(g_kv.values))
    c["expect"] = c.rbin.map(f_r).astype(float) * c.kvbin.map(g_kv).astype(float)
    c["resid"] = c.rel / c.expect.clip(lower=0.05)
    log(f"cost calibration on {len(c):,} studies since {RECENT}: f(size/headroom) {f_r.round(2).to_dict()}, "
        f"g(kV) {g_kv.round(2).to_dict()}")

    # local index: weighted median of residuals within RADIUS_KM, shrunk towards 1
    tree = cKDTree(c[["x", "y"]].values)
    lres, logres = c.resid.values, np.log(c.resid.clip(lower=0.02).values)
    local_idx, n_eff = np.ones(len(b)), np.zeros(len(b))
    for i, nb_ in enumerate(tree.query_ball_point(b[["x", "y"]].values, RADIUS_KM * 1e3)):
        if not nb_:
            continue
        nb_ = np.asarray(nb_)
        d = np.hypot(c.x.values[nb_] - b.x.values[i], c.y.values[nb_] - b.y.values[i]) / 1e3
        w = np.exp(-0.5 * (d / KERNEL_KM) ** 2)
        n_eff[i] = w.sum()
        lm = float(np.exp(wmedian(logres[nb_], w)))
        local_idx[i] = np.clip((n_eff[i] * lm + SHRINK_N * 1.0) / (n_eff[i] + SHRINK_N), *LOCAL_CLIP)
    b["cost_local_index"], b["cost_n_local"] = local_idx, n_eff
    spread = (float(np.quantile(lres, .25)), float(np.quantile(lres, .75)))

    # ---- time calibration: years to operation by network-cost tier ----------------------------------------
    t = costs[costs.cod_date.notna() & costs.q_date.notna() & (costs.q_date.dt.year >= 2010)].copy()
    t["yrs"] = (t.cod_date - t.q_date).dt.days / 365.25
    t = t[t.yrs.between(0.3, 20)]
    tier_med = t.groupby(pd.cut(t.net_kw, COST_TIERS, labels=COST_TIER_LABELS), observed=False).yrs.median()
    tier_mult = (tier_med / float(t.yrs.median())).fillna(1.0)

    def dur(region):
        d = stats["durations"].get(region) or {}
        return d.get("to_cod") or stats["durations"]["national"]["to_cod"]

    # ---- queue ahead: simulate which active requests at each bus get built ---------------------------
    qa = queue[queue.node.isin(b.node)]
    qstats = {}
    for node, g in qa.groupby("node"):
        p, mw = g.weight.values, g.mw.values
        qstats[node] = {"n": len(g), "mw": float(mw.sum()), "exp": float((p * mw).sum()), "p": p, "w": mw,
                        "p_any_wd": float(1 - np.prod(p)), "exp_wd_n": float((1 - p).sum()), "county_share": float(
                            (g.match_method.eq("county proximity") * mw).sum() / max(mw.sum(), 1))}

    # ---- local track record from the located LBNL history ---------------------------------------------
    hr = hist[(hist.node >= 0) & hist.q_status.isin(["operational", "withdrawn"])]
    trk = hr.groupby("node").q_status.value_counts().unstack(fill_value=0)
    comp = stats["completion"]

    # ---- per bus x size (vectorised over buses) -----------------------------------------------------------
    nb = len(b)
    poi_musd = np.array([miso(kv, MISO_ADD_POSITION_BAAH) for kv in b.kv_max])
    reg_net = b.region.map(reg_med).fillna(nat_med).values
    ercot = b.region.eq("ERCOT").values
    d50, d25, d75 = (b.region.map(lambda g: dur(g)[k]).values for k in ("p50", "p25", "p75"))
    g_arr = np.asarray(g_kv.reindex(KV_LABELS).fillna(1.0))[np.digitize(b.kv_max, KV_BINS[1:-1])]
    f_arr = np.asarray(f_r.reindex(R_LABELS).fillna(1.0))
    m_arr = np.asarray(tier_mult.reindex(COST_TIER_LABELS).fillna(1.0))
    hg = b.headroom_gen_mw.clip(lower=5).values
    base_left = b.headroom_gen_after_queue_mw.clip(lower=1).values
    # capacity fit: buses without queued projects have a closed form (log-normal model error);
    # buses with queued projects add a simulation of which requests get built
    fit = {P: 1 - norm_cdf(np.log(P / base_left) / MODEL_SIGMA) for P in SIZES}
    ln_err = RNG.normal(0, MODEL_SIGMA, N_DRAWS)
    pos = pd.Series(np.arange(nb), index=b.node.values)
    for node, qs in qstats.items():
        i = pos[node]
        draws = (RNG.random((N_DRAWS, len(qs["p"]))) < qs["p"]) @ qs["w"]
        qs.update(p10=float(np.quantile(draws, .1)), p50=float(np.quantile(draws, .5)),
                  p90=float(np.quantile(draws, .9)))
        left = base_left[i] * np.exp(ln_err) + qs["exp"] - draws
        for P in SIZES:
            fit[P][i] = float((left >= P).mean())
    frames = []
    for P in SIZES:
        f = f_arr[np.digitize(P / hg, R_BINS[1:-1])]
        net = np.where(ercot, 0.0, reg_net * f * g_arr * b.cost_local_index.values)
        poi = poi_musd * 1e6 / (P * 1e3)
        m = m_arr[np.digitize(net, COST_TIERS[1:-1])]
        frames.append(pd.DataFrame({
            "node": b.node.values, "size_mw": P, "cost_kw": (poi + net).round(1), "poi_kw": poi.round(1),
            "net_kw": net.round(1), "cost_kw_lo": (poi + net * spread[0]).round(1),
            "cost_kw_hi": (poi + net * spread[1]).round(1), "years": (d50 * m).round(2),
            "years_lo": (d25 * m).round(2), "years_hi": (d75 * m).round(2), "p_fit": fit[P].round(3)}))
    rk = pd.concat(frames, ignore_index=True)

    def score(x, lo, hi):
        return np.clip((hi - x) / (hi - lo), 0, 1)
    rk["score"] = (WEIGHTS["cost"] * score(rk.cost_kw, *COST_ANCHOR) + WEIGHTS["time"] * score(rk.years, *TIME_ANCHOR)
                   + WEIGHTS["fit"] * rk.p_fit)
    rk["risk"] = (100 * (1 - rk.score)).round(1)

    # ---- per bus attributes ------------------------------------------------------------------------------
    per_bus = b[["node", "region", "cost_local_index", "cost_n_local"]].copy()
    for k in ("n", "mw", "exp", "p10", "p50", "p90", "p_any_wd", "exp_wd_n", "county_share"):
        per_bus[f"q_{k}"] = per_bus.node.map({n_: v[k] for n_, v in qstats.items()})
    per_bus["hist_built"] = per_bus.node.map(trk.get("operational")).fillna(0).astype(int)
    per_bus["hist_withdrawn"] = per_bus.node.map(trk.get("withdrawn")).fillna(0).astype(int)
    n_res = per_bus.hist_built + per_bus.hist_withdrawn
    prior = per_bus.region.map(lambda g: (comp.get(g) or comp["national"])["Early study"])
    per_bus["hist_rate"] = (per_bus.hist_built + 10 * prior) / (n_res + 10)
    conf = np.full(len(per_bus), 2)
    conf -= (per_bus.cost_n_local.values < 2) | per_bus.region.isin(["CAISO"]).values
    conf -= per_bus.q_county_share.fillna(0).values >= 0.5
    per_bus["confidence"] = np.clip(conf, 0, 2)
    rk = rk.merge(per_bus, on="node", how="left")
    rk.to_parquet(PROCESSED / "risk.parquet", index=False)

    meta = {
        "sizes": SIZES, "weights": WEIGHTS, "cost_anchor": COST_ANCHOR, "time_anchor": TIME_ANCHOR,
        "model_sigma": MODEL_SIGMA, "recent_from": RECENT, "radius_km": RADIUS_KM, "kernel_km": KERNEL_KM,
        "shrink_n": SHRINK_N, "local_clip": LOCAL_CLIP, "n_cost_studies": int(len(c)),
        "region_net_median_kw": reg_med.round(1).to_dict(), "national_net_median_kw": round(nat_med, 1),
        "f_size_ratio": f_r.round(3).to_dict(), "g_kv": g_kv.round(3).to_dict(),
        "residual_iqr": [round(spread[0], 3), round(spread[1], 3)],
        "tier_years_median": tier_med.round(2).to_dict(), "tier_multiplier": tier_mult.round(3).to_dict(),
        "durations": stats["durations"], "completion": comp,
        "miso": {"kv": MISO_KV, "add_position_baah_musd": MISO_ADD_POSITION_BAAH,
                 "reconductor_musd_per_mile": MISO_RECONDUCTOR_MI, "new_line_musd_per_mile": MISO_NEW_LINE_MI},
    }
    (PROCESSED / "risk_meta.json").write_text(json.dumps(meta, indent=1, default=float))
    d = rk[rk.size_mw == 200]
    log(f"risk computed for {len(b):,} buses x {len(SIZES)} sizes")
    print(d[["cost_kw", "years", "p_fit", "risk"]].describe(percentiles=[.1, .25, .5, .75, .9]).round(2).to_string())
    print(d.groupby("region")[["cost_kw", "years", "p_fit", "risk"]].median().round(2).to_string())


if __name__ == "__main__":
    main()
