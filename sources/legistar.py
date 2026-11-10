"""Layer 2, what they did: NYC Council legislation from the Legistar Web API.

Two paths, as the data plan says:
  - data/legistar.duckdb, a snapshot of this term's bills, sponsors and votes, built offline by
    offline/build_legistar.py. Fast, and it works without a token at request time.
  - the live API, for anything newer than the snapshot. Needs LEGISTAR_TOKEN (emailed by the
    Council after you fill out its request form).
"""

import os

import duckdb
import httpx

from . import DATA, error, to_json

API = "https://webapi.legistar.com/v1/nyc"
SNAPSHOT = DATA / "legistar.duckdb"
TERM_START = "2026-01-01"
MAX_BILLS = 25


def bill_url(matter_id, guid) -> str:
    return f"https://legistar.council.nyc.gov/LegislationDetail.aspx?ID={matter_id}&GUID={guid}"


def available() -> bool:
    return SNAPSHOT.exists() or bool(os.environ.get("LEGISTAR_TOKEN"))


def _get(path: str, params: dict | None = None) -> list | dict:
    token = os.environ.get("LEGISTAR_TOKEN")
    if not token:
        raise RuntimeError("LEGISTAR_TOKEN is not set")
    r = httpx.get(f"{API}/{path}", params={**(params or {}), "token": token}, timeout=60)
    r.raise_for_status()
    return r.json()


def _odata_str(s: str) -> str:
    return s.replace("'", "''")


def _snapshot():
    return duckdb.connect(str(SNAPSHOT), read_only=True) if SNAPSHOT.exists() else None


def _snapshot_date(con) -> str:
    return con.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()[0]


def search_legislation(sponsor: str = "", keyword: str = "", since: str = TERM_START) -> str:
    if not sponsor and not keyword:
        return error("Give a sponsor, a keyword, or both.")
    con = _snapshot()
    if con:
        sql = """
            SELECT m.matter_id, m.file, m.title, m.type, m.status, m.intro_date, m.passed_date, m.guid,
                   string_agg(s.name, '; ' ORDER BY s.sequence) AS sponsors
            FROM matters m LEFT JOIN sponsors s USING (matter_id)
            WHERE m.intro_date >= ?
            GROUP BY ALL
        """
        rows = con.execute(f"SELECT * FROM ({sql}) WHERE 1=1"
                           + (" AND strip_accents(lower(sponsors)) LIKE strip_accents(lower(?))" if sponsor else "")
                           + (" AND (lower(title) LIKE lower(?) OR lower(file) LIKE lower(?))" if keyword else "")
                           + f" ORDER BY intro_date DESC LIMIT {MAX_BILLS}",
                           [since] + ([f"%{sponsor}%"] if sponsor else []) + ([f"%{keyword}%"] * 2 if keyword else [])).fetchall()
        cols = ["matter_id", "file", "title", "type", "status", "intro_date", "passed_date", "guid", "sponsors"]
        bills = [dict(zip(cols, r)) for r in rows]
        for b in bills:
            b["source_url"] = bill_url(b["matter_id"], b.pop("guid"))
        built = _snapshot_date(con)
        con.close()
        return to_json({
            "from": f"Legistar snapshot built {built}",
            "bills": bills,
            "note": "Bills introduced after the snapshot date aren't here; call search_legislation_live for those."
            if bills else "No matching bills in the snapshot. Say so; try a broader keyword or search_legislation_live.",
        })
    return search_legislation_live(sponsor, keyword, since)


