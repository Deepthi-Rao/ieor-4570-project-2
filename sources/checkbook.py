"""Layer 3: Checkbook NYC, the Comptroller's record of city spending and contracts.

Did the promised program get funded and launched? The API takes an XML request and answers in
XML. Checkbook sits behind bot protection that blocks bursts of requests, so this module sends one
request at a time and reports a block as "temporarily unavailable" instead of retrying hard.
"""

import asyncio
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import httpx

from . import error, to_json

API = "https://www.checkbooknyc.com/api"
MAX_RECORDS = 25

# Agency codes confirmed against the API (Checkbook names the agency in each record).
# Pass any other 3-digit code directly as agency_code.
AGENCY_CODES = {
    "mayoralty": "002", "board of elections": "003", "campaign finance board": "004", "comptroller": "015",
    "law department": "025", "city planning": "030", "education": "040", "doe": "040", "cuny": "042",
    "housing preservation and development": "806", "hpd": "806",
}

_lock = asyncio.Lock()  # one request at a time


def _criteria(name: str, value: str) -> str:
    return f"<criteria><name>{name}</name><type>value</type><value>{escape(value)}</value></criteria>"


async def search_spending(kind: str = "contracts", agency: str = "", vendor: str = "", fiscal_year: int = 2026, keyword: str = "") -> str:
    kind = kind.lower()
    if kind not in ("contracts", "spending"):
        return error("kind must be 'contracts' or 'spending'.")
    code = ""
    if agency:
        code = agency if agency.isdigit() else AGENCY_CODES.get(agency.lower().strip(), "")
        if not code:
            return error(f"Unknown agency '{agency}'.", f"Use a 3-digit Checkbook agency code, or one of: {sorted(set(AGENCY_CODES))}.")
    criteria = [_criteria("fiscal_year", str(fiscal_year))]
    if code:
        criteria.append(_criteria("agency_code", code))
    if kind == "contracts":
        criteria += [_criteria("status", "registered"), _criteria("category", "expense")]
        if vendor:
            criteria.append(_criteria("prime_vendor", vendor.upper()))
    elif vendor:
        criteria.append(_criteria("payee_name", vendor.upper()))
    body = (
        f"<request><type_of_data>{kind.capitalize()}</type_of_data><records_from>1</records_from>"
        f"<max_records>{MAX_RECORDS}</max_records><search_criteria>{''.join(criteria)}</search_criteria></request>"
    )
    async with _lock:
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(API, content=body, headers={"Content-Type": "application/xml"})
        except Exception as e:
            return error(f"Checkbook NYC request failed: {type(e).__name__}: {e}")
    if "Incapsula" in r.text or not r.text.lstrip().startswith("<?xml"):
        return error("Checkbook NYC is temporarily blocking requests.", "Don't retry now. Say spending data was unavailable.")
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError as e:
        return error(f"Checkbook NYC sent unreadable XML: {e}")
    if root.findtext("status/result") != "success":
        messages = [m.text for m in root.iter("message") if m.text]
        return error(f"Checkbook NYC rejected the query: {messages or r.text[:300]}")

    tag = "contract" if kind == "contracts" else "transaction"
    records = [{child.tag: child.text for child in rec if child.text} for rec in root.iter(tag)]
    if keyword:
        words = [w.lower() for w in re.findall(r"\w{3,}", keyword)]
        records = [rec for rec in records if any(w in " ".join(rec.values()).lower() for w in words)]
    return to_json({
        "source_url": "https://www.checkbooknyc.com/",
        "query": {"kind": kind, "agency_code": code, "vendor": vendor, "fiscal_year": fiscal_year, "keyword": keyword},
        "total_matching_in_checkbook": root.findtext("result_records/record_count"),
        "records_shown": len(records),
        "records": records,
        "note": f"Only the first {MAX_RECORDS} records are fetched; the keyword filter applies to those." if keyword else "",
    })
