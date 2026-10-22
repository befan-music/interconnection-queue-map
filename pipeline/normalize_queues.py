"""Normalize the 7 ISO/RTO live queues + LBNL Queued Up into one active-project table.

Policy
- Live ISO files (Oct 2026) are authoritative for their ISO.
- LBNL (end-2025) covers the non-ISO West and Southeast, and backfills active LBNL projects whose
  ID never appears in an ISO's full-history live file (e.g. CAISO Cluster 15, which CAISO
  publishes outside its Public Queue Report).
Output: data/interim/queue_projects.parquet
"""
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import INTERIM, RAW, log  # noqa: E402

Q = RAW / "queues"
PHASES = ["Early study", "Late study", "IA executed", "Construction"]


def std_type(*parts):
    """Map heterogeneous fuel/technology codes to a compact resource type."""
    s = " ".join(str(p) for p in parts if isinstance(p, str) or (p is not None and not pd.isna(p)))
    s = f" {s.upper()} "
    has = lambda pat: re.search(pat, s) is not None  # noqa: E731
    osw = has(r"OFFSHORE|\bOSW\b")
    solar = has(r"SOLAR|PHOTOVOLTAIC|\bPV\b|\bSOL\b|\bSUN\b|^ S ")
    wind = osw or has(r"WIND|\bWT\b|\bWIN\b|\bWND\b|^ W ")
    batt = has(r"STORAGE|BATTER|BESS|\bBA\b|\bBAT\b|\bES\b|\bMWH\b")
    if osw:
        base = "Offshore Wind"
    elif solar and wind:
        base = "Solar+Wind"
    elif solar:
        base = "Solar"
    elif wind:
        base = "Wind"
    elif has(r"NUCLEAR|\bNUC\b|\bSMR\b"):
        base = "Nuclear"
    elif has(r"GAS|\bNG\b|\bCC\b|\bCT\b|\bGT\b|\bIC\b|COMBUSTION|COMBINED CYCLE|STEAM TURBINE|METHANE"):
        base = "Gas"
    elif has(r"HYDRO|\bWAT\b|\bHYD\b|\bH\b|PUMPED"):
        base = "Hydro"
    elif has(r"GEOTHERM|\bGEO\b"):
        base = "Geothermal"
    elif has(r"COAL"):
        base = "Coal"
    else:
        base = None
    if batt:
        return f"{base}+Battery" if base else "Battery"
    return base or "Other"


def kv_from(text):
    if not isinstance(text, str):
        return np.nan
    m = re.findall(r"(\d{2,3}(?:\.\d+)?)\s*-?\s*kv", text, flags=re.I)
    return max(float(x) for x in m) if m else np.nan


def norm_id(x):
    s = str(x).strip().upper().split(" - ")[0].strip()  # PJM: "AG2-637 - MOVED TO TC2"
    return s.lstrip("0") if s.isdigit() else s


def year_of(x):
    d = pd.to_datetime(x, errors="coerce", format="mixed")
    return d.year if not pd.isna(d) else np.nan


def frame(iso, **cols):
    df = pd.DataFrame(cols)
    df.insert(0, "iso", iso)
    return df


# --- per-ISO readers: return (active_df, set_of_all_ids_in_live_history) ----------------------

