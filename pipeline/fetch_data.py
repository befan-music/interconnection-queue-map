"""Download every public source used by the interconnection model into data/raw/.

Usage:  python -I pipeline/fetch_data.py [source ...]      (no args = all sources)
Every file is logged with URL, timestamp and sha256 in data/raw/manifest.json.
"""
import re
import sys
import time
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW, SESSION, arcgis_layer_to_geojson, download, get, log, record  # noqa: E402

HIFLD = "https://services1.arcgis.com/Hp6G80Pky0om7QvQ/arcgis/rest/services"
CONTROL_AREAS = ("https://services5.arcgis.com/HDRa0B57OVrv2E1q/ArcGIS/rest/services/"
                 "Control_Areas/FeatureServer/0")

US_STATES = ["AL", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "ID", "IL", "IN", "IA",
             "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH",
             "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX",
             "UT", "VT", "VA", "WA", "WV", "WI", "WY"]  # contiguous US (the three interconnections)


# --- Physical grid -----------------------------------------------------------------------

def hifld_lines():
    """HIFLD Electric Power Transmission Lines (69 kV-765 kV, with endpoint substation names)."""
    arcgis_layer_to_geojson("HIFLD Electric Power Transmission Lines",
                            f"{HIFLD}/Electric_Power_Transmission_Lines/FeatureServer/0",
                            RAW / "hifld" / "transmission_lines.geojson")


def blm_solar():
    """BLM lands open / closed to utility-scale solar application (siting overlay)."""
    for layer in ("BLM_lands_open_to_solar_application", "Not_open_to_solar_application"):
        arcgis_layer_to_geojson(f"BLM via HIFLD: {layer}", f"{HIFLD}/{layer}/FeatureServer/0",
                                RAW / "blm" / f"{layer}.geojson", max_offset=0.002)


def control_areas():
    """HIFLD Control Areas (balancing authorities) with peak load and capacity attributes."""
    arcgis_layer_to_geojson("HIFLD Control Areas (mirror)", CONTROL_AREAS,
                            RAW / "hifld" / "control_areas.geojson", max_offset=0.01)


def osm_substations():
    """OpenStreetMap power=substation features (name, voltage, operator), one query per state."""
    out_dir = RAW / "osm"
    out_dir.mkdir(parents=True, exist_ok=True)
    endpoints = ["https://overpass.private.coffee/api/interpreter",
                 "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
                 "https://overpass-api.de/api/interpreter"]
    states = US_STATES
    if "--reverse" in sys.argv:  # second worker: walk the list backwards against the other mirror
        states, endpoints = US_STATES[::-1], endpoints[1:] + endpoints[:1]
    for st in states:
        dest = out_dir / f"substations_US-{st}.json"
        if dest.exists() and dest.stat().st_size > 1000:
            continue
        q = (f'[out:json][timeout:600];area["ISO3166-2"="US-{st}"][admin_level=4]->.a;'
             f'(node["power"="substation"](area.a);way["power"="substation"](area.a);'
             f'relation["power"="substation"](area.a););out center tags;')
        for attempt in range(6):
            url = endpoints[attempt % len(endpoints)]
            try:
                r = SESSION.post(url, data={"data": q}, timeout=900,
                                 headers={"User-Agent": "InterconnectionMapMVP/0.1 (research)"})
                if r.status_code == 200 and r.text.lstrip().startswith("{"):
                    dest.write_bytes(r.content)
                    record("OpenStreetMap via Overpass API", dest, url, f"power=substation US-{st}")
                    log(f"  OSM {st}: {len(r.json()['elements'])} substations")
                    break
                log(f"  OSM {st}: HTTP {r.status_code} from {url}, retrying")
            except Exception as e:  # noqa: BLE001
                log(f"  OSM {st}: {e}")
            time.sleep(15 * (attempt + 1))
        else:
            log(f"  OSM {st}: FAILED")
        time.sleep(2)


# --- Generation fleet --------------------------------------------------------------------

def eia860():
    """EIA-860 annual (final 2025): plants with lat/lon & grid voltage, generators, proposed units."""
    page = get("https://www.eia.gov/electricity/data/eia860/").text
    rel = re.findall(r'href="(xls/eia860\d{4}\.zip)"', page)[0]
    url = f"https://www.eia.gov/electricity/data/eia860/{rel}"
    dest = download("EIA-860", url, RAW / "eia860" / Path(rel).name)
    with zipfile.ZipFile(dest) as z:
        z.extractall(RAW / "eia860" / dest.stem)


def eia860m():
    """EIA-860M: most recent monthly generator inventory (operating / planned / retired)."""
    months = ["january", "february", "march", "april", "may", "june", "july", "august",
              "september", "october", "november", "december"]
    today = date.today()
    for back in range(0, 8):
        m = (today.month - 1 - back) % 12
        y = today.year - (1 if today.month - 1 - back < 0 else 0)
        for sub in ("xls", "archive/xls"):
            url = f"https://www.eia.gov/electricity/data/eia860m/{sub}/{months[m]}_generator{y}.xlsx"
            r = SESSION.get(url, timeout=180)
            if r.status_code == 200 and r.content[:2] == b"PK":
                dest = RAW / "eia860m" / Path(url).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(r.content)
                record("EIA-860M", dest, url)
                log(f"  saved {dest.name}")
                return


