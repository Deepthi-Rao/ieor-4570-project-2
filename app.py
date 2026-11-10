"""Promise Tracker: did NYC officials elected in 2025 do what they promised?

Deep research over three layers of evidence:
  1. what they promised   archived campaign pages (offline corpus), voter guide, mayoral releases
  2. what they did        Council bills, sponsors and votes (Legistar); discretionary funding
  3. what happened        NYC Open Data, Checkbook NYC spending and contracts, news

A Lead checks the question is specific enough (or asks), looks up the promise, then fans out
specialist Researchers in parallel, each in a fresh context with its own tools. It sends more when
the findings leave a gap. A Writer turns the findings into a cited verdict, and a Reviewer checks
the draft for neutrality and for claims or links the findings don't support.
"""

import asyncio
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

import uvicorn
from agents import Agent, ModelSettings, RunContextWrapper, Runner, function_tool, set_tracing_disabled
from agents.extensions.models.litellm_model import LitellmModel
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from context_panel import AgentContexts, PanelHooks, add_context_panel
from sources import checkbook, funding, legistar, opendata, promises, web

# LEGISTAR_TOKEN and SOCRATA_APP_TOKEN (both optional) come from .env locally, or Cloud Run's variables.
load_dotenv(Path(__file__).parent / ".env")

# Traces upload to OpenAI by default, which needs an OpenAI key. We're on Gemini.
set_tracing_disabled(True)

# LiteLLM routes the call to Gemini. api_key is unused: Vertex AI uses your gcloud credentials.
MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash", api_key="unused")
RESEARCHER_MODEL = LitellmModel(model="vertex_ai/gemini-3.5-flash-lite", api_key="unused")
# A stalled model call would hang the page: give up after 90 s, and retry twice.
SETTINGS = ModelSettings(extra_args={"vertex_location": "global", "timeout": 90, "num_retries": 2})

# Graders wait up to 5 minutes per answer. Research stops at this many seconds so the Writer and
# Reviewer have time to finish; prompts ask for restraint, and code enforces it.
RESEARCH_SECONDS = 180
MAX_RESEARCHERS = 8
LEAD_TURNS = 10
RESEARCHER_TURNS = 10
TOOL_RESULT_CHARS = 2_000  # how much of each tool result /chat returns in tool_calls


@dataclass
class Research:
    """One run's state, shared by every agent and tool in it. The model never sees it."""

    hooks: PanelHooks
    deadline: float
    researchers: int = 0
    findings: list[str] = field(default_factory=list)


# --- Tools: thin wrappers over sources/. Blocking HTTP runs in a thread so parallel
# researchers really run in parallel instead of queueing on the event loop. ---


@function_tool
async def list_officials() -> str:
    """List every official in scope (the mayor and the 51 Council members elected in 2025), with district and how many promises are on file."""
    return promises.list_officials()


@function_tool
async def get_promises(ctx: RunContextWrapper[Research], official: str, topic: str = "", specific_only: bool = False) -> str:
    """Get an official's campaign promises from the archived campaign-page corpus.
    Each promise has the exact quote, whether it's specific (checkable) or vague, and the archived URL to cite.

    Args:
        official: A name ('Mamdani', 'Caban'), 'mayor', or a district ('District 33').
        topic: Optional words to filter by, e.g. 'rent' or 'buses'.
        specific_only: True for only the promises specific enough to check against data.
    """
    result = promises.get_promises(official, topic, specific_only)
    # The Writer sees only findings: put the promise wording and its archived link there too.
    entry = f"Task (promises): get_promises(official={official!r}, topic={topic!r}, specific_only={specific_only})\n\n{result}"
    if entry not in ctx.context.findings:
        ctx.context.findings.append(entry)
    return result


@function_tool
async def search_legislation(sponsor: str = "", keyword: str = "") -> str:
    """Search NYC Council bills introduced this term (since 2026-01-01) by sponsor and/or title keyword.
    Returns file numbers, titles, status, sponsors and a Legistar link for each.

    Args:
        sponsor: A Council member's last name, e.g. 'Restler'.
        keyword: A word or phrase in the bill's title, e.g. 'rent' or 'bus lane'.
    """
    return await asyncio.to_thread(legistar.search_legislation, sponsor, keyword)


@function_tool
async def search_legislation_live(sponsor: str = "", keyword: str = "") -> str:
    """Like search_legislation, but against the live Legistar API: for bills newer than the snapshot.

    Args:
        sponsor: A Council member's last name.
        keyword: A word or phrase in the bill's title.
    """
    return await asyncio.to_thread(legistar.search_legislation_live, sponsor, keyword)


