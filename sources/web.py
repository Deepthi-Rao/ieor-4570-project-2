"""News search and page reading.

web_search: Gemini with Google Search grounding, in its own model call (a grounded call can't
also use function tools). Returns the summary plus the URLs it was grounded on, so every claim
can be traced to a page.

fetch_page: reads one page as text. Limited to official and archival sites, so a researcher can
read a press release, executive order or archived campaign page the search turned up.
"""

import asyncio
import re
from urllib.parse import urlparse

import httpx
import litellm
from bs4 import BeautifulSoup

from . import error, to_json

SEARCH_MODEL = "vertex_ai/gemini-3.5-flash-lite"
MAX_PAGE_CHARS = 10_000
ALLOWED_HOSTS = (
    "nyc.gov", "council.nyc.gov", "legistar.council.nyc.gov", "web.archive.org", "nyccfb.info", "voting.nyc",
    "checkbooknyc.com", "data.cityofnewyork.us", "comptroller.nyc.gov", "ibo.nyc.gov",
)


async def web_search(query: str) -> str:
    try:
        r = await litellm.acompletion(
            model=SEARCH_MODEL,
            vertex_location="global",
            timeout=60,
            messages=[{"role": "user", "content": (
                "Search the news for this and report only what the sources say, with dates. "
                "Keep each source's facts separate, and say when sources disagree.\n\n" + query
            )}],
            tools=[{"googleSearch": {}}],
        )
    except Exception as e:
        return error(f"Search failed: {type(e).__name__}: {str(e)[:300]}")
    sources = []
    for meta in getattr(r, "vertex_ai_grounding_metadata", None) or []:
        for chunk in meta.get("groundingChunks", []):
            web = chunk.get("web") or {}
            if web.get("uri") and web["uri"] not in (s["url"] for s in sources):
                sources.append({"title": web.get("title"), "url": web["uri"]})
    sources = await _resolve(sources)
    if not sources:
        return to_json({"summary": r.choices[0].message.content, "sources": [],
                        "note": "The search returned no sources: treat this summary as unverified and don't cite it."})
    return to_json({"summary": r.choices[0].message.content, "sources": sources,
                    "note": "Cite the source URLs, not the summary. Source titles are often just the publisher's domain."})


async def _resolve(sources: list[dict]) -> list[dict]:
    """Grounding returns Google redirect links that expire. Follow each to the real article URL."""
    async def one(src: dict) -> dict:
        if "grounding-api-redirect" not in src["url"]:
            return src
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
                r = await client.get(src["url"])
            if r.headers.get("location"):
                return {**src, "url": r.headers["location"]}
        except httpx.HTTPError:
            pass
        return src
    return list(await asyncio.gather(*(one(s) for s in sources)))


def _allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def html_to_text(html: str) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = re.sub(r"\n\s*\n+", "\n\n", soup.get_text("\n", strip=True))
    return title, text


async def fetch_page(url: str) -> str:
    if not _allowed(url):
        return error(f"fetch_page only reads official and archival sites, not {urlparse(url).hostname}.",
                     "For news sites, rely on web_search's summary and cite its source URLs.")
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (promise-tracker research agent)"}) as client:
            r = await client.get(url)
        r.raise_for_status()
    except Exception as e:
        return error(f"Could not fetch {url}: {type(e).__name__}: {e}")
    title, text = html_to_text(r.text)
    if len(text) > MAX_PAGE_CHARS:
        text = text[:MAX_PAGE_CHARS] + "\n\n[Page truncated.]"
    return to_json({"title": title, "source_url": str(r.url), "text": text})
