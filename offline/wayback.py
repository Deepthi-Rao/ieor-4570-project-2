"""The Wayback Machine's CDX API: free, no key. Finds the last copy of a page saved before a date."""

import re
import time

import httpx

CDX = "https://web.archive.org/cdx/search/cdx"
ELECTION_DAY = "20251104"  # the 2025 general election: promises are what the page said before this


def last_snapshot_before(url: str, before: str = ELECTION_DAY, retries: int = 3) -> dict | None:
    """Return {timestamp, original, archived_url} for the last 200 OK copy before `before`, or None."""
    params = {"url": url, "output": "json", "to": before, "filter": "statuscode:200", "limit": -1, "fl": "timestamp,original"}
    for attempt in range(retries):
        try:
            r = httpx.get(CDX, params=params, timeout=60)
            r.raise_for_status()
            rows = r.json() if r.text.strip() else []
            break
        except (httpx.HTTPError, ValueError):
            time.sleep(5 * (attempt + 1))  # the CDX server is slow and rate-limits: back off
    else:
        return None
    if len(rows) < 2:  # the first row is the header
        return None
    timestamp, original = rows[-1]
    return {
        "timestamp": timestamp,
        "original": original,
        "archived_url": f"https://web.archive.org/web/{timestamp}/{original}",
        "raw_url": f"https://web.archive.org/web/{timestamp}id_/{original}",  # id_: the page without the archive's toolbar
    }


def archived_pages(url: str, path_pattern, before: str = ELECTION_DAY, limit: int = 3000) -> list[str]:
    """Every page on url's site archived before `before` whose path matches path_pattern.

    Finds policy subpages a JavaScript-rendered site never links to in its HTML."""
    host = url.split("//", 1)[-1].split("/", 1)[0].removeprefix("www.")
    params = {"url": f"{host}/", "matchType": "prefix", "to": before, "filter": "statuscode:200",
              "collapse": "urlkey", "fl": "original", "limit": limit}
    for attempt in range(3):
        try:
            r = httpx.get(CDX, params=params, timeout=120)
            r.raise_for_status()
            break
        except httpx.HTTPError:
            time.sleep(5 * (attempt + 1))
    else:
        print(f"  (couldn't list archived pages for {host})")
        return []
    pages = []
    for line in r.text.splitlines():
        page = line.split("?")[0].split("#")[0].rstrip("/")
        path = page.split(host, 1)[-1]
        if "%" in path or "." in path.rsplit("/", 1)[-1] or not path_pattern.search(path):
            continue  # skip escaped junk (%5C), files, and pages that aren't about policy
        if re.match(r"^/[a-z]{2}(-[A-Z]{2})?/", path):
            continue  # translations of the English pages
        if page not in pages:
            pages.append(page)
    return pages
