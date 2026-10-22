"""Recompute every quantitative claim in README.md, Market_Analysis.md and the atlas from the
pipeline's own files, and record how each was derived.

Output: map/data/claims.json, shown in the web map's "Claims & calculations" panel
        (each claim has an ID such as C3; link to it as <atlas URL>#C3).
Run after build_model.py:  python -I pipeline/claims.py
"""
import json
import re
import sys
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, MANIFEST, MAP_DATA, PROCESSED, RAW, ROOT, log  # noqa: E402
from normalize_queues import LBNL_SHEET, read_lbnl_raw  # noqa: E402

CODE = "https://github.com/befan-music/interconnection-queue-map/blob/main/pipeline/"
MANI = json.loads(MANIFEST.read_text())
CLAIMS = []


# ---- formatting helpers -------------------------------------------------------------------------
def n(x):
    return f"{int(round(x)):,}"


def gw(mw, nd=1):
    return f"{mw / 1e3:,.{nd}f}"


def pct(a, b, nd=1):
    return f"{100 * a / b:.{nd}f}%" if b else "–"


def src(rel, detail):
    m = MANI.get(rel, {})
    return {"name": m.get("source", rel), "file": rel, "detail": detail, "url": m.get("url"),
            "retrieved": m.get("retrieved_utc", "")[:10]}


def claim(cid, title, value, statement, kind, sources, method, table=None, caveats=None, used_in=(), code=""):
    CLAIMS.append({"id": cid, "title": title, "value": value, "statement": statement, "kind": kind,
                   "sources": sources, "method": method, "table": table, "caveats": caveats,
                   "used_in": list(used_in), "code": CODE + code})


LBNL_REL = "data/raw/queues/lbnl_queued_up_thru2025.xlsx"
LBNL_SRC = lambda cols: src(LBNL_REL, f"sheet '{LBNL_SHEET}', columns {cols}")  # noqa: E731
QUEUE_FILES = ["caiso_public_queue.xlsx", "miso_queue.json", "spp_active_queue.csv", "nyiso_queue.xlsx",
               "pjm_queue.xlsx", "ercot_gis_report.xlsx", "isone_queue.html", "lbnl_queued_up_thru2025.xlsx"]


# ---- queue claims -------------------------------------------------------------------------------
def c1_active_queue():
    q = pd.read_parquet(INTERIM / "queue_projects.parquet")
    by = q.groupby(["iso", "source"]).agg(req=("uid", "size"), mw=("mw", "sum")).reset_index()
    by = by.sort_values("mw", ascending=False)
    rows = [[r.iso, r.source, n(r.req), gw(r.mw)] for r in by.itertuples()]
    rows.append(["Total", "", n(len(q)), gw(q.mw.sum())])
    claim("C1", "Active interconnection queue, October 2026", f"{n(len(q))} requests · {gw(q.mw.sum(), 0)} GW",
          f"The seven ISO/RTO queues (retrieved October 2026) plus LBNL data for non-ISO utilities contain "
          f"{n(len(q))} active requests totalling {gw(q.mw.sum(), 0)} GW.",
          "Calculation",
          [src(f"data/raw/queues/{f}", "active requests") for f in QUEUE_FILES],
          ["Read each ISO's live queue and keep active requests. Per-ISO rules are in normalize_queues.py: "
           "SPP drops 'IA FULLY EXECUTED/COMMERCIAL OPERATION'; ERCOT drops projects already approved for "
           "synchronisation (they are in EIA's operating fleet); PJM keeps Active, Confirmed, Engineering and "
           "Procurement, Suspended, Under Construction and Partially in Service.",
           "MW per request = the ISO's injection or net-to-grid figure (CAISO 'Net MWs to Grid', PJM 'MFO', "
           "NYISO 'SP (MW)', ERCOT 'Capacity (MW)', MISO/SPP/ISO-NE the largest MW field).",
           "Add LBNL active requests for regions 'West' and 'Southeast' (utilities outside ISOs, end-2025).",
           "For CAISO, MISO, NYISO, PJM and ISO-NE, whose live files also list withdrawn or completed requests, add "
           "LBNL active requests whose ID never appears in the live file (e.g. CAISO Cluster 15).",
           "Drop requests with missing or non-positive MW, then duplicate ISO:ID pairs.",
           f"Count rows and sum MW: {n(len(q))} requests, {q.mw.sum():,.1f} MW."],
          {"columns": ["Region", "Source", "Requests", "GW"], "rows": rows},
          "Mixed vintages: ISO data is from October 2026, non-ISO utilities are LBNL end-2025. Hybrids are counted "
          "once at their injection limit, so the total is not comparable with LBNL's separate generation + storage "
          "figures (1,312 GW + 749 GW).",
          ["Market_Analysis.md §1, §6", "CLAUDE.md"], "normalize_queues.py")