def caiso():
    path = Q / "caiso_public_queue.xlsx"
    a = pd.read_excel(path, sheet_name="Grid GenerationQueue", header=3).dropna(subset=["Queue Position"])
    ids = set(a["Queue Position"].map(norm_id))
    for sh in ("Completed Generation Projects", "Withdrawn Generation Projects"):
        raw = pd.read_excel(path, sheet_name=sh, header=None)
        hdr = raw.index[raw.apply(lambda r: r.astype(str).str.contains("Queue Position").any(), axis=1)][0]
        h = pd.read_excel(path, sheet_name=sh, header=hdr)
        ids |= set(h["Queue Position"].dropna().map(norm_id))
    fas = a["Facilities Study (FAS) or \nPhase II Cluster Study"].notna()
    phase = np.where(a["Interconnection Agreement \nStatus"].eq("Executed"), "IA executed",
                     np.where(fas, "Late study", "Early study"))
    df = frame("CAISO", queue_id=a["Queue Position"].map(norm_id), project_name=a["Project Name"],
               poi_name=a["Station or Transmission Line"], state=a["State"], county=a["County"],
               entity=a["Utility"], mw=pd.to_numeric(a["Net MWs to Grid"], errors="coerce"),
               type=[std_type(*r) for r in a[["Type-1", "Type-2", "Type-3", "Fuel-1", "Fuel-2"]].values],
               status_raw=a["Application Status"], phase=phase,
               q_date=pd.to_datetime(a["Queue Date"], errors="coerce"),
               cod_year=a["Current\nOn-line Date"].map(year_of))
    return df, ids


def miso():
    m = pd.DataFrame(json.load(open(Q / "miso_queue.json", encoding="utf-8")))
    ids = set(m.projectNumber.map(norm_id))
    a = m[m.applicationStatus.isin(["Active", "Pending Revision Approval", "Pending Transfer"])]
    mw = a[["summerNetMW", "winterNetMW", "dp1ErisMw", "dp1NrisMw", "dp2ErisMw", "dp2NrisMw"]].max(axis=1)
    gia = a.postGIAStatus.isin(["Not Started", "Under Construction"])
    phase = np.select([a.postGIAStatus.eq("Under Construction"), gia,
                       a.studyPhase.isin(["Phase 2", "Phase 3"])],
                      ["Construction", "IA executed", "Late study"], "Early study")
    df = frame("MISO", queue_id=a.projectNumber.map(norm_id), project_name=a.projectNumber,
               poi_name=a.poiName.replace("", np.nan), state=a.state.replace("", np.nan),
               county=a.county.replace("", np.nan), entity=a.transmissionOwner, mw=mw,
               type=[std_type(f, t) for f, t in zip(a.fuelType, a.facilityType)],
               status_raw=a.applicationStatus + " / " + a.studyPhase + " / " + a.studyCycle,
               phase=phase, q_date=pd.to_datetime(a.queueDate, errors="coerce", utc=True).dt.tz_localize(None),
               cod_year=a.inService.map(year_of))
    return df, ids


def spp():
    s = pd.read_csv(Q / "spp_active_queue.csv", skiprows=1)
    s = s[~s.Status.str.contains("COMMERCIAL OPERATION", na=False)]
    st = s.Status.fillna("")
    phase = np.select([st.str.startswith("IA FULLY EXECUTED"),
                       st.isin(["IA PENDING", "FACILITY STUDY STAGE"])],
                      ["IA executed", "Late study"], "Early study")
    mw = pd.concat([pd.to_numeric(s["Capacity"], errors="coerce"),
                    pd.to_numeric(s["Requested Maximum Injection Capability (MW)"], errors="coerce")],
                   axis=1).max(axis=1)
    df = frame("SPP", queue_id=s["Generation Interconnection Number"].map(norm_id),
               project_name=s["Generation Interconnection Number"], poi_name=s["Substation or Line"],
               state=s["State"], county=s[" Nearest Town or County"], entity=s["TO at POI"], mw=mw,
               type=[std_type(g, f) for g, f in zip(s["Generation Type"], s["Fuel Type"])],
               status_raw=s.Status, phase=phase,
               q_date=pd.to_datetime(s["Request Received"], errors="coerce"),
               cod_year=s["Commercial Operation Date"].map(year_of))
    return df, set()  # active-only file: no backfill from LBNL


