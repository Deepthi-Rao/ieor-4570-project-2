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
            │  reads findings; sends more Researchers for gaps (≤ 2 rounds, ≤ 8 Researchers, 180 s budget)
            ▼
         Writer (findings only → cited verdict) ─► Reviewer (neutrality + every fact/link checked against findings) ─► report
```

- **Five agent shapes:** Lead, Researcher (4 specialist kinds), Writer, Reviewer. The Reviewer is the "neutrality reviewer" from our data plan.
- **Parallel fan-out:** the Lead calls `research` several times in one turn. Each Researcher is a fresh `Agent` run with its own context and tools (`app.py: research`).
- **Goes back for gaps:** the Lead reads the findings and sends more Researchers for any missing layer, up to 2 more rounds. Code caps it: `MAX_RESEARCHERS = 8`, `RESEARCH_SECONDS = 180`, `LEAD_TURNS = 10`, `RESEARCHER_TURNS = 10`, which keeps every answer inside the graders' 5 minutes.
- **Clarification:** a question without an official and a promise/topic ("Is the city doing better?") gets `CLARIFY:` with examples drawn from the real roster. Your answer continues the same session.
- **Citations:** every tool returns a `source_url` (archived page, dataset, Legistar bill, Checkbook, news article). Grounded search links are resolved to the real article URL. The Writer may only use links that appear in the findings, and the Reviewer removes any that don't.
- **Admits what the data can't answer:** tools return explicit "no evidence found" and coverage notes (e.g. funding data ends in FY2021), and the Writer separates "no evidence" from "didn't happen".

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
| Did | NYC Open Data [Council Discretionary Funding](https://data.cityofnewyork.us/d/4d7f-74pe) | `data/funding/*.csv.gz`, queried with DuckDB | `uv run offline/build_funding.py [extra CSV URLs]` |
| Happened | NYC Open Data (any dataset: housing production, 311, parks, …) | Live: Socrata catalog + SoQL (`search_datasets`, `query_open_data`) | — (`SOCRATA_APP_TOKEN` optional) |
| Happened | [Checkbook NYC API](https://www.checkbooknyc.com/check-book-nyc-api) contracts and spending | Live: `search_spending` | — |
| Happened | News | Live: Gemini with Google Search grounding (`web_search`) | — |

All shipped data is committed (largest file ~10 MB gzipped), so the Cloud Run deploy has it with no bucket.

### Known gaps (the agent says so when they matter)

- **Legistar token:** the Council emails it by hand. Until `LEGISTAR_TOKEN` is set (locally in `.env`, or on Cloud Run as a variable) and `build_legistar.py` has run, the legislation researcher is disabled and reports say bills and votes couldn't be checked.
- **Discretionary funding** on NYC Open Data stops at **FY2021**, so it can't show current-term awards. Add the Council's FY2026/FY2027 discretionary funding files with `build_funding.py <csv-url>`: any CSV in `data/funding/` is searched automatically.
- **Checkbook NYC** sits behind bot protection that can block bursts. Requests go one at a time, and a block is reported as "unavailable", not as "no spending".
- **Promise corpus:** the mayor's archived platform and policy pages are built (56 promises, 32 specific). Council members' campaign sites come from `--discover` and need a hand check before demos.
- **Matching promises to evidence** is the hard part. When no dataset measures a promise, the outcomes researcher says so instead of stretching a loosely related one.

## Sample queries

SAMPLES_PLACEHOLDER

## Run it locally

```bash
gcloud auth application-default login       # Gemini on Vertex AI
cp .env.example .env                         # optional: LEGISTAR_TOKEN, SOCRATA_APP_TOKEN
uv run uvicorn app:app --port 8000           # http://localhost:8000
uv run offline/run_samples.py                # re-run the README samples (agent + no-tools baseline)
```

Deploys continuously to Cloud Run from `main` (buildpack, entrypoint `uvicorn app:app --host 0.0.0.0 --port $PORT`).

## Team and data sources

One data source per team member, from our data plan:

| Owner | Source |
|---|---|
| _TBD_ | Promise corpus (Wayback Machine, voter guide): `offline/build_promises.py`, `sources/promises.py` |
| _TBD_ | Legistar bills and votes: `offline/build_legistar.py`, `sources/legistar.py` |
| _TBD_ | NYC Open Data and Checkbook outcomes: `sources/opendata.py`, `sources/checkbook.py` |
| _TBD_ | Discretionary funding: `offline/build_funding.py`, `sources/funding.py` |
| _TBD_ | News search and the neutrality Reviewer: `sources/web.py`, `reviewer` in `app.py` |
