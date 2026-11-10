"""Layer 2: Council discretionary funding — which local projects each member steered money to.

DuckDB reads every CSV in data/funding/, so a new year's file can be dropped in without code changes:
  - discretionary_fy2009_2021.csv.gz   NYC Open Data 4d7f-74pe (offline/build_funding.py). Stops at FY2021.
  - schedule_c_fy2027.csv.gz           Schedule C of the FY2027 adopted budget, parsed from the Council's
                                       PDF (offline/build_schedule_c.py). The first budget this Council passed.
Both files share column names (Fiscal Year, Council Member, Council District, Amount ($), ...), and each
Schedule C row carries a source_url to its page in the PDF.
"""

import json
import re

import duckdb

from . import DATA, MAX_RESULT_CHARS, error, to_json

FUNDING = DATA / "funding"
SOURCES = FUNDING / "sources.json"  # file name -> where it was downloaded from, for citations
MAX_ROWS = 25
MAX_PURPOSE = 160  # characters of "Purpose of Funds" per award
# What the model needs to describe and cite an award. Addresses, EINs, BBLs etc. would crowd the context.
KEEP = {
    "Fiscal Year", "Source", "Category", "Council Member", "Council District", "Legal Name of Organization",
    "Program Name", "Amount ($)", "Agency", "Purpose of Funds", "Sponsor", "source_url", "filename",
}


def search_discretionary_funding(keyword: str = "", council_member: str = "", fiscal_year: str = "") -> str:
    files = sorted(FUNDING.glob("*.csv")) + sorted(FUNDING.glob("*.csv.gz"))
    if not files:
        return error("No discretionary funding data loaded.", "Run: uv run offline/build_schedule_c.py")
    if not (keyword or council_member):
        return error("Give a keyword (organization, program, park, school) or a council_member.")
    origins = json.loads(SOURCES.read_text()) if SOURCES.exists() else {}
    con = duckdb.connect()
    paths = ", ".join("'" + str(f).replace("'", "''") + "'" for f in files)  # views can't take parameters
    con.execute(f"CREATE VIEW funding AS SELECT * FROM read_csv_auto([{paths}], union_by_name = true, filename = true, all_varchar = true)")
    columns = [r[0] for r in con.execute("DESCRIBE funding").fetchall()]

    where, params = [], []
    if keyword:
        # Search every column: an organization, program name or purpose can carry the keyword.
        where.append("strip_accents(lower(CAST(f AS VARCHAR))) LIKE strip_accents(lower(?))")
        params.append(f"%{keyword}%")
    if council_member:
        district = re.fullmatch(r"(?:council\s+)?district\s*(\d+)", council_member.strip(), re.I)
        if district:
            where.append('"Council District" = ?')
            params.append(district[1])
        else:
            # Schedule C names members by last name ('Restler'), with an initial when two share it
            # ('P. Sanchez', 'J. Sanchez'). Match the last name, and the initial if both have one.
            words = council_member.replace(".", " ").split()
            where.append('strip_accents(lower("Council Member")) LIKE strip_accents(lower(?))')
            params.append(f"%{words[-1]}%")
            if len(words) > 1:
                where.append("""("Council Member" NOT SIMILAR TO '[A-Z]\\. .*' OR lower("Council Member") LIKE lower(?))""")
                params.append(f"{words[0][0]}. %")
    if fiscal_year:
        where.append('"Fiscal Year" = ?')
        year = re.sub(r"\D", "", fiscal_year)
        params.append(f"20{year}" if len(year) == 2 else year)  # 'FY27' -> '2027'
    condition = " AND ".join(where)

    rows = con.execute(
        f'SELECT * FROM funding f WHERE {condition} ORDER BY "Fiscal Year" DESC, TRY_CAST("Amount ($)" AS DOUBLE) DESC LIMIT {MAX_ROWS}',
        params,
    ).fetchall()
    matched, total = con.execute(
        f'SELECT count(*), sum(TRY_CAST("Amount ($)" AS DOUBLE)) FROM funding f WHERE {condition}', params
    ).fetchone()
    years = [r[0] for r in con.execute('SELECT DISTINCT "Fiscal Year" FROM funding ORDER BY 1').fetchall()]
    con.close()

    records = []
    for r in rows:
        rec = {k: v for k, v in zip(columns, r) if v not in (None, "") and k in KEEP}
        name = rec.pop("filename", "").rsplit("/", 1)[-1].removesuffix(".gz")
        rec.setdefault("source_url", origins.get(name, f"data/funding/{name}"))  # Schedule C rows link their page
        if len(rec.get("Purpose of Funds", "")) > MAX_PURPOSE:
            rec["Purpose of Funds"] = rec["Purpose of Funds"][:MAX_PURPOSE] + "..."
        records.append(rec)

    def payload() -> dict:
        return {
            "awards_matched": matched,
            "total_amount": total,
            "awards_shown": len(records),
            "records": records,
            "fiscal_years_loaded": f"{years[0]}–{years[-1]}, with no data for FY2022–FY2026" if years else "",
            "note": (
                "FY2027 is Schedule C of the budget adopted 2026-06-30: the first one this Council passed. "
                "It lists awards designated at adoption; some initiatives are designated later ('post-adoption') "
                "and aren't in it. Older years come from NYC Open Data and often have no Council Member. "
                + (f"Showing the {len(records)} largest of {matched}, newest year first: narrow with a keyword "
                   "or fiscal_year to see the rest. awards_matched and total_amount cover all of them."
                   if matched > len(records) else "")
            ),
        }

    # Drop the smallest awards until the result fits, so it's never cut off mid-record.
    while records and len(json.dumps(payload(), default=str, ensure_ascii=False)) > MAX_RESULT_CHARS - 200:
        records.pop()
    return to_json(payload())