def nyiso():
    path = Q / "nyiso_queue.xlsx"
    xl = pd.ExcelFile(path)
    parts, ids = [], set()
    for sh in xl.sheet_names:
        if sh.strip() in ("NYISO Zonal Map", "Load Projects", "Load Project Tracking"):
            continue
        d = xl.parse(sh)
        if "Queue Pos." not in d:
            continue
        ids |= set(d["Queue Pos."].dropna().map(norm_id))
        if sh.strip() in ("Interconnection Queue", "Cluster Projects"):
            parts.append(d)
    a = pd.concat(parts, ignore_index=True).dropna(subset=["Queue Pos."])
    code = pd.to_numeric(a["Project Status #"], errors="coerce")
    a = a[(code > 0) & (code != 14)]
    code = pd.to_numeric(a["Project Status #"], errors="coerce")
    phase = np.select([code.isin([12, 13, 15]), code.isin([10, 11]), code.isin([7, 8, 9])],
                      ["Construction", "IA executed", "Late study"], "Early study")
    df = frame("NYISO", queue_id=a["Queue Pos."].map(norm_id), project_name=a["Project Name"],
               poi_name=a["Points of Interconnection"], state=a["State"], county=a["County"],
               entity=a["Utility"], mw=pd.to_numeric(a["SP (MW)"], errors="coerce"),
               type=[std_type(t) for t in a["Type/ Fuel"]], status_raw=code.astype("Int64").astype(str),
               phase=phase, q_date=pd.to_datetime(a["Date of IR"], errors="coerce"),
               cod_year=a["Proposed COD"].map(year_of))
    return df, ids


def pjm():
    p = pd.read_excel(Q / "pjm_queue.xlsx")
    p = p[p["Project Type"].eq("Generation Interconnection")]
    ids = set(p["Project ID"].map(norm_id))
    active = {"Active": "Early study", "Confirmed": "Late study",
              "Engineering and Procurement": "IA executed", "Suspended": "IA executed",
              "Under Construction": "Construction",
              "Partially in Service - Under Construction": "Construction"}
    a = p[p.Status.isin(active)]
    df = frame("PJM", queue_id=a["Project ID"].map(norm_id), project_name=a["Commercial Name"].fillna(a["Name"]),
               poi_name=a["Name"], state=a["State"], county=a["County"].replace("Unknown", np.nan),
               entity=a["Transmission Owner"],
               mw=pd.to_numeric(a["MFO"], errors="coerce").fillna(pd.to_numeric(a["MW Energy"], errors="coerce")),
               type=[std_type(f) for f in a.Fuel], status_raw=a.Status, phase=a.Status.map(active),
               q_date=pd.to_datetime(a["Submitted Date"], errors="coerce"),
               cod_year=a["Projected In Service Date"].map(year_of))
    return df, ids


def ercot():
    e = pd.read_excel(Q / "ercot_gis_report.xlsx", sheet_name="Project Details - Large Gen", header=30)
    e = e[e["INR"].astype(str).str.contains("INR", na=False)]
    e = e[e["Approved for Synchronization"].isna()]  # synchronized units are counted in EIA-860M
    real = lambda c: pd.to_datetime(e[c], errors="coerce").dt.year.gt(1900)  # noqa: E731  (1900 = n/a)
    phase = np.select([real("Construction Start"), real("IA Signed"), real("FIS Requested")],
                      ["Construction", "IA executed", "Late study"], "Early study")
    df = frame("ERCOT", queue_id=e["INR"].map(norm_id), project_name=e["Project Name"],
               poi_name=e["POI Location"], state="TX", county=e["County"],
               entity=e["Interconnecting Entity"], mw=pd.to_numeric(e["Capacity (MW)"], errors="coerce"),
               type=[std_type(f, t) for f, t in zip(e.Fuel, e.Technology)],
               status_raw=e["GIM Study Phase"], phase=phase, q_date=pd.NaT,
               cod_year=e["Projected COD"].map(year_of))
    return df, set()


