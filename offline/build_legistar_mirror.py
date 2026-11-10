"""Build data/legistar.duckdb from a public mirror of the Council's Legistar data, without a token.

The Legistar Web API needs a token the Council emails by hand (offline/build_legistar.py uses it).
Until one arrives, this builds the same snapshot from github.com/jehiah/nyc_legislation, which
syncs the Legistar API every few hours (it powers intro.nyc). It has every bill and resolution, its
sponsors, and each action in its history with the roll-call votes taken at it.

Every bill still links to its official page on legistar.council.nyc.gov (from its ID and GUID), so
citations point at the Council, not the mirror. This only downloads JSON files and reads them.

    uv run offline/build_legistar_mirror.py
"""

import json
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import duckdb
import httpx

ROOT = Path(__file__).parent.parent
OUT = ROOT / "data" / "legistar.duckdb"
REPO = "jehiah/nyc_legislation"
BRANCH = "master"
TERM_START = "2026-01-01"
FOLDERS = ["introduction/2026", "resolution/2026"]  # this term's bills and resolutions
HEADERS = {"User-Agent": "promise-tracker-nyc/0.1 (offline data build)", "Accept": "application/vnd.github+json"}


def get(client: httpx.Client, url: str):
    for attempt in range(4):
        try:
            r = client.get(url, timeout=60)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def files_in(client: httpx.Client, folder: str) -> list[str]:
    """Every file path in a folder. The contents API stops at 1,000 entries; a git tree doesn't."""
    parent, name = folder.rsplit("/", 1)
    entries = get(client, f"https://api.github.com/repos/{REPO}/contents/{parent}?ref={BRANCH}")
    sha = next(e["sha"] for e in entries if e["name"] == name)
    tree = get(client, f"https://api.github.com/repos/{REPO}/git/trees/{sha}")
    if tree.get("truncated"):
        sys.exit(f"The git tree for {folder} is truncated: list it another way.")
    return [f"{folder}/{t['path']}" for t in tree["tree"] if t["path"].endswith(".json")]


def clean(name: str) -> str:
    """'Althea V.  Stevens' -> 'Althea V. Stevens': the source has stray double spaces."""
    return " ".join(name.split())


def day(value: str | None) -> str | None:
    return value[:10] if value and not value.startswith("0001") else None


def main() -> None:
    with httpx.Client(headers=HEADERS, follow_redirects=True) as client:
        synced = get(client, f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/last_sync.json")
        paths = [p for folder in FOLDERS for p in files_in(client, folder)]
        print(f"{len(paths)} files; mirror last synced Matters at {synced.get('Matters')}")
        raw = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/"
        with ThreadPoolExecutor(8) as pool:
            matters = list(pool.map(lambda p: get(client, raw + p), paths))

    matters = [m for m in matters if (day(m.get("IntroDate")) or "9999") >= TERM_START]
    rows_m, rows_s, rows_v = [], [], []
    for m in matters:
        rows_m.append((m["ID"], m["File"], m.get("Title") or m.get("Name"), m.get("Name"), m.get("TypeName"),
                       m.get("StatusName"), day(m.get("IntroDate")), day(m.get("PassedDate")), m["GUID"],
                       m.get("BodyName"), (m.get("Summary") or "")[:2000]))
        for i, s in enumerate(m.get("Sponsors") or []):
            rows_s.append((m["ID"], clean(s["FullName"]), i))
        for h in m.get("History") or []:
            votes = h.get("Votes") or []
            if not votes:
                continue
            counts = Counter(v["Vote"] for v in votes)
            tally = f"{counts.get('Affirmative', 0)}-{counts.get('Negative', 0)}"  # Legistar's own tally format
            for v in votes:
                rows_v.append((m["ID"], day(h.get("Date")), h.get("Action"), h.get("BodyName"), tally,
                               h.get("PassedFlag"), clean(v["FullName"]), v["Vote"]))

    tmp = OUT.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
    con.executemany("INSERT INTO meta VALUES (?, ?)", [
        ("built_at", date.today().isoformat()), ("term_start", TERM_START),
        ("source", f"github.com/{REPO} (a mirror of the Legistar API)"), ("mirror_synced", synced.get("Matters", "")),
    ])
    con.execute("""CREATE TABLE matters (matter_id INTEGER, file VARCHAR, title VARCHAR, name VARCHAR, type VARCHAR,
                   status VARCHAR, intro_date DATE, passed_date DATE, guid VARCHAR, committee VARCHAR, summary VARCHAR)""")
    con.executemany("INSERT INTO matters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows_m)
    con.execute("CREATE TABLE sponsors (matter_id INTEGER, name VARCHAR, sequence INTEGER)")
    con.executemany("INSERT INTO sponsors VALUES (?, ?, ?)", rows_s)
    con.execute("CREATE TABLE votes (matter_id INTEGER, date DATE, action VARCHAR, body VARCHAR, tally VARCHAR, passed INTEGER, person VARCHAR, vote VARCHAR)")
    con.executemany("INSERT INTO votes VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows_v)
    con.close()
    tmp.replace(OUT)
    types = Counter(r[4] for r in rows_m)
    print(f"Wrote {OUT.name}: {len(rows_m)} matters {dict(types)}, {len(rows_s)} sponsorships, "
          f"{len(rows_v)} votes, {OUT.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
