# Project handoff: Interconnection Headroom Atlas

MVP for a UC Berkeley *Sustainable Ventures* startup idea: a national map of where new generation or
large load (data centers) can connect to the US transmission grid, given the interconnection queue
ahead. Product thesis: `context.md`. Market analysis (TAM/SAM/SOM, competitors, positioning):
`Market_Analysis.md`. Full build history: `docs/claude-session-transcript.txt`.

## Layout

- `pipeline/`: data pipeline, run in order by `run_all.py`
  1. `fetch_data.py`: downloads all public sources into `data/raw/` (logged in `data/raw/manifest.json`)
  2. `normalize_queues.py`: 7 ISO queue formats + LBNL into one active-project table
  3. `prepare_eia.py`: EIA-860M/860 fleet by technology, plant grid voltage, NERC region
  4. `build_network.py`: buses from HIFLD line endpoints + OSM names; line parameters; interconnection seam cuts
  5. `match_queue.py`: queue requests to buses (fuzzy POI name, else county proximity)
  6. `build_model.py`: load, dispatch, DC power flow, PTDF headroom, queue-loaded case
  7. `export_map.py`: compact JSON for the web map
  8. `claims.py`: recomputes every quantitative claim (C1–C16) with sources and steps into
     `map/data/claims.json`, shown in the map's "Claims & calculations" panel. Rule: any new number
     we cite must be added here, not computed ad hoc in the terminal.
- `map/index.html` + `map/data/*.json`: MapLibre web map (serve over HTTP: `python -m http.server -d map`)
- `data/`: git-ignored, re-create with `python -I pipeline/run_all.py --fetch` (~1 h, OSM is slowest)

## Environment notes (Windows)

- Python 3.14 venv: `python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt`
- Run scripts with `python -I` (isolated mode); scripts add their own folder to `sys.path`.
- `gridstatus` does not install on Python 3.14 (old lxml pin), so ISO queues are fetched directly.
- `curl` on this machine needs `--ssl-no-revoke`; Python `requests` is fine.
- Overpass: `overpass-api.de` is overloaded or rejects requests; use `overpass.private.coffee` or the mail.ru mirror.
  `fetch_data.py osm_substations --reverse` runs a second worker against the other mirror.
- HIFLD substations are no longer public. Lines come from HIFLD's ArcGIS FeatureServer
  (`services1.arcgis.com/Hp6G80Pky0om7QvQ`); the EIA balancing-authority layer needs a token, so a
  public mirror of HIFLD Control Areas is used.
- The PJM queue export uses the public API key embedded in pjm.com.

## Key modelling decisions (and why)

- **Buses from line endpoints.** Endpoints within 300 m are one bus, also merged within 500 m of the
  same OSM transmission substation. HIFLD `SUB_1`/`SUB_2` carry restricted substation IDs (`UNKNOWN123`, `TAP123`).
- **Interconnection seams are cut.** Snapping fuses the AC lines on both sides of DC ties into one
  network. Buses are labelled Eastern, Western or ERCOT by balancing-authority polygon (coded by the
  majority NERC region of EIA plants inside), and the ~219 lines that cross a seam are removed.
- **Ratings by voltage class**, capped by the St. Clair curve. Where modelled flows exceed them,
  ratings are raised so existing flows use 90% of the usable limit (~7–21% of lines per interconnection).
- **Headroom** = MW addable before a line with |DF| ≥ 20% (ERIS-style) hits 85% of its rating.
  Injections are absorbed by non-nuclear output in the bus's own balancing authority, and capped by
  the substation's N-1 outlet capacity. Counterflow from queued projects is never credited. A 5% DF
  deliverability figure is reported alongside.
  - Earlier attempts failed: 5% DF with an interconnection-wide sink made ~90% of buses read zero
    after the queue, and one TVA 500 kV line bound 7,800 buses.
- **Queue weights** (probability of reaching operation): early study 0.10, late study 0.25,
  IA executed 0.55, construction 0.90. Historically 20% of 2000–2019 requests reached operation.
- **Signals:** green ≥ 250 MW after queue, yellow 75–250, red < 75 or queue > 1.5× N-1 branch capacity.

## Known limitations

No real ratings, transformer or breaker-bay data, or N-1 contingency analysis (needs CEII base
cases). Big 500 kV hubs read at the 3,000 MW cap. Load follows population (misses data-center
clusters). ~51% of queue requests are placed by county, not POI name. Lines < 100 kV are not in HIFLD.

## Current state (2026-10-22)

- National model built: ~29k modelled buses, ~41k lines, 7,323 active queue requests (1,686 GW).
- Published map (Claude artifact, shared with anyone who has the link): https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq
- Business conclusion so far: a pure data/map product is a ~$100M/yr US market with funded competitors
  (Nira, LandGate/Wood Mackenzie, Paces, Enverus PRISM). The stronger angle is turning red constraints
  into reconductoring or storage projects, plus queue-risk audits for investors and off-takers.

## Possible next steps

- Dedicated queue view: project-level points, filters (ISO, phase, fuel, COD), years in queue,
  withdrawal churn from LBNL history.
- "Reconductoring" lens ranking heat-limited constraint lines by queued MW behind them.
- Validate headroom against published ISO heatmaps / cluster-study results for 20–30 substations.
- Customer interviews per `Market_Analysis.md` section 6.
