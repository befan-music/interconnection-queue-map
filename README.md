# Interconnection Headroom Atlas (MVP)

A screening-level digital twin of the contiguous-US transmission grid, built only from public data.
For every substation it estimates how many MW of new **generation** (or new **large load**) can
connect before a transmission line hits its limit, both on today's grid and after the active
interconnection queue ahead of you. See `context.md` for the product thesis.

> Screening estimates, not interconnection studies. Real ratings, contingencies and breaker
> positions sit in CEII-protected base cases. See *Limitations*.

## Live map

- **GitHub Pages:** https://befan-music.github.io/interconnection-queue-map/. It is rebuilt by
  `.github/workflows/pages.yml` on every push that changes `map/`. One-time setup: Settings → Pages →
  Source: *GitHub Actions*.
- **Claude artifact:** https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq
- Every figure we cite is recomputed in the map's **Claims & calculations** panel; link to one with `#C1`…`#C16`.

## Quick start

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -I pipeline/run_all.py --fetch   # download everything + build (≈1 h, OSM is slow)
.venv/Scripts/python -I pipeline/run_all.py           # rebuild from data/raw only
python -m http.server 8000 -d map                     # then open http://localhost:8000
```

The map has to be served over HTTP, because browsers block `fetch()` from `file://`.

## Data sources (all public)

| Layer | Source | Vintage | What it contributes |
|---|---|---|---|
| Lines | HIFLD Electric Power Transmission Lines (ArcGIS FeatureServer) | 2017–2023 validation dates | 52k segments, 100–765 kV, owner, endpoint substation labels |
| Substations | OpenStreetMap `power=substation` (Overpass API) | live | names, voltages, operators for bus naming and POI matching |
| Generation | EIA-860M (Aug 2026) + EIA-860 2025 annual | 2026 | 26.9k operating generators (1,305 GW) with lat/lon, grid voltage, BA, NERC |
| Load | EIA-930 hourly BA demand (Jul–Oct 2026) | 2026 | summer-2026 peak per balancing authority |
| Load geography | Census 2020 tract centers of population; HIFLD Control Areas | 2020 / 2018 | spreads BA peak to buses; BA polygons |
| Queues (live) | CAISO, MISO, SPP, NYISO, PJM, ERCOT (GIS report), ISO-NE (IRTT) | Oct 2026 | active requests, POI, MW, phase, COD |
| Queues (national) | LBNL *Queued Up* 2026 ed. (CC BY 4.0) | end-2025 | non-ISO West & Southeast, plus backfill (e.g. CAISO Cluster 15) |
| Siting | BLM lands open to solar application (HIFLD mirror) | 2023 | overlay |
| Boundaries | Census cartographic boundaries 2024 | 2024 | states, counties (queue geocoding) |

Every downloaded file is logged with URL, retrieval time and SHA-256 in `data/raw/manifest.json`.

## Pipeline

| Step | Script | Output |
|---|---|---|
| 1 | `fetch_data.py` | `data/raw/**` |
| 2 | `normalize_queues.py`: 7 ISO formats + LBNL into one schema, phase-standardized | `data/interim/queue_projects.parquet` |
| 3 | `prepare_eia.py`: fleet by tech, plant grid voltage, NERC region | `data/interim/{generators,plants}.parquet` |
| 4 | `build_network.py`: snap line ends into buses, name them, assign line parameters, cut interconnection seams | `data/interim/{nodes,lines}.parquet` |
| 5 | `match_queue.py`: POI-name fuzzy matching within 150 km of the project county, voltage-aware; county fallback | `data/interim/queue_located.parquet` |
| 6 | `build_model.py`: load, dispatch, DC power flow, PTDF headroom, queue-loaded case | `data/processed/*.parquet`, `model_summary.json` |
| 7 | `export_map.py`: compact JSON for the browser | `map/data/*.json` |
| 8 | `claims.py`: recomputes every number cited in our documents, with sources and steps | `map/data/claims.json` (atlas panel *Claims & calculations*) |

## Model

1. **Topology.** HIFLD line endpoints within 300 m become one bus (also merged when they fall within
   500 m of the same OSM transmission substation). The interconnections only meet through DC ties,
   but snapping fuses the AC lines on both sides of each converter. Each bus is therefore labelled
   Eastern/Western/ERCOT, using the BA polygon it sits in (that polygon's interconnection is the
   majority NERC region of the EIA plants inside it) or its nearest plants, and lines that cross a
   seam are cut.
