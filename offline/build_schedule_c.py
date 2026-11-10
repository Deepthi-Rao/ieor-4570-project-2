# /// script
# requires-python = ">=3.10"
# dependencies = ["pdfplumber>=0.11", "httpx>=0.27"]
# ///
"""Build data/funding/schedule_c_fy2027.csv.gz: this term's Council discretionary awards.

NYC Open Data's discretionary funding dataset stops at FY2021. The Council publishes current awards
only as a PDF, Schedule C of the adopted budget. FY2027 (adopted June 30, 2026) is the first budget
this Council passed: FY2026 was adopted in June 2025, by the previous Council.

This reads every award table in the PDF: the Council initiatives, and the three appendices (Aging
Discretionary, Local Initiatives, Youth Discretionary). It writes one row per award, with the
initiative it belongs to, the member who designated it, their district, and a link to the PDF page,
so every award can be cited. Column names match the Open Data file, so DuckDB unions the two.

    uv run offline/build_schedule_c.py            # pdfplumber is only needed here, not in the app
    uv run offline/build_schedule_c.py <pdf-url>  # a later Schedule C, e.g. FY2028
"""

import csv
import gzip
import json
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

import httpx
import pdfplumber

ROOT = Path(__file__).parent.parent
OUT = ROOT / "data" / "funding"
DEFAULT_URL = "https://council.nyc.gov/budget/wp-content/uploads/sites/54/2026/06/Fiscal-2027-Schedule-C-Final.pdf"

# Output columns. The first ones share names with Open Data's 4d7f-74pe file.
FIELDS = [
    "Fiscal Year", "Source", "Council Member", "Council District", "Legal Name of Organization",
    "Program Name", "EIN", "Amount ($)", "Agency", "Purpose of Funds", "Sponsor", "Category", "source_url",
]

# A table's header cell -> our column. Main tables say "EIN", the appendices say "Tax ID".
HEADER = {
    "council member": "Council Member", "borough delegation": "Council Member",  # 'Bronx Delegation'
    "sponsor": "Sponsor", "ein": "EIN", "tax id": "EIN",
    "amount": "Amount ($)", "agency": "Agency", "purpose of funds": "Purpose of Funds",
    "legal name of organization – program name": "organization", "legal name of organization - program name": "organization",
}

# A table of contents line: "Parks and Recreation Services ....... 188"
TOC = re.compile(r"^(?P<name>[A-Z].*?\S) ?\.{5,} ?(?P<page>\d+)$")


