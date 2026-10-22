# Interconnection Headroom Atlas: Market Analysis

*Sustainable Ventures, UC Berkeley · Draft, October 2026*

Figures tagged [[C1]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C1)–[[C16]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C16) are our own calculations. Each one is recomputed from the source files by `pipeline/claims.py` and shown, with its sources, filters, steps and result table, in the atlas's **Claims & calculations** panel. Each tag links straight to its claim. Anyone with the atlas link can open it.

| Section | What it covers |
|---|---|
| [1. Problem and target customer](#1-problem-and-target-customer) | What we solve, who experiences it, who would pay |
| [2. Market sizing](#2-market-sizing) | Customer counts, spend, TAM / SAM / SOM |
| [3. Competitive landscape](#3-competitive-landscape) | Direct competitors, alternatives, what customers do today |
| [4. Competitor comparison](#4-competitor-comparison) | Customers, capabilities, pricing, strengths, weaknesses, traction |
| [5. Our positioning](#5-our-positioning) | The need we serve better, and why customers would switch |
| [6. Sources and assumptions](#6-sources-and-assumptions) | Evidence, uncertainties, what interviews must validate |

---

## 1. Problem and target customer

### What problem do we solve?

US interconnection queues have become the main bottleneck for new power plants and for large new loads such as AI data centers.

- **Size of the backlog.** At the end of 2025 about 8,200 projects were waiting in US queues: 1,312 GW of generation plus 749 GW of storage (LBNL *Queued Up*, 2026 edition; the underlying data file marks 8,513 requests as active [[C2]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C2)). Our own merge of the seven ISO/RTO queues (Oct 2026) with LBNL data for non-ISO utilities counts 7,323 active requests totalling 1,686 GW [[C1]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C1).
- **Most requests never get built.** Of the requests filed between 2000 and 2019, only **20%** reached commercial operation and **71% withdrew**. By capacity, only **14%** of the requested MW was built [[C3]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C3).
- **Siting is blind.** Developers pick land first and learn years later whether the substation can take the project. The new rules make that mistake more expensive: CAISO's IPE 5.0 drops pre-application reports and requires a 2× commercial readiness deposit, and FERC Order 2023 penalises speculative requests.
- **Data centers face the same wall.** Speed to power is now the main criterion in data-center site selection (JLL), and grid capacity is often checked only after land and permitting decisions are made (Hitachi Energy).

**Problem statement:** *People who want to connect to the grid cannot see, before they commit money, where the grid has room after accounting for everyone already queued ahead of them. As a result, they file speculative requests, burn deposits, and wait years. That in turn clogs the queue for everyone else.*

### Who experiences it, and who pays?

People who feel the pain are not always the ones who would pay. We separate them:

| Group | How they experience the problem | Would they pay? |
|---|---|---|
| **Renewable and storage developers** (solar, wind, BESS, hybrids) | Land options, studies and deposits wasted on congested sites; multi-year waits | **Yes. Primary payer.** |
| **Data-center developers, hyperscalers, site selectors** | Cannot find sites with power fast enough | **Yes. Highest willingness to pay.** |
| **Investors, lenders, off-takers** (infrastructure funds, banks, CCAs, corporate buyers) | Fund or sign power contracts for projects that never connect | **Yes.** Due diligence and portfolio risk |
| **Utilities, transmission owners, ISOs** | Speculative applications, cascading restudies, engineering backlog | **Maybe.** Long sales cycles; later segment |
| **Policymakers, regulators** | Clean-energy and reliability targets slip | **No.** Influencers, not buyers. Free public view |

A correction to our first draft: we said customers are "projects stuck in a queue." The biggest value comes **before** a project enters the queue, when choosing where to apply. Projects already in the queue are a second use case: monitoring queue risk and finding upgrades that would unlock their constraint.

### What is our solution?

We have already built a working MVP: the **Interconnection Headroom Atlas**, a national screening model and map. Each substation gets a colour:
- **green**: room to connect;
- **yellow**: limited room;
- **red**: congested once the queue ahead is counted.

For each substation it shows:
- **Megawatts of new generation it can take,** both on today's grid and after the active queue ahead is added. Each queued request is weighted by its odds of being built.
- **Megawatts of new load it can serve.** This is the data-center question.
- **Queue pressure:** queued MW divided by substation capacity.
- **The line that limits it.** This tells a utility or developer what to fix, e.g. through reconductoring or storage.

It is built entirely from public data: HIFLD transmission lines, OpenStreetMap substations, EIA generators and demand, ISO queues and Census population. Each interconnection gets a DC power-flow model, solved for summer peak and for light load.

Corrections to our first draft:

- **"Monitoring grid power parameters" and "aging issues."** Public data has no real-time measurements or equipment age. The MVP models where capacity is constrained, not equipment condition. Monitoring would need utility data partnerships, so it belongs in a later phase.
- **"AI-based."** The core is a physics model (power flow). AI and machine learning help at the edges: matching queue requests to substations by fuzzy name, predicting which projects will withdraw, and later learning from real study results. We should describe it as *physics-based modelling enhanced by AI*. That framing is also more credible to utility engineers.
- **The sidenote about compute cost.** It is not a real concern. Our full national model (29,170 buses and 41,484 lines in three interconnections, two scenarios) is built and solved in about 6 minutes on a laptop, excluding the one-off data download [[C9]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C9). Compute is a rounding error in our costs; the expensive inputs are data and engineering time.

Benefits from our draft that still hold:
- data centers find buildable sites faster;
- utilities and developers (including data centers) get a shared, transparent picture of where capacity exists, which makes collaboration easier;
- fewer dead-on-arrival applications should mean fewer cascading restudies.

---

## 2. Market sizing

### How many potential customers exist?

Our first-draft formula counted *projects* ("applications − speculative + utilities"). Customers are **organisations**, and one developer files many projects. Speculative applicants are also customers: they are exactly the people who need screening before they apply. We therefore count buying organisations per segment:

| Segment | Estimate of organisations | Evidence and basis |
|---|---|---|
| Renewable and storage developers | **~1,200** (range 1,000–2,000) | The LBNL data names a developer on only 33% of active requests, and those already show **1,871 distinct developer names** [[C6]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C6). Many are project-specific LLCs, so parent companies are fewer. Demand signal: **2,300–3,400 new requests per year** (2019–2025) [[C4]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C4) |
| Data-center developers and site selectors | **~300** (range 150–500) | Assumption: ~10 hyperscalers, ~150 colocation and wholesale developers, ~150 powered-land developers, brokers and site consultants. *To validate.* |
| Investors, lenders, off-takers | **~400** (range 250–600) | Assumption: ~150 infrastructure/PE funds, ~50 project-finance banks, ~200 off-takers (CCAs, corporate buyers, utilities). *To validate.* |
| Utilities and transmission providers | **57** | Distinct transmission-provider entities in the LBNL dataset [[C5]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C5) |

### What might they spend?

No competitor publishes prices. These annual contract values are hypotheses to test in interviews:

| Segment | Assumed annual contract value (ACV) | Reasoning |
|---|---|---|
| Developers | **$30.5k weighted average**: small (70%) $15k, mid (25%) $50k, large (5%) $150k | A single avoided bad site can save land options, studies and deposits worth far more than the subscription |
| Data-center developers | **$100k** (range $50–250k) | Speed to power drives their siting; the alternatives (on-site generation, batteries, microgrids, or waiting years) are far more expensive |
| Investors and off-takers | **$40k** | Per-project due diligence and portfolio queue-risk audits |
| Utilities and transmission providers | **$250k** | Enterprise tools for screening incoming applications and planning; long sales cycles |

### TAM, SAM and SOM (US, annual recurring revenue)

The full arithmetic, with each input, is in [[C14]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C14).

| Level | Definition used | Calculation | Annual value |
|---|---|---|---|
| **TAM** | All four segments, whole US, full adoption | 1,200 × $30.5k + 300 × $100k + 400 × $40k + 57 × $250k | **≈ $97M** |
| **SAM** | What our screening-grade product can serve: developers except the largest (who need study-grade tools), early-stage data-center siting (~70%), and investors/off-takers. Utilities are excluded at first because of sales cycles and accuracy needs | 1,200 × (0.70 × $15k + 0.25 × $50k) + 0.70 × $30M + $16M | **≈ $65M** |
| **SOM** | Plausible to win within **5 years** | ~80 developer accounts × $23k + 15 data-center accounts × $100k + 25 investor accounts × $40k | **≈ $4.3M ARR** (~7% of SAM) |

**Cross-check (transactional model).** About 2,300–3,400 new requests per year, times ~5 sites screened per request, times $1,000 per site report, gives **$12–17M per year**. That is the same order of magnitude as the developer segment's TAM, so the two methods broadly agree.

**What this tells us.** A pure data or software business for US interconnection intelligence is a **~$100M per year market**. That is real, but modest for a venture-scale company, and it already has well-funded competitors (section 4). The bigger opportunity is to act on the constraints the map reveals:

- **Reconductoring and constraint-relief upside (not counted in the TAM above).** Our model flags **316 heat-limited line segments under 80 km** with about **630 GW of queued projects** behind them (screening estimate) [[C12]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C12).
  - Reconductoring with advanced conductors can roughly double a line's capacity at about half the cost of a new line (UC Berkeley/GridLab).
  - If we helped unlock 5 GW over five years and charged a $10/kW success fee, that would be about **$50M of cumulative revenue**. These are illustrative numbers, to test with developers.

---

## 3. Competitive landscape

### How customers handle the problem today

| Approach | How it works | Why it falls short |
|---|---|---|
| **Do nothing and apply anyway** | File at several sites and let the queue study sort it out | This is the source of the speculative-application problem. It now costs 2× deposits and FERC Order 2023 penalties, and still means 3–5+ years of waiting |
| **Utility pre-application reports** | Ask the utility about a specific connection point | CAISO is discontinuing them under IPE 5.0. Elsewhere they are slow and per-site |
| **Free public data** | ISO heatmaps (FERC Order 2023), LBNL *Queued Up*, utility hosting-capacity maps | Fragmented by ISO and utility, static, hard to interpret, and with no view of the queue ahead |
| **Engineering consultants** | Commission custom power-flow studies | Accurate, but expensive per site and slow; doesn't scale to screening hundreds of sites |
| **In-house transmission teams** | Large developers run their own studies on ISO models | Only affordable for the biggest players |
| **Commercial software** | Subscribe to the platforms in section 4 | Enterprise pricing; mostly reveals constraints rather than resolving them |

### Direct and adjacent competitors

**Paces** *(first-draft notes, updated)*
- Map-based site analytics for power developers. Use cases:
  1. find viable sites;
  2. validate grid capacity;
  3. speed up securing site control (enterprise);
  4. local utility hosting-capacity maps.
- Customers: generation and storage developers; it also markets to data centers.
- Correction to our draft: Paces does run power-flow studies to estimate viability and upgrade costs, so "no view of interconnection bottlenecks" is too strong. Its focus is site and parcel viability. How deeply it covers queue competition and shared constraints is *to check in a demo*.

**GridStatus** *(first-draft notes, updated)*
- Real-time North American grid data: market (LMP) prices and operating metrics across CAISO, ERCOT, PJM, MISO and SPP, plus public queue data. It launched an Interconnection Queues app in Feb 2026, and its open-source library is widely used.
- Strong for market analysts, traders and high-level queue tracking.
- Weakness: it shows data rather than siting decisions. There is no capacity or headroom analysis that tells a developer where it can connect faster.
- Customers: traders, analysts, researchers, policy users.

**Other competitors and alternatives found in research**
- **Nira Energy:** substation-level injection capacity (≥69 kV) and upgrade-cost estimates, from ISO models.
- **LandGate:** acquired by Wood Mackenzie in 2026. Injection and offtake capacity plus a land marketplace.
- **Enverus PRISM:** includes the acquired Pearl Street Technologies.
- **PVcase Prospect.**
- **Transect:** environmental and permitting siting.
- **Hitachi Energy Velocity Suite** and **Yes Energy:** market and congestion data.
- **Grid8** (free queue dashboard), **interconnection.fyi** (queue data), **Wood Mackenzie Lens.**
- **GridCARE:** "power acceleration" for AI data centers.

All are compared in section 4.

---

## 4. Competitor comparison

| Competitor | Target customers | Capabilities | Pricing | Strengths | Weaknesses | Traction |
|---|---|---|---|---|---|---|
| **Nira Energy** | Solar, storage and data-center developers | Injection capacity at every substation ≥69 kV, upgrade-cost estimates, portfolio cost tracking | Not published | Uses the ISOs' own power-flow models; claims >97% match with ISO study results | Enterprise focus; reveals constraints but doesn't resolve them; little visible investor tooling | >100 developer customers (claimed); growth investment from Energize Capital |
| **LandGate** (Wood Mackenzie) | Developers, data centers, landowners | Injection and offtake capacity at ~21k connection points, upgrade costs, subtransmission data, land marketplace | Not published | Breadth (land + power); data-center offtake; claims AC power-flow models | A competitor (PVcase) says it ignores N-1 contingencies (unverified) | Acquired by Wood Mackenzie, June 2026 |
| **Paces** | Developers; data centers | Site discovery, grid-capacity validation, permitting data, power-flow studies | Not published | Integrated land + permitting + grid workflow | Depth of queue-competition analysis unclear (check in demo) | Reported $11M raise |
| **Enverus PRISM** (+ Pearl Street) | Developers, IPPs, investors | Site scoring, available transfer capability (ERCOT, SPP, PJM, MISO), queue vs injection capability, Pearl Street interconnection risk tools | Enterprise (not published) | Market-data incumbent; broad energy dataset; also in Europe | Expensive, broad suite; not specialised in solving constraints | Acquired Pearl Street; large existing customer base |
| **PVcase Prospect** | Solar developers; data centers | Interconnection and offtake capacity, fiber, zoning, parcels, inside a solar-design suite | Not published | Bundled with design tools developers already use | Mostly tied to the solar workflow | Large existing PVcase user base |
| **GridStatus** | Traders, analysts, researchers | Real-time ISO data, prices, queue app (Feb 2026), open-source library | Free data plus paid plans (to verify) | Data quality, developer community, API | No headroom or siting analytics | Widely used open-source library |
| **Grid8** | Broad / public | Free interconnection dashboard: queue requests by county, market and technology | Free | Free and easy to use | Descriptive only | New launch |
| **Transect** | Developers (solar, wind) | Parcels, environmental and permitting screening, community sentiment, distance to transmission | Free trial; paid plans | Environmental depth | Shallow grid analysis | Established in renewables siting |
| **Hitachi Velocity Suite** / **Yes Energy** | Developers, traders, planners | Market, grid and congestion data, queue visibility | Enterprise (not published) | Long-established data incumbents | Data products that customers must analyse themselves; not decision-ready | Established enterprise customers |
| **GridCARE** | AI data centers, utilities | Physics-based AI to find near-term capacity on existing grids | Not published (likely project-based) | Solves the problem rather than mapping it; utility partnerships | Focused on large loads | $64M Series A (May 2026); 2 GW pipeline (claimed) |
| **Free public data** (ISO heatmaps, LBNL, utility maps) | Everyone | Static capacity maps, queue datasets | Free | Free; official | Fragmented, static, no view of the queue ahead | Mandated by FERC Order 2023 |
| **Engineering consultants** | Developers, utilities | Custom power-flow and interconnection studies | Per study (to research) | Study-grade accuracy | Slow, costly per site | Standard practice |

---

## 5. Our positioning

### The gap we can serve better

Competitors answer *"where is there capacity today?"* Customers also need to know:

1. **"How much will be left after everyone ahead of me?"** Our *headroom after the queue* weights every queued request by its odds of reaching operation (historically about 20% [[C3]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C3)) before counting what remains. This is part of our MVP, not a promise.
2. **"What would it take to fix the red?"** Every red substation in our model points to the line that limits it and how many megawatts are queued behind that line. That turns a red marker into a **reconductoring or storage opportunity**, which competitors mostly leave to the customer.
3. **Affordable access for smaller buyers.** The incumbents sell enterprise contracts. Small and mid-sized developers, CCAs, landowners and smaller investors are underserved.

### Positioning statement

> For **renewable, storage and data-center developers, and the investors behind them**, who must commit land, studies and doubled deposits without knowing whether the grid can take their project, the **Interconnection Headroom Atlas** shows the capacity left after the queue ahead, the exact constraint that limits each site, and a path to relieve it. Unlike Nira, LandGate or Paces, which map today's capacity for enterprise customers, we price for self-serve use, combine generation and large-load views, and turn congestion into actionable reconductoring and storage opportunities.

### Why customers might switch

- **Price and access:** a self-serve tier and per-site reports instead of enterprise contracts.
- **Queue realism:** headroom after the queue, weighted by completion odds, rather than a static capacity snapshot.
- **Action, not just a map:** each red substation shows its binding line and the megawatts queued behind it, which leads into constraint-relief projects.
- **One view for both sides:** generation and large load on the same map, a natural meeting point for data centers, developers and utilities.
- **Transparent method:** documented sources, open assumptions, a reproducible pipeline.

### Honest weaknesses

- **Accuracy.** We use public data and estimated line ratings. Nira and LandGate claim ISO-model or AC-model precision. Mitigations: obtain the grid operators' protected base cases under NDA, partner with these providers, and calibrate against published study results.
- **Copyable.** A map built only from public data is easy to replicate. Defensibility must come from network-upgrade cost data extracted from ISO cluster studies, completion odds learned from ~24,000 withdrawn requests [[C2]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C2), data customers contribute, and a constraint-relief service track record.
- **Free alternatives** (ISO heatmaps, Grid8) set a low price ceiling for basic maps.

---

## 6. Sources and assumptions

### Evidence

Every figure we calculated ourselves is recomputed by `pipeline/claims.py` and shown, step by step, in the atlas's **Claims & calculations** panel (https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq). The claim IDs below link to the matching entry.

| Claim | Source |
|---|---|
| ~8,200 active projects; 1,312 GW generation + 749 GW storage (end-2025) | [LBNL *Queued Up* 2026 edition](https://eta-publications.lbl.gov/publications/queued-2026-edition-characteristics) · [LBNL queues page](https://emp.lbl.gov/queues) |
| 20% of 2000–2019 requests reached operation, 71% withdrew [[C3]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C3); 2,300–3,400 new requests/yr [[C4]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C4); 57 transmission providers [[C5]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C5); 1,871 named developers [[C6]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C6); status counts [[C2]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C2) | Our analysis of the [LBNL data file](https://emp.lbl.gov/sites/default/files/2026-05/LBNL_Ix_Queue_Data_File_thru2025.xlsx) (CC BY 4.0) |
| 7,323 active requests / 1,686 GW (Oct 2026) [[C1]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C1); queue placement [[C8]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C8); model size [[C9]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C9); 316 heat-limited constraint segments with ~630 GW behind them [[C12]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C12); TAM/SAM/SOM arithmetic [[C14]](https://claude.ai/artifact/VF2dNzKvJ3potjwQMJE6Uq#C14) | Our MVP pipeline (`pipeline/`, `README.md`): CAISO, MISO, SPP, NYISO, PJM, ERCOT and ISO-NE live queues + LBNL |
| CAISO IPE 5.0 drops pre-application reports and adds 2× deposits; FERC Order 2023 heatmaps and penalties | Course brief (`context.md`); to verify against CAISO and FERC filings |
| Speed to power is the main data-center siting criterion | [JLL via Data Centre Magazine](https://datacentremagazine.com/news/jll-how-ai-is-reshaping-the-power-market-for-data-centres) · [Hitachi Energy](https://www.hitachienergy.com/us/en/news-and-events/blogs/2026/05/data-center-site-selection-finding-power-on-a-constrained-grid) |
| Reconductoring roughly doubles capacity at about half the cost of new lines | [GridLab 2035 Reconductoring Report](https://leg.mt.gov/content/Committees/Interim/2023-2024/Energy-and-Telecommunications/Meetings/July-16-17-2024/GridLab_2035-Reconductoring-Technical-Report.pdf) · [Utility Dive](https://www.utilitydive.com/news/reconductoring-power-lines-transmission-capacity-goldman-gridlab/712643/) |
| Nira capabilities and traction | [Nira Energy](https://www.datacenterdynamics.com/en/company/nira-energy/) · [Energize Capital investment](https://fmicorp.com/about/news/fmi-advises-nira-energy-in-strategic-growth-investment-from-energize-capital) |
| LandGate capabilities and acquisition | [LandGate offtake capacity](https://landgate.com/energy-markets/offtake-capacity) · [pv magazine: Wood Mackenzie acquires LandGate](https://pv-magazine-usa.com/2026/06/24/wood-mackenzie-acquires-landgate-as-data-centers-forecast-to-drive-68-of-u-s-load-growth/) · [PVcase comparison](https://pvcase.com/blog/landgate-vs-pvcase-prospect-data-center-interconnection) |
| Paces | [DCD profile](https://www.datacenterdynamics.com/en/company/paces/) · [pv magazine](https://pv-magazine-usa.com/?p=13437) |
| Enverus PRISM, Pearl Street acquisition | [Enverus PRISM](https://www.enverus.com/blog/how-enverus-prism-drives-success-in-siting-power-assets/) · [Renewable Energy World](https://renewableenergyworld.com/power-grid/one-year-after-winning-dtechs-initiate-startup-spotlight-pearl-street-gets-acquired/) |
| GridStatus queue app | [GridStatus changelog, Feb 2026](https://docs.gridstatus.io/changelog/february-2026/4-february-release-interconnection-queues-application) |
| Grid8 free dashboard | [TechEdge AI](https://techedgeai.com/grid8-launches-free-dashboard-to-demystify-u-s-grid-interconnection-chaos/) |
| GridCARE funding | [ESG Today](https://esgtoday.com/gridcare-raises-64-million-to-squeeze-more-power-out-of-grids) |
| PVcase, Transect, Hitachi Velocity Suite, Yes Energy | [PVcase data-center siting](https://pvcase.com/data-center-siting) · [Transect](https://www.transect.com/industries/solar-energy-siting) · [Yes Energy](https://www.yesenergy.com/solutions) · [Data Center Knowledge](https://www.datacenterknowledge.com/energy-power-supply/de-risking-data-centers-through-early-grid-insight) |

Most competitor claims (accuracy, coverage, customer counts) come from the companies' own marketing and have not been independently verified.

### Key assumptions and uncertainty

| Assumption | Value used | Confidence | How to validate |
|---|---|---|---|
| Number of developer organisations | 1,200 (1,000–2,000) | Medium | Deduplicate parent companies in queue data; check with industry associations (SEIA, ACP) |
| Developer annual spend | $15k / $50k / $150k by size | **Low** | Interviews: what they pay today for Nira, LandGate, Paces, Enverus or consultants |
| Data-center organisations and spend | 300 × $100k | **Low** | Interviews with site selectors and brokers; data-center association lists |
| Investor/off-taker organisations and spend | 400 × $40k | **Low** | Interviews with infrastructure funds, project-finance banks, CCAs |
| Utilities' spend | 57 × $250k | Low | 2–3 utility planning interviews |
| SAM shares (largest developers excluded; 70% of data-center siting) | as stated | Low–medium | Ask whether screening-grade accuracy is enough to make a decision |
| Win rate within 5 years | ~7% of SAM | Low | Pilot conversion rates |
| Reconductoring success fee | $10/kW on 5 GW | **Very low** | Developer interviews: would they co-fund an upgrade for a share of the capacity? |
| Model accuracy is "good enough" for screening | Unknown | Low | Compare our headroom with published ISO heatmaps and cluster-study results for 20–30 substations |

### What we need to learn in interviews

**Who:** 15–20 developers, 5–8 data-center site selectors, 5 investors or off-takers, and 2–3 utility planners.

1. How do you choose sites today, and at what step do you first learn the grid capacity? What did your last bad site cost you?
2. Which tools do you pay for today (Nira, LandGate, Paces, Enverus, consultants), what do you pay, and what can't they answer?
3. Would "headroom after the queue ahead" change your decision compared with today's capacity alone? How accurate does it need to be for you to act?
4. Would you pay per site report or by subscription? What price would feel cheap, and what would feel too expensive?
5. Developers stuck behind a constraint: would you co-fund an upgrade such as reconductoring if it got you connected years sooner? On what terms?
6. Data centers: how many sites do you screen per deal, and what is a month of faster power worth to you?
7. Investors and off-takers: how do you assess interconnection risk in due diligence today, and who does the work?

**Validation milestones**

1. **Willingness to pay:** at least 3 paid pilots, e.g. 10 site reports at a fixed price.
2. **Accuracy:** at least 70% agreement between our red/green colours and published ISO heatmaps on a test set.
3. **Interest in constraint relief:** at least one developer consortium interested in a specific constraint.