2. **Line parameters by voltage class.** Typical reactance (Ω/km), thermal rating (A) and surge
   impedance loading: 115 kV ≈ 160 MVA, 230 kV ≈ 480 MVA, 345 kV ≈ 1,200 MVA, 500 kV ≈ 2,600 MVA,
   765 kV ≈ 4,600 MVA. Long lines are capped by the St. Clair curve (3.0×SIL at 50 mi … 1.0×SIL at 300 mi).
3. **Load.** EIA-930 summer-2026 peak per BA (3rd-highest hour), spread by Census tract population and
   attached to the nearest bus.
4. **Dispatch.** Two scenarios per interconnection. Each bus belongs to a balancing authority (BA polygon
   coded by the EIA plants inside it), and each BA meets its own load first before the
   interconnection-wide residual is settled. *Summer peak*: renewables at typical evening-peak
   output, and thermal scaled to balance. *Light load* (55% of peak): solar 85%, wind 45%, thermal at a
   15% floor, renewables curtailed if needed.
5. **DC power flow** per interconnection (sparse LU). Where the approximated ratings cannot carry the
   modeled existing flow, the rating is raised so existing flow uses 90% of the usable limit. This
   assumes today's grid is operated within limits, and the share of lines adjusted is reported.
6. **Headroom (PTDF).** For each bus, compute the distribution factors of a 1 MW injection there,
   absorbed pro rata by the non-nuclear output of the bus's own BA or ISO, as ISO studies do. Small
   BAs fall back to an interconnection-wide sink. An earlier run with one interconnection-wide sink
   forced unrealistic long-distance transfers (one TVA 500 kV line bound 7,800 buses). Headroom is the MW that can be added before any
   line with |DF| ≥ 20% reaches 85% of its rating. The 20% matches the energy-only (ERIS) cut-off
   MISO and SPP use, and the 85% stands in for an N-1 margin. Generation headroom takes the worse of
   the two scenarios, and load headroom is the same with withdrawal at peak. A stricter
   deliverability-style figure (|DF| ≥ 5%, as in PJM/NRIS studies) is reported alongside. An early
   run that used 5% for everything pushed 89% of buses to zero once the queue was added, because
   distant backbone lines dominated.
   **Substation outlet limit.** Injection at a bus is also capped at 85% of its N-1 branch capacity
   less what it already exports. Without it, big 500 kV hubs where no line carries 20% read as
   unlimited.
7. **Queue ahead.** Active requests are weighted by their odds of reaching operation (early study 0.10,
   late study 0.25, IA executed 0.55, construction 0.90) and by scenario output, capped at the output
   each BA can actually displace. They are added to the base case and headroom is recomputed.
   Counterflow from queued projects is not credited, so headroom after the queue is never above
   today's. That gives *headroom after queue*, the map's main signal.
8. **Substation capacity check** (the scoring rule from `context.md`). N-1 branch capacity is the sum of
   incident line limits minus the largest. Queue pressure is queued MW divided by that capacity, red
   above 1.5× and green below 0.5×.

Signals: **green** ≥ 250 MW after queue · **yellow** 75–250 MW · **red** < 75 MW or queue pressure > 1.5×.
**Storage candidates**: < 100 MW generation headroom with ≥ 150 MW of solar/wind behind the bus.

## Limitations (what public data cannot give)

- No real line ratings, transformer or breaker-bay data, and no N-1 contingency analysis. This needs
  ISO base cases (CEII/NDA) or each provider's FERC Order 2023 heatmap. As a result, big 500 kV hubs
  (Vincent, Gates, Mead, Palo Verde…) read at the 3,000 MW display cap, even though in reality
  transformer banks, contingencies and deliverability limit them.
- HIFLD omits lines below 100 kV and some newer lines. Its geometry is 2017–2023 vintage.
- Topology comes from geometry, so taps and switching arrangements are approximated, and ~4.5% of buses
  sit on fragments not connected to the main network (structural estimate only).
- Load follows population, so data-center and industrial clusters are under-represented.
- Queue projects whose POI name cannot be matched are placed by county proximity, which is reliable
  in aggregate but can be wrong for an individual project.
- Planned transmission upgrades and assigned network-upgrade costs are not included.

## Next data to add

FERC Order 2023 heatmaps (per transmission provider), utility hosting-capacity / ICA maps (PG&E,
SCE, SDG&E, NY utilities…), CAISO TPP transmission capability estimates by zone, nodal LMP congestion
components (EIA-860 already carries each plant's LMP node), and ISO cluster-study upgrade costs.