# --- Load ------------------------------------------------------------------------------

def eia930():
    """EIA-930 hourly balancing-authority demand (Jul-Dec of the current year: summer peak)."""
    base = "https://www.eia.gov/electricity/gridmonitor/sixMonthFiles"
    download("EIA-930", f"{base}/EIA930_BALANCE_{date.today().year}_Jul_Dec.csv",
             RAW / "eia930" / "EIA930_BALANCE_Jul_Dec.csv")


# --- Interconnection queues --------------------------------------------------------------

def lbnl_queue():
    """LBNL Queued Up 2026 edition: standardized national queue data through end of 2025."""
    download("LBNL Queued Up (2026 ed.)",
             "https://emp.lbl.gov/sites/default/files/2026-05/LBNL_Ix_Queue_Data_File_thru2025.xlsx",
             RAW / "queues" / "lbnl_queued_up_thru2025.xlsx", note="CC BY 4.0, LBNL & GridTracker")


def caiso_queue():
    download("CAISO Public Queue Report",
             "https://www.caiso.com/PublishedDocuments/PublicQueueReport.xlsx",
             RAW / "queues" / "caiso_public_queue.xlsx")


def miso_queue():
    download("MISO GI Queue API", "https://www.misoenergy.org/api/giqueue/getprojects",
             RAW / "queues" / "miso_queue.json")


def spp_queue():
    download("SPP GI Active Queue", "https://opsportal.spp.org/Studies/GenerateActiveCSV",
             RAW / "queues" / "spp_active_queue.csv")


def nyiso_queue():
    download("NYISO Interconnection Queue",
             "https://www.nyiso.com/documents/20142/1407078/NYISO-Interconnection-Queue.xlsx",
             RAW / "queues" / "nyiso_queue.xlsx")


def pjm_queue():
    download("PJM Planning API queue export",
             "https://services.pjm.com/PJMPlanningApi/api/Queue/ExportToXls",
             RAW / "queues" / "pjm_queue.xlsx", method="POST",
             headers={"api-subscription-key": "E29477D0-70E0-4825-89B0-43F460BF9AB4",
                      "Origin": "https://www.pjm.com", "Referer": "https://www.pjm.com/"},
             note="public key embedded in pjm.com queue page")


def ercot_queue():
    """ERCOT monthly Generation Interconnection Status (GIS) report."""
    docs = get("https://www.ercot.com/misapp/servlets/IceDocListJsonWS?reportTypeId=15933").json()
    docs = [d["Document"] for d in docs["ListDocsByRptTypeRes"]["DocumentList"]]
    gis = sorted((d for d in docs if d["FriendlyName"].startswith("GIS_Report")),
                 key=lambda d: d["PublishDate"], reverse=True)[0]
    download("ERCOT GIS Report",
             f"https://www.ercot.com/misdownload/servlets/mirDownload?doclookupId={gis['DocID']}",
             RAW / "queues" / "ercot_gis_report.xlsx", note=gis["FriendlyName"])


def isone_queue():
    """ISO-NE Interconnection Request Tracking Tool public queue (HTML table)."""
    url = "https://irtt.iso-ne.com/reports/external"
    r = get(url)  # session follows the ASP.NET cookie-detection redirect
    dest = RAW / "queues" / "isone_queue.html"
    dest.write_bytes(r.content)
    record("ISO-NE IRTT public queue", dest, url)
    log(f"  saved {dest.name} ({len(r.content) / 1e6:.1f} MB)")


# --- Demographics / boundaries (for load allocation and county geocoding) -----------------

def census():
    base = "https://www2.census.gov/geo/tiger"
    for year in (2024, 2023):
        try:
            for f in (f"cb_{year}_us_county_500k.zip", f"cb_{year}_us_state_20m.zip"):
                download("US Census cartographic boundaries", f"{base}/GENZ{year}/shp/{f}",
                         RAW / "census" / f)
            break
        except Exception as e:  # noqa: BLE001
            log(f"  census {year} failed: {e}")
    download("US Census 2020 tract centers of population",
             "https://www2.census.gov/geo/docs/reference/cenpop2020/tract/CenPop2020_Mean_TR.txt",
             RAW / "census" / "CenPop2020_Mean_TR.txt")


SOURCES = {f.__name__: f for f in (
    hifld_lines, control_areas, blm_solar, eia860, eia860m, eia930, lbnl_queue, caiso_queue,
    miso_queue, spp_queue, nyiso_queue, pjm_queue, ercot_queue, isone_queue, census,
    osm_substations)}


if __name__ == "__main__":
    wanted = [a for a in sys.argv[1:] if not a.startswith("--")] or list(SOURCES)
    failed = []
    for name in wanted:
        log(f"== {name}: {SOURCES[name].__doc__.strip().splitlines()[0] if SOURCES[name].__doc__ else ''}")
        try:
            SOURCES[name]()
        except Exception as e:  # noqa: BLE001
            log(f"  FAILED {name}: {e}")
            failed.append(name)
    log(f"done; failed: {failed or 'none'}")