def c2_lbnl_status(lb):
    t = lb.groupby("q_status").agg(req=("q_id", "size"), mw=("mw_1", "sum")).sort_values("req", ascending=False)
    rows = [[s, n(r.req), pct(r.req, len(lb)), gw(r.mw, 0)] for s, r in t.iterrows()]
    rows.append(["Total", n(len(lb)), "100%", gw(t.mw.sum(), 0)])
    act, wd = t.loc["active", "req"], t.loc["withdrawn", "req"]
    claim("C2", "LBNL dataset: requests by status", f"{n(act)} active · {n(wd)} withdrawn",
          f"The LBNL Queued Up data file (through end-2025) lists {n(len(lb))} requests: {n(act)} active and "
          f"{n(wd)} withdrawn.",
          "Calculation",
          [LBNL_SRC("q_id, q_status, mw_1")],
          ["Load every row of the project-level sheet (all years, all statuses).",
           "Group by q_status; count rows and sum mw_1 (capacity of the main resource type)."],
          {"columns": ["Status", "Requests", "Share", "GW (mw_1)"], "rows": rows},
          "LBNL's report cites about 8,200 active projects and 1,312 GW generation + 749 GW storage. Those are its "
          "own cleaned figures and are quoted, not recomputed here. The data file marks more rows as active.",
          ["Market_Analysis.md §1, §5"], "claims.py")


def c3_outcomes(lb):
    yrs = pd.to_numeric(lb.q_year, errors="coerce")
    c = lb[yrs.between(2000, 2019)]
    cy = pd.to_numeric(c.q_year)
    bins = pd.cut(cy, [1999, 2004, 2009, 2014, 2019], labels=["2000–04", "2005–09", "2010–14", "2015–19"])
    rows = []
    for label, g in list(c.groupby(bins, observed=True)) + [("2000–19 total", c)]:
        s = g.q_status.value_counts()
        rows.append([str(label), n(len(g))] + [pct(s.get(k, 0), len(g)) for k in
                                                  ("operational", "withdrawn", "active", "suspended")])
    op = (c.q_status == "operational").sum()
    wd = (c.q_status == "withdrawn").sum()
    mw_op = c.loc[c.q_status == "operational", "mw_1"].sum() / c.mw_1.sum()
    claim("C3", "Most requests never get built", f"{pct(op, len(c), 0)} built · {pct(wd, len(c), 0)} withdrew",
          f"Of {n(len(c))} requests that entered a queue in 2000–2019, {n(op)} ({pct(op, len(c))}) reached "
          f"commercial operation and {n(wd)} ({pct(wd, len(c))}) withdrew. By capacity, {100 * mw_op:.1f}% of the "
          f"requested MW was built.",
          "Calculation",
          [LBNL_SRC("q_year, q_status, mw_1")],
          ["Load the project-level sheet.",
           f"Keep rows whose q_year (year the request entered the queue) is 2000–2019 inclusive: {n(len(c))} rows "
           f"({n(yrs.isna().sum())} rows without q_year excluded).",
           f"Operational share = rows with q_status 'operational' ÷ all kept rows = {n(op)} ÷ {n(len(c))}.",
           f"Withdrawn share = rows with q_status 'withdrawn' ÷ all kept rows = {n(wd)} ÷ {n(len(c))}.",
           "Repeat by 5-year cohort (table)."],
          {"columns": ["Queue-entry years", "Requests", "Operational", "Withdrawn", "Still active", "Suspended"],
           "rows": rows},
          "Counts requests, not MW. The 2015–19 cohort still has active requests that may yet be built or withdraw, "
          "so its final completion rate is not settled. 2020+ cohorts are excluded for that reason.",
          ["Market_Analysis.md §1, §5", "Atlas: queue weights"], "claims.py")


