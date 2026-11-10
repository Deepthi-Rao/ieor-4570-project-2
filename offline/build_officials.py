"""Build data/officials.json: every NYC official in scope, with the campaign pages to archive.

Council members come from NYC Open Data's "City Council Members (1999 to Present)" (uvw5-9znb):
everyone whose term started on or after 2026-01-01. The mayor is added by hand.

campaign_urls are the pages build_promises.py reads from the Wayback Machine. They start empty.
Fill them in by hand, or run with --discover to have Gemini (with Google Search) suggest each
member's 2025 campaign site, then check the suggestions: the script keeps a URL only if the
Wayback Machine has a copy from before Election Day.

    uv run offline/build_officials.py              # roster only, keeps campaign_urls you've added
    uv run offline/build_officials.py --discover   # also suggest campaign sites for members without one
"""

import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent.parent))
from offline.wayback import last_snapshot_before  # noqa: E402

OUT = Path(__file__).parent.parent / "data" / "officials.json"
ROSTER = "https://data.cityofnewyork.us/resource/uvw5-9znb.json"

MAYOR = {
    "name": "Zohran Mamdani",
    "role": "Mayor",
    "district": None,
    "term_start": "2026-01-01",
    "campaign_urls": ["https://www.zohranfornyc.com/platform"],
}


def roster() -> list[dict]:
    rows = httpx.get(ROSTER, params={"$where": "term_start >= '2026-01-01'", "$order": "district", "$limit": 200}, timeout=60).json()
    return [
        {"name": " ".join(r["name"].split()), "role": "Council Member", "district": int(r["district"]),
         "term_start": r["term_start"][:10], "term_end": r.get("term_end", "")[:10], "campaign_urls": []}
        for r in rows if r.get("district")
    ]


async def discover(person: dict) -> list[str]:
    import litellm

    prompt = (
        f"What was the official 2025 campaign website of {person['name']}, NYC City Council District "
        f"{person['district']}? Reply with only the URL of its issues or platform page (or the home page if "
        "there's no issues page), or NONE."
    )
    r = await litellm.acompletion(model="vertex_ai/gemini-3.5-flash", vertex_location="global",
                                  messages=[{"role": "user", "content": prompt}], tools=[{"googleSearch": {}}], timeout=90)
    text = (r.choices[0].message.content or "").strip()
    urls = [w.strip("<>()[].,") for w in text.split() if w.startswith("http")]
    kept = []
    for url in urls[:2]:
        if last_snapshot_before(url):  # only pages archived before Election Day are usable
            kept.append(url)
    return kept


async def main(discover_sites: bool) -> None:
    previous = {o["name"]: o for o in json.loads(OUT.read_text())} if OUT.exists() else {}
    officials = [MAYOR] + roster()
    for o in officials:
        o["campaign_urls"] = previous.get(o["name"], {}).get("campaign_urls") or o["campaign_urls"]
    if discover_sites:
        todo = [o for o in officials if not o["campaign_urls"]]
        print(f"Discovering campaign sites for {len(todo)} officials...")
        for o in todo:  # one at a time: the Wayback Machine rate-limits
            try:
                o["campaign_urls"] = await discover(o)
                o["campaign_urls_unverified"] = True  # a model suggested these: check them by hand
            except Exception as e:
                print(f"  {o['name']}: {type(e).__name__}: {e}")
            print(f"  D{o['district']} {o['name']}: {o['campaign_urls'] or 'none archived'}")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(officials, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote {len(officials)} officials to {OUT}")


if __name__ == "__main__":
    asyncio.run(main("--discover" in sys.argv))
