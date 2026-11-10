"""Build data/legistar.duckdb: this Council term's bills, sponsors and roll-call votes.

Needs LEGISTAR_TOKEN in .env (request it from the Council: it's emailed by hand, so ask early).
Takes a while: one request per bill for sponsors, and a few more per bill that reached a vote.

    uv run offline/build_legistar.py
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import duckdb
import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")
API = "https://webapi.legistar.com/v1/nyc"
OUT = ROOT / "data" / "legistar.duckdb"
TERM_START = "2026-01-01"
PAGE = 1000


def get(path: str, params: dict | None = None):
    for attempt in range(4):
        try:
            r = httpx.get(f"{API}/{path}", params={**(params or {}), "token": os.environ["LEGISTAR_TOKEN"]}, timeout=90)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def all_matters() -> list[dict]:
    out, skip = [], 0
    while True:
        page = get("matters", {"$filter": f"MatterIntroDate ge datetime'{TERM_START}'", "$top": PAGE, "$skip": skip})
        out += page
        if len(page) < PAGE:
            return out
        skip += PAGE


def votes_for(matter: dict) -> list[tuple]:
    mid, rows = matter["MatterId"], []
    for h in get(f"matters/{mid}/histories"):
        if not h.get("MatterHistoryTally") or not h.get("MatterHistoryEventId"):
            continue
        for item in get(f"events/{h['MatterHistoryEventId']}/eventitems"):
            if item.get("EventItemMatterId") != mid or not item.get("EventItemTally"):
                continue
            for v in get(f"eventitems/{item['EventItemId']}/votes"):
                rows.append((mid, (h.get("MatterHistoryActionDate") or "")[:10], h.get("MatterHistoryActionName"),
                             h.get("MatterHistoryActionBodyName"), h.get("MatterHistoryTally"),
                             h.get("MatterHistoryPassedFlag"), v.get("VotePersonName"), v.get("VoteValueName")))
    return rows


def main() -> None:
    if not os.environ.get("LEGISTAR_TOKEN"):
        sys.exit("Set LEGISTAR_TOKEN in .env first.")
    matters = all_matters()
    print(f"{len(matters)} matters since {TERM_START}")
    with ThreadPoolExecutor(8) as pool:
        sponsors = list(pool.map(lambda m: get(f"matters/{m['MatterId']}/sponsors"), matters))
        voted = [m for m in matters if m.get("MatterPassedDate") or (m.get("MatterStatusName") or "").lower() in ("adopted", "enacted", "approved", "failed", "vetoed")]
        votes = [row for rows in pool.map(votes_for, voted) for row in rows]
    print(f"{sum(map(len, sponsors))} sponsorships, {len(votes)} votes on {len(voted)} bills")

    tmp = OUT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
    con.execute("INSERT INTO meta VALUES ('built_at', ?), ('term_start', ?)", [date.today().isoformat(), TERM_START])
    con.execute("CREATE TABLE matters (matter_id INTEGER, file VARCHAR, title VARCHAR, type VARCHAR, status VARCHAR, intro_date DATE, passed_date DATE, guid VARCHAR)")
    con.executemany("INSERT INTO matters VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
        (m["MatterId"], m["MatterFile"], m["MatterTitle"], m.get("MatterTypeName"), m.get("MatterStatusName"),
         (m.get("MatterIntroDate") or None) and m["MatterIntroDate"][:10], (m.get("MatterPassedDate") or None) and m["MatterPassedDate"][:10],
         m["MatterGuid"]) for m in matters])
    con.execute("CREATE TABLE sponsors (matter_id INTEGER, name VARCHAR, sequence INTEGER)")
    con.executemany("INSERT INTO sponsors VALUES (?, ?, ?)", [
        (m["MatterId"], s["MatterSponsorName"], s.get("MatterSponsorSequence", 0)) for m, ss in zip(matters, sponsors) for s in ss])
    con.execute("CREATE TABLE votes (matter_id INTEGER, date DATE, action VARCHAR, body VARCHAR, tally VARCHAR, passed INTEGER, person VARCHAR, vote VARCHAR)")
    con.executemany("INSERT INTO votes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [tuple(v[:1]) + ((v[1] or None),) + tuple(v[2:]) for v in votes])
    con.close()
    tmp.replace(OUT)
    print(f"Wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
