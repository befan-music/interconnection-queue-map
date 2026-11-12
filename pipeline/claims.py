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


# ---- risk-assessment claims ---------------------------------------------------------------------
def c17_completion_odds():
    st = json.loads((PROCESSED / "queue_stats.json").read_text())
    rows = [[r, n(d["n_resolved"]), n(d["n_ia"]), pct(d["built_after_ia"], d["n_ia"], 0),
             f"{100 * d['Early study']:.0f}%", f"{100 * d['Late study']:.0f}%", f"{100 * d['IA executed']:.0f}%"]
            for r, d in st["completion"].items()]
    nat = st["completion"]["national"]
    claim("C17", "Odds that a queued request gets built, by region and stage",
          f"{100 * nat['Early study']:.0f}% before an agreement · {100 * nat['IA executed']:.0f}% after",
          f"Of {n(nat['n_resolved'])} requests filed 2000–2019 that have since been built or withdrawn, "
          f"{100 * nat['Early study']:.0f}% were built overall, and {100 * nat['IA executed']:.0f}% of those that signed an "
          f"interconnection agreement (IA) were built. The map weights every active request by these odds for its region and stage.",
          "Calculation",
          [LBNL_SRC("q_year, q_status, ia_date, IA_phase_clean, region")],
          ["Keep requests that entered a queue in 2000–2019 and are now 'operational' (built) or 'withdrawn'.",
           "A request 'reached an IA' if it has an IA date or LBNL marks it 'IA Executed' (LBNL marks all built "
           "projects this way).",
           "IA-executed odds = built ÷ requests that reached an IA. Early-study odds = share that reached an IA × "
           "IA-executed odds. Late-study odds = midpoint of the two (assumption: LBNL does not record the stage at "
           "which withdrawn requests left). Under construction = 90% (assumption).",
           f"Regional values are shrunk towards the national value with the weight of {50} requests."],
          {"columns": ["Region", "Resolved requests", "Reached an IA", "Built after IA (raw)", "Early study", "Late study",
                       "IA executed"], "rows": rows},
          "Survivorship: the 2000–2019 cohorts reflect older queue rules. Since FERC Order 2023, deposits and "
          "readiness rules may raise completion rates for today's requests.",
          ["Atlas: queue weights and risk assessment", "build_model.py", "risk_model.py"], "queue_stats.py")


def c18_durations():
    st = json.loads((PROCESSED / "queue_stats.json").read_text())["durations"]
    q = lambda d: f"{d['p50']:.1f} ({d['p25']:.1f}–{d['p75']:.1f}), n={n(d['n'])}" if d else "– (too few)"  # noqa: E731
    rows = [[r, q(d.get("to_ia")), q(d.get("to_cod"))] for r, d in st.items()]
    claim("C18", "How long interconnection takes, by region",
          f"{st['national']['to_cod']['p50']:.1f} years to operation (national median)",
          f"Projects that came online 2018–2025 took a median {st['national']['to_cod']['p50']:.1f} years from request to "
          f"operation; interconnection agreements signed 2020–2025 took a median {st['national']['to_ia']['p50']:.1f} years "
          f"from request.",
          "Calculation",
          [LBNL_SRC("q_date, ia_date, on_date, region")],
          ["Request → operation: built projects with an online date in 2018–2025; years = online date − queue date "
           "(0–25 years kept).",
           "Request → agreement: requests with an IA date in 2020–2025; years = IA date − queue date (0–20 kept).",
           "Report median and 25th–75th percentile per region; regions with fewer than 20 cases fall back to national."],
          {"columns": ["Region", "Years to signed IA: median (P25–P75)", "Years to operation: median (P25–P75)"],
           "rows": rows},
          "Only completed projects count, so slow projects still in the queue are missing: true durations for "
          "today's applicants are likely longer.",
          ["Atlas: risk assessment (time)"], "queue_stats.py")


def c19_cost_studies():
    c = pd.read_parquet(INTERIM / "cost_projects.parquet")
    t = c.groupby("region").agg(n_=("q_id", "size"), placed=("node", lambda s: (s >= 0).mean()),
                                recent=("study_year", lambda s: (s >= 2018).sum()), tot=("total_kw", "median"),
                                net=("net_kw", "median"))
    rows = [[r, n(v.n_), f"{100 * v.placed:.0f}%", n(v.recent), f"${v.tot:,.0f}", f"${v.net:,.0f}"] for r, v in t.iterrows()]
    rows.append(["Total", n(len(c)), f"{100 * (c.node >= 0).mean():.0f}%", n((c.study_year >= 2018).sum()),
                 f"${c.total_kw.median():,.0f}", f"${c.net_kw.median():,.0f}"])
    claim("C19", "What interconnection studies actually cost", f"{n(len(c))} studied projects",
          f"LBNL's cost datasets cover {n(len(c))} studied projects in MISO, PJM, SPP, NYISO, ISO-NE and five non-ISO "
          f"balancing authorities; {100 * (c.node >= 0).mean():.0f}% are placed on a substation of our network.",
          "Calculation",
          [src(f"data/raw/costs/{f}", "project-level POI, network and total cost per kW") for f in
           ("miso_costs_2021_clean_data.xlsx", "pjm_costs_2022_clean_data.xlsx", "spp_costs_2023_clean_data.xlsx",
            "nyiso_2022_final_data_cleaned_publication_vfinal.xlsx",
            "isone_interconnection_cost_data_publication_vfinal.xlsx", "ba_costs_2024_clean_data.xlsx")],
          ["Read each file; for the non-ISO file keep the latest study per project.",
           "Convert ISO costs from 2022 to 2024 dollars with the CPI-U annual-average ratio 313.689 / 292.655 = 1.072.",
           "Place ISO projects on a bus by joining their queue ID to the located LBNL history; place non-ISO projects "
           "at the nearest bus within 15 km of their published POI coordinates.",
           "Medians by region (all study years and statuses)."],
          {"columns": ["Region", "Projects", "Placed on a bus", "Studies since 2018", "Median total $/kW",
                       "Median network $/kW"], "rows": rows},
          "Costs are those assigned in studies, not final costs; withdrawn projects (often the most expensive) "
          "are included because a new applicant faces the same study. CAISO and ERCOT are not covered.",
          ["Atlas: risk assessment (cost)"], "cost_data.py")


