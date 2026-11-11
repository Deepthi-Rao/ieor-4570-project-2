"""Discretionary funding: the shipped Schedule C data and the search over it. Offline: reads data/funding/."""

import csv
import gzip
import json

import pytest

from sources import DATA, MAX_RESULT_CHARS
from sources.funding import search_discretionary_funding as search
from sources.promises import load_officials

SCHEDULE_C = DATA / "funding" / "schedule_c_fy2027.csv.gz"
NOT_MEMBERS = {"Speaker", "Citywide"}  # plus '... Delegation': awards that span districts


@pytest.fixture(scope="module")
def awards() -> list[dict]:
    with gzip.open(SCHEDULE_C, "rt", newline="") as f:
        return list(csv.DictReader(f))


def test_every_award_has_an_amount_and_a_page_to_cite(awards):
    assert len(awards) > 9000
    for a in awards:
        float(a["Amount ($)"])
        assert a["source_url"].startswith("https://council.nyc.gov/") and "#page=" in a["source_url"]
        assert a["Source"], "every award belongs to an initiative"


def test_every_member_has_a_district_on_the_roster(awards):
    districts = {o["district"] for o in load_officials() if o.get("district")}
    for a in awards:
        member = a["Council Member"]
        if not member or member in NOT_MEMBERS or member.endswith("Delegation"):
            continue
        assert a["Council District"], f"{member} has no district"
        assert int(a["Council District"]) in districts


def test_restler_parks_awards():
    """README sample 3's expected answer."""
    result = json.loads(search(keyword="park", council_member="Lincoln Restler", fiscal_year="2027"))
    assert result["awards_matched"] == 11 and result["total_amount"] == 127000
    parks_equity = [r for r in result["records"] if r["Source"] == "Parks Equity Initiative"]
    assert sum(float(r["Amount ($)"]) for r in parks_equity) == 90000


def test_members_who_share_a_last_name_are_kept_apart():
    pierina = json.loads(search(council_member="Pierina Ana Sanchez", fiscal_year="2027"))
    justin = json.loads(search(council_member="Justin Sanchez", fiscal_year="2027"))
    assert {r["Council Member"] for r in pierina["records"]} == {"P. Sanchez"}
    assert {r["Council Member"] for r in justin["records"]} == {"J. Sanchez"}


def test_district_and_accents():
    district_3 = json.loads(search(council_member="District 3", fiscal_year="FY27"))
    assert {r["Council Member"] for r in district_3["records"]} == {"Wilson"}
    assert json.loads(search(council_member="Cabán"))["awards_matched"] > 0


def test_big_results_stay_whole_json():
    """A broad search is trimmed to fit, never cut mid-record, and still counts everything."""
    text = search(council_member="District 3")
    assert len(text) <= MAX_RESULT_CHARS
    result = json.loads(text)
    assert result["awards_matched"] > result["awards_shown"] > 0


def test_needs_a_filter():
    assert "error" in json.loads(search())


def test_search_database_matches_the_csvs(awards):
    """data/funding.duckdb is built from the CSVs by offline/build_funding_db.py: rebuild it after changing them."""
    from sources.funding import _load

    con = _load().cursor()  # the app's own connection: DuckDB allows one configuration per file
    fy2027 = con.execute("""SELECT count(*) FROM funding WHERE filename = 'schedule_c_fy2027.csv.gz'""").fetchone()[0]
    con.close()
    assert fy2027 == len(awards), "data/funding.duckdb is stale: run uv run offline/build_funding_db.py"
