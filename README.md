# Interconnection Headroom Atlas (MVP)

A screening-level digital twin of the contiguous-US transmission grid, built only from public data.
For every substation it estimates how many MW of new **generation** (or new **large load**) can
connect before a transmission line hits its limit, both on today's grid and after the active
interconnection queue ahead of you. For a project of a given size it also estimates the
**interconnection risk**: expected cost, time to operation, the queue ahead and its dropout odds,
combined into a grade A–E. See `context.md` for the product thesis.

> Screening estimates, not interconnection studies. Real ratings, contingencies and breaker
> positions sit in CEII-protected base cases. See *Limitations*.

## Live map

- **GitHub Pages:** https://befan-music.github.io/interconnection-queue-map/. It is rebuilt by
  `.github/workflows/pages.yml` on every push that changes `map/`. One-time setup: Settings → Pages →
  Source: *GitHub Actions*.
- **Claude artifact:** https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq
- Every figure we cite is recomputed in the map's **Claims & calculations** panel; link to one with `#C1`…`#C23`.

## Quick start

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt
.venv/Scripts/python -I pipeline/run_all.py --fetch   # download everything + build (≈1 h, OSM is slow)
.venv/Scripts/python -I pipeline/run_all.py           # rebuild from data/raw only
.venv/Scripts/python -I pipeline/serve_map.py        # then open http://localhost:8000
```

The map has to be served over HTTP, because browsers block `fetch()` from `file://`. `serve_map.py` wraps
`map/index.html` (written in the Claude artifact format, without a document skeleton) exactly as the
GitHub Pages workflow does, into `_site/`, and serves that.

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
| Interconnection costs | LBNL *Generator Interconnection Costs to the Transmission System* (MISO, PJM, SPP, NYISO, ISO-NE, 5 non-ISO BAs) | studies to 2024 | 4,713 studied projects: POI, network and total $/kW |
| Unit costs | MISO Transmission Cost Estimation Guide for MTEP24 | 2024 $ | substation positions, reconductoring and new lines per mile |

Every downloaded file is logged with URL, retrieval time and SHA-256 in `data/raw/manifest.json`.

## Pipeline

| Step | Script | Output |
|---|---|---|
| 1 | `fetch_data.py` | `data/raw/**` |
| 2 | `normalize_queues.py`: 7 ISO formats + LBNL into one schema, phase-standardized | `data/interim/queue_projects.parquet` |
| 2b | `queue_stats.py`: completion odds by region and stage; durations to agreement and operation | `data/processed/queue_stats.json` |
| 3 | `prepare_eia.py`: fleet by tech, plant grid voltage, NERC region | `data/interim/{generators,plants}.parquet` |
| 4 | `build_network.py`: snap line ends into buses, name them, assign line parameters, cut interconnection seams | `data/interim/{nodes,lines}.parquet` |
| 5 | `match_queue.py`: POI-name fuzzy matching within 150 km of the project county, voltage-aware; county fallback. Also places the full LBNL history | `data/interim/{queue,lbnl}_located.parquet` |
| 5b | `cost_data.py`: LBNL cost studies in one table (2024 $), placed on buses | `data/interim/cost_projects.parquet` |
| 6 | `build_model.py`: load, dispatch, DC power flow, PTDF headroom, queue-loaded case, five tightest constraints per bus | `data/processed/*.parquet`, `model_summary.json` |
| 6b | `risk_model.py`: cost, time, queue-ahead simulation and risk index per bus and project size | `data/processed/risk.parquet`, `risk_meta.json` |
| 6c | `project_model.py`: project risk score (chance to reach operation) from LBNL outcomes, cost studies, EIA-860M permits, NREL ordinances | `map/data/{score,comps}.json` |
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
7. **Queue ahead.** Active requests are weighted by their odds of reaching operation, estimated from
   what happened to 18,000 requests filed 2000–2019, by region and stage (`queue_stats.py`; nationally
   22% before an interconnection agreement, 60% after, 90% under construction; claim C17). They are also
   weighted by scenario output and capped at the output each BA can actually displace. They are added to the base case and headroom is recomputed.
   Counterflow from queued projects is not credited, so headroom after the queue is never above
   today's. That gives *headroom after queue*, the map's main signal.
8. **Substation capacity check** (the scoring rule from `context.md`). N-1 branch capacity is the sum of
   incident line limits minus the largest. Queue pressure is queued MW divided by that capacity, red
   above 1.5× and green below 0.5×.

Signals: **green** ≥ 250 MW after queue · **yellow** 75–250 MW · **red** < 75 MW or queue pressure > 1.5×.
**Storage candidates**: < 100 MW generation headroom with ≥ 150 MW of solar/wind behind the bus.

## Risk assessment (per substation and project size: 50 to 1,000 MW)

- **Cost ($2024/kW)** = substation connection (MISO MTEP24 cost of a breaker-and-a-half position at the bus
  voltage) + network upgrades. Network upgrades = the regional median from LBNL cost studies since 2018
  × a multiplier for project size ÷ modelled headroom × a voltage multiplier × a local index from studies
  within ~80 km (shrunk towards 1, bounded 0.3–3×). The multipliers are fitted on the same studies; projects
  at substations the map colours red were assigned about 1.8× the network cost of green ones (claim C20).
  ERCOT: network upgrades are funded by transmission utilities, so only the connection counts. CAISO has no
  LBNL cost dataset (national median used).
- **Time** = regional median years from request to operation (projects online 2018–2025) × a multiplier for
  the expected network-cost tier. Projects with over $150/kW of network upgrades took ~1.6× longer (C21).
- **Queue ahead** = every active request at the substation simulated 10,000 times with its regional,
  stage-specific odds: expected MW built (P10–P90), expected withdrawals, chance at least one withdraws
  (restudy trigger), plus the substation's track record from the located LBNL history.
- **Capacity fit** = chance the project still fits once the queue resolves, allowing a log-normal error
  (σ = 0.5) on the modelled headroom.
- **Lines to upgrade** = which of the five tightest constraints a project of this size would overload,
  priced with MISO reconductor / new-line costs and split with queued projects that also overload them.
- **Risk index** = 100 − (35% cost score + 30% time score + 35% capacity fit); grades A < 25 … E ≥ 70.
  Anchors: cost 100 at $25/kW, 0 at $400/kW; time 100 at 2.5 years,
  0 at 8 years (judgement calls, claim C22).

## Project risk score ("Score my project")

The atlas opens as a three-step flow. **1 Project:** the developer describes their project (technology,
size, stage, queue date, target date, study cost, permits, local opposition, offtake, parallel filings).
**2 Location:** they search for or click their substation; the map is coloured by risk grade for that
project size. **3 Score:** the page returns
the chance of reaching operation, the chance of being online by the target year, the expected online year,
network-upgrade cost, the drivers in percentage points, and comparable past projects. It exports to PDF.

- **Public attributes** (C24): logistic regression on 19,761 LBNL requests filed 2000–2019 (active ones
  weighted by their stage odds). AUC 0.67 vs 0.58 for region alone; the lowest-rated tenth was 9% built,
  the highest 49%. A second fit after an interconnection agreement scores later stages.
- **Study cost** (C25): 63% of requests assigned < $10/kW of network upgrades were built, 3% of those > $400/kW.
- **Permits** (C26): EIA-860M planned units, December 2021 → today: 32% built with approvals not started,
  49% with approvals received.
- **Local opposition, offtake, construction** (C27): team assumptions, marked * on the page.
- **Time**: comparable built projects (same region, technology, ±2× size), conditioned on years already in the queue.

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