def c20_validation():
    c = pd.read_parquet(INTERIM / "cost_projects.parquet")
    b = gpd.read_parquet(PROCESSED / "buses.parquet")[["node", "modeled", "headroom_gen_mw", "gen_signal"]]
    x = c[(c.node >= 0) & (c.study_year >= 2018) & c.net_kw.notna()].merge(b, on="node")
    x = x[x.modeled]
    meta = json.loads((PROCESSED / "risk_meta.json").read_text())
    rows = [[f"Signal: {s}", n(len(g)), f"${g.net_kw.median():,.0f}"] for s, g in x.groupby("gen_signal")]
    ratio = x.mw / x.headroom_gen_mw.clip(lower=5)
    bins = pd.cut(ratio, [0, 0.5, 1, 2, 4, float("inf")], labels=["<0.5", "0.5-1", "1-2", "2-4", ">4"])
    rows += [[f"Size ÷ modelled headroom {k}", n(len(g)), f"${g.net_kw.median():,.0f}  (×{meta['f_size_ratio'].get(str(k), 1):.2f})"]
             for k, g in x.groupby(bins, observed=True)]
    rows += [[f"Bus voltage {k}", "", f"×{v:.2f}"] for k, v in meta["g_kv"].items()]
    red, green = x[x.gen_signal == "red"].net_kw.median(), x[x.gen_signal == "green"].net_kw.median()
    claim("C20", "Does the map's congestion signal match real study costs?",
          f"red ${red:,.0f}/kW vs green ${green:,.0f}/kW",
          f"Across {n(len(x))} studies since 2018 placed on modelled substations, projects at substations the map colours "
          f"red were assigned a median ${red:,.0f}/kW of network upgrades, versus ${green:,.0f}/kW at green ones "
          f"({red / green:.1f}×). Costs also rise with project size relative to the modelled headroom.",
          "Model output",
          [{"name": "Cost studies on buses + model buses", "file": "data/interim/cost_projects.parquet, data/processed/buses.parquet",
            "detail": "net_kw, gen_signal, headroom_gen_mw", "url": None, "retrieved": ""}],
          ["Keep cost studies from 2018 on that sit on a modelled substation.",
           "Group by the substation's generation signal today and compare median network-upgrade cost.",
           "Group by project MW ÷ modelled existing-grid headroom. The multipliers in brackets (median of cost ÷ "
           "regional median) are what the risk model applies; voltage multipliers likewise."],
          {"columns": ["Group", "Studies", "Median network $/kW (multiplier used)"], "rows": rows},
          "Today's grid model is compared with studies done 2018–2023, while the grid and queue changed in between. "
          "It shows a directional relationship, not a precise prediction.",
          ["Atlas: risk assessment (cost)", "Method"], "risk_model.py")


def c21_cost_time():
    c = pd.read_parquet(INTERIM / "cost_projects.parquet")
    meta = json.loads((PROCESSED / "risk_meta.json").read_text())
    t = c[c.cod_date.notna() & c.q_date.notna() & (c.q_date.dt.year >= 2010)].copy()
    t["yrs"] = (t.cod_date - t.q_date).dt.days / 365.25
    t = t[t.yrs.between(0.3, 20)]
    tiers = pd.cut(t.net_kw, [-1, 10, 50, 150, float("inf")], labels=list(meta["tier_years_median"]))
    rows = [[k, n(len(g)), f"{g.yrs.median():.1f}", f"×{meta['tier_multiplier'][k]:.2f}"]
            for k, g in t.groupby(tiers, observed=False)]
    lo, hi = meta["tier_years_median"][rows[0][0]], meta["tier_years_median"][rows[-1][0]]
    claim("C21", "Bigger upgrades, longer waits", f"{lo:.1f} → {hi:.1f} years",
          f"Built projects in MISO, PJM and SPP (queued 2010 or later) with under $10/kW of network upgrades took a median "
          f"{lo:.1f} years from request to operation; those with over $150/kW took {hi:.1f} years.",
          "Calculation",
          [src("data/raw/costs/miso_costs_2021_clean_data.xlsx", "Done Date"),
           src("data/raw/costs/pjm_costs_2022_clean_data.xlsx", "Actual In Service Date"),
           src("data/raw/costs/spp_costs_2023_clean_data.xlsx", "In-Service Date")],
          ["Keep cost-study projects with a queue date (2010+) and an in-service date; years = in-service − queue (0.3–20).",
           "Group by network-upgrade cost tier; multiplier = tier median ÷ overall median. The risk model multiplies the "
           "regional time-to-operation by the multiplier of the expected cost tier."],
          {"columns": ["Network cost tier", "Projects", "Median years", "Multiplier used"], "rows": rows},
          "Small samples in the top tier; NYISO, ISO-NE and non-ISO files have no in-service dates.",
          ["Atlas: risk assessment (time)"], "risk_model.py")


