"""Checkbook NYC: parsing its XML, and its quirks. Offline: httpx is replaced by canned responses."""

import asyncio
import json

import httpx
import pytest

from sources import checkbook

CONTRACT = """
      <transaction>
        <prime_vendor>ARCADIS OF NEW YORK INC</prime_vendor>
        <prime_contract_purpose>Citywide Park Tree Planting Supervision</prime_contract_purpose>
        <prime_contracting_agency>Department of Parks and Recreation</prime_contracting_agency>
        <prime_contract_current_amount>1068275.00</prime_contract_current_amount>
        <parent_contract_id>-</parent_contract_id>
        <prime_oca_number>1018655434</prime_oca_number>
      </transaction>"""


def response(records: str, count: int) -> str:
    return (f'<?xml version="1.0"?><response><status><result>success</result></status><result_records>'
            f"<record_count>{count}</record_count><contract_transactions>{records}</contract_transactions>"
            f"</result_records></response>")


@pytest.fixture
def reply(monkeypatch):
    """Make the next Checkbook request return the given body. Records the request sent."""
    sent = {}

    def set_reply(body: str):
        async def post(self, url, content="", headers=None):
            sent["body"] = content
            return httpx.Response(200, text=body)
        monkeypatch.setattr(httpx.AsyncClient, "post", post)
        return sent

    return set_reply


def run(**kwargs) -> dict:
    return json.loads(asyncio.run(checkbook.search_spending(**kwargs)))


def test_contracts_are_parsed(reply):
    """Contracts come back as <transaction>, like payments. The old parser looked for <contract> and found none."""
    sent = reply(response(CONTRACT * 3, 74))
    result = run(kind="contracts", agency="parks", keyword="tree")
    assert result["total_matching_in_checkbook"] == "74" and result["records_shown"] == 3
    record = result["records"][0]
    assert record["prime_vendor"] == "ARCADIS OF NEW YORK INC"
    assert "parent_contract_id" not in record and "prime_oca_number" not in record  # placeholders and noise dropped
    assert "<name>agency_code</name><type>value</type><value>846</value>" in sent["body"]
    assert "<name>purpose</name><type>value</type><value>tree</value>" in sent["body"]  # searched by Checkbook


def test_results_fit_without_cutting_a_record(reply):
    big = CONTRACT.replace("Supervision", "Supervision " + "x" * 600)
    reply(response(big * 25, 25))
    result = run(kind="contracts", agency="parks")
    assert 0 < result["records_shown"] < 25


def test_contracts_cant_be_searched_by_vendor():
    result = run(kind="contracts", vendor="City Harvest")
    assert "error" in result and "spending" in result["how_to_fix"]


def test_unknown_agency_and_bot_block(reply):
    assert "error" in run(kind="contracts", agency="fire department")
    reply("<html>Request unsuccessful. Incapsula incident ID</html>")
    assert "blocking" in run(kind="spending", agency="hpd")["error"]


def test_rejection_explains_why(reply):
    reply('<?xml version="1.0"?><response><status><result>failure</result><messages><message>'
          "<code>1101</code><description>Provided request parameter 'x' is not valid</description>"
          "</message></messages></status></response>")
    assert "is not valid" in run(kind="spending", agency="hpd")["error"]
