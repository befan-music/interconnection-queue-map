"""Project risk score: the model behind the atlas's "Score my project" tool.

P(reach commercial operation) for one project, from what the developer tells us plus what the atlas
knows about the substation. Every term is a log-odds adjustment, so the page can add them up and
show each one as a driver.

1. Public attributes (validated): logistic regression on LBNL Queued Up requests filed 2000-2019.
   Resolved requests count as 1 (built) or 0 (withdrawn); the 8.5% still active count fractionally
   with their region x stage odds (queue_stats.json), so slow-but-alive projects are not dropped.
   Features: region, technology, size, POI voltage, parallel filings (other requests in the same
   county and technology within 60 days), county siting ordinance (NREL 2025; solar and wind only),
   entry era. Two fits: from application (all requests) and after an interconnection agreement (IA).
   Late study = midpoint of the two in log-odds; under construction = 90% (assumption, as in C17).
   Coefficients are centred on the training mix, so 0 = typical and each term reads as a driver.
2. Network-upgrade cost from the developer's study (validated): logistic regression of built vs
   withdrawn on cost tier + region, LBNL cost studies. Applied before an IA only.
3. Permits (validated): EIA-860M planned units, permit status in December 2021 vs operating by
   the current file. Odds ratios against "not started".
4. Local opposition and offtake (assumption): team priors until users' outcomes calibrate them.
5. Comparable projects: resolved LBNL requests filed 2005-2019 with location, for the comps list
   and the time-to-operation range (computed on the page).
Outputs: map/data/score.json, map/data/comps.json, data/processed/project_model.json (for claims.py)
"""
import json
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, MAP_DATA, PROCESSED, RAW, log  # noqa: E402
from normalize_queues import read_lbnl_raw  # noqa: E402
from success_factors import auc, design, features, logit_fit  # noqa: E402

COHORT = (2000, 2019)
NOW_ERA = "2015-19"                     # newest era with mostly resolved outcomes; applied to new projects
P_CONSTRUCTION = 0.90
STAGE_MAP = {"IA Executed": "IA executed", "Construction": "Construction", "Facility Study": "Late study",
             "IA Pending": "Late study", "System Impact Study": "Late study"}
FEATS = ["region", "tech", "size", "voltage", "siblings", "ordinance", "era"]
REF = {"region": "PJM", "tech": "Solar", "size": "50-100 MW", "voltage": "100-150 kV", "siblings": "none",
       "ordinance": "none", "era": "2010-14"}
COST_BINS = [-1, 10, 50, 150, 400, np.inf]
COST_LABELS = ["<$10/kW", "$10-50/kW", "$50-150/kW", "$150-400/kW", ">$400/kW"]
PERMIT_CODES = {"P": "Not started", "L": "Applied", "T": "Approved"}
# team assumptions (odds multipliers), shown as such on the page until outcome data calibrates them
PRIORS = {
    "opposition": {"None known": 1.0, "Some concerns": 0.75, "Organized / litigation": 0.45},
    "offtake": {"None": 1.0, "Negotiating": 1.3, "Signed": 2.0},
}


# ---- county siting ordinances (NREL) -------------------------------------------------------------
def ordinances():
    """{GEOID: category} per technology; 1 = ordinance on record, 2 = restrictive (ban/moratorium,
    project-size cap, or large setback from non-participating homes)."""
    out = {}
    for tech, min_ft, mult_ft in (("wind", 2000, 500), ("solar", 500, 15)):
        d = pd.read_excel(RAW / "ordinances" / f"{tech}_ordinances_2025.xlsx", sheet_name="County-Level", header=1)
        d["geoid"] = pd.to_numeric(d["County Subdivision FIPS Code"], errors="coerce")
        d = d[d.geoid.notna()]
        d["geoid"] = d.geoid.astype(int).astype(str).str.zfill(5)
        s = d[d.Feature.eq("Structures (Non-Participating)")]
        v = pd.to_numeric(s.Value, errors="coerce")
        u = s.Units.astype(str)
        ft = np.select([u.eq("feet"), u.eq("meters"), u.eq("miles"), u.str.contains("multiplier")],
                       [v, v * 3.28, v * 5280, v * mult_ft], np.nan)
        restrictive = set(s.geoid[ft >= min_ft]) | set(d.geoid[d.Feature.isin(["Prohibitions", "Maximum Project Size"])])
        out[tech] = {g: (2 if g in restrictive else 1) for g in d.geoid.unique()}
    return out


