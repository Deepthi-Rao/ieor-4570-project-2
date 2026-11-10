"""Layer 2: Council discretionary funding — which local projects each member steered money to.

DuckDB reads every CSV in data/funding/ (built by offline/build_funding.py), so newer files the
Council publishes can be dropped in next to the Open Data download without code changes.
Known gap: NYC Open Data's dataset (4d7f-74pe) stops at FY2021 and has no council-member column.
Current-term awards need the Council's own FY2026+ discretionary funding files in data/funding/.
"""

import json

import duckdb

from . import DATA, error, to_json

FUNDING = DATA / "funding"
SOURCES = FUNDING / "sources.json"  # file name -> where it was downloaded from, for citations
MAX_ROWS = 40


def search_discretionary_funding(keyword: str = "", council_member: str = "", fiscal_year: str = "") -> str:
    files = sorted(FUNDING.glob("*.csv")) + sorted(FUNDING.glob("*.csv.gz"))
    if not files:
        return error("No discretionary funding data loaded.", "Run: uv run offline/build_funding.py")
    if not (keyword or council_member):
        return error("Give a keyword (organization, program, park, school) or a council_member.")
    origins = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    con = duckdb.connect()
    paths = ", ".join("'" + str(f).replace("'", "''") + "'" for f in files)  # views can't take parameters
    con.execute(f"CREATE VIEW funding AS SELECT * FROM read_csv_auto([{paths}], union_by_name = true, filename = true, all_varchar = true)")
    columns = [r[0] for r in con.execute("DESCRIBE funding").fetchall()]
    where, params = [], []
    # Search every column: the Council's files and Open Data's name their columns differently.
    for term in (keyword, council_member):
        if term:
            where.append("strip_accents(lower(CAST(f AS VARCHAR))) LIKE strip_accents(lower(?))")
            params.append(f"%{term}%")
    year_col = next((c for c in columns if "fiscal" in c.lower() and "year" in c.lower()), None)
    if fiscal_year and year_col:
        where.append(f'"{year_col}" LIKE ?')
        params.append(f"%{fiscal_year[-2:]}%")
    rows = con.execute(f"SELECT * FROM funding f WHERE {' AND '.join(where)} LIMIT {MAX_ROWS}", params).fetchall()
    years = con.execute(f'SELECT min("{year_col}"), max("{year_col}") FROM funding').fetchone() if year_col else None
    con.close()
    records = []
    for r in rows:
        rec = {k: v for k, v in zip(columns, r) if v not in (None, "")}
        name = rec.pop("filename", "").rsplit("/", 1)[-1].removesuffix(".gz")
        rec["source_url"] = origins.get(name, f"data/funding/{name}")
        records.append(rec)
    note = ""
    if council_member and not any(c for c in columns if "council" in c.lower() or "member" in c.lower()):
        note = "These files have no council-member column, so a member's name only matches if it appears in the text. "
    if years and years[1] and years[1] < "2026":
        note += f"Funding data covers fiscal years {years[0]}–{years[1]} only: it can't show awards made this term."
    return to_json({"records": records, "row_count": len(records), "note": note.strip()})
