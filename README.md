# Promises, Kept? (Promise Tracker NYC)

**Did the officials New York elected in 2025 do what they said they would?** Ask about a promise,
and a team of research agents checks it against the record, in three layers:

1. **What they promised**: the candidate's campaign pages, as archived by the Wayback Machine before Election Day (Nov 4, 2025).
2. **What they did**: Council bills, sponsors and roll-call votes (Legistar), and the discretionary funding they steered.
3. **What actually happened**: NYC Open Data outcome datasets, Checkbook NYC contracts and spending, and the news.

It gives each promise a verdict (**Kept**, **In progress**, **No action found**, **Broken** or
**Can't determine**), cites a source for every fact, and says plainly when the data can't answer.

**Try it live: [ieor-4570-project-2-git-64584940628.europe-west1.run.app](https://ieor-4570-project-2-git-64584940628.europe-west1.run.app)** (sign in with a Columbia account; access is through Cloud Run IAP). The same link is the `deploy_url` in [submission.json](submission.json).

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
| Promised | Campaign sites via the [Wayback CDX API](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server) (last copy before each official's election: 2025-11-04, or 2026-04-28 for Carl Wilson), incl. policy subpages found in the archive index. Sites come from the roster's `--discover` step and members' Wikipedia pages, each checked against its archived copy | `data/promises.jsonl`: 879 promises from 33 officials, one per line with exact quote, category, specific/vague flag, archived URL, snapshot date | `uv run offline/build_promises.py [name]`. Gemini extracts promises. **Any promise whose quote isn't found verbatim in the page is dropped.** Set `hand_checked: true` on reviewed lines to keep them across rebuilds. |
| Promised | Mayor's press releases / executive orders, CFB voter guide | Live: `fetch_page` (official and archival sites only) | — |
| Did | NYC Council legislation from [Legistar](https://legistar.council.nyc.gov/Legislation.aspx), via [jehiah/nyc_legislation](https://github.com/jehiah/nyc_legislation), a public mirror of the Legistar API that syncs every few hours (it powers [intro.nyc](https://intro.nyc)) | `data/legistar.duckdb`: 1,868 bills and resolutions introduced since 2026-01-01, 12,527 sponsorships and 17,363 roll-call votes. Every bill links to its official Legistar page | `uv run offline/build_legistar_mirror.py` (no token). With a `LEGISTAR_TOKEN`, `build_legistar.py` builds the same file from the API directly |
| Did | Council discretionary funding, FY2027: [Schedule C](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf) of the adopted budget (PDF, 497 pages) | `data/funding/schedule_c_fy2027.csv.gz`: 9,987 awards, $655M, 163 initiatives, with member, district and a link to the PDF page, queried with DuckDB | `uv run offline/build_schedule_c.py [pdf-url]`, then `uv run offline/build_funding_db.py` (the compact database the search opens) |
| Did | NYC Open Data [Council Discretionary Funding](https://data.cityofnewyork.us/d/4d7f-74pe), FY2009–FY2021 | `data/funding/discretionary_fy2009_2021.csv.gz`, same columns, so both files are searched together | `uv run offline/build_funding.py`, then `build_funding_db.py` |
| Happened | NYC Open Data (any dataset: housing production, 311, parks, …) | Live: Socrata catalog + SoQL (`search_datasets`, `query_open_data`) | — (`SOCRATA_APP_TOKEN` optional) |
| Happened | [Checkbook NYC API](https://www.checkbooknyc.com/check-book-nyc-api) contracts and spending | Live: `search_spending` | — |
| Happened | News | Live: Gemini with Google Search grounding (`web_search`) | — |

All shipped data is committed (largest file ~10 MB gzipped), so the Cloud Run deploy has it with no bucket.

### Known gaps (the agent says so when they matter)

- **Legislation is a snapshot:** `data/legistar.duckdb` is as of its build date (shown in every result). Rebuild it with `build_legistar_mirror.py` before a demo. The official Legistar API needs a token the Council emails by hand; the mirror stands in for it, and `search_legislation_live` only works once a token is set.
- **Discretionary funding** has FY2027 (this Council's first budget) and FY2009–FY2021. There's nothing for FY2022–FY2026: FY2026 was the previous Council's budget. Schedule C lists awards designated at adoption, so initiatives the Council designates later ("post-adoption", e.g. Swim Safety) aren't in it.
- **Checkbook NYC** sits behind bot protection that can block bursts. Requests go one at a time, and a block is reported as "unavailable", not as "no spending". Contracts can be searched by agency and purpose, but not by vendor name: the API only takes vendor codes, so vendor questions go through payments (`kind='spending'`).
- **Special elections:** NYC Open Data's roster doesn't list Carl Wilson yet. He won District 3's special election on 2026-04-28 after Erik Bottcher left office ([City & State](https://www.cityandstateny.com/politics/2026/04/carl-wilson-wins-nyc-council-special-election-blow-mamdani/413190/)), so `build_officials.py` adds him from a short `SUPPLEMENT` list. His term start there is the election date: his swearing-in date isn't confirmed.
- **Promise corpus:** 879 promises (366 flagged specific) from the mayor and 32 of the 51 Council members in office. Each member's campaign site was checked: the last archived copy before Election Day had to name the member and the Council race. That check rejected four Wikipedia-listed domains that now host spam or unrelated content. Not covered: 15 members with no archived 2025 campaign site (some, like Chi Ossé and Sandy Nurse, ran without one), and 4 whose archived pages are JavaScript shells or list no promises (Menin, Aldebol, Farías, Gennaro). For them the agent falls back to news search and says so. 188 promises come from pages last archived before 2025 (an earlier campaign); the tool flags them and the agent gives the date. `quote` is checked word for word against the page; the one-line `promise`, `category` and `specific` flag are Gemini's reading of it.
- **Matching promises to evidence** is the hard part. When no dataset measures a promise, the outcomes researcher says so instead of stretching a loosely related one.

## Sample queries

Each sample was run with `uv run offline/run_samples.py`: the agent, and the same model (`gemini-3.5-flash`) with no tools and no system prompt. Full outputs and every tool call are in [`samples/`](samples/).

### 1. Is Mayor Mamdani on track to build 200,000 affordable homes?

**Expected answer and source:** **In progress.** The promise is on file: "construct 200,000 new affordable homes over 10 years" (`data/promises.jsonl`, from the archived [housing policy page](https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york), 2025-11-04). The outcome comes from NYC Open Data [Affordable Housing Production by Building](https://data.cityofnewyork.us/d/hg8x-zxpr) (`hg8x-zxpr`): **11,585** counted units started in 2026, vs **29,277** in 2025. One year into a 10-year promise, so too early to call kept or broken.

**What the tools add:** The baseline doesn't know Mamdani won. It calls him an Assemblymember and candidate and attributes the 200,000 figure to de Blasio. The agent quotes the archived promise, finds the May 2026 "Block by Block" plan, and checks starts against HPD data.

<details><summary><b>Agent's answer</b> (report, 66 s, 24 tool calls by Lead, Researcher 1 (outcomes), Researcher 2 (news), Researcher 3 (legislation), Researcher 4 (news))</summary>

**In progress**
Mayor Mamdani has released a 10-year housing plan, committed initial capital funding, and initiated zoning fast-tracks, but early construction and financing rates are currently below the pace required to meet the 10-year target.

### What they promised
During his campaign, Zohran Mamdani promised that his administration would "construct 200,000 new affordable homes over 10 years for low-income households, seniors, and working families" [https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york]. He also pledged to "triple the City’s production of publicly subsidized, permanently affordable, union-built, rent-stabilized homes, constructing 200,000 new units over the next 10 years" [https://web.archive.org/web/20251104044801/https://www.zohranfornyc.com/policies/housing-by-and-for-new-york].

### What they did
In May 2026, Mayor Mamdani released a housing plan titled *Block by Block: The Housing Plan for a New Era*, which outlines a target to build 200,000 new affordable homes and preserve another 200,000 existing affordable homes over the next decade [https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n]. This 10-year plan is backed by a capital commitment of $22 billion over its first five years [https://www.nyc.gov/mayors-office/news/2026/05/mayor-mamdani-releases--block-by-block--the-housing-plan-for-a-n]. In October 2026, the administration placed 12 community districts on a fast track for zoning updates to accelerate affordable housing production [https://www.nyc.gov/mayors-office/news/2026/10/mamdani-administration-puts-12-neighborhoods-on-fast-track-for-a].

### What happened
During the first six months of 2026, the city financed 12,491 affordable homes, which included 6,632 new units, 4,951 preserved units, and 908 NYCHA PACT units [https://citylimits.org/citys-affordable-housing-production-ticked-down-in-first-half-of-2026-and-what-else-happened-this-week/]. This represented the lowest six-month production total since 2022 [https://citylimits.org/citys-affordable-housing-production-ticked-down-in-first-half-of-2026-and-what-else-happened-this-week/]. Analysts noted that to achieve the 10-year goal of 200,000 new homes, the administration must average 10,000 new units every six months [https://citylimits.org/citys-affordable-housing-production-ticked-down-in-first-half-of-2026-and-what-else-happened-this-week/]. Additionally, from January 1 through September 30, 2026, the Department of Housing Preservation and Development recorded 11,585 affordable housing units with a project start date, compared to 29,277 units in the full year of 2025 [https://data.cityofnewyork.us/d/hg8x-zxpr]. Housing experts noted that development pipelines take years to move and that early term figures reflect transition periods [https://citylimits.org/citys-affordable-housing-production-ticked-down-in-first-half-of-2026-and-what-else-happened-this-week/].

</details>

<details><summary><b>No-tools baseline's answer</b></summary>

To answer your question, it is first important to clarify a key detail: **Zohran Mamdani is not the Mayor of New York City.** 

He is currently a New York State Assemblymember representing Astoria, Queens, and is a candidate running in the June 2025 Democratic primary for Mayor of New York City. Because he is not in office, he does not have an active mayoral administration or a track record of municipal building projects to evaluate.

However, housing is the cornerstone of Mamdani’s mayoral campaign. Here is how his housing platform and the "200,000 homes" figure align with New York City's political landscape:

### 1. The "200,000 Affordable Homes" Target (A Previous Mayor's Plan)
The specific goal of building or preserving **200,000 affordable homes** was the signature housing policy of former NYC Mayor **Bill de Blasio**. 
* De Blasio launched his "Housing New York" plan in 2014 with the goal of building or preserving 200,000 affordable units over 10 years (a goal he later accelerated to 300,000 units by 2026). 
* Current Mayor **Eric Adams** has set a different "moonshot" goal of facilitating the construction of **500,000 new homes** over a decade through his "City of Yes" zoning text amendments.

### 2. Zohran Mamdani's Housing Platform
As a democratic socialist, Mamdani’s mayoral platform rejects the traditional developer-subsidy model used by previous mayors to build affordable housing. Instead of relying on private developers to build "affordable" units in exchange for tax breaks, Mamdani’s platform focuses on:
* **Social Housing:** Creating publicly funded, permanently affordable, and tenant-controlled housing.
* **Rent Freezes:** Utilizing the Rent Guidelines Board to implement rent freezes for the city’s roughly 1 million rent-stabilized apartments.
* **Fully Funding NYCHA:** Stripping city resources from other sectors to heavily invest in repairing the New York City Housing Authority's public housing stock.
* **Universal Tenant Protections:** Strengthening eviction protections and expanding the right to counsel for tenants facing eviction.

### Summary
Because Mamdani is a candidate and not the sitting mayor, he is not "on track" to build any municipal housing units yet. If he is elected in 2025, his approach to housing will likely focus on rent control and public/social housing acquisition rather than the traditional private-developer-led "affordable housing" targets set by Mayors de Blasio and Adams.

</details>

### 2. What has Council Member Julie Won done about her housing promises?

**Expected answer and source:** Mostly **In progress** or **No action found**, promise by promise. Her promises are on file from her campaign site (`data/promises.jsonl`; some pages were last archived in 2021, so the report should say they're from her earlier campaign). From `data/legistar.duckdb` (tables `matters`, `sponsors`): this term she co-sponsored Int 0839-2026 (permanent Certification of No Harassment, **Enacted**), Int 1015-2026 (**Enacted**), Int 0905-2026 (first opportunity for qualified nonprofits to buy, **Enacted**, at the Mayor's desk), and Int 0650-2026 and Int 0059-2026 (both **in committee**). She sponsored no bill this term that mentions NYCHA, and her only "task force" bill is Int 0124-2026, about e-bikes and street design. From `data/funding/schedule_c_fy2027.csv.gz`: FY2027 awards of $20,000 to the Queensbridge Houses Tenants Association and $15,000 each to the Woodside Houses Tenants Association and the Ravenswood Residents Association ([Schedule C, pp. 17–18](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=17)).

**What the tools add:** The baseline knows her first term (the 2022 Innovation QNS deal) but nothing from this term: no 2026 bills, votes or budget awards, and no sources. The agent quotes the archived promises with their dates, checks each against her bills and their status in Legistar and her FY2027 awards, and says "No action found" where the record is silent rather than guessing.

<details><summary><b>Agent's answer</b> (report, 107 s, 27 tool calls by Lead, Researcher 1 (legislation), Researcher 2 (legislation), Researcher 3 (legislation), Researcher 4 (news), Researcher 5 (news), Researcher 6 (outcomes))</summary>

### Promise 1: Comprehensive Planning in District 26

**In progress**  
Council Member Won initiated a community planning process in District 26 and approved the One Long Island City Neighborhood Plan, which has active timelines extending through 2033 ([https://council.nyc.gov/julie-won/one-lic/](https://council.nyc.gov/julie-won/one-lic/)).

*   **What they promised**: "Julie will bring Comprehensive Planning to our neighborhood, and put an end to the “spot rezonings” that pit our communities against each other." ([https://web.archive.org/web/20251006130400/https://www.juliewon.com/](https://web.archive.org/web/20251006130400/https://www.juliewon.com/), snapshot from October 6, 2025).
*   **What they did**: On October 10, 2023, Council Member Won announced the launch of holistic community planning processes in District 26, including the *One Long Island City* (OneLIC) study and planning corridors for Astoria, Sunnyside, and Woodside ([https://council.nyc.gov/julie-won/2023/10/10/council-member-julie-won-announces-comprehensive-community-planning-for-district-26/](https://council.nyc.gov/julie-won/2023/10/10/council-member-julie-won-announces-comprehensive-community-planning-for-district-26/)). The City Council and Won unanimously approved the OneLIC Neighborhood Plan on November 12, 2025 ([https://council.nyc.gov/julie-won/one-lic/](https://council.nyc.gov/julie-won/one-lic/)). This 54-block rezoning across Hunters Point and Dutch Kills is projected to yield 14,700 new housing units, including approximately 4,350 permanently affordable units ([https://www.akerman.com/en/perspectives/new-york-city-council-approves-the-long-island-city-neighborhood-plan.html](https://www.akerman.com/en/perspectives/new-york-city-council-approves-the-long-island-city-neighborhood-plan.html)). In a November 5, 2026 interview, Won highlighted that she approved over 15,000 units of housing under major rezonings in her district during her tenure ([https://www.youtube.com/watch?v=V6zn6Q9-EwU](https://www.youtube.com/watch?v=V6zn6Q9-EwU)). 
*   **What happened**: Following the approval, the Pratt Center announced the facilitation of a "OneLIC Oversight Task Force" to monitor city agency accountability and capital commitments ([https://prattcenter.net/our_work/onelic](https://prattcenter.net/our_work/onelic)). While no bills sponsored by Won in 2026 matched "comprehensive planning", "spot rezoning", or "rezoning", the OneLIC project is progressing on its multi-year schedule. Under the approved plan, the selection of a design consultant for waterfront elements was scheduled for June 2026, with subsequent design, permitting, and bidding scheduled to begin by June 2028, and construction targeted for completion by 2033 ([https://council.nyc.gov/julie-won/one-lic/](https://council.nyc.gov/julie-won/one-lic/)). 

---

### Promise 2: $2 Billion Annual Investment and NYCHA Audit

**No action found**  
No legislation was found showing Council Member Won sponsored or advocated for a $2 billion annual investment in NYCHA or an operational audit of the agency. 

*   **What they promised**: "Julie will push for $2 billion annual investment in the maintenance and rehabilitation of New York public housing along with an audit and overhaul of NYCHA operations and management that puts residents and families first" ([https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues](https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues)). This promise comes from her earlier 2021 campaign website ([https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues](https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues), snapshot from July 28, 2021).
*   **What they did**: On June 30, 2026, Won announced that the City Council secured funding for NYCHA and affordable housing in the adopted city budget ([https://council.nyc.gov/julie-won/budget/](https://council.nyc.gov/julie-won/budget/)). In the FY 2027 adopted budget, Won directed discretionary funding of $20,000 to the Queensbridge Houses Tenants Association, $15,000 to the Woodside Houses Tenants Association, and $15,000 to the Ravenswood Residents Association ([https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=17](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=17)). She also directed $10,000 to the East River Development Alliance for the Urban Upbound NYCHA Program ([https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=376](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=376)) and $22,000 to Woodside on the Move for tenant advocacy ([https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=451](https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf#page=451)). However, no legislation matching "NYCHA" or "audit" was sponsored by Won, and no evidence was found in budget records of her securing or pushing for a $2 billion allocation.
*   **What happened**: The findings contain no information about an audit or overhaul of NYCHA operations.

---

### Promise 3: Housing Discrimination Task Force

**No action found**  
No bills or resolutions to establish a housing discrimination task force were found sponsored by Council Member Won in the legislative records.

*   **What they promised**: "Additionally, she will join with other community leaders and council members to establish a city-wide task force on housing descrimination to evaluate the impact of decades of racist housing policy and sponsor legislation based on their recommendations" ([https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues](https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues)). This promise comes from her earlier 2021 campaign website ([https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues](https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues), snapshot from July 28, 2021).
*   **What they did**: Won sponsored several other housing-related bills, such as Int 1015-2026, but none of these proposed a housing discrimination task force ([https://legistar.council.nyc.gov/LegislationDetail.aspx?ID=79329&GUID=D2DAB1EB-1A6B-45F8-9CBD-921DE95B7F1E](https://legistar.council.nyc.gov/LegislationDetail.aspx?ID=79329&GUID=D2DAB1EB-1A6B-45F8-9CBD-921DE95B7F1E)). Her only sponsored "task force" legislation in 2026 was Int 0124-2026, which relates to street design, infrastructure, and electric bicycles ([https://legistar.council.nyc.gov/LegislationDetail.aspx?ID=77393&GUID=CEAD57AE-7764-46EE-B72A-25B6DF666B80](https://legistar.council.nyc.gov/LegislationDetail.aspx?ID=77393&GUID=CEAD57AE-7764-46EE-B72A-25B6DF666B80)).
*   **What happened**: The findings contain no information regarding the creation or activity of a city-wide housing discrimination task force.

---

### Promise 4: Arts Spaces Property Tax Credit and Zoning

**No action found**  
No evidence was found of legislation sponsored by Council Member Won to establish a property tax credit or eliminate zoning barriers for arts spaces.

*   **What they promised**: "Julie will prioritize the use of public land for community arts centers by eliminating zoning barriers that inhibit the construction of mixed use spaces for hybrid residential and artistic development, and by establishing a property tax credit for arts spaces that operate within a half mile of subsidized or public housing" ([https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues](https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues)). This promise comes from her earlier 2021 campaign website ([https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues](https://web.archive.org/web/20210728144714/https://www.juliewon.com/issues), snapshot from July 28, 2021).
*   **What they did**: The findings show no direct legislation sponsored by Won to establish a tax credit or alter zoning specifically for mixed-use arts centers. Separately, New York City lawmakers introduced a bill in November 2025 to allow housing programs to favor working artists under city fair-housing laws ([https://www.urgentmatter.press/housing-programs-could-give-preference-to-artists-under-nyc-council-bill/](https://www.urgentmatter.press/housing-programs-could-give-preference-to-artists-under-nyc-council-bill/)). 
*   **What happened**: A February 2026 report recommended zoning standards and set-asides to help secure artist workspace and housing ([https://policycommons.net/artifacts/42939514/integrating-artists-into-nycs-affordable-housing-strategy/43838566/](https://policycommons.net/artifacts/42939514/integrating-artists-into-nycs-affordable-housing-strategy/43838566/)). Additionally, the Public Housing Community Fund launched the "From Roots to Arts" program in April 2024, funding artist residencies at NYCHA campuses—including Astoria Houses in Won't district—through 2026 ([https://citylimits.org/new-program-seeks-nycha-artists-in-residence-to-help-beautify-public-housing-campuses/](https://citylimits.org/new-program-seeks-nycha-artists-in-residence-to-help-beautify-public-housing-campuses/)). No further outcomes regarding Won's proposed property tax credit or public land prioritization were found.

</details>

<details><summary><b>No-tools baseline's answer</b></summary>

Since taking office in January 2022 as the representative for New York City Council District 26 (covering Sunnyside, Woodside, Long Island City, and Astoria), Julie Won has made housing a central focus of her legislative and district-level work. Her campaign promised to fight tenant displacement, demand deeply affordable housing from developers, and hold landlords accountable. 

Her record on these promises is characterized by high-profile land-use negotiations, legislative advocacy for tenant protections, and significant debate from both progressive and pro-housing groups.

Here is a breakdown of what Council Member Julie Won has done regarding her housing promises:

---

### 1. Major Land-Use and Rezoning Negotiations
In New York City, the local Council Member holds significant power over land-use decisions in their district due to a practice known as "member deference." Won has used this leverage to negotiate for higher percentages of affordable units in private developments.

*   **Innovation QNS (Astoria):** This was the most high-profile and controversial housing battle of her first term. The proposed $2 billion, five-block mixed-use development initially offered 25% affordable housing out of 2,800 units. Won opposed the initial plan, demanding 50% affordability and deeper income targets (lower Area Median Income, or AMI, levels). 
    *   **The Outcome:** After intense negotiations, Won approved a modified plan in November 2022. The final agreement expanded the project to roughly 3,200 units, with **45% (about 1,436 units) designated as affordable**, including 500 units set aside for those earning under 30% of the AMI. It also included a $2 million tenant anti-harassment fund.
*   **44-01 Northern Boulevard (Long Island City):** For this mixed-use development, Won negotiated with the developers to secure **35% affordable housing** (well above the city’s Mandatory Inclusionary Housing baseline of 25–30%), with a focus on family-sized units.
*   **Barnett Avenue (Sunnyside):** Won supported a 100% affordable housing development on Barnett Avenue, which provides homes specifically tailored for low-income seniors and working-class families.

### 2. Tenant Protections and Legislative Record
Won has aligned herself with the progressive wing of the City Council to advocate for tenant rights and housing security:

*   **Good Cause Eviction:** Won has been an active proponent of statewide "Good Cause Eviction" legislation, which aims to prevent landlords from evicting tenants without a designated legal reason and caps annual rent increases.
*   **Rental Assistance and Vouchers:** She has voted in favor of expanding the CityFHEPS (Family Homelessness and Eviction Prevention Supplement) housing voucher program to make it easier for low-income New Yorkers to secure and keep housing.
*   **NYCHA Advocacy:** Won’t’s district includes some of the largest public housing developments in the country, including Queensbridge and Ravenswood Houses. She has consistently lobbied for increased federal and state funding for NYCHA repairs, held resource fairs for public housing residents, and pushed for accountability regarding mold, lead, and heating issues.
*   **Illegal Eviction Protections:** Won co-sponsored and supported local legislation to crack down on illegal lockouts and tenant harassment by landlords.

### 3. Perspectives and Criticisms
Won’s approach to housing has drawn both praise and criticism from various political factions:

*   **Praise for Securing Unprecedented Affordability:** Supporters and housing advocates praised Won for her tenacity during the Innovation QNS negotiations. They argue she successfully forced multi-billion-dollar developers to double their affordable housing commitments, setting a new precedent for private developments in NYC.
*   **Criticism from the Left (Anti-Gentrification Advocates):** Some progressive activists and local socialist groups criticized Won for ultimately voting "yes" on Innovation QNS. They argued that bringing thousands of market-rate units into a working-class neighborhood would accelerate gentrification and displacement, regardless of the affordable units secured, and felt she compromised on her campaign promise to reject developer-driven rezonings.
*   **Criticism from Pro-Housing/YIMBY ("Yes In My Backyard") Groups:** Pro-housing groups and some business leaders criticized Won for delaying projects like Innovation QNS during a severe housing shortage. They argued that her combative negotiating style and demands risked killing projects entirely, which would have resulted in zero new housing units being built.

### Summary
Julie Won has approached her housing promises by acting as a strict gatekeeper for development in her district. Rather than rejecting all private development or accepting standard city baselines, she has used her legislative power to extract historic affordability concessions from developers. While this pragmatic compromise alienated some of her most progressive supporters and frustrated pro-development groups, it resulted in the approval of thousands of new affordable apartments in Western Queens.

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
| Deepthi Rao (dgr2116) | Legistar bills and votes: `offline/build_legistar_mirror.py` (or `build_legistar.py` with a token), `sources/legistar.py` |
| Deepthi Rao (dgr2116) | News search and the neutrality Reviewer: `sources/web.py`, `reviewer` in `app.py` |
| Lavina Mital (lm4046) | NYC Open Data and Checkbook outcomes: `sources/opendata.py`, `sources/checkbook.py` |
| Lavina Mital (lm4046) | Discretionary funding: `offline/build_funding.py`, `sources/funding.py` |
