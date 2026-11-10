"""Council legislation: the snapshot built from the Legistar mirror, and the search over it. Offline."""

import json
import re

import pytest

from sources import MAX_RESULT_CHARS, legistar

pytestmark = pytest.mark.skipif(not legistar.SNAPSHOT.exists(), reason="no data/legistar.duckdb: run offline/build_legistar_mirror.py")


def test_snapshot_is_available_without_a_token(monkeypatch):
    monkeypatch.delenv("LEGISTAR_TOKEN", raising=False)
    assert legistar.available()


def test_every_bill_links_to_the_official_legistar_page():
    result = json.loads(legistar.search_legislation(keyword="rent"))
    assert result["bills"]
    for bill in result["bills"]:
        assert bill["source_url"].startswith("https://legistar.council.nyc.gov/LegislationDetail.aspx?ID=")


def test_keywords_match_whole_words():
    """'rent' must not match 'current' or 'different'."""
    files = [b["file"] for b in json.loads(legistar.search_legislation(keyword="rent"))["bills"]]
    con = legistar._snapshot()  # results trim summaries, so check the full stored text
    for file in files:
        text = con.execute("SELECT concat_ws(' ', title, name, summary, file) FROM matters WHERE file = ?", [file]).fetchone()[0]
        assert re.search(r"\brent\b", text.lower()), file
    con.close()


def test_a_members_own_bills_come_first():
    result = json.loads(legistar.search_legislation(sponsor="Restler"))
    assert result["bills_as_prime_sponsor"] > 0
    flags = [b["is_prime_sponsor"] for b in result["bills"]]
    assert flags == sorted(flags, reverse=True), "prime-sponsored bills first, then co-sponsored"


def test_results_stay_whole_json():
    text = legistar.search_legislation(sponsor="Menin")
    assert len(text) <= MAX_RESULT_CHARS
    result = json.loads(text)
    assert result["bills_matched"] >= result["bills_shown"] > 0


def test_votes_are_grouped_by_roll_call():
    """Int 0001-2026 passed the Council 44-5: the five no votes are named, the yes votes counted."""
    votes = json.loads(legistar.get_votes("Int 0001-2026"))["votes"]
    council = next(v for v in votes if v["action"] == "Approved by Council")
    assert council["tally"] == "44-5" and council["affirmative"] == 44 and len(council["negative"]) == 5
    assert council["result"] == "passed"