def c4_requests_per_year(lb):
    yrs = pd.to_numeric(lb.q_year, errors="coerce")
    t = lb.assign(y=yrs)[yrs.between(2015, 2025)].groupby("y").agg(req=("q_id", "size"), mw=("mw_1", "sum"))
    rows = [[str(int(y)), n(r.req), gw(r.mw, 0)] for y, r in t.iterrows()]
    recent = t.loc[2019:2025, "req"]
    claim("C4", "New interconnection requests per year", f"{n(recent.min())}–{n(recent.max())} per year (2019–25)",
          f"Between {n(recent.min())} and {n(recent.max())} new requests entered US queues each year in 2019–2025.",
          "Calculation",
          [LBNL_SRC("q_year, q_id, mw_1")],
          ["Load the project-level sheet (all statuses: requests that later withdrew still count as filed).",
           "Group by q_year; count rows and sum mw_1."],
          {"columns": ["Year", "New requests", "GW requested"], "rows": rows},
          "2025 may be slightly undercounted if some requests were added to queues after LBNL's data cut-off.",
          ["Market_Analysis.md §2 (demand signal, cross-check)"], "claims.py")


def c5_transmission_providers(lb):
    act = lb[lb.q_status == "active"]
    t = lb.groupby("region").entity.nunique().rename("all").to_frame().join(
        act.groupby("region").entity.nunique().rename("active")).fillna(0)
    rows = [[r, n(v["all"]), n(v["active"])] for r, v in t.sort_values("all", ascending=False).iterrows()]
    rows.append(["Total (distinct)", n(lb.entity.nunique()), n(act.entity.nunique())])
    claim("C5", "Transmission providers in the queue data", f"{lb.entity.nunique()} entities",
          f"The LBNL data covers {lb.entity.nunique()} distinct transmission-provider entities "
          f"({act.entity.nunique()} with active requests).",
          "Calculation",
          [LBNL_SRC("entity, region, q_status")],
          ["Count distinct values of 'entity' (transmission provider: ISO or utility), overall and among "
           "active requests."],
          {"columns": ["Region", "Entities (all)", "Entities (active)"], "rows": rows},
          "Each ISO counts as one entity even though it covers many transmission owners.",
          ["Market_Analysis.md §2 (utility segment, TAM)"], "claims.py")


def norm_dev(s):
    s = re.sub(r"\b(llc|inc|lp|corp|corporation|company|co|ltd|l\.l\.c\.)\b", "", str(s).lower())
    return s.replace(",", "").replace(".", "").strip()


def c6_developers(lb):
    act = lb[lb.q_status == "active"]
    d = act.developer.dropna()
    d = d[~d.str.lower().isin(["nan", "unknown", "n/a", "not available", ""])]
    names = d.map(norm_dev)
    vc = names.value_counts()
    named = act.developer.notna() & ~act.developer.fillna("").str.lower().isin(["nan", "unknown", "n/a", "not available", ""])
    reg = act.assign(named=named).groupby("region").agg(req=("q_id", "size"), named=("named", "sum"))
    rows = [[r, n(v.req), n(v.named), pct(v.named, v.req, 0)] for r, v in reg.sort_values("req", ascending=False).iterrows()]
    rows.append(["Total", n(len(act)), n(len(d)), pct(len(d), len(act), 0)])
    claim("C6", "Developers named in the active queue", f"{n(names.nunique())} names on {pct(len(d), len(act), 0)} of requests",
          f"Only {pct(len(d), len(act), 0)} of {n(len(act))} active requests name a developer, and those show "
          f"{n(names.nunique())} distinct developer names ({(vc >= 2).sum()} with 2+ requests, {(vc >= 5).sum()} with 5+).",
          "Calculation",
          [LBNL_SRC("developer, q_status, region")],
          ["Keep active requests.",
           "Drop empty developer fields ('nan', 'unknown', 'n/a', 'not available').",
           "Normalise names: lower-case; remove LLC, Inc, LP, Corp, Corporation, Company, Co, Ltd; remove commas and full stops.",
           "Count distinct normalised names and requests per name."],
          {"columns": ["Region", "Active requests", "With developer", "Share named"], "rows": rows},
          "Many names are project-specific companies (one LLC per project), so parent developers are fewer than "
          "the distinct names. Several ISOs publish no developer names at all, so the national count is uncertain.",
          ["Market_Analysis.md §2 (developer count)"], "claims.py")