def ordinance_level(tech, geoid, ords):
    k = "wind" if tech == "Wind" else "solar" if tech in ("Solar", "Solar+Storage") else None
    if k is None or not isinstance(geoid, str):
        return "none"
    return ["none", "ordinance", "restrictive"][ords[k].get(geoid, 0)]


# ---- fitting helpers -----------------------------------------------------------------------------
def fit_centered(d, y, feats, ref):
    """Logistic fit; returns intercept and per-level coefficients centred on the training mix."""
    X = design(d, feats, ref)
    w = logit_fit(X.values, y)
    beta = dict(zip(X.columns, w))
    a = beta.pop("intercept")
    coef = {}
    for f in feats:
        levels = d[f].astype(str)
        b = {lv: float(beta.get(f"{f}={lv}", 0.0)) for lv in levels.unique()}
        center = float(levels.map(b).mean())
        coef[f] = {lv: round(v - center, 4) for lv, v in b.items()}
        a += center
    return float(a), coef, X, w


def main():
    lb = read_lbnl_raw()
    d = features(lb)
    d["siblings"] = d.sibling_filings.astype(str)
    ords = ordinances()
    geoid = pd.to_numeric(lb.loc[d.index, "fips_code"], errors="coerce")
    d["geoid"] = geoid.map(lambda x: f"{int(x):05d}" if pd.notna(x) else None)
    d["ordinance"] = [ordinance_level(t, g, ords) for t, g in zip(d.tech, d.geoid)]
    d["stage"] = lb.loc[d.index, "IA_phase_clean"].map(STAGE_MAP).fillna("Early study")
    has_ia = (pd.to_datetime(lb.loc[d.index, "ia_date"], errors="coerce").notna()
              | lb.loc[d.index, "IA_phase_clean"].eq("IA Executed"))
    d["has_ia"] = has_ia.values

    stats = json.loads((PROCESSED / "queue_stats.json").read_text())["completion"]
    c = d[d.q_year.between(*COHORT) & d.status.isin(["operational", "withdrawn", "active", "suspended"])
          & (d.mw > 0)].copy()
    odds = [(stats.get(r) or stats["national"])[s] for r, s in zip(c.region, c.stage)]
    c["y"] = np.where(c.status.eq("operational"), 1.0, np.where(c.status.eq("withdrawn"), 0.0, odds))
    log(f"cohort {COHORT}: {len(c):,} requests ({c.status.isin(['active', 'suspended']).sum():,} active, counted "
        f"fractionally); mean outcome {c.y.mean():.3f}")

    # 1a. from application
    a0, coef0, X0, w0 = fit_centered(c, c.y.values, FEATS, REF)
    # 1b. after an interconnection agreement (resolved requests that signed one, plus active IA-executed)
    ci = c[c.has_ia | c.stage.eq("IA executed")]
    a1, coef1, _, _ = fit_centered(ci, ci.y.values, FEATS, REF)
    log(f"after-IA fit on {len(ci):,} requests; mean outcome {ci.y.mean():.3f}")

    # validation on resolved requests only: 5-fold CV and out-of-time (train <= 2014, test 2015-19)
    res = c.status.isin(["operational", "withdrawn"]).values
    yb = c.status.eq("operational").values
    Xv = X0.values
    folds = np.random.default_rng(3).integers(0, 5, len(c))
    s_cv = np.zeros(len(c))
    for k in range(5):
        w = logit_fit(Xv[folds != k], c.y.values[folds != k])
        s_cv[folds == k] = Xv[folds == k] @ w
    tr = c.q_year.le(2014).values
    w_t = logit_fit(Xv[tr], c.y.values[tr])
    te = ~tr & res
    p_te = 1 / (1 + np.exp(-(Xv[te] @ w_t)))
    p_all = 1 / (1 + np.exp(-(Xv @ w0)))
    dec = pd.qcut(pd.Series(p_all[res]), 10, labels=False, duplicates="drop").values
    validation = {
        "n": int(len(c)), "n_resolved": int(res.sum()), "n_active_fractional": int((~res).sum()),
        "auc_cv": round(auc(yb[res], s_cv[res]), 3), "auc_out_of_time": round(auc(yb[te], Xv[te] @ w_t), 3),
        "out_of_time_pred": round(float(p_te.mean()), 3), "out_of_time_actual": round(float(yb[te].mean()), 3),
        "decile_low": round(float(yb[res][dec == 0].mean()), 3), "decile_high": round(float(yb[res][dec == dec.max()].mean()), 3),
        "auc_region_only": round(auc(yb[res], c.region.map(c[res].groupby("region").y.mean()).values[res]), 3),
    }
    log(f"validation: {validation}")

    # 2. network cost from the developer's study
    cs = pd.read_parquet(INTERIM / "cost_projects.parquet")
    cs = cs[cs.status.isin(["Withdrawn", "Complete", "Operational"]) & cs.net_kw.notna() & (cs.net_kw >= 0)].copy()
    cs["tier"] = pd.cut(cs.net_kw, COST_BINS, labels=COST_LABELS).astype(str)
    cs["region"] = cs.region.astype(str)
    yc = cs.status.ne("Withdrawn").astype(float).to_numpy()
    Xc = design(cs, ["region", "tier"], {"region": "PJM", "tier": "<$10/kW"})
    wc = dict(zip(Xc.columns, logit_fit(Xc.values, yc)))
    tb = {t: wc.get(f"tier={t}", 0.0) for t in COST_LABELS}
    center = float(cs.tier.map(tb).mean())
    cost = {"bins": COST_BINS[1:-1], "labels": COST_LABELS, "beta": [round(tb[t] - center, 4) for t in COST_LABELS],
            "n": int(len(cs)), "built_share": [round(float(yc[cs.tier.eq(t).values].mean()), 3) for t in COST_LABELS],
            "n_tier": [int(cs.tier.eq(t).sum()) for t in COST_LABELS]}
    log(f"cost tiers (centred log-odds): {dict(zip(COST_LABELS, cost['beta']))}")

    # 3. permits: EIA-860M planned units, December 2021 -> current file
    def eia(path, sheet):
        x = pd.read_excel(path, sheet_name=sheet, header=2)
        x = x[pd.to_numeric(x["Plant ID"], errors="coerce").notna()]
        x["k"] = x["Plant ID"].astype(int).astype(str) + "|" + x["Generator ID"].astype(str).str.strip()
        return x
    cur = RAW / "eia860m" / sorted(p.name for p in (RAW / "eia860m").glob("*.xlsx"))[-1]
    old = eia(RAW / "eia860m_archive" / "december_generator2021.xlsx", "Planned")
    old = old[pd.to_numeric(old["Nameplate Capacity (MW)"], errors="coerce") >= 5]
    built_keys = set(eia(cur, "Operating").k) | set(eia(cur, "Retired").k)
    old["code"] = old.Status.astype(str).str.extract(r"\((\w+)\)")[0]
    old["built"] = old.k.isin(built_keys)
    pm = {}
    for code, label in PERMIT_CODES.items():
        g = old[old.code.eq(code)]
        pm[label] = {"n": int(len(g)), "built": round(float(g.built.mean()), 3)}
    lo = lambda p: np.log(p / (1 - p))  # noqa: E731
    base = lo(pm["Not started"]["built"])
    permits = {k: {**v, "beta": round(float(lo(v["built"]) - base), 4)} for k, v in pm.items()}
    log(f"permits: {permits}")

    # 4. regional reference: mean outcome of the 2015-19 cohort by region (shown as 'typical')
    recent = c[c.era.astype(str).eq(NOW_ERA)]
    typical = recent.groupby("region").y.mean().round(3).to_dict()

    # 5. comparable projects
    h = pd.read_parquet(INTERIM / "lbnl_located.parquet")[["lon", "lat"]]
    cp = d[d.q_year.between(2005, 2019) & d.status.isin(["operational", "withdrawn"]) & d.mw.notna()].copy()
    cp = cp.join(h)
    ia_d = pd.to_datetime(lb.loc[cp.index, "ia_date"], errors="coerce")
    cp["yrs"] = ((cp.on_date - cp.q_date).dt.days / 365.25).where(lambda s: s.between(0, 25))
    cp["iac"] = ((cp.on_date - ia_d).dt.days / 365.25).where(lambda s: s.between(0, 15))
    regions = sorted(c.region.unique())
    techs = sorted(c.tech.unique())
    rnd = lambda s, k: [None if pd.isna(v) else round(float(v), k) for v in s]  # noqa: E731
    txt = lambda s: [None if pd.isna(v) or not str(v).strip() else str(v).strip()[:48] for v in s]  # noqa: E731
    comps = {"regions": regions, "techs": techs,
             "rg": [regions.index(r) for r in cp.region], "t": [techs.index(t) for t in cp.tech],
             "mw": rnd(cp.mw, 0), "y": cp.q_year.astype(int).tolist(), "b": cp.built.astype(int).tolist(),
             "yr": rnd(cp.yrs, 1), "iac": rnd(cp.iac, 1), "lat": rnd(cp.lat, 2), "lon": rnd(cp.lon, 2),
             "st": txt(lb.loc[cp.index, "state"]), "poi": txt(lb.loc[cp.index, "poi_name"]),
             "name": txt(lb.loc[cp.index, "project_name"])}

    # county ordinance lookup for the page, keyed like buses.json ("ST|County")
    cty = gpd.read_file(next((RAW / "census").glob("cb_*_us_county_500k.zip")), ignore_geometry=True)
    key = dict(zip(cty.GEOID, cty.STUSPS + "|" + cty.NAME))
    ord_out = {}
    for tech in ("solar", "wind"):
        for g, v in ords[tech].items():
            if g in key:
                ord_out.setdefault(key[g], [0, 0])[0 if tech == "solar" else 1] = v

    risk_meta = json.loads((PROCESSED / "risk_meta.json").read_text())
    score = {
        "now_era": NOW_ERA, "p_construction": P_CONSTRUCTION,
        "m0": {"a": round(a0, 4), "coef": coef0}, "m_ia": {"a": round(a1, 4), "coef": coef1},
        "size_bins": [20, 50, 100, 200, 500], "size_labels": ["<20 MW", "20-50 MW", "50-100 MW", "100-200 MW",
                                                              "200-500 MW", ">500 MW"],
        "kv_bins": [100, 150, 250, 400], "kv_labels": ["<100 kV", "100-150 kV", "230 kV", "345 kV", "500+ kV"],
        "cost": cost, "permits": permits, "priors": PRIORS, "typical": typical, "validation": validation,
        "ordinance": ord_out, "cost_time_mult": risk_meta["tier_multiplier"],
        "cost_time_bins": [10, 50, 150],
    }
    (MAP_DATA / "score.json").write_text(json.dumps(score, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    (MAP_DATA / "comps.json").write_text(json.dumps(comps, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    (PROCESSED / "project_model.json").write_text(json.dumps(
        {**{k: score[k] for k in ("m0", "m_ia", "cost", "permits", "priors", "typical", "validation")},
         "n_after_ia": int(len(ci)), "n_comps": int(len(cp)),
         "n_ordinance_counties": {k: len(v) for k, v in ords.items()},
         "n_restrictive_counties": {k: sum(1 for x in v.values() if x == 2) for k, v in ords.items()}}, indent=1))
    log(f"saved score.json ({(MAP_DATA / 'score.json').stat().st_size / 1e3:.0f} kB), comps.json "
        f"({(MAP_DATA / 'comps.json').stat().st_size / 1e6:.2f} MB, {len(cp):,} projects)")
    for f in FEATS:
        print(f"{f:10s}", {k: v for k, v in sorted(coef0[f].items(), key=lambda kv: kv[1])})


if __name__ == "__main__":
    main()