def c22_risk_grades():
    rk = pd.read_parquet(PROCESSED / "risk.parquet")
    meta = json.loads((PROCESSED / "risk_meta.json").read_text())
    cuts = [-1, 25, 40, 55, 70, 101]
    rows = []
    for P, g in rk.groupby("size_mw"):
        gr = pd.cut(g.risk, cuts, labels=list("ABCDE")).value_counts().reindex(list("ABCDE")).fillna(0)
        rows.append([f"{P:,} MW"] + [f"{100 * v / len(g):.0f}%" for v in gr.values] + [f"${g.cost_kw.median():,.0f}",
                                                                                         f"{g.years.median():.1f}",
                                                                                         f"{100 * g.p_fit.median():.0f}%"])
    w = meta["weights"]
    claim("C22", "How risk grades are distributed", "share of substations by grade and project size",
          "With default weights, most substations earn A or B for a 50 MW project, while most earn D or E for 1,000 MW.",
          "Assumption-based arithmetic",
          [{"name": "Risk model", "file": "data/processed/risk.parquet", "detail": "", "url": None, "retrieved": ""}],
          [f"Cost score: 100 at ${meta['cost_anchor'][0]}/kW falling linearly to 0 at ${meta['cost_anchor'][1]}/kW. "
           f"Time score: 100 at {meta['time_anchor'][0]} years to 0 at {meta['time_anchor'][1]} years. Capacity score = "
           "chance the project still fits (0–100).",
           f"Risk index = 100 − ({100 * w['cost']:.0f}% cost + {100 * w['time']:.0f}% time + {100 * w['fit']:.0f}% capacity). "
           "Grades: A < 25, B 25–40, C 40–55, D 55–70, E ≥ 70. Viewers can change the weights in the map.",
           f"Capacity fit allows a log-normal error of σ = {meta['model_sigma']} on the modelled headroom (assumption)."],
          {"columns": ["Project size", "A", "B", "C", "D", "E", "Median $/kW", "Median years", "Median fit"], "rows": rows},
          "The anchors, weights and model-error allowance are judgement calls, not data; they decide the grade boundaries.",
          ["Atlas: risk assessment"], "risk_model.py")


def c23_unit_costs():
    m = json.loads((PROCESSED / "risk_meta.json").read_text())["miso"]
    rows = [[f"{kv} kV", f"${a:.1f}M", f"${r:.2f}M", f"${nl:.2f}M"] for kv, a, r, nl in
            zip(m["kv"], m["add_position_baah_musd"], m["reconductor_musd_per_mile"], m["new_line_musd_per_mile"])]
    claim("C23", "Unit costs used to price upgrades", "MISO MTEP24 cost guide",
          "Substation connections and line upgrades are priced with MISO's exploratory unit costs (2024 dollars, including "
          "30% contingency and financing during construction).",
          "Source table",
          [src("data/raw/costs/miso_cost_guide_mtep24.pdf", "Tables 4.1-1, 4.1-3, 4.2-1")],
          ["Connection = 'add 1 position, breaker-and-a-half bus' at the substation's highest voltage.",
           "Line upgrade = reconductor if the line is heat-limited and the needed extra flow is at most its usable "
           "rating; otherwise a new single-circuit line (median of MISO's state values).",
           "Cost per mile × line length; the project's share = its added flow ÷ (its added flow + the queue's overload)."],
          {"columns": ["Voltage", "Add 1 position", "Reconductor per mile", "New line per mile"], "rows": rows},
          "MISO's figures are planning estimates for its footprint; costs elsewhere (e.g. California, the Northeast) "
          "are typically higher. Reconductoring with advanced conductors costs more than the ACSR/ACSS basis.",
          ["Atlas: risk assessment (upgrade list)"], "risk_model.py")


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
    c17_completion_odds()
    c18_durations()
    c19_cost_studies()
    c20_validation()
    c21_cost_time()
    c22_risk_grades()
    c23_unit_costs()
    out = {"generated": str(date.today()), "claims": CLAIMS}
    (MAP_DATA / "claims.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    for c in CLAIMS:
        log(f"{c['id']:>4} {c['title']}: {c['value']}")


if __name__ == "__main__":
    main()