def c7_in_study():
    q = pd.read_parquet(INTERIM / "queue_projects.parquet")
    t = q.groupby("phase").agg(req=("uid", "size"), mw=("mw", "sum")).reindex(
        ["Early study", "Late study", "IA executed", "Construction"])
    rows = [[p, n(r.req), gw(r.mw, 0), pct(r.req, len(q))] for p, r in t.iterrows()]
    s = t.loc[["Early study", "Late study"]]
    claim("C7", "Requests still in study", f"{n(s.req.sum())} requests · {gw(s.mw.sum(), 0)} GW",
          f"{n(s.req.sum())} of the {n(len(q))} active requests ({gw(s.mw.sum(), 0)} GW) have not yet signed an "
          f"interconnection agreement.",
          "Calculation",
          [src("data/raw/queues/" + f, "status / study phase") for f in QUEUE_FILES],
          ["Start from the active-queue table of C1.",
           "Each ISO's status is mapped to four phases in normalize_queues.py (e.g. MISO DPP Phase 2/3 → late study; "
           "PJM 'Engineering and Procurement' → IA executed; NYISO status codes 10–11 → IA executed).",
           "Group by phase; count requests and sum MW."],
          {"columns": ["Phase", "Requests", "GW", "Share of requests"], "rows": rows},
          "ISO-NE publishes no study phase, so its requests are counted as early study.",
          ["Atlas: queue weights"], "normalize_queues.py")


def c8_matching():
    q = pd.read_parquet(PROCESSED / "queue.parquet")
    t = q.groupby("match_method").agg(req=("uid", "size"), mw=("mw", "sum")).sort_values("req", ascending=False)
    rows = [[m, n(r.req), pct(r.req, len(q)), gw(r.mw, 0)] for m, r in t.iterrows()]
    cp = t.loc["county proximity", "req"]
    claim("C8", "How queue requests were placed on the map", f"{pct(cp, len(q), 0)} by county only",
          f"{pct(len(q) - cp - t.loc['unlocated', 'req'], len(q), 0)} of requests are placed by matching their "
          f"point-of-interconnection name to a substation; {pct(cp, len(q), 0)} by county proximity only.",
          "Model output",
          [src("data/raw/queues/lbnl_queued_up_thru2025.xlsx", "county FIPS"), {"name": "OpenStreetMap substations + HIFLD line endpoint labels", "file": "data/raw/osm, data/raw/hifld", "detail": "substation names", "url": None, "retrieved": ""}],
          ["Geocode each request's county (Census boundaries; LBNL FIPS where available).",
           "Clean the POI text (remove kV, 'substation', bus numbers, …) and fuzzy-match it (score ≥ 87) to named "
           "substations within 150 km of the county, favouring matching voltage and proximity. 'Line endpoint' "
           "means the POI named a line (A–B) and one end matched.",
           "Otherwise place it at the nearest substation of plausible voltage to the county centre.",
           "Requests with neither county nor matchable name stay unlocated."],
          {"columns": ["Method", "Requests", "Share", "GW"], "rows": rows},
          "County-proximity placements are right in aggregate but can be wrong for an individual project.",
          ["Market_Analysis.md §6", "README.md", "Atlas"], "match_queue.py")