def isone():
    t = pd.read_html(Q / "isone_queue.html")[0]
    ids = set(t.QP.map(norm_id))
    a = t[(t.Status == "A") & (t.Type == "G")]
    df = frame("ISO-NE", queue_id=a.QP.map(norm_id), project_name=a["Alternative Name"], poi_name=a.POI,
               state=a.ST, county=a.County, entity=a.POI.str.extract(r"^([^0-9]+?)\s+\d", expand=False),
               mw=a[["Net MW", "Summer MW", "Winter MW"]].max(axis=1),
               type=[std_type(f, u) for f, u in zip(a["Fuel Type"], a.Unit)],
               status_raw=a.Cluster.astype(str), phase="Early study",
               q_date=pd.to_datetime(a.Requested, errors="coerce"), cod_year=a["Op Date"].map(year_of))
    return df, ids


def lbnl():
    lb = pd.read_parquet(INTERIM / "lbnl_queue_raw.parquet") if (INTERIM / "lbnl_queue_raw.parquet").exists() \
        else pd.read_excel(Q / "lbnl_queued_up_thru2025.xlsx", sheet_name="03. Complete Queue Data", header=1)
    a = lb[lb.q_status.eq("active")].copy()
    m = {"IA Executed": "IA executed", "Construction": "Construction", "Facility Study": "Late study",
         "IA Pending": "Late study", "System Impact Study": "Late study"}
    return frame(a.region.astype(str), queue_id=a.q_id.map(norm_id), project_name=a.project_name,
                 poi_name=a.poi_name, state=a.state, county=a.county, entity=a.entity.astype(str),
                 mw=pd.to_numeric(a.mw_1, errors="coerce"), type=a.type_clean.astype(str),
                 status_raw=a.IA_phase_clean, phase=a.IA_phase_clean.map(m).fillna("Early study"),
                 q_date=pd.to_datetime(a.q_date, errors="coerce"),
                 cod_year=pd.to_numeric(a.prop_year, errors="coerce")).assign(
        fips=pd.to_numeric(a.fips_code, errors="coerce").values, utility=a.utility.values)


def main():
    readers = {"CAISO": caiso, "MISO": miso, "SPP": spp, "NYISO": nyiso, "PJM": pjm,
               "ERCOT": ercot, "ISO-NE": isone}
    lb = lbnl()
    out = []
    for iso, fn in readers.items():
        df, hist = fn()
        df["source"] = f"{iso} live queue (Oct 2026)"
        out.append(df)
        if hist:
            extra = lb[(lb.iso == iso) & ~lb.queue_id.isin(hist)].copy()
            extra["source"] = "LBNL Queued Up (end-2025) backfill"
            out.append(extra)
            log(f"{iso}: {len(df)} live active + {len(extra)} LBNL backfill")
        else:
            log(f"{iso}: {len(df)} live active")
    rest = lb[lb.iso.isin(["West", "Southeast"])].copy()
    rest["source"] = "LBNL Queued Up (end-2025)"
    out.append(rest)
    log(f"West/Southeast (non-ISO): {len(rest)} from LBNL")

    q = pd.concat(out, ignore_index=True)
    q = q[q.mw.fillna(0) > 0]
    q["poi_kv"] = q.poi_name.map(kv_from)
    q["state"] = q.state.astype("string").str.strip().str.upper().str[:2]
    for c in ("project_name", "poi_name", "county", "entity", "status_raw", "queue_id", "type", "utility"):
        q[c] = q[c].astype("string")
    q["uid"] = q.iso + ":" + q.queue_id
    q = q.drop_duplicates("uid")
    q.to_parquet(INTERIM / "queue_projects.parquet", index=False)
    log(f"total active projects: {len(q):,}  ({q.mw.sum() / 1e3:,.0f} GW)")
    print(q.groupby("iso").agg(n=("uid", "size"), gw=("mw", lambda x: round(x.sum() / 1e3, 1))).to_string())
    print(q.groupby("phase").mw.sum().div(1e3).round(1).to_string())
    print(q.type.value_counts().head(12).to_string())


if __name__ == "__main__":
    main()
