"""Exploratory: which project attributes, known at application time, predict whether an
interconnection request reaches commercial operation? (LBNL Queued Up, all US queues 2000-2025)

Cohort: requests that entered a queue 2000-2019 and have since resolved (operational or withdrawn).
Outcome: built = reached commercial operation.

Features (all known when the request is filed, no look-ahead):
  region, technology, size, entry era, service type (NRIS/ERIS), existing site (upgrade/surplus/
  replacement), proposed lead time (proposed COD - request date), POI voltage,
  county crowding (other requests in the same county entered within +-1 year),
  sibling filings (same county + technology entered within 60 days: parallel/duplicate requests),
  POI crowding (other requests at the same named POI within +-2 years),
  county track record (built share of requests in the county entered >= 4 years earlier),
  developer experience (ERCOT and NYISO only; elsewhere LBNL does not record the developer).

Outputs data/processed/success_factors.json: built share per feature level (with n), a regularised
logistic regression (odds ratios), discrimination (AUC) in 5-fold CV and out-of-time
(train 2000-2014, test 2015-2019), and years to operation by technology / size / region.
Exploratory only: numbers that go into the product or pitch must be added to claims.py.
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, PROCESSED, log  # noqa: E402
from normalize_queues import read_lbnl_raw  # noqa: E402

COHORT = (2000, 2019)
L2 = 1.0
RNG = np.random.default_rng(11)

TECH = {"Solar": "Solar", "Wind": "Wind", "Battery": "Storage", "Other Storage": "Storage",
        "Solar+Battery": "Solar+Storage", "Gas": "Gas", "Gas+Oil": "Gas", "Gas+Battery": "Gas",
        "Offshore Wind": "Offshore wind", "Wind+Battery": "Wind", "Hydro": "Hydro/geo/nuclear",
        "Geothermal": "Hydro/geo/nuclear", "Nuclear": "Hydro/geo/nuclear", "Coal": "Coal/oil",
        "Oil": "Coal/oil", "Diesel": "Coal/oil"}


def norm_poi(s):
    s = str(s).lower()
    s = re.sub(r"\d+(\.\d+)?\s*kv", " ", s)
    s = re.sub(r"\b(substation|sub|switching station|switchyard|station|tap|line|kv)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def within_count(df, key, days):
    """Per row: number of other rows with the same key whose q_date is within +-days."""
    out = np.zeros(len(df), dtype=int)
    t = df.q_date.values.astype("datetime64[D]").astype(np.int64)
    for _, idx in df.groupby(key, sort=False).indices.items():
        if len(idx) < 2:
            continue
        tt = t[idx]
        o = np.argsort(tt)
        ts = tt[o]
        lo = np.searchsorted(ts, ts - days, "left")
        hi = np.searchsorted(ts, ts + days, "right")
        out[idx[o]] = hi - lo - 1
    return out


def features(lb):
    mw = lb[["mw_1", "mw_2", "mw_3"]].apply(pd.to_numeric, errors="coerce")
    d = pd.DataFrame({
        "region": lb.region.astype(str),
        "tech": lb.type_clean.map(TECH).fillna("Other/hybrid"),
        "mw": mw.sum(axis=1, min_count=1),
        "q_date": pd.to_datetime(lb.q_date, errors="coerce"),
        "q_year": pd.to_numeric(lb.q_year, errors="coerce"),
        "prop_date": pd.to_datetime(lb.prop_date, errors="coerce"),
        "on_date": pd.to_datetime(lb.on_date, errors="coerce"),
        "wd_date": pd.to_datetime(lb.wd_date, errors="coerce"),
        "status": lb.q_status.astype(str),
        "service": lb.service.fillna("unknown").replace({"Other": "unknown"}),
        "existing_site": lb.project_type.isin(["Surplus", "Upgrade", "Replacement"]),
        "county_key": lb.fips_code.astype("Int64").astype(str).where(lb.fips_code.notna(),
                                                                     lb.state.astype(str) + "|" + lb.county.astype(str)),
        "poi_key": lb.region.astype(str) + "|" + lb.poi_name.map(norm_poi),
        "developer": lb.developer.where(~lb.developer.isin(["Masked", "Third Party"])),
    })
    hist = pd.read_parquet(INTERIM / "lbnl_located.parquet")[["poi_kv"]]
    d["poi_kv"] = hist.poi_kv.values
    d = d[d.q_date.notna()].copy()

    d["size"] = pd.cut(d.mw, [0, 20, 50, 100, 200, 500, np.inf],
                       labels=["<20 MW", "20-50 MW", "50-100 MW", "100-200 MW", "200-500 MW", ">500 MW"])
    d["era"] = pd.cut(d.q_year, [1999, 2004, 2009, 2014, 2019, 2026],
                      labels=["2000-04", "2005-09", "2010-14", "2015-19", "2020-25"])
    lead = (d.prop_date - d.q_date).dt.days / 365.25
    d["lead_time"] = pd.cut(lead, [-np.inf, 2, 3, 5, np.inf], labels=["<2 yr", "2-3 yr", "3-5 yr", ">5 yr"]) \
        .cat.add_categories("unknown").fillna("unknown")
    d["voltage"] = pd.cut(d.poi_kv, [0, 100, 150, 250, 400, 2000],
                          labels=["<100 kV", "100-150 kV", "230 kV", "345 kV", "500+ kV"]) \
        .cat.add_categories("unknown").fillna("unknown")

    pk = d.poi_key.where(d.poi_key.str.len() > d.region.str.len() + 3)
    d["n_county_1y"] = within_count(d, "county_key", 365)
    d["n_siblings"] = within_count(d.assign(k=d.county_key + "|" + d.tech), "k", 60)
    d["n_poi_2y"] = np.where(pk.notna(), within_count(d.assign(k=pk.fillna("na")), "k", 730), -1)
    d["county_crowding"] = pd.cut(d.n_county_1y, [-1, 0, 2, 5, 10, np.inf],
                                  labels=["0 others", "1-2", "3-5", "6-10", ">10"])
    d["sibling_filings"] = pd.cut(d.n_siblings, [-1, 0, 2, np.inf], labels=["none", "1-2", "3+"])
    d["poi_crowding"] = pd.cut(d.n_poi_2y, [-2, -1, 0, 2, np.inf],
                               labels=["POI unnamed", "alone at POI", "1-2 others", "3+ others"])

    # county track record: built share of resolved requests entered >= 4 years earlier, shrunk to 0.2
    d["built"] = d.status.eq("operational")
    res = d[d.status.isin(["operational", "withdrawn"])]
    tr = np.full(len(d), np.nan)
    ntr = np.zeros(len(d))
    for ck, g in d.groupby("county_key", sort=False):
        r = res[res.county_key.eq(ck)]
        if r.empty:
            continue
        ry, rb = r.q_year.values, r.built.values
        for i, y in zip(g.index, g.q_year.values):
            m = ry <= y - 4
            n = m.sum()
            ntr[d.index.get_loc(i)] = n
            if n:
                tr[d.index.get_loc(i)] = (rb[m].sum() + 5 * 0.2) / (n + 5)
    d["county_track"] = pd.cut(pd.Series(tr, index=d.index), [0, 0.15, 0.25, 0.4, 1],
                               labels=["<15% built", "15-25%", "25-40%", ">40%"]) \
        .cat.add_categories("no history").fillna("no history")

    # developer experience (ERCOT, NYISO): requests the same developer filed before this one
    dev = d[d.region.isin(["ERCOT", "NYISO"]) & d.developer.notna()].sort_values("q_date")
    d["dev_prior"] = np.nan
    d.loc[dev.index, "dev_prior"] = dev.groupby(dev.developer.str.lower().str[:12]).cumcount().values
    d["developer_experience"] = pd.cut(d.dev_prior, [-1, 0, 4, 19, np.inf],
                                       labels=["first filing", "1-4 earlier", "5-19 earlier", "20+ earlier"])
    return d


def auc(y, s):
    r = pd.Series(s).rank().values
    n1 = y.sum()
    n0 = len(y) - n1
    return float((r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def logit_fit(X, y, l2=L2, iters=50):
    w = np.zeros(X.shape[1])
    pen = np.full(X.shape[1], l2)
    pen[0] = 0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (y - p) - pen * w
        H = (X * (p * (1 - p))[:, None]).T @ X + np.diag(pen)
        step = np.linalg.solve(H, g)
        w += step
        if np.abs(step).max() < 1e-7:
            break
    return w


def design(d, cols, ref):
    parts = [pd.Series(1.0, index=d.index, name="intercept")]
    for c in cols:
        dm = pd.get_dummies(d[c].astype(str), prefix=c, prefix_sep="=", dtype=float)
        drop = f"{c}={ref[c]}"
        parts.append(dm.drop(columns=[drop], errors="ignore"))
    return pd.concat(parts, axis=1)


def rates(d, col):
    g = d.groupby(col, observed=True).built.agg(["size", "mean"])
    return {str(k): {"n": int(v["size"]), "built": round(float(v["mean"]), 3)} for k, v in g.iterrows()}


def main():
    d = features(read_lbnl_raw())
    c = d[d.q_year.between(*COHORT) & d.status.isin(["operational", "withdrawn"]) & d.mw.notna()].copy()
    cohort_all = d[d.q_year.between(*COHORT)]
    log(f"cohort {COHORT}: {len(c):,} resolved of {len(cohort_all):,} requests "
        f"({cohort_all.status.isin(['active', 'suspended']).mean():.1%} still active/suspended); "
        f"built share {c.built.mean():.1%}")

    # lead_time is reported but kept out of the models: LBNL stores the latest proposed online date,
    # which for built projects is revised towards the real one (look-ahead leak)
    cols = ["region", "tech", "size", "era", "service", "existing_site", "voltage",
            "county_crowding", "sibling_filings", "poi_crowding", "county_track"]
    ref = {"region": "PJM", "tech": "Solar", "size": "50-100 MW", "era": "2010-14", "service": "NRIS",
           "existing_site": "False", "lead_time": "2-3 yr", "voltage": "100-150 kV", "county_crowding": "0 others",
           "sibling_filings": "none", "poi_crowding": "alone at POI", "county_track": "15-25%"}
    out = {"cohort": {"years": COHORT, "n_resolved": int(len(c)), "n_all": int(len(cohort_all)),
                      "built_share": round(float(c.built.mean()), 3),
                      "still_active_share": round(float(cohort_all.status.isin(["active", "suspended"]).mean()), 3)},
           "rates": {col: rates(c, col) for col in cols + ["lead_time", "developer_experience"]}}

    # within-region view of the strongest factors (to separate them from regional mix)
    out["rates_by_region"] = {col: {r: rates(g, col) for r, g in c.groupby("region")}
                              for col in ["tech", "size", "sibling_filings", "county_crowding"]}

    # ---- models: nested feature sets, 5-fold CV and out-of-time ---------------------------------------
    y = c.built.values
    sets = {"region only (current model)": ["region"],
            "+ technology, size, era": ["region", "tech", "size", "era"],
            "+ service, site, POI voltage": ["region", "tech", "size", "era", "service", "existing_site",
                                             "voltage"],
            "+ crowding, siblings, track record (all)": cols}
    folds = RNG.integers(0, 5, len(c))
    train_t = c.q_year.le(2014).values
    perf = {}
    for name, fs in sets.items():
        X = design(c, fs, ref).values
        s = np.zeros(len(c))
        for k in range(5):
            w = logit_fit(X[folds != k], y[folds != k])
            s[folds == k] = X[folds == k] @ w
        w = logit_fit(X[train_t], y[train_t])
        s_t = X[~train_t] @ w
        p_t = 1 / (1 + np.exp(-s_t))
        perf[name] = {"auc_cv": round(auc(y, s), 3), "auc_out_of_time": round(auc(y[~train_t], s_t), 3),
                      "test_mean_pred": round(float(p_t.mean()), 3), "test_actual": round(float(y[~train_t].mean()), 3)}
    out["discrimination"] = perf

    Xd = design(c, cols, ref)
    w = logit_fit(Xd.values, y)
    p = 1 / (1 + np.exp(-Xd.values @ w))
    se = np.sqrt(np.diag(np.linalg.inv((Xd.values * (p * (1 - p))[:, None]).T @ Xd.values + L2 * np.eye(len(w)))))
    out["odds_ratios"] = {k: {"or": round(float(np.exp(b)), 2), "lo": round(float(np.exp(b - 1.96 * s_)), 2),
                              "hi": round(float(np.exp(b + 1.96 * s_)), 2)}
                          for k, b, s_ in zip(Xd.columns, w, se) if k != "intercept"}
    out["reference_levels"] = ref
    q = pd.Series(p)
    out["predicted_spread"] = {"p10": round(float(q.quantile(.1)), 3), "p50": round(float(q.quantile(.5)), 3),
                               "p90": round(float(q.quantile(.9)), 3)}
    dec = pd.qcut(q, 10, labels=False, duplicates="drop")
    out["calibration_deciles"] = [{"pred": round(float(q[dec == k].mean()), 3), "actual": round(float(y[dec == k].mean()), 3)}
                                  for k in sorted(dec.unique())]

    # developer experience, ERCOT + NYISO only
    dv = c[c.developer_experience.notna()]
    if len(dv):
        dcols = ["region", "tech", "size", "era", "developer_experience"]
        dref = {**ref, "region": "ERCOT", "developer_experience": "first filing"}
        Xv = design(dv, dcols, dref)
        wv = logit_fit(Xv.values, dv.built.values)
        out["developer_experience_model"] = {"n": int(len(dv)), "odds_ratios": {
            k: round(float(np.exp(b)), 2) for k, b in zip(Xv.columns, wv) if k.startswith("developer_experience")}}

    # ---- time to operation (built projects) ---------------------------------------------------------------
    b = d[d.built & d.on_date.notna()].copy()
    b["yrs"] = (b.on_date - b.q_date).dt.days / 365.25
    b = b[b.yrs.between(0, 25) & b.on_date.dt.year.between(2018, 2025)]

    def q3(g):
        return {"n": int(len(g)), "p25": round(float(g.quantile(.25)), 1), "p50": round(float(g.quantile(.5)), 1),
                "p75": round(float(g.quantile(.75)), 1)}
    out["years_to_cod"] = {"online": "2018-2025", **{col: {str(k): q3(g.yrs) for k, g in b.groupby(col, observed=True)
                                                           if len(g) >= 15}
                                                     for col in ["tech", "size", "region", "era", "sibling_filings",
                                                                 "county_crowding"]}}
    # when do withdrawals happen (years after request), resolved cohort
    wd = c[~c.built & c.wd_date.notna()]
    wy = ((wd.wd_date - wd.q_date).dt.days / 365.25).where(lambda s: s.between(0, 25)).dropna()
    out["years_to_withdrawal"] = q3(wy)

    # ---- network-upgrade cost vs outcome (LBNL cost studies carry each studied request's status) --------
    cs = pd.read_parquet(INTERIM / "cost_projects.parquet")
    cs = cs[cs.status.isin(["Withdrawn", "Complete", "Operational"]) & cs.net_kw.notna() & (cs.net_kw >= 0)].copy()
    cs["built"] = cs.status.ne("Withdrawn").astype(bool).to_numpy()
    cs["cost_tier"] = pd.cut(cs.net_kw, [-1, 10, 50, 150, 400, np.inf],
                             labels=["<$10/kW", "$10-50/kW", "$50-150/kW", "$150-400/kW", ">$400/kW"])
    out["cost_vs_outcome"] = {"n": int(len(cs)), "all": rates(cs, "cost_tier"),
                              "by_region": {r: rates(g, "cost_tier") for r, g in cs.groupby("region")},
                              "median_net_kw": {k: round(float(v), 1) for k, v in
                                                cs.groupby(cs.built.map({True: "built", False: "withdrawn"})).net_kw.median().items()},
                              "auc_net_cost": round(auc(~cs.built.values, cs.net_kw.values), 3)}

    (PROCESSED / "success_factors.json").write_text(json.dumps(out, indent=1))
    log("saved data/processed/success_factors.json")
    print(pd.DataFrame(perf).T.to_string())
    for col in ["size", "tech", "sibling_filings"]:
        print(f"\n{col} by region:")
        print(pd.DataFrame({r: {k: f"{v['built']:.0%}" if v["n"] >= 40 else "" for k, v in lv.items()}
                            for r, lv in out["rates_by_region"][col].items()}).fillna("").to_string())
    for col in cols + ["lead_time", "developer_experience"]:
        print(f"\n{col}:", ", ".join(f"{k} {v['built']:.0%} (n={v['n']})" for k, v in out["rates"][col].items()))
    print("\nodds ratios:")
    for k, v in out["odds_ratios"].items():
        print(f"  {k:45s} {v['or']:5.2f}  [{v['lo']:.2f}-{v['hi']:.2f}]")
    print("\ncalibration:", out["calibration_deciles"])
    print("spread:", out["predicted_spread"])
    print("dev model:", out.get("developer_experience_model"))
    print("withdrawal years:", out["years_to_withdrawal"])
    for col in ["tech", "size", "region", "sibling_filings"]:
        print(f"years to COD by {col}:", {k: v["p50"] for k, v in out["years_to_cod"][col].items()})
    cv = out["cost_vs_outcome"]
    print(f"\nnetwork cost vs outcome (n={cv['n']}, AUC {cv['auc_net_cost']}, medians {cv['median_net_kw']}):")
    print("  all:", ", ".join(f"{k} {v['built']:.0%} (n={v['n']})" for k, v in cv["all"].items()))
    print(pd.DataFrame({r: {k: f"{v['built']:.0%} ({v['n']})" for k, v in lv.items()}
                        for r, lv in cv["by_region"].items()}).fillna("").to_string())


if __name__ == "__main__":
    main()