# ---- model claims -------------------------------------------------------------------------------
def c9_model_size():
    s = json.loads((PROCESSED / "model_summary.json").read_text())
    ics = s["interconnections"]
    rows = [[k, n(v["buses"]), n(v["lines"]), f"{v['gen_gw']:.0f}", f"{v['peak_load_gw']:.0f}",
             f"{100 * v['share_lines_calibrated']:.1f}%"] for k, v in ics.items()]
    tb, tl = sum(v["buses"] for v in ics.values()), sum(v["lines"] for v in ics.values())
    rows.append(["Total", n(tb), n(tl), "", "", ""])
    runtime = None
    log_path = ROOT / "data" / "rebuild.log"
    if log_path.exists():
        ts = re.findall(r"\[(\d{2}):(\d{2}):(\d{2})\]", log_path.read_text(errors="ignore"))
        if len(ts) >= 2:
            sec = lambda t: int(t[0]) * 3600 + int(t[1]) * 60 + int(t[2])  # noqa: E731
            runtime = (sec(ts[-1]) - sec(ts[0])) / 60
    claim("C9", "Size of the network model", f"{n(tb)} buses · {n(tl)} lines",
          f"The power-flow model solves {n(tb)} buses and {n(tl)} lines in three interconnections for two "
          f"scenarios" + (f"; building network, queue matching, model and map export took {runtime:.0f} minutes on a "
                          f"laptop (data/rebuild.log)." if runtime else "."),
          "Model output",
          [{"name": "Model summary", "file": "data/processed/model_summary.json", "detail": "interconnections",
            "url": None, "retrieved": ""}],
          ["Count buses and lines inside the three main connected networks (islands excluded).",
           "Runtime = first to last timestamp in data/rebuild.log (steps 4–7 of the pipeline; downloads excluded)."],
          {"columns": ["Interconnection", "Buses", "Lines", "Gen GW", "Peak load GW", "Lines re-rated"], "rows": rows},
          "Runtime depends on the machine; downloads (≈1 hour, mostly OpenStreetMap) are not included.",
          ["Market_Analysis.md §1 (compute sidenote)", "CLAUDE.md"], "build_model.py")


def c10_signals(b):
    m = b[b.modeled & (b.kv_max >= 115) & b.node_type.eq("substation")]
    vc = m.gen_signal.value_counts()
    rows = [[s, n(vc.get(s, 0)), pct(vc.get(s, 0), len(m))] for s in ("green", "yellow", "red")]
    claim("C10", "Substations with room for new generation", " · ".join(f"{pct(vc.get(s, 0), len(m), 0)} {s}" for s in ("green", "yellow", "red")),
          f"Of {n(len(m))} named, modelled substations at 115 kV or above, "
          + ", ".join(f"{pct(vc.get(s, 0), len(m), 0)} are {s}" for s in ("green", "yellow", "red")) + ".",
          "Model output",
          [{"name": "Model buses", "file": "data/processed/buses.parquet", "detail": "gen_signal, kv_max, node_type, modeled", "url": None, "retrieved": ""}],
          ["Keep buses in the three modelled networks, with a name, at ≥ 115 kV.",
           "Signal: green ≥ 250 MW of generation headroom after the queue; yellow 75–250 MW; red < 75 MW or queued MW "
           "> 1.5 × the substation's N-1 branch capacity.",
           "Count by signal."],
          {"columns": ["Signal", "Substations", "Share"], "rows": rows},
          "Screening model: estimated ratings, no N-1 contingency analysis (see README limitations).",
          ["Earlier summaries", "Atlas legend"], "build_model.py")


