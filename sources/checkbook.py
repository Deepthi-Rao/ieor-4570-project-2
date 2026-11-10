"""Layer 3: Checkbook NYC, the Comptroller's record of city spending and contracts.

Did the promised program get funded and launched? The API takes an XML request and answers in
XML. Checkbook sits behind bot protection that blocks bursts of requests, so this module sends one
request at a time and reports a block as "temporarily unavailable" instead of retrying hard.
"""

import asyncio
import json
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import httpx

from . import MAX_RESULT_CHARS, error, to_json

API = "https://www.checkbooknyc.com/api"
MAX_RECORDS = 25

# City budget agency codes, as printed in the Council's Schedule C ("Agency: DPR (846)") and checked
# against Checkbook. Any other 3-digit code can be passed directly.
AGENCY_CODES = {
    "mayoralty": "002", "board of elections": "003", "campaign finance board": "004", "comptroller": "015",
    "law department": "025", "city planning": "030", "dcp": "030",
    "nypl": "037", "new york public library": "037", "bpl": "038", "brooklyn public library": "038",
    "qbpl": "039", "queens public library": "039",
    "education": "040", "doe": "040", "cuny": "042",
    "police": "056", "nypd": "056", "veterans": "063", "dvs": "063",
    "children's services": "068", "acs": "068", "homeless services": "071", "dhs": "071",
    "aging": "125", "dfta": "125", "cultural affairs": "126", "dcla": "126", "criminal justice": "128", "mocj": "128",
    "youth and community development": "260", "dycd": "260", "probation": "781", "dop": "781",
    "small business services": "801", "sbs": "801",
    "housing preservation and development": "806", "hpd": "806",
    "health and mental hygiene": "816", "health": "816", "dohmh": "816", "dhmh": "816",
    "health and hospitals": "819", "hhc": "819", "sanitation": "827", "dsny": "827",
    "transportation": "841", "dot": "841", "parks": "846", "parks and recreation": "846", "dpr": "846",
    "consumer and worker protection": "866", "dcwp": "866",
}

# The fields worth reading. A Checkbook record has ~25; the rest would crowd the context.
FIELDS = {
    "contracts": [
        "prime_vendor", "prime_contract_purpose", "prime_contracting_agency", "prime_contract_current_amount",
        "prime_vendor_spent_to_date", "prime_contract_start_date", "prime_contract_end_date",
        "prime_contract_registration_date", "prime_contract_id", "prime_contract_type",
    ],
    "spending": [
        "payee_name", "agency", "department", "check_amount", "issue_date", "expense_category",
        "contract_id", "contract_purpose", "budget_code", "capital_project",
    ],
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
            return error(f"Unknown agency '{agency}'.", f"Use a 3-digit city agency code, or one of: {sorted(set(AGENCY_CODES))}.")
    criteria = [_criteria("fiscal_year", str(fiscal_year))]
    if code:
        criteria.append(_criteria("agency_code", code))
    if kind == "contracts":
        if vendor:
            return error("Checkbook can't search contracts by vendor name (only by its internal vendor code).",
                         "Use kind='spending' with this vendor to see what the city paid them, or search contracts by agency and keyword.")
        criteria += [_criteria("status", "registered"), _criteria("category", "expense")]
        if keyword:
            criteria.append(_criteria("purpose", keyword))  # Checkbook matches it inside the contract's purpose
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
        messages = [m.text.strip() for m in root.iter("description") if m.text and m.text.strip()]
        return error(f"Checkbook NYC rejected the query: {messages or r.text[:300]}")

    # Contracts and payments both come back as <transaction> records.
    keep = FIELDS[kind]
    records = [{f: rec.findtext(f) for f in keep if (rec.findtext(f) or "").strip() not in ("", "N/A", "-")}
               for rec in root.iter("transaction")]
    note = ""
    if keyword and kind == "spending":
        # The spending API can't search by text: filter the records it returned.
        words = [w.lower() for w in re.findall(r"\w{3,}", keyword)]
        records = [rec for rec in records if any(w in " ".join(rec.values()).lower() for w in words)]
        note = f"The keyword filter applies to the first {MAX_RECORDS} payments only; filter by vendor for more."
    total = root.findtext("result_records/record_count")

    def payload() -> dict:
        return {
            "source_url": "https://www.checkbooknyc.com/",
            "query": {"kind": kind, "agency_code": code, "vendor": vendor, "fiscal_year": fiscal_year, "keyword": keyword},
            "total_matching_in_checkbook": total,
            "records_shown": len(records),
            "records": records,
            "note": note,
        }

    while records and len(json.dumps(payload(), ensure_ascii=False)) > MAX_RESULT_CHARS - 200:
        records.pop()  # never cut a record in half
    return to_json(payload())
