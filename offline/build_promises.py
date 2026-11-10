"""Build data/promises.jsonl: each official's campaign promises, from archived campaign pages.

For every URL in data/officials.json's campaign_urls (and voter_guide_url, if set):
  1. Ask the Wayback Machine for the last copy saved before Election Day 2025.
  2. Fetch that copy and strip it to text.
  3. Have Gemini pull out each promise, with the sentence it came from, and flag it
     "specific" (checkable against data: a number, place, date or named program) or "vague".
  4. Keep only promises whose quote really appears in the page, so nothing is invented.

One line per promise, with the archived URL and snapshot date to cite. Hand-check the officials
you use in demos: extraction is a model's reading of a page.

    uv run offline/build_promises.py                 # every official with campaign_urls
    uv run offline/build_promises.py "Mamdani"       # just officials whose name matches
"""

import asyncio
import json
import re
import sys
import unicodedata
from pathlib import Path

from urllib.parse import urljoin, urlparse

import httpx
import litellm
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).parent.parent))
from offline.wayback import archived_pages, last_snapshot_before  # noqa: E402
from sources.web import html_to_text  # noqa: E402

DATA = Path(__file__).parent.parent / "data"
OUT = DATA / "promises.jsonl"
MODEL = "vertex_ai/gemini-3.5-flash"
MAX_PAGE_CHARS = 40_000
# Campaign sites put detail on subpages ("/policies/free-buses"). Follow links like these, same site only.
POLICY_LINK = re.compile(r"/(polic(?!y)|platform|issue|plan|agenda|priorit)", re.I)  # not privacy-policy
MAX_SUBPAGES = 12

PROMPT = """\
Below is the text of a 2025 NYC campaign page for {name} ({role}{district}).
List every policy promise on it: something the candidate says they will do, fund, build, pass,
stop or change if elected. Skip biography, endorsements and attacks on opponents.

Reply with a JSON array only. One object per promise:
  "promise":   the promise in one plain sentence, in your words
  "quote":     the exact sentence or phrase from the page that makes it, copied character for character
  "category":  one of housing, transit, public safety, education, childcare, health, environment,
               parks, economy, labor, taxes, immigration, government, other
  "specific":  true if it can be checked against data (a number, place, date or named program), false if vague
  "location":  the neighborhood, district, street or site it names, or ""
  "target":    the number or deadline it commits to, or ""

PAGE TEXT:
{text}
"""


def _squash(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\W+", " ", s).lower().strip()


async def extract(person: dict, text: str) -> list[dict]:
    district = f", District {person['district']}" if person.get("district") else ""
    r = await litellm.acompletion(
        model=MODEL, vertex_location="global", timeout=180, num_retries=2,
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": PROMPT.format(name=person["name"], role=person["role"], district=district, text=text)}],
    )
    raw = r.choices[0].message.content
    data = json.loads(raw[raw.find("["): raw.rfind("]") + 1] if "[" in raw else raw)
    return data if isinstance(data, list) else data.get("promises", [])


def policy_links(html: str, page_url: str) -> list[str]:
    """Same-site links to policy subpages, as live URLs (not archive URLs)."""
    host = urlparse(page_url).hostname.removeprefix("www.")
    links = []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        url = urljoin(page_url, a["href"]).split("#")[0].split("?")[0].rstrip("/")
        if "web.archive.org/web/" in url:  # an archived link: take the original back out
            url = re.sub(r"^https?://web\.archive\.org/web/\d+[a-z_]*/", "", url)
        if (urlparse(url).hostname or "").removeprefix("www.") == host and POLICY_LINK.search(urlparse(url).path):
            if url not in links and url.rstrip("/") != page_url.rstrip("/"):
                links.append(url)
    return links[:MAX_SUBPAGES]


async def promises_for(person: dict) -> list[dict]:
    queue = list(person.get("campaign_urls", [])) + ([person["voter_guide_url"]] if person.get("voter_guide_url") else [])
    seen: set[str] = set()
    out = []
    while queue:
        url = queue.pop(0)
        if url.rstrip("/") in seen:
            continue
        seen.add(url.rstrip("/"))
        snap = await asyncio.to_thread(last_snapshot_before, url)
        if not snap:
            print(f"  {url}: no copy archived before Election Day")
            continue
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            page = await client.get(snap["raw_url"])
        if url in person.get("campaign_urls", []):  # only the pages you listed fan out, one level deep
            # Links in the HTML, plus pages the archive saved that a JavaScript-rendered site never links to
            found = policy_links(page.text, snap["original"]) + await asyncio.to_thread(archived_pages, url, POLICY_LINK)
            queue += [u for u in dict.fromkeys(found) if u.rstrip("/") not in seen][:MAX_SUBPAGES]
        _, text = html_to_text(page.text)
        found = await extract(person, text[:MAX_PAGE_CHARS])
        page_words = _squash(text)
        kept = 0
        for p in found:
            # Drop anything the model didn't copy from the page: a promise we can't quote, we can't cite.
            if not p.get("promise") or _squash(p.get("quote", ""))[:80] not in page_words:
                continue
            kept += 1
            out.append({
                "official": person["name"], "role": person["role"], "district": person.get("district"),
                "promise": p["promise"], "quote": p["quote"], "category": p.get("category", "other"),
                "specific": bool(p.get("specific")), "location": p.get("location", ""), "target": p.get("target", ""),
                "source_url": snap["archived_url"], "original_url": snap["original"],
                "snapshot_date": f"{snap['timestamp'][:4]}-{snap['timestamp'][4:6]}-{snap['timestamp'][6:8]}",
                "hand_checked": False,
            })
        print(f"  {url}: snapshot {snap['timestamp'][:8]}, {kept} of {len(found)} promises kept")
    return out


async def main(name_filter: str) -> None:
    officials = json.loads((DATA / "officials.json").read_text())
    chosen = [o for o in officials if (o.get("campaign_urls") or o.get("voter_guide_url"))
              and name_filter.lower() in o["name"].lower()]
    existing = [json.loads(l) for l in OUT.read_text().splitlines() if l.strip()] if OUT.exists() else []
    rebuilt = {o["name"] for o in chosen}
    # Keep promises already hand-checked: re-extraction would throw that work away.
    keep = [p for p in existing if p["official"] not in rebuilt or p.get("hand_checked")]
    checked = {(p["official"], p["quote"]) for p in keep}
    new = []
    for person in chosen:
        print(person["name"])
        new += [p for p in await promises_for(person) if (p["official"], p["quote"]) not in checked]
    OUT.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in keep + new))
    print(f"Wrote {len(keep) + len(new)} promises to {OUT}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else ""))