def c11_red_by_iso(b, q):
    m = b[b.modeled & (b.kv_max >= 115) & b.node_type.eq("substation")]
    iso = q[q.node >= 0].groupby("node").iso.agg(lambda s: s.mode().iat[0])
    m = m.assign(iso=m.node.map(iso)).dropna(subset=["iso"])
    t = m.groupby("iso").agg(subs=("node", "size"), red=("gen_signal", lambda s: (s == "red").mean()),
                             hq=("headroom_gen_after_queue_mw", "median")).sort_values("red", ascending=False)
    rows = [[i, n(r.subs), f"{100 * r.red:.0f}%", n(r.hq)] for i, r in t.iterrows()]
    claim("C11", "Congestion where projects are queued, by region",
          ", ".join(f"{i} {100 * r.red:.0f}%" for i, r in t.head(3).iterrows()) + " red",
          "Among named substations at ≥ 115 kV that host queued requests, the share rated red differs sharply by region.",
          "Model output",
          [{"name": "Model buses + located queue", "file": "data/processed/buses.parquet, data/processed/queue.parquet",
            "detail": "gen_signal, headroom_gen_after_queue_mw, iso", "url": None, "retrieved": ""}],
          ["Keep named, modelled substations at ≥ 115 kV with at least one queued request.",
           "Assign each substation the region (ISO, or LBNL 'West'/'Southeast') that most of its queued requests come from.",
           "Per region: share of substations with a red signal, and median generation headroom after the queue."],
          {"columns": ["Region", "Substations with queue", "Red", "Median MW left after queue"], "rows": rows},
          "Screening model; a region's red share also reflects how large its queue is.",
          ["Earlier summaries"], "claims.py")


def c12_constraints(lines):
    t = lines[lines.queue_mw_behind >= 500]
    th = t[(t.limit_basis == "thermal") & (t.length_km <= 80)]
    top = lines.nlargest(10, "queue_mw_behind")
    nm = lambda s: "–" if (not isinstance(s, str) or re.match(r"^(UNKNOWN|TAP|NOT AVAIL)", s)) else s.title()  # noqa: E731
    rows = [[f"{nm(r.sub_1)} – {nm(r.sub_2)}", f"{r.kv:.0f}", (r.owner or "").title(), f"{r.length_km:.0f}",
             f"{r.loading_q_pct:.0f}%", n(r.binding_buses), gw(r.queue_mw_behind)] for r in top.itertuples()]
    claim("C12", "Lines holding back the queue (reconductoring candidates)",
          f"{len(th)} heat-limited lines < 80 km · {gw(th.queue_mw_behind.sum(), 0)} GW behind",
          f"{len(t)} line segments ({n(t.length_km.sum())} km) set the limit for substations holding "
          f"{gw(t.queue_mw_behind.sum(), 0)} GW of queued requests. Of these, {len(th)} are limited by heat rather "
          f"than stability and shorter than 80 km ({n(th.length_km.sum())} km, {gw(th.queue_mw_behind.sum(), 0)} GW "
          f"behind), the lines where new conductors add capacity.",
          "Model output",
          [{"name": "Model lines", "file": "data/processed/lines.parquet",
            "detail": "queue_mw_behind, limit_basis, length_km, binding_buses", "url": None, "retrieved": ""}],
          ["For every substation, the model records which line sets its generation headroom after the queue "
           "(the binding constraint).",
           "'Queued MW behind' a line = total queued MW at the substations whose limit is that line. Each "
           "substation is counted once, so totals do not double count.",
           "Keep lines with ≥ 500 MW behind them; then those whose screening limit is thermal (not the St. Clair "
           "stability limit) and that are ≤ 80 km long."],
          {"columns": ["Line (ends)", "kV", "Owner", "km", "Loading with queue", "Substations limited", "GW behind"],
           "rows": rows},
          "Screening model. Many queued MW sit at substations placed by county proximity (C8), and line names "
          "come from HIFLD endpoint labels.",
          ["Market_Analysis.md §2 (upside), §6", "Reconductoring discussion"], "build_model.py")


def c13_storage(b):
    s = b[b.storage_opportunity]
    claim("C13", "Storage and reconductoring candidate substations", f"{n(len(s))} substations",
          f"{n(len(s))} modelled buses have under 100 MW of generation headroom after the queue but at least 150 MW "
          f"of solar and wind behind them.",
          "Model output",
          [{"name": "Model buses", "file": "data/processed/buses.parquet",
            "detail": "storage_opportunity, gen_solar_mw, gen_wind_mw, queue_mw", "url": None, "retrieved": ""}],
          ["Renewables behind a bus = existing solar + wind at the bus (EIA-860M) + 30% of its queued MW.",
           "Candidate if modelled, headroom after queue < 100 MW and renewables behind ≥ 150 MW."],
          {"columns": ["Interconnection", "Candidates"],
           "rows": [[k, n(v)] for k, v in s.interconnection.value_counts().items()]},
          "The 30% share of queued MW and both thresholds are screening assumptions.",
          ["Earlier summaries", "Atlas: Storage lens"], "build_model.py")