@function_tool
async def get_votes(bill: str) -> str:
    """Get the roll-call votes on one bill: how each member voted, at each vote.

    Args:
        bill: The bill's file number (e.g. 'Int 0123-2026') or matter_id from search_legislation.
    """
    return await asyncio.to_thread(legistar.get_votes, bill)


@function_tool
async def search_datasets(query: str) -> str:
    """Find NYC Open Data datasets about a topic. Returns each dataset's id, columns and when it was last updated.

    Args:
        query: A topic, e.g. 'affordable housing production', '311 heat complaints', 'bus speeds'.
    """
    return await asyncio.to_thread(opendata.search_datasets, query)


@function_tool
async def query_open_data(dataset_id: str, where: str = "", select: str = "", group: str = "", order: str = "", limit: int = 20) -> str:
    """Query one NYC Open Data dataset with SoQL (Socrata's SQL). Use column names from search_datasets.

    Args:
        dataset_id: The dataset id, e.g. 'hg8x-zxpr'.
        where: A SoQL filter, e.g. "council_district = '33' AND project_start_date >= '2026-01-01'".
        select: Columns or aggregates, e.g. "borough, sum(all_counted_units)".
        group: Group-by columns, e.g. 'borough'.
        order: Sort, e.g. 'project_start_date DESC'.
        limit: Rows to return, at most 50.
    """
    return await asyncio.to_thread(opendata.query_open_data, dataset_id, where, select, group, order, limit)


@function_tool
async def search_spending(kind: str = "contracts", agency: str = "", vendor: str = "", fiscal_year: int = 2026, keyword: str = "") -> str:
    """Search Checkbook NYC for city contracts or payments: did a promised program get funded and launched?

    Args:
        kind: 'contracts' (registered expense contracts) or 'spending' (payments).
        agency: A 3-digit city agency code, or a name like 'hpd', 'parks', 'dot', 'dsny', 'dycd', 'dhs', 'education'.
        vendor: A payee name, e.g. 'City Harvest'. Only with kind='spending': contracts can't be searched by vendor.
        fiscal_year: City fiscal year; FY2026 runs July 2025–June 2026, FY2027 July 2026–June 2027.
        keyword: For contracts, words in the contract's purpose, e.g. 'tree planting' (searched by Checkbook).
            For spending, a filter on the payments returned.
    """
    return await checkbook.search_spending(kind, agency, vendor, fiscal_year, keyword)


@function_tool
async def search_discretionary_funding(keyword: str = "", council_member: str = "", fiscal_year: str = "") -> str:
    """Search Council discretionary funding awards: which organizations and projects a member steered money to.
    Covers FY2027 (the budget this Council adopted in June 2026, with each award's page in the PDF) and FY2009–FY2021.
    Returns the number and total of matching awards, and the largest ones.

    Args:
        keyword: An organization, program, park or school name, e.g. 'McCarren' or 'park'.
        council_member: A Council member's name ('Restler', 'Pierina Sanchez') or a district ('District 33').
        fiscal_year: e.g. '2027'. There's no data for FY2022–FY2026.
    """
    return await asyncio.to_thread(funding.search_discretionary_funding, keyword, council_member, fiscal_year)


@function_tool
async def web_search(query: str) -> str:
    """Search the news. Returns a summary and the source URLs it came from: cite the URLs.

    Args:
        query: What to find, with the official's name and the topic, e.g. 'Mamdani free buses 2026'.
    """
    return await web.web_search(query)


@function_tool
async def fetch_page(url: str) -> str:
    """Read a page on an official or archival site: nyc.gov press releases and executive orders,
    council.nyc.gov, the Wayback Machine, the Campaign Finance Board's voter guide.

    Args:
        url: The page's URL.
    """
    return await web.fetch_page(url)


# --- Researchers: one specialist per evidence layer, each run in a fresh context ---

CITE = """\
Reply with the facts that answer your task and nothing else, at most 10 bullets. End every bullet
with a markdown link to the exact source it came from, using the source_url the tool returned,
e.g. ([NYC Open Data: Housing New York](https://data.cityofnewyork.us/d/hg8x-zxpr)).
Give numbers and dates exactly as the tools return them. If a tool returns an error or no results,
say so in a bullet: "no evidence found" is a finding. Never fill a gap from memory."""

