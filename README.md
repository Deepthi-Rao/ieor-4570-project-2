# Promise Tracker NYC

**Did the officials New York elected in 2025 do what they said they would?** Ask about a promise,
and a team of research agents checks it against the record, in three layers:

1. **What they promised**: the candidate's campaign pages, as archived by the Wayback Machine before Election Day (Nov 4, 2025).
2. **What they did**: Council bills, sponsors and roll-call votes (Legistar), and the discretionary funding they steered.
3. **What actually happened**: NYC Open Data outcome datasets, Checkbook NYC contracts and spending, and the news.

It gives each promise a verdict (**Kept**, **In progress**, **No action found**, **Broken** or
**Can't determine**), cites a source for every fact, and says plainly when the data can't answer.

**Live:** see `deploy_url` in [submission.json](submission.json). Sign in with a Columbia account (Cloud Run IAP).

## Who it's for

New Yorkers deciding how they feel about their representatives ten months into the term, local
reporters, and civic groups. They want a neutral check with sources, not a talking point. The scope is
Mayor Zohran Mamdani and the 51 City Council members whose terms began January 2026. That's recent and
local enough that a model can't answer from memory (see the no-tools baselines below).

## How it works (Deep Research path)

```
            ┌──────────── asks a clarifying question if the request is vague
 question → Lead ── get_promises (offline corpus) ──┐
            │  fans out 2–4 Researchers in ONE turn, │ findings
            │  each in a fresh context, in parallel  │
            ├─► Researcher (promises)    get_promises, fetch_page, web_search
            ├─► Researcher (legislation) search_legislation, search_legislation_live, get_votes
            ├─► Researcher (outcomes)    search_datasets, query_open_data, search_spending, search_discretionary_funding
            ├─► Researcher (news)        web_search, fetch_page
            │  reads findings; sends more Researchers for gaps (≤ 2 more rounds, ≤ 8 Researchers, 180 s budget)
            ▼
         Writer (findings only → cited verdict) ─► Reviewer (neutrality + every fact/link checked against findings) ─► report
```

- **Five agent shapes:** Lead, Researcher (4 specialist kinds), Writer, Reviewer. The Reviewer is the "neutrality reviewer" from our data plan.
- **Parallel fan-out:** the Lead calls `research` several times in one turn. Each Researcher is a fresh `Agent` run with its own context and tools (`app.py: research`).
- **Goes back for gaps:** the Lead reads the findings and sends more Researchers for any missing layer, up to 2 more rounds. Code caps it, not just the prompt: `MAX_ROUNDS = 3` (researchers sent in one turn are one round), `MAX_RESEARCHERS = 8`, `RESEARCH_SECONDS = 180`, `LEAD_TURNS = 10`, `RESEARCHER_TURNS = 10`, which keeps every answer inside the graders' 5 minutes.
- **Clarification:** a question without an official and a promise/topic gets a clarifying question instead of research, with examples drawn from the real roster (never from the model's memory). Your answer continues the same session. A real run (4 s, no tools called):
  > **Q:** Is the city council doing a good job?
  > **A:** Which official and specific policy topic would you like to evaluate? For example, you could ask about Mayor Zohran Mamdani's promises on public transit or District 22 Council Member Tiffany L. Cabán's promises on public safety.
- **Citations:** every tool returns a `source_url` (archived page, dataset, Legistar bill, Checkbook, news article). Grounded search links are resolved to the real article URL. The Writer may only use links that appear in the findings, and the Reviewer removes any that don't.
- **Admits what the data can't answer:** tools return explicit "no evidence found" and coverage notes (e.g. funding data has FY2027 and FY2009–FY2021, nothing in between), and the Writer separates "no evidence" from "didn't happen".

### API

`POST /chat` keeps Project 1's shape. Every tool call from every agent is in `tool_calls`, with an `agent` field:

```json
// request
{"message": "What happened to the mayor's free buses promise?", "session_id": null}
// response
{"response": "**In progress** ...markdown report...",
 "session_id": "…",
 "tool_calls": [
   {"agent": "Lead", "name": "get_promises", "args": {"official": "mayor", "topic": "buses"}, "result": "…"},
   {"agent": "Lead", "name": "research", "args": {"task": "…", "kind": "news"}, "result": "…"},
   {"agent": "Researcher 1 (news)", "name": "web_search", "args": {"query": "…"}, "result": "…"}
 ]}
```

If the question is vague, `response` is the clarifying question. Send the answer with the same `session_id`.
The page uses `GET /run?question=…&session_id=…`, which runs the same research streamed as Server-Sent Events, plus a context panel showing each agent's exact context.

## Data: where it comes from and how to load it

| Layer | Source | In the repo / how it's queried | Build or refresh |
|---|---|---|---|
| Who's in scope | NYC Open Data [City Council Members](https://data.cityofnewyork.us/d/uvw5-9znb) + the mayor | `data/officials.json` | `uv run offline/build_officials.py` (`--discover` suggests campaign sites with Gemini + Google Search, kept only if archived before Election Day; flagged `campaign_urls_unverified`) |
| Promised | Campaign sites via the [Wayback CDX API](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) (last copy before 2025-11-04), incl. policy subpages found in the archive index | `data/promises.jsonl`: one promise per line with exact quote, category, specific/vague flag, archived URL, snapshot date | `uv run offline/build_promises.py [name]`. Gemini extracts promises. **Any promise whose quote isn't found verbatim in the page is dropped.** Set `hand_checked: true` on reviewed lines to keep them across rebuilds. |
| Promised | Mayor's press releases / executive orders, CFB voter guide | Live: `fetch_page` (official and archival sites only) | — |
| Did | NYC Council [Legistar Web API](https://council.nyc.gov/legislation/api/) | `data/legistar.duckdb` snapshot (bills, sponsors, votes since 2026-01-01) + live API for newer bills | `uv run offline/build_legistar.py` (needs `LEGISTAR_TOKEN`) |
| Did | Council discretionary funding, FY2027: [Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf) of the adopted budget (PDF, 497 pages) | `data/funding/schedule_c_fy2027.csv.gz`: 9,987 awards, $655M, 163 initiatives, with member, district and a link to the PDF page, queried with DuckDB | `uv run offline/build_schedule_c.py [pdf-url]` |
| Did | NYC Open Data [Council Discretionary Funding](https://data.cityofnewyork.us/d/4d7f-74pe), FY2009–FY2021 | `data/funding/discretionary_fy2009_2021.csv.gz`, same columns, so both files are searched together | `uv run offline/build_funding.py` |
| Happened | NYC Open Data (any dataset: housing production, 311, parks, …) | Live: Socrata catalog + SoQL (`search_datasets`, `query_open_data`) | — (`SOCRATA_APP_TOKEN` optional) |
| Happened | [Checkbook NYC API](https://www.checkbooknyc.com/check-book-nyc-api) contracts and spending | Live: `search_spending` | — |
| Happened | News | Live: Gemini with Google Search grounding (`web_search`) | — |

All shipped data is committed (largest file ~10 MB gzipped), so the Cloud Run deploy has it with no bucket.

### Known gaps (the agent says so when they matter)

- **Legistar token:** the Council emails it by hand. Until `LEGISTAR_TOKEN` is set (locally in `.env`, or on Cloud Run as a variable) and `build_legistar.py` has run, the legislation researcher is disabled and reports say bills and votes couldn't be checked.
- **Discretionary funding** has FY2027 (this Council's first budget) and FY2009–FY2021. There's nothing for FY2022–FY2026: FY2026 was the previous Council's budget. Schedule C lists awards designated at adoption, so initiatives the Council designates later ("post-adoption", e.g. Swim Safety) aren't in it.
- **Checkbook NYC** sits behind bot protection that can block bursts. Requests go one at a time, and a block is reported as "unavailable", not as "no spending". Contracts can be searched by agency and purpose, but not by vendor name: the API only takes vendor codes, so vendor questions go through payments (`kind='spending'`).
- **Special elections:** NYC Open Data's roster doesn't list Carl Wilson yet. He won District 3's special election on 2026-04-28 after Erik Bottcher left office ([City & State](https://www.cityandstateny.com/politics/2026/04/carl-wilson-wins-nyc-council-special-election-blow-mamdani/413190/)), so `build_officials.py` adds him from a short `SUPPLEMENT` list. His term start there is the election date: his swearing-in date isn't confirmed.
- **Promise corpus:** the mayor's archived platform and policy pages are built (56 promises, 32 specific). `--discover` found archived campaign sites for 29 of the 51 Council members (`campaign_urls_unverified: true` in `data/officials.json`). Check them, then run `build_promises.py` to add their promises.
- **Matching promises to evidence** is the hard part. When no dataset measures a promise, the outcomes researcher says so instead of stretching a loosely related one.

## Sample queries

Each sample was run with `uv run offline/run_samples.py`: the agent, and the same model (`gemini-3.5-flash`) with no tools and no system prompt. Full outputs and every tool call are in [`samples/`](samples/).

### 1. Is Mayor Mamdani on track to build 200,000 affordable homes?

**Expected answer and source:** **In progress.** The promise is on file: "construct 200,000 new affordable homes over 10 years" (`data/promises.jsonl`, from the archived [housing policy page](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york), 2025-11-04). The outcome comes from NYC Open Data [Affordable Housing Production by Building](https://data.cityofnewyork.us/d/hg8x-zxpr) (`hg8x-zxpr`): **11,585** counted units started in 2026, vs **29,277** in 2025. One year into a 10-year promise, so too early to call kept or broken.

**What the tools add:** The baseline doesn't know Mamdani won. It calls him an Assemblymember and candidate and attributes the 200,000 figure to de Blasio. The agent quotes the archived promise, finds the May 2026 "Block by Block" plan, and checks starts against HPD data.

<details><summary><b>Agent's answer</b> (report, 81 s, 13 tool calls by Lead, Researcher 1 (outcomes), Researcher 2 (news))</summary>

---

#### Promise 1: Triple the city's production of publicly subsidized, permanently affordable, union-built, rent-stabilized homes, constructing 200,000 new units over the next 10 years.

**In progress**
Mayor Mamdani released a 10-year housing blueprint setting this target and has started construction on 11,585 affordable units, though this is early in the 10-year timeline ([NYC Mayor's Office](https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n), [NYC Open Data: Affordable Housing Production by Building](https://data.cityofnewyork.us/d/hg8x-zxpr)).

#### What they promised
"Zohran will triple the City’s production of publicly subsidized, permanently affordable, union-built, rent-stabilized homes, constructing 200,000 new units over the next 10 years." ([https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york), November 4, 2025).

#### What they did
In May 2026, Mayor Mamdani and Deputy Mayor Leila Bozorg released "Block by Block: The Housing Plan for a New Era," which outlines the goal to build 200,000 new affordable homes over the next decade ([NYC Mayor's Office](https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n)). The plan aims to scale annual production from 14,000 new homes in fiscal year 2027 to over 21,000 new homes annually by fiscal year 2031 ([HousingWire](https://www.housingwire.com/articles/nyc-affordable-housing-200000/)). On October 1, 2026, the administration fast-tracked 12 community districts to advance this initiative ([NYC Mayor's Office](https://www.nyc.gov/mayors-office/news/2026/10/mamdani-administration-puts-12-neighborhoods-on-fast-track-for-a)).

#### What happened
Housing Preservation and Development (HPD) records show that 11,585 affordable units have been started across 539 projects since January 1, 2026 ([NYC Open Data: Affordable Housing Production by Building](https://data.cityofnewyork.us/d/hg8x-zxpr)). This rate is below the preceding baseline year of 2025, when 29,277 units were started ([NYC Open Data: Affordable Housing Production by Building](https://data.cityofnewyork.us/d/hg8x-zxpr)). Analysts noted that hitting the 10-year goal of 200,000 new and 200,000 preserved homes requires maintaining a consistent pace of 10,000 new and 10,000 preserved units every six months ([City Limits](https://citylimits.org/citys-affordable-housing-production-ticked-down-in-first-half-of-2026-and-what-else-happened-this-week/)). However, the administration's plan is scheduled to scale up over time rather than starting at that peak rate ([HousingWire](https://www.housingwire.com/articles/nyc-affordable-housing-200000/)).

---

#### Promise 2: Invest $100 billion over 10 years to provide housing for families earning under $70,000 a year.

**In progress**
The administration backed its 10-year housing blueprint with a $22 billion capital investment over the first five years, though exact spending data from Checkbook NYC is currently unavailable ([NYC Mayor's Office](https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n), [Checkbook NYC](https://www.checkbooknyc.com/)).

#### What they promised
"This $100 billion dollar commitment over 10 years will once again make New York City a leader in providing homes for families who earn less than $70,000 a year—the median income for renters in New York—and ensure that our City’s resources are used to provide jobs with safety and dignity." ([https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york), November 4, 2025).

#### What they did
The Mamdani administration backed its 10-year blueprint with a capital investment of $22 billion in housing over its first five years ([NYC Mayor's Office](https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n)). The plan dedicates 30% of the 200,000 new affordable homes to extremely low-income households and 20% to very low-income residents ([HousingWire](https://www.housingwire.com/articles/nyc-affordable-housing-200000/)).

#### What happened
Checkbook NYC spending and contract data for HPD is currently unavailable to query due to a temporary access blockage, so the exact amount spent so far under this administration cannot be verified ([Checkbook NYC](https://www.checkbooknyc.com/)). This indicates a lack of available data rather than confirming that no spending occurred ([Checkbook NYC](https://www.checkbooknyc.com/)).

---

#### Promise 3: Construct 200,000 new affordable homes over 10 years for low-income households, seniors, and working families.

**In progress**
The administration has established its 10-year plan for building 200,000 new homes and has completed or started several thousand units, although it is early in the 10-year timeline ([NYC Mayor's Office](https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n), [NYC Open Data: Affordable Housing Production by Building](https://data.cityofnewyork.us/d/hg8x-zxpr)).

#### What they promised
"A Mamdani administration will construct 200,000 new affordable homes over 10 years for low-income households, seniors, and working families." ([https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york), November 4, 2025).

#### What they did
Mayor Mamdani released "Block by Block: The Housing Plan for a New Era," a 10-year housing blueprint setting a target to build 200,000 new affordable homes ([NYC Mayor's Office](https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n)). This plan scales up annual production over several years to reach its decade-long goals ([HousingWire](https://www.housingwire.com/articles/nyc-affordable-housing-200000/)).

#### What happened
The HPD dataset records 11,585 affordable units started and 15,660 affordable units completed across projects since January 1, 2026 ([NYC Open Data: Affordable Housing Production by Building](https://data.cityofnewyork.us/d/hg8x-zxpr)). This is lower than the previous baseline year of 2025, which saw 29,277 starts and 25,697 completions ([NYC Open Data: Affordable Housing Production by Building](https://data.cityofnewyork.us/d/hg8x-zxpr)).

</details>

<details><summary><b>No-tools baseline's answer</b></summary>

It appears there is a slight misconception in your question: **Zohran Mamdani is not currently the Mayor.** 

Mamdani is a New York State Assemblymember representing District 36 in Astoria, Queens. In October 2024, he announced his candidacy to run for Mayor of New York City in the June 2025 Democratic primary. 

Because he is a candidate and does not hold the office of mayor, **he does not have an active administration or municipal track record of building housing.** 

However, his **campaign platform** focuses heavily on housing reform. Here is what we know about his housing proposals and how they relate to large-scale affordable housing goals:

#### 1. The Platform: "Social Housing"
As a member of the Democratic Socialists of America (DSA), Mamdani’s housing platform rejects the traditional private-developer-led model of affordable housing. Instead, he campaigns on a **social housing** model. This involves:
*   Creating a city- or state-run authority to acquire land and build permanently affordable, publicly owned, and tenant-controlled housing.
*   Moving away from relying on tax incentives (like the former 421-a or the newer 485-x programs) to entice private developers to build a small percentage of "affordable" units in luxury buildings.
*   Implementing universal rent control and stronger tenant protections.

#### 2. Where does the "200,000" figure come from?
While Mamdani’s specific campaign documents will outline his exact numerical targets as the 2025 race progresses, the target of building or preserving hundreds of thousands of units of social housing is a common benchmark for the progressive coalitions supporting him. 
*   For example, state-level progressive coalitions (which Mamdani has championed in the Assembly) have pushed for a **Social Housing Development Authority** to construct and preserve hundreds of thousands of green, deeply affordable homes across New York State over a decade.

#### 3. The Current Mayor's Housing Track Record
If you are thinking of the current administration’s housing targets, that belongs to **Mayor Eric Adams**:
*   In late 2022, Mayor Adams announced a "moonshot" goal of building **500,000 new homes** over a decade (the "Get Stuff Built" initiative).
*   **Is Adams on track?** Most housing policy experts and industry trackers note that NYC is currently **not on track** to hit Adams' 500,000-home goal. High interest rates, the expiration of key tax incentives, and bureaucratic delays have slowed housing starts. To reach 500,000 homes in 10 years, the city needs to build roughly 50,000 units a year, but recent years have seen new housing permits hover closer to 11,000 to 16,000 annually. Adams has championed a major zoning reform package called "City of Yes for Housing Opportunity" to try and accelerate this pace.

</details>

### 2. Which of Mayor Mamdani's housing promises are specific enough to check against data?

**Expected answer and source:** The promises flagged `"specific": true` that match "housing" in `data/promises.jsonl` (28 of the mayor's 56). Examples: 200,000 new affordable homes over 10 years; $100 billion over 10 years for families under $70,000; $70 billion in new capital in the Ten-Year Capital Plan; double fines for hazardous violations and triple them for immediately hazardous ones; a 78°F indoor cooling standard when it's 82°F or hotter outside. Each comes from an archived campaign page with an exact quote.

**What the tools add:** The baseline again treats him as a candidate and lists promises from memory (rent freeze, Right to Counsel) with no sources and no dates. The agent answers from the archived corpus, quote-verified, with a link for each promise.

<details><summary><b>Agent's answer</b> (report, 70 s, 2 tool calls by Lead)</summary>

* Mayor Zohran Mamdani promised to triple the city's production of publicly subsidized, permanently affordable, union-built, rent-stabilized homes, constructing 200,000 new units over the next 10 years [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to fast-track 100% affordable developments, reducing approval times for projects like those at West 108th Street, the Inwood library, and the Greenpoint Hospital site [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to invest $100 billion over 10 years to provide housing for families earning under $70,000 a year [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to construct 200,000 new affordable homes over 10 years for low-income households, seniors, and working families [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to significantly expand the Senior Affordable Rental Apartments (SARA) program to produce more low-income housing for seniors [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to significantly expand the Extremely Low and Low-Income Affordability (ELLA) program to build housing for families earning under $72,000 [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to expand HRA's Master Lease Program to pool rental assistance programs to create subsidized housing [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to drop lawsuits against CityFHEPS expansion and ensure the program's expansion proceeds as scheduled under City Law [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to double capital investments in NYCHA renovations, develop housing on NYCHA parking lots, and use City subsidies directly for upgrades [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to use City capital to build new affordable, publicly-controlled housing on NYCHA land [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to lobby the state government in Albany to establish an annual capital commitment to NYCHA [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to fully fund and staff the operating budgets of housing agencies including HPD, DCP, and NYCHA [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to direct the Office of Management and Budget to avoid bottlenecking development pipelines and city services [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to lobby state and federal governments to raise the city's public debt ceiling and remove volume caps on affordable housing bonds [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to allocate $70 billion in new capital via municipal bonds in the Ten-Year Capital Plan for affordable housing [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to pursue a Comprehensive City Plan to direct growth, housing development, and equity [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to increase zoned capacity to allow mixed-income and affordable housing in neighborhoods historically left out of zoning goals, including areas excluded from City of Yes [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to lobby in Albany to expand rent stabilization rules to cover all new housing production [(Source)](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york).
* He promised to rebuild the Mayor’s Office to Protect Tenants to safeguard New York City renters [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).
* He promised to coordinate housing code enforcement across city agencies under the Mayor's Office to Protect Tenants [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).
* He promised to create a centralized, user-friendly website portal for tracking housing violations and fines [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).
* He promised to double the fines for hazardous housing violations and triple them for immediately hazardous violations [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).
* He promised to ensure outstanding and future housing violation fines are actually collected [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).
* He promised to update the Housing Maintenance Code by 2025 to make failing to provide adequate cooling during summer a Class C violation [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).
* He promised to require building owners to maintain indoor temperatures at or below 78°F when it is 82°F or hotter outdoors [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).
* He promised to double the capacity of the Alternative Enforcement Program and expand the Office of Special Enforcement [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).
* He promised to restore funding for the Anti-Harassment Tenant Protection program [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).
* He promised to fully fund the Neighborhood Pillars program at the level secured by the City Council [(Source)](https://web.archive.org/web/20251102190503/https://www.zohranfornyc.com/policies/protecting-new-yorks-tenants).

</details>

<details><summary><b>No-tools baseline's answer</b></summary>

*Note: As of current records, Zohran Mamdani is a New York State Assembly Member (representing District 36 in Queens) who launched a campaign for Mayor of New York City in the 2025 Democratic primary. Because he is currently a candidate and not yet mayor, these refer to his **mayoral campaign platform promises**. If elected, several of his key housing proposals are highly specific and can be directly measured against municipal data.*

Here are Mamdani’s most specific housing promises and the exact datasets or metrics that can be used to track them:

---

#### 1. Enacting a Rent Freeze on Rent-Stabilized Apartments
* **The Promise:** Mamdani has committed to appointing members to the NYC Rent Guidelines Board (RGB) who are committed to voting for a **0% rent increase (a rent freeze)** on the city’s approximately 1 million rent-stabilized units. 
* **How to check against data:** 
  * **The Data Source:** The annual vote results and final explanatory statements published by the **NYC Rent Guidelines Board (RGB)**. 
  * **The Metric:** The percentage rate increase authorized for 1-year and 2-year leases. If Mamdani is mayor, any approved increase above 0% on rent-stabilized leases would represent a failure to meet this specific promise.

#### 2. Fully Funding and Enforcing the "Right to Counsel" (RTC)
* **The Promise:** Though NYC passed a "Right to Counsel" law in 2017 guaranteeing low-income tenants facing eviction a free lawyer, the program has been severely underfunded, leaving many tenants to face housing court alone. Mamdani promises to fully fund the program so that **100% of eligible tenants facing eviction are assigned a lawyer**.
* **How to check against data:**
  * **The Data Source:** The **NYC Office of Civil Justice (OCJ) Annual Reports** and housing court records.
  * **The Metric:** The representation rate of low-income tenants in NYC housing court. Currently, this rate sits well below 100% (often hovering around 35% to 50% due to attorney shortages). A successful implementation would show this metric rising to and sustaining at 100%.
  * **Budgetary Data:** The **NYC Adopted Budget**, specifically the funding lines allocated to the Human Resources Administration (HRA) for contracted legal services.

#### 3. Direct Public/Social Housing Construction
* **The Promise:** Mamdani advocates for "social housing"—housing that is publicly owned or under community control and permanently shielded from the private market. He promises to use city funds and city-owned land to construct new social housing rather than relying on private developers and tax breaks (like the state's 485-x/former 421-a programs).
* **How to check against data:**
  * **The Data Source:** The **NYC Department of Housing Preservation and Development (HPD) Housing Production Reports** and the **Mayor’s Management Report (MMR)**.
  * **The Metric:** The number of "new construction starts" classified as publicly owned, municipal, or Community Land Trust (CLT)-run housing, compared to private, market-rate, or public-private partnership developments. 

#### 4. Direct Municipal Funding for NYCHA Repairs
* **The Promise:** Mamdani has called for massive, direct municipal capital investments into the New York City Housing Authority (NYCHA) to address its estimated $78 billion capital repair backlog, rather than relying on privatization schemes like RAD (Rental Assistance Demonstration) or the NYCHA Preservation Trust.
* **How to check against data:**
  * **The Data Source:** The **NYC Capital Commitment Plan** (published by the Office of Management and Budget) and **NYCHA's Physical Needs Assessment (PNA)**.
  * **The Metric:** The exact dollar amount of New York City capital funds (not federal or state funds) committed and actually spent on NYCHA capital projects annually. Additionally, tracking the reduction in NYCHA's open maintenance backlogs and physical emergency work orders (available via NYCHA’s metrics dashboard).

#### 5. Banning Tenant-Paid Broker Fees
* **The Promise:** Mamdani has strongly supported shifting the burden of apartment broker fees from tenants to landlords (similar to the FARE Act debated in the City Council). As mayor, he would be responsible for enforcing such rules.
* **How to check against data:**
  * **The Data Source:** **NYC Department of Consumer and Worker Protection (DCWP)** enforcement data and housing market consumer complaint databases.
  * **The Metric:** The number of investigations, citations, and fines issued by the DCWP to real estate brokerages violating broker fee rules.

</details>

### 3. Did Council Member Lincoln Restler steer discretionary funding to parks in District 33 this year?

**Expected answer and source:** **Yes.** In the FY2027 budget (adopted June 30, 2026, the first this Council passed), Restler designated **$90,000 in 5 Parks Equity Initiative awards**: Open Space Alliance for North Brooklyn $35,000, NYC Parks for McCarren & McGolrick Parks $18,000, Newtown Creek Alliance $17,000, Brooklyn Heights Association $10,000 and New York Restoration Project $10,000. Smaller Local Initiatives awards add park programming, such as McCarren Park cleanups and the demonstration garden ($5,000 each). Source: `data/funding/schedule_c_fy2027.csv.gz`, parsed from the Council's [FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=230) (Parks Equity Initiative, pp. 230–239). Each row links to its page.

**What the tools add:** The baseline doesn't know which budget is current (it guesses FY2025 or FY2026), gives no amounts or sources, and names groups (Friends of Cooper Park, North Brooklyn Parks Alliance) that aren't among Restler's FY2027 parks awards. The agent lists this year's actual awards with amounts, each linked to its page of the budget, and says separately what it couldn't find (no parks promise on file, no news coverage).

<details><summary><b>Agent's answer</b> (report, 62 s, 7 tool calls by Lead, Researcher 1 (outcomes), Researcher 2 (news))</summary>

Yes, Council Member Lincoln Restler allocated discretionary funding to parks and open spaces in District 33 in the FY 2027 budget ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=231)). 

There are no campaign promises regarding parks on file for Council Member Restler ([https://lincolnforcouncil.com/issues](https://lincolnforcouncil.com/issues)). Additionally, no 2026 news coverage or announcements were found regarding him steering discretionary or capital funding to District 33 parks, though this lack of news evidence does not mean the funding did not occur ([City & State NY](https://www.cityandstateny.com/nyn-media/2026/09/restler-joins-neverending-war-against-late-nonprofit-payments/416276/)). Official budget documents confirm that the following discretionary allocations were made:

* **Open Space Alliance for North Brooklyn, Inc.**: $35,000 for Council District 33 under the Parks Equity Initiative ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=237)).
* **Department of Parks and Recreation**: $18,000 for McCarren & McGorlrick Parks under the Parks Equity Initiative ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=234)).
* **Newtown Creek Alliance, Inc.**: $17,000 for Environmental Education & Community Stewardship in Council District 33 under the Parks Equity Initiative ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=237)).
* **Brooklyn Heights Association, Inc.**: $10,000 for Council District 33 under the Parks Equity Initiative ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=231)).
* **New York Restoration Project**: $10,000 for Council District 33 under the Parks Equity Initiative ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=237)).
* **Brooklyn Bridge Park Boathouse, Inc.**: $5,000 for Family Paddle programming and Kayak Skills and Safety Training under Local Initiatives ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=346)).
* **Citizens Committee for New York City, Inc.**: $5,000 for Community Cleanups in McCarren Park and Council District 33 under Local Initiatives ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=357)).
* **Council on the Environment, Inc.**: $5,000 for the maintenance of the McCarren Park Demonstration Garden under Local Initiatives ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=364)).
* **Fund for the City of New York, Inc.**: $5,000 to support WNYC Transmitter Park, including community outreach and educational programming, under Local Initiatives ([NYC Council Discretionary Funding: FY2027 Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=382)).

</details>

<details><summary><b>No-tools baseline's answer</b></summary>

New York City Council Member Lincoln Restler (representing District 33, which spans Brooklyn Heights, DUMBO, Greenpoint, Williamsburg, Downtown Brooklyn, and adjacent neighborhoods) has consistently directed substantial discretionary and capital funding toward local parks. 

While the exact details depend on the specific fiscal year cycle you are referring to (such as the FY 2025 budget adopted in June 2024 or the ongoing FY 2026 process), Restler has made funding open space and parks a central priority of his office's discretionary spending.

Here is how he has directed funding to District 33 parks through various discretionary mechanisms:

### 1. Capital Discretionary Allocations
Council Members receive millions of dollars in capital discretionary funding each fiscal year to fund long-term physical improvements. Restler has steered significant portions of this funding to district parks. Key projects in recent budget cycles have included:
*   **McGolrick Park (Greenpoint):** Funding for playground upgrades, path repaving, and restoration efforts.
*   **Cooper Park (East Williamsburg):** Capital dollars directed toward upgrading the dog run, sports courts, and overall park infrastructure.
*   **Transmitter Park (Greenpoint):** Funding for repairs to the pier, open green space maintenance, and safety improvements.
*   **Commodore Barry Park (Fort Greene/Navy Yard):** Capital allocations to improve the pool, playground facilities, and turf.

### 2. Expense Discretionary Funding (Community Grants)
In addition to large physical capital projects, Council Members allocate smaller "expense" discretionary grants to local non-profit organizations. Restler regularly steers this funding to local volunteer park groups (often referred to as "Friends of" groups) and conservancies to assist with maintenance, gardening, programming, and community events. Beneficiaries have included:
*   The **Friends of Cooper Park**
*   The **Friends of McGolrick Park**
*   The **North Brooklyn Parks Alliance** (which helps maintain and program several parks under the BQE and along the waterfront)

### 3. Participatory Budgeting
Restler’s office utilizes **Participatory Budgeting (PB)**, a democratic process where District 33 residents vote directly on how to spend $1 million or more of the Council Member's capital discretionary budget. Year after year, park improvements are among the most popular projects proposed and voted on by residents. Winning PB projects in District 33 have frequently included tree planting, park water-fountain installations, and minor playground renovations.

***

*If you are looking for a specific line-item allocation from the most recent New York City Council discretionary database, you can view the complete, searchable database of Council Member allocations on the official [New York City Council website](https://council.nyc.gov/) under their "Budget" section, which lists every individual expense and capital allocation made by Council Member Restler for the current fiscal year.*

</details>

## Run it locally

```bash
gcloud auth application-default login       # Gemini on Vertex AI
cp .env.example .env                         # optional: LEGISTAR_TOKEN, SOCRATA_APP_TOKEN
uv run uvicorn app:app --port 8000           # http://localhost:8000
uv run offline/run_samples.py                # re-run the README samples (agent + no-tools baseline)
uv run pytest                                # offline tests: data, Checkbook parsing, round cap, citation check
```

Deploys continuously to Cloud Run from `main` (buildpack, entrypoint `uvicorn app:app --host 0.0.0.0 --port $PORT`).

## Team and data sources

Two members, Deepthi Rao (dgr2116) and Lavina Mital (lm4046), with at least one data source each, split by evidence layer:

| Owner | Source |
|---|---|
| Deepthi Rao (dgr2116) | Promise corpus (Wayback Machine, voter guide): `offline/build_promises.py`, `sources/promises.py` |
| Deepthi Rao (dgr2116) | Legistar bills and votes: `offline/build_legistar.py`, `sources/legistar.py` |
| Deepthi Rao (dgr2116) | News search and the neutrality Reviewer: `sources/web.py`, `reviewer` in `app.py` |
| Lavina Mital (lm4046) | NYC Open Data and Checkbook outcomes: `sources/opendata.py`, `sources/checkbook.py` |
| Lavina Mital (lm4046) | Discretionary funding: `offline/build_funding.py`, `sources/funding.py` |