def c14_market_sizing():
    dev_n, dev_mix = 1200, [(0.70, 15e3), (0.25, 50e3), (0.05, 150e3)]
    dc_n, dc_acv, inv_n, inv_acv, ut_n, ut_acv = 300, 100e3, 400, 40e3, 57, 250e3
    dev_acv = sum(s * v for s, v in dev_mix)
    tam_parts = [("Developers", dev_n, dev_acv), ("Data-center developers", dc_n, dc_acv),
                 ("Investors & off-takers", inv_n, inv_acv), ("Utilities & transmission providers", ut_n, ut_acv)]
    tam = sum(c * a for _, c, a in tam_parts)
    sam_dev = dev_n * (0.70 * 15e3 + 0.25 * 50e3)
    sam = sam_dev + 0.70 * dc_n * dc_acv + inv_n * inv_acv
    som_parts = [("Developer accounts", 80, 23e3), ("Data-center accounts", 15, 100e3), ("Investor accounts", 25, 40e3)]
    som = sum(c * a for _, c, a in som_parts)
    m = lambda x: f"${x / 1e6:,.1f}M"  # noqa: E731
    rows = [[f"TAM · {k}", n(c), f"${a / 1e3:,.1f}k", m(c * a)] for k, c, a in tam_parts]
    rows += [["TAM total", "", "", m(tam)],
             ["SAM · developers (excl. largest 5%)", n(dev_n), "70% × $15k + 25% × $50k", m(sam_dev)],
             ["SAM · data centers (70% early-stage siting)", n(dc_n * 0.7), "$100k", m(0.7 * dc_n * dc_acv)],
             ["SAM · investors & off-takers", n(inv_n), "$40k", m(inv_n * inv_acv)],
             ["SAM total", "", "", m(sam)]]
    rows += [[f"SOM · {k}", n(c), f"${a / 1e3:,.0f}k", m(c * a)] for k, c, a in som_parts]
    rows += [["SOM total (year 5)", "", f"{100 * som / sam:.1f}% of SAM", m(som)]]
    lo, hi = 2300 * 5 * 1000, 3400 * 5 * 1000
    rows += [["Cross-check: site reports", "2,300–3,400 req/yr × 5 sites", "$1,000/report", f"{m(lo)}–{m(hi)}"],
             ["Reconductoring upside (illustrative)", "5 GW unlocked", "$10/kW", m(5e6 * 10)]]
    claim("C14", "Market sizing arithmetic (TAM / SAM / SOM)", f"TAM {m(tam)} · SAM {m(sam)} · SOM {m(som)}",
          f"With the stated assumptions, the US market is about {m(tam)} a year (TAM), {m(sam)} serviceable (SAM), "
          f"and {m(som)} of recurring revenue plausibly won by year 5 (SOM).",
          "Assumption-based arithmetic",
          [{"name": "Counts from C4, C5, C6; prices are hypotheses", "file": "Market_Analysis.md §2", "detail": "",
            "url": None, "retrieved": ""}],
          ["Developers: 1,200 organisations (C6 shows 1,871 names, many project LLCs) × weighted price "
           f"(70% × $15k + 25% × $50k + 5% × $150k = ${dev_acv / 1e3:.1f}k).",
           "Data centers 300 × $100k; investors & off-takers 400 × $40k; utilities 57 (C5) × $250k.",
           "SAM drops the largest developers, 30% of data-center demand and all utilities.",
           "SOM = named account targets for year 5.",
           "Cross-check: new requests per year (C4) × 5 sites screened × $1,000 per report."],
          {"columns": ["Line item", "Count", "Price / basis", "Annual value"], "rows": rows},
          "Only the organisation counts for utilities (C5) and the developer signal (C4, C6) come from data. All "
          "prices, the data-center and investor counts, and the SAM/SOM shares are assumptions to test in interviews.",
          ["Market_Analysis.md §2"], "claims.py")