RESEARCHERS = {
    "promises": (
        "You find exactly what an NYC official promised. Start with get_promises. If the corpus has nothing, "
        "look for the promise in the official's own words: the mayor's press releases and executive orders on nyc.gov, "
        "or an archived campaign page (fetch_page on web.archive.org). Quote the promise and give its date.",
        [get_promises, fetch_page, web_search],
    ),
    "legislation": (
        "You find what an NYC Council member did in office about a promise: bills they sponsored, their status, "
        "and how members voted. Search by sponsor and by keyword. Bill titles are formal ('rent stabilization', "
        "'heat and hot water'), so try two or three phrasings. Then get_votes on the bills that matter.",
        [search_legislation, search_legislation_live, get_votes],
    ),
    "outcomes": (
        "You find what actually happened on the ground. Find a NYC Open Data dataset that measures the promise "
        "(search_datasets), check its columns and data_updated_at, then query it for this term (2026 on) and, for "
        "a baseline, the year before. Use search_spending for whether a program got contracts or money, and "
        "search_discretionary_funding for money a member steered to a named place. If no dataset measures the "
        "promise, say so plainly: don't stretch a loosely related one to fit.",
        [search_datasets, query_open_data, search_spending, search_discretionary_funding],
    ),
    "news": (
        "You find news coverage of whether a promise launched, stalled or was blocked, and why. Run web_search "
        "with the official's name and the specific program. Report dated events, and who said what. Read official "
        "pages the results point to with fetch_page. Note when sources disagree.",
        [web_search, fetch_page],
    ),
}


def researcher(kind: str, number: int) -> Agent:
    instructions, tools = RESEARCHERS[kind]
    return Agent(
        name=f"Researcher {number} ({kind})",
        instructions=f"{instructions}\n\nToday is {time.strftime('%Y-%m-%d')}.\n\n{CITE}",
        model=RESEARCHER_MODEL,
        model_settings=SETTINGS,
        tools=tools,
    )


@function_tool
async def research(ctx: RunContextWrapper[Research], task: str, kind: str) -> str:
    """Send a specialist researcher to answer one task, in its own fresh context. Returns its cited findings.

    Args:
        task: One specific thing to find out. Name the official, the promise and the time window.
        kind: 'promises' (what they promised), 'legislation' (Council bills and votes),
              'outcomes' (Open Data, city spending, discretionary funding), or 'news' (news coverage).
    """
    if kind not in RESEARCHERS:
        return f"Unknown kind '{kind}'. Use one of: {', '.join(RESEARCHERS)}."
    if kind == "legislation" and not legistar.available():
        return ("Council legislation data isn't loaded in this deployment (no Legistar snapshot or token). "
                "Don't send legislation researchers; the writer will say bills and votes couldn't be checked.")
    if ctx.context.researchers >= MAX_RESEARCHERS:
        return "No researchers left. Reply DONE."
    if time.monotonic() > ctx.context.deadline:
        return "Out of research time. Reply DONE: the writer will note what's unanswered."
    # What researcher.as_tool() does: run it in a fresh context and return its answer.
    # Written out so each run gets a numbered copy, and its own column in the context panel.
    ctx.context.researchers += 1
    agent = researcher(kind, ctx.context.researchers)
    try:
        result = await asyncio.wait_for(
            Runner.run(agent, f"Task: {task}", context=ctx.context, hooks=ctx.context.hooks, max_turns=RESEARCHER_TURNS),
            timeout=max(30, ctx.context.deadline - time.monotonic() + 30),
        )
        findings = str(result.final_output)
    except Exception as e:  # out of turns or time: the Lead and Writer should know this part is missing
        findings = f"- This researcher didn't finish ({type(e).__name__}); treat this task as unanswered."
    ctx.context.findings.append(f"Task ({kind}): {task}\n\n{findings}")
    return findings


# --- The Lead: clarifies, plans, fans out, checks for gaps ---


def roster() -> str:
    """Who's in scope, from data/officials.json, so the Lead never names officials from memory."""
    lines = []
    for o in promises.load_officials():
        where = f"District {o['district']}" if o.get("district") else o["role"]
        lines.append(f"- {where}: {o['name']}" + (f" (left office {o['term_end']})" if o.get("term_end", "9") < time.strftime("%Y-%m-%d") else ""))
    return "\n".join(lines)


