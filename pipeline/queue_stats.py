"""Empirical queue statistics from the LBNL Queued Up history (2000-2025).

- Completion odds by region and study stage, from requests that entered queues in 2000-2019 and
  have since been resolved (built or withdrawn). LBNL records whether a request ever signed an
  interconnection agreement (IA), but not the stage at which a withdrawn request left, so:
      p(IA executed)  = built / resolved requests that signed an IA
      p(early study)  = share of resolved requests that reached an IA x p(IA executed)
      p(late study)   = midpoint of the two (assumption: no stage history for withdrawals)
      p(construction) = 0.90 (assumption)
  Small regional samples are shrunk towards the national figure (prior weight PRIOR_N requests).
- Durations: request -> signed IA (IAs signed 2020-2025) and request -> commercial operation
  (projects that came online 2018-2025), quartiles by region.
Output: data/processed/queue_stats.json (read by build_model.py and risk_model.py)
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROCESSED, log  # noqa: E402
from normalize_queues import read_lbnl_raw  # noqa: E402

PRIOR_N = 50
P_CONSTRUCTION = 0.90
MIN_DURATION_N = 20


def shrink(x, n, p0):
    return (x + PRIOR_N * p0) / (n + PRIOR_N)


def quartiles(s):
    s = s.dropna()
    return {"n": int(len(s)), "p25": round(float(s.quantile(.25)), 2), "p50": round(float(s.quantile(.5)), 2),
            "p75": round(float(s.quantile(.75)), 2)} if len(s) else None


def main():
    lb = read_lbnl_raw()
    for c in ("q_date", "ia_date", "on_date"):
        lb[c] = pd.to_datetime(lb[c], errors="coerce")
    lb["has_ia"] = lb.ia_date.notna() | lb.IA_phase_clean.eq("IA Executed")
    lb["region"] = lb.region.astype(str)

    c = lb[pd.to_numeric(lb.q_year, errors="coerce").between(2000, 2019) & lb.q_status.isin(["operational", "withdrawn"])]
    c = c.assign(built=c.q_status.eq("operational"))

    def stage_odds(g):
        n_res, n_ia = len(g), int(g.has_ia.sum())
        built_ia = int((g.built & g.has_ia).sum())
        return n_res, n_ia, built_ia

    N, NI, NB = stage_odds(c)
    nat = {"p_ia": NB / NI, "p_reach_ia": NI / N}
    regions = {}
    for r, g in c.groupby("region"):
        n_res, n_ia, built_ia = stage_odds(g)
        p_ia = shrink(built_ia, n_ia, nat["p_ia"])
        p_reach = shrink(n_ia, n_res, nat["p_reach_ia"])
        p_early = p_reach * p_ia
        regions[r] = {"n_resolved": n_res, "n_ia": n_ia, "built_after_ia": built_ia,
                      "p_reach_ia": round(p_reach, 3), "Early study": round(p_early, 3),
                      "Late study": round((p_early + p_ia) / 2, 3), "IA executed": round(p_ia, 3),
                      "Construction": P_CONSTRUCTION}
    p_early_nat = nat["p_reach_ia"] * nat["p_ia"]
    national = {"n_resolved": N, "n_ia": NI, "built_after_ia": NB, "p_reach_ia": round(nat["p_reach_ia"], 3),
                "Early study": round(p_early_nat, 3), "Late study": round((p_early_nat + nat["p_ia"]) / 2, 3),
                "IA executed": round(nat["p_ia"], 3), "Construction": P_CONSTRUCTION}

    # durations (years)
    op = lb[lb.q_status.eq("operational") & lb.on_date.dt.year.between(2018, 2025)]
    op_yrs = ((op.on_date - op.q_date).dt.days / 365.25).where(lambda s: s.between(0, 25))
    ia = lb[lb.has_ia & lb.ia_date.dt.year.between(2020, 2025)]
    ia_yrs = ((ia.ia_date - ia.q_date).dt.days / 365.25).where(lambda s: s.between(0, 20))
    dur = {"national": {"to_cod": quartiles(op_yrs), "to_ia": quartiles(ia_yrs)}}
    for r in sorted(lb.region.unique()):
        cod = quartiles(op_yrs[op.region.eq(r)])
        iaq = quartiles(ia_yrs[ia.region.eq(r)])
        dur[r] = {"to_cod": cod if cod and cod["n"] >= MIN_DURATION_N else None,
                  "to_ia": iaq if iaq and iaq["n"] >= MIN_DURATION_N else None}

    out = {"completion": {"national": national, **regions}, "durations": dur,
           "method": {"cohort": "queue entry 2000-2019, resolved (built or withdrawn)", "prior_n": PRIOR_N,
                      "late_study": "midpoint of early-study and IA-executed odds (assumption)",
                      "construction": f"{P_CONSTRUCTION} (assumption)",
                      "to_cod": "projects online 2018-2025", "to_ia": "IAs signed 2020-2025"}}
    (PROCESSED / "queue_stats.json").write_text(json.dumps(out, indent=1))
    t = pd.DataFrame({r: {k: v for k, v in d.items() if k in ("Early study", "Late study", "IA executed", "n_resolved")}
                      for r, d in out["completion"].items()}).T
    print(t.to_string())
    print(pd.DataFrame({r: {"to_cod_p50": (d["to_cod"] or {}).get("p50"), "to_ia_p50": (d["to_ia"] or {}).get("p50")}
                        for r, d in dur.items()}).T.to_string())
    log("saved data/processed/queue_stats.json")


if __name__ == "__main__":
    main()