def c15_spot_checks(b):
    names = ["Vincent", "Windhub", "Gates", "Mead", "Palo Verde", "Lamar", "Comanche", "Sooner", "Hoskins", "Big Hill"]
    m = b[b.modeled & b.node_type.eq("substation")]
    rows = []
    for nm in names:
        hit = m[m.name.str.upper() == nm.upper()].sort_values("kv_max", ascending=False).head(1)
        for r in hit.itertuples():
            rows.append([r.name, r.state, r.kv_levels, n(r.headroom_gen_mw), n(r.headroom_gen_after_queue_mw),
                         n(r.queue_mw), "–" if pd.isna(r.queue_to_capacity) else f"{r.queue_to_capacity:.2f}×",
                         r.gen_signal])
    claim("C15", "Spot checks at well-known substations", f"{len(rows)} hubs",
          "Model values at substations named in our discussion, for comparison with what is known about them.",
          "Model output",
          [{"name": "Model buses", "file": "data/processed/buses.parquet", "detail": "", "url": None, "retrieved": ""}],
          ["Look up each name among modelled substations; where a name repeats, take the highest-voltage one."],
          {"columns": ["Substation", "State", "kV", "MW today", "MW after queue", "Queued MW", "Queue ÷ capacity", "Signal"],
           "rows": rows},
          "Values at or near 3,000 MW are the display cap: big 500 kV hubs read too high because transformer "
          "limits and contingencies are not modelled.",
          ["Earlier summaries", "README limitations"], "build_model.py")


def c16_data_volume():
    lines_raw = pyogrio.read_info(RAW / "hifld" / "transmission_lines.geojson")["features"]
    osm = sum(len(json.load(open(f, encoding="utf-8"))["elements"]) for f in (RAW / "osm").glob("substations_US-*.json"))
    g = pd.read_parquet(INTERIM / "generators.parquet")
    pl = gpd.read_parquet(INTERIM / "plants.parquet")
    rows = [["HIFLD transmission line segments", n(lines_raw), "all US incl. AK/HI; 51,861 AC segments in CONUS used"],
            ["OpenStreetMap substations", n(osm), "49 state queries (contiguous US + DC)"],
            ["EIA-860M operating generators (CONUS)", n(len(g)), f"{gw(g.mw.sum(), 0)} GW net summer capacity"],
            ["Power plants (aggregated)", n(len(pl)), "with coordinates in CONUS"]]
    claim("C16", "Size of the input datasets", f"{n(lines_raw)} line segments · {n(len(g))} generators",
          "Record counts of the main physical-grid inputs.",
          "Calculation",
          [src("data/raw/hifld/transmission_lines.geojson", "feature count"),
           src("data/raw/eia860m/august_generator2026.xlsx", "sheet 'Operating', status OP or SB")],
          ["Count features / rows in each downloaded file (generators: operating or standby units in the "
           "contiguous US; capacity = net summer MW)."],
          {"columns": ["Dataset", "Records", "Note"], "rows": rows},
          None, ["README.md", "Atlas: Method & sources"], "claims.py")


def main():
    lb = read_lbnl_raw()
    b = gpd.read_parquet(PROCESSED / "buses.parquet")
    q = pd.read_parquet(PROCESSED / "queue.parquet")
    lines = gpd.read_parquet(PROCESSED / "lines.parquet")
    c1_active_queue()
    c2_lbnl_status(lb)
    c3_outcomes(lb)
    c4_requests_per_year(lb)
    c5_transmission_providers(lb)
    c6_developers(lb)
    c7_in_study()
    c8_matching()
    c9_model_size()
    c10_signals(b)
    c11_red_by_iso(b, q)
    c12_constraints(lines)
    c13_storage(b)
    c14_market_sizing()
    c15_spot_checks(b)
    c16_data_volume()
    out = {"generated": str(date.today()), "claims": CLAIMS}
    (MAP_DATA / "claims.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    for c in CLAIMS:
        log(f"{c['id']:>4} {c['title']}: {c['value']}")


if __name__ == "__main__":
    main()