lead = Agent(
    name="Lead",
    instructions=f"""\
You lead a team that checks whether NYC officials elected in 2025 (the mayor and City Council
members, in office since January 2026) kept their campaign promises. Today is {time.strftime('%Y-%m-%d')}.

1. Decide if the question is specific enough. It needs an official (a name, 'the mayor', or a
   district) and a promise or topic, or a clear ask like "which of X's promises are specific?".
   If either is missing, or the official isn't in scope, reply with exactly
   CLARIFY: <one short question that would make it answerable, with 2-3 concrete examples>
   and stop. Don't research a vague question. Examples must use officials from the roster
   below, with their right titles: never name an official from memory.
2. Call get_promises for the official and topic, so you know the exact promise wording.
   If there's no promise on file, a 'promises' researcher can look further.
3. Split the check into 2 to 4 independent tasks and call research once per task, all in the
   same turn, so the researchers work in parallel. Cover the layers that fit the promise:
   what they promised ('promises', if not on file), what they did ('legislation' for Council
   members; for the mayor, 'news' about executive actions), and what happened ('outcomes', 'news').
4. Read the findings. If a layer the verdict depends on is still empty, call research again for
   just that gap. Do this at most twice.
5. When the question is covered, or the gaps can't be filled, reply DONE and nothing else.

Officials in scope:
{roster()}""",
    model=MODEL,
    model_settings=SETTINGS,
    tools=[list_officials, get_promises, research],
)

# --- The Writer and the Reviewer: one call each, after the research ends ---

writer = Agent(
    name="Writer",
    instructions="""\
Write a short report in markdown that answers the question using only the findings you're given.

- Answer the question that was asked. If it asks for something other than whether a promise was
  kept (e.g. "which promises are specific?"), answer that directly, as a list, and skip verdicts.
- When checking whether a promise was kept, start with a verdict line for each promise checked:
  **Kept**, **In progress**, **No action found**, **Broken** (only if the findings show it was
  dropped or reversed), or **Can't determine** (the data doesn't measure it), then one sentence on why.
  Then three short sections: What they promised (quote it, with the archived link and date),
  What they did, What happened. In each, say plainly if the findings have nothing.
- Keep the links: end every sentence that states a fact with the link from its finding.
  Only use links that appear in the findings.
- "No evidence found" is not the same as "didn't happen": say which one the findings support.
- Ten months is early for multi-year promises: compare progress to the promise's own timeline.
- Write plain, specific, neutral sentences. No adjectives that judge the official.""",
    model=MODEL,
    model_settings=SETTINGS,
)

reviewer = Agent(
    name="Reviewer",
    instructions="""\
You review a draft promise-tracking report for neutrality and accuracy before it's published.
You get the question, the researchers' findings and the draft. Check every sentence:

- Every fact must appear in the findings, and its link must be the one the findings give for it.
  Remove facts the findings don't support, and fix or remove wrong links.
- The verdict must follow from the findings. "Broken" needs evidence the promise was dropped or
  reversed; with no evidence either way, it's "Can't determine" or "No action found".
- Language must be neutral: no loaded or partisan words, and the same standard for every official.
- Gaps must be stated, not papered over.

Reply with the corrected report in markdown and nothing else. If the draft is fine, return it unchanged.""",
    model=MODEL,
    model_settings=SETTINGS,
)


def transcript(conversation: list[dict]) -> str:
    """The user's question, plus any clarifying exchange, as one prompt."""
    return "\n".join(f"{'User' if t['role'] == 'user' else 'You asked'}: {t['content']}" for t in conversation)


async def investigate(conversation: list[dict], hooks: PanelHooks) -> tuple[str, str]:
    """Returns ("clarify", question) or ("report", markdown)."""
    run = Research(hooks=hooks, deadline=time.monotonic() + RESEARCH_SECONDS)

    # 1. Research: clarify or plan, fan out, check for gaps
    result = await Runner.run(lead, transcript(conversation), context=run, hooks=hooks, max_turns=LEAD_TURNS)
    answer = str(result.final_output).strip()
    if answer.upper().startswith("CLARIFY:") and not run.researchers:
        return "clarify", answer.split(":", 1)[1].strip()
    if not run.researchers and not run.findings:
        return "report", "The research team didn't gather any findings for this question, so there's nothing to report. Try naming an official and a promise."

    # 2. Write, then review: outside the loop. Both see only the question and the findings.
    question = transcript(conversation)
    findings = "\n\n---\n\n".join(run.findings)
    draft = await Runner.run(writer, f"Question:\n{question}\n\nFindings:\n\n{findings}", hooks=hooks)
    final = await Runner.run(
        reviewer, f"Question:\n{question}\n\nFindings:\n\n{findings}\n\nDraft report:\n\n{draft.final_output}", hooks=hooks
    )
    return "report", verify_links(str(final.final_output), findings)


LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")


def verify_links(report: str, findings: str) -> str:
    """The last check on citations, in code rather than a prompt: a link must appear in the findings.
    Any other link was made up by the Writer or Reviewer. Keep its text and drop the link."""
    def check(m: re.Match) -> str:
        url = m.group(2)
        if url in findings or unquote(url) in unquote(findings):
            return m.group(0)
        return f"{m.group(1)} (unverified source removed)"
    return LINK.sub(check, report)