def search_legislation_live(sponsor: str = "", keyword: str = "", since: str = TERM_START) -> str:
    filters = [f"MatterIntroDate ge datetime'{since}'"]
    if keyword:
        filters.append(f"substringof('{_odata_str(keyword)}', MatterTitle)")
    try:
        matters = _get("matters", {"$filter": " and ".join(filters), "$orderby": "MatterIntroDate desc", "$top": 200})
        bills = []
        for m in matters:
            names = [s["MatterSponsorName"] for s in _get(f"matters/{m['MatterId']}/sponsors")] if sponsor or len(bills) < 10 else []
            if sponsor and not any(sponsor.lower() in n.lower() for n in names):
                continue
            bills.append({
                "matter_id": m["MatterId"], "file": m["MatterFile"], "title": m["MatterTitle"],
                "type": m.get("MatterTypeName"), "status": m.get("MatterStatusName"),
                "intro_date": (m.get("MatterIntroDate") or "")[:10], "passed_date": (m.get("MatterPassedDate") or "")[:10],
                "sponsors": "; ".join(names), "source_url": bill_url(m["MatterId"], m["MatterGuid"]),
            })
            if len(bills) >= MAX_BILLS:
                break
    except RuntimeError as e:
        return error(f"Legistar live API unavailable: {e}.", "Use the snapshot results only, and say newer bills couldn't be checked.")
    except Exception as e:
        return error(f"Legistar request failed: {type(e).__name__}: {e}")
    return to_json({"from": "Legistar live API", "bills": bills})


def get_votes(bill: str) -> str:
    """bill: a matter_id (e.g. '75123') or a file number (e.g. 'Int 0123-2026')."""
    con = _snapshot()
    if con:
        if bill.isdigit():
            row = con.execute("SELECT matter_id, file, title, guid FROM matters WHERE matter_id = ?", [int(bill)]).fetchone()
        else:
            row = con.execute("SELECT matter_id, file, title, guid FROM matters WHERE lower(file) = lower(?)", [bill.strip()]).fetchone()
        if row:
            votes = con.execute(
                "SELECT date, action, body, tally, passed, person, vote FROM votes WHERE matter_id = ? ORDER BY date, person",
                [row[0]],
            ).fetchall()
            con.close()
            if votes:
                return to_json({
                    "bill": {"matter_id": row[0], "file": row[1], "title": row[2], "source_url": bill_url(row[0], row[3])},
                    "votes": [dict(zip(["date", "action", "body", "tally", "passed", "person", "vote"], v)) for v in votes],
                })
        else:
            con.close()
    return _get_votes_live(bill)


def _get_votes_live(bill: str) -> str:
    try:
        if bill.isdigit():
            matter = _get(f"matters/{bill}")
        else:
            found = _get("matters", {"$filter": f"MatterFile eq '{_odata_str(bill.strip())}'"})
            if not found:
                return error(f"No bill with file number '{bill}'.", "File numbers look like 'Int 0123-2026'.")
            matter = found[0]
        mid = matter["MatterId"]
        out = []
        for h in _get(f"matters/{mid}/histories"):
            if not h.get("MatterHistoryTally") or not h.get("MatterHistoryEventId"):
                continue
            # Votes hang off the agenda item, not the history row: find this bill's item in that meeting.
            items = _get(f"events/{h['MatterHistoryEventId']}/eventitems")
            for item in (i for i in items if i.get("EventItemMatterId") == mid and i.get("EventItemTally")):
                for v in _get(f"eventitems/{item['EventItemId']}/votes"):
                    out.append({
                        "date": (h.get("MatterHistoryActionDate") or "")[:10], "action": h.get("MatterHistoryActionName"),
                        "body": h.get("MatterHistoryActionBodyName"), "tally": h.get("MatterHistoryTally"),
                        "passed": h.get("MatterHistoryPassedFlag"), "person": v.get("VotePersonName"), "vote": v.get("VoteValueName"),
                    })
    except RuntimeError as e:
        return error(f"No votes in the snapshot, and the live API is unavailable: {e}.", "Say the vote record couldn't be checked.")
    except Exception as e:
        return error(f"Legistar request failed: {type(e).__name__}: {e}")
    return to_json({
        "bill": {"matter_id": mid, "file": matter["MatterFile"], "title": matter["MatterTitle"], "source_url": bill_url(mid, matter["MatterGuid"])},
        "votes": out or "No recorded roll-call votes on this bill yet.",
    })