def norm(s: str) -> str:
    """Lowercase, no accents, no punctuation: 'Cabán' and 'Caban' match."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower().replace("-", " ")).strip()


def member_districts() -> dict[str, int]:
    """Schedule C names members by last name ('Restler', 'P. Sanchez'). Map those to districts."""
    officials = json.loads((ROOT / "data" / "officials.json").read_text())
    by_name: dict[str, int] = {}
    for o in officials:
        if not o.get("district"):
            continue
        parts = o["name"].replace(".", "").split()
        last = parts[-1]
        if len(parts) >= 3 and parts[-2].lower() in ("de", "la"):  # 'Carmen N. De La Rosa'
            last = " ".join(parts[parts.index(parts[-3] if parts[-3].lower() == "de" else parts[-2]):])
        by_name.setdefault(norm(last), o["district"])
        by_name[norm(f"{parts[0][0]} {last}")] = o["district"]  # 'P. Sanchez' vs 'J. Sanchez'
    # A last name two members share is only usable with the initial.
    lasts = [norm(o["name"].split()[-1]) for o in officials if o.get("district")]
    for last in {x for x in lasts if lasts.count(x) > 1}:
        by_name.pop(last, None)
    return by_name


def categories(pdf) -> list[tuple[int, str]]:
    """(printed page, category) from the table of contents, in page order."""
    toc = []
    for page in pdf.pages[:4]:
        for line in (page.extract_text() or "").splitlines():
            m = TOC.match(line.strip())
            if m and "Introduction" not in m["name"]:
                toc.append((int(m["page"]), m["name"]))
    return sorted(toc)


def printed_page(text: str) -> int | None:
    last = text.strip().splitlines()[-1].strip() if text.strip() else ""
    return int(last) if last.isdigit() else None


def initiative_heading(cells: list[list]) -> str:
    """Each initiative opens with a small table whose first row is just its name and total:
    ['', 'Abortion Access Fund', None, '', '', '$850,000', ''] -> 'Abortion Access Fund'.
    A long name wraps onto the next row. (The name also appears earlier, in the section's summary
    table, so its position in the page text can't be trusted.)"""
    first = [" ".join(c.split()) for c in cells[0] if c]
    if len(first) != 2 or not re.fullmatch(r"\$[\d,]+", first[1]):  # award tables open with a header row
        return ""
    name = first[0]
    rest = [" ".join(c.split()) for c in cells[1] if c] if len(cells) > 1 else []
    if len(rest) == 1 and not rest[0].startswith("Agency") and rest[0] != name:
        name += " " + rest[0]  # 'Homeowner Stabilization Services (Formerly ... and' + 'Resolution ...)'
    return name


def split_org(cell: str) -> tuple[str, str]:
    """'Per Scholas, Inc. – Tech Training Program' -> ('Per Scholas, Inc.', 'Tech Training Program')."""
    for dash in (" – ", " - "):
        if dash in cell:
            org, program = cell.split(dash, 1)
            return org.strip(), program.strip()
    return cell.strip(), ""


def build(url: str) -> list[dict]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "schedule_c.pdf"
        # council.nyc.gov turns away httpx's default User-Agent with a 403.
        headers = {"User-Agent": "promise-tracker-nyc/0.1 (offline data build)"}
        with httpx.stream("GET", url, timeout=300, follow_redirects=True, headers=headers) as r:
            r.raise_for_status()
            with path.open("wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
        fiscal_year = (re.search(r"Fiscal[- ](\d{4})", url) or re.search(r"FY ?(\d{4})", url, re.I))
        fiscal_year = fiscal_year[1] if fiscal_year else ""
        districts = member_districts()
        rows: list[dict] = []
        unmatched: set[str] = set()
        skipped: list = []  # rows whose cells couldn't be matched to the header

        with pdfplumber.open(path) as pdf:
            toc = categories(pdf)
            if not fiscal_year:
                fiscal_year = (re.search(r"Fiscal Year (\d{4})", pdf.pages[0].extract_text() or "") or [None, ""])[1]
            category, initiative = "", ""
            for number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                lines = [line.strip() for line in text.splitlines()]
                if lines and lines[0].startswith("Appendix"):
                    category = initiative = lines[0].split(":", 1)[-1].strip()  # 'Local Initiatives'
                elif (p := printed_page(text)) and toc:
                    category = next((name for start, name in reversed(toc) if start <= p), category)

                # Tables top to bottom. An award table belongs to the last initiative heading above
                # it, which may be on an earlier page.
                for table in sorted(page.find_tables(), key=lambda t: t.bbox[1]):
                    cells = table.extract()
                    if not cells:
                        continue
                    if heading := initiative_heading(cells):
                        initiative = heading
                        continue
                    # The header and the rows don't line up by position (the header sits one cell
                    # to the right, between spacer cells), so match them by order instead.
                    names = [HEADER.get(" ".join(c.lower().split()), "") for c in cells[0] if c]
                    if "Amount ($)" not in names or "organization" not in names:
                        continue  # a summary table (initiative totals), not awards
                    for raw in cells[1:]:
                        values = [c for c in raw if c is not None]  # None: a spacer or merged cell
                        if len(values) != len(names):
                            values = [c for c in values if c]
                            if len(values) == len(names) - 1 and "Sponsor" in names:
                                values.insert(names.index("Sponsor"), "")  # most rows have no sponsor
                            if len(values) != len(names):
                                skipped.append((number, raw))
                                continue
                        rec = {name: " ".join(v.split()) for name, v in zip(names, values) if name}
                        amount = rec.get("Amount ($)", "").replace("$", "").replace(",", "")
                        if not re.fullmatch(r"\d+(\.\d+)?", amount) or not rec.get("organization"):
                            continue  # a TOTAL line or a blank row
                        org, program = split_org(rec.pop("organization"))
                        member = rec.get("Council Member", "")
                        district = districts.get(norm(member), "")
                        if member and not district:
                            unmatched.add(member)
                        rows.append({
                            **rec,
                            "Fiscal Year": fiscal_year,
                            "Source": initiative,
                            "Council District": district,
                            "Legal Name of Organization": org,
                            "Program Name": program,
                            "Amount ($)": amount,
                            "Category": category,
                            "source_url": f"{url}#page={number}",
                        })
        # A member not on the roster (e.g. one elected in a special election) still names their
        # district in most award titles: use the district they name most often.
        for member in sorted(unmatched):
            mentions = [re.search(r"Council District (\d+)", r["Program Name"] + " " + r.get("Purpose of Funds", ""))
                        for r in rows if r.get("Council Member") == member]
            counts = Counter(m[1] for m in mentions if m)
            # Not for the Speaker, 'Citywide' or a borough delegation: their awards span districts.
            if counts and member not in ("Speaker", "Citywide") and not member.endswith("Delegation"):
                district, n = counts.most_common(1)[0]
                print(f"{member}: not in officials.json; District {district} from {n} award titles")
                for r in rows:
                    if r.get("Council Member") == member:
                        r["Council District"] = district
            else:
                print(f"{member}: no district (citywide, the Speaker or a borough delegation)")
        print(f"{len(skipped)} table rows skipped (cells didn't match the header), e.g. {skipped[:2]}")
        return rows


def main(url: str) -> None:
    rows = build(url)
    year = rows[0]["Fiscal Year"] if rows else "unknown"
    out = OUT / f"schedule_c_fy{year}.csv.gz"
    with gzip.open(out, "wt", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    sources_file = OUT / "sources.json"
    sources = json.loads(sources_file.read_text()) if sources_file.exists() else {}
    sources[out.name.removesuffix(".gz")] = url
    sources_file.write_text(json.dumps(sources, indent=2) + "\n")
    total = sum(float(r["Amount ($)"]) for r in rows)
    members = {r["Council Member"] for r in rows if r.get("Council Member")}
    print(f"{out.name}: {len(rows)} awards, ${total:,.0f}, {len(members)} members, "
          f"{len({r['Source'] for r in rows})} initiatives, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL)