class ToolCallHooks(PanelHooks):
    """PanelHooks that also keep every tool call, from every agent, for /chat's tool_calls."""

    def __init__(self, contexts: AgentContexts, emit):
        super().__init__(contexts, emit)
        self.tool_calls: list[dict] = []
        self._open: dict[str, dict] = {}

    async def on_tool_start(self, context, agent, tool):
        await super().on_tool_start(context, agent, tool)
        raw = getattr(context, "tool_arguments", None) or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = raw
        call = {"agent": agent.name, "name": tool.name, "args": args, "result": None}
        self.tool_calls.append(call)
        self._open[getattr(context, "tool_call_id", None) or f"{agent.name}:{tool.name}:{len(self.tool_calls)}"] = call

    async def on_tool_end(self, context, agent, tool, result):
        key = getattr(context, "tool_call_id", None)
        call = self._open.pop(key, None) if key else None
        if call is None:  # no id: match the oldest open call of this tool by this agent
            key = next((k for k, c in self._open.items() if c["agent"] == agent.name and c["name"] == tool.name), None)
            call = self._open.pop(key, None) if key else None
        if call is not None:
            text = str(result)
            call["result"] = text if len(text) <= TOOL_RESULT_CHARS else text[:TOOL_RESULT_CHARS] + " ...[truncated]"


# --- FastAPI App ---

HERE = Path(__file__).parent

# session_id -> the conversation so far (a question and any clarifying exchange). In-memory.
conversations: dict[str, list[dict]] = {}
# run_id -> every agent's context, in order. The context panel reads it.
sessions: dict[str, AgentContexts] = {}

app = FastAPI()
add_context_panel(app, sessions)


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]


def _remember(session_id: str, kind: str, text: str) -> None:
    """After a clarifying question, keep the conversation so the answer builds on it.
    After a report, start fresh: the next message is a new question."""
    if kind == "clarify":
        conversations[session_id].append({"role": "assistant", "content": text})
    else:
        conversations.pop(session_id, None)


@app.get("/")
def index():
    # no-cache: browsers otherwise keep serving an old page after a redeploy
    return FileResponse(HERE / "index.html", headers={"Cache-Control": "no-cache"})


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Project 1's response shape. Sub-agent tool calls are in tool_calls too, each with an agent field."""
    session_id = request.session_id or str(uuid.uuid4())
    conversations.setdefault(session_id, []).append({"role": "user", "content": request.message})
    hooks = ToolCallHooks(AgentContexts(), lambda event: None)
    try:
        kind, text = await investigate(conversations[session_id], hooks)
        _remember(session_id, kind, text)
    except Exception as e:
        # Auth, quota, a model that won't stop: say so in the response, not as a 500.
        text = f"The research failed: {type(e).__name__}: {str(e)[:300]}"
        conversations.pop(session_id, None)
    return ChatResponse(response=text, session_id=session_id, tool_calls=hooks.tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    conversations.pop(session_id, None)
    return {"status": "ok"}


@app.get("/run")
async def run(question: str, session_id: str = ""):
    """The page's endpoint: the same research as /chat, streamed as Server-Sent Events."""
    session_id = session_id or str(uuid.uuid4())
    conversations.setdefault(session_id, []).append({"role": "user", "content": question})
    run_id = str(uuid.uuid4())
    sessions[run_id] = AgentContexts()
    queue: asyncio.Queue = asyncio.Queue()
    hooks = ToolCallHooks(sessions[run_id], queue.put_nowait)

    async def work():
        try:
            kind, text = await investigate(conversations[session_id], hooks)
            _remember(session_id, kind, text)
            queue.put_nowait({"type": kind, "text": text, "tool_calls": hooks.tool_calls})
        except Exception as e:
            conversations.pop(session_id, None)
            queue.put_nowait({"type": "error", "message": f"{type(e).__name__}: {str(e)[:500]}"})

    task = asyncio.create_task(work())

    async def events():
        try:
            yield f"data: {json.dumps({'type': 'start', 'run_id': run_id, 'session_id': session_id})}\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event["type"] in ("report", "clarify", "error"):
                    break
        finally:
            task.cancel()  # the page closed the stream: stop spending tokens

    return StreamingResponse(events(), media_type="text/event-stream")


if __name__ == "__main__":
    # Cloud Run sets PORT and needs 0.0.0.0 (its entrypoint runs uvicorn directly). Locally: localhost:8000.
    uvicorn.run(app, host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", 8000)))
