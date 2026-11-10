"""The harness rules that code, not prompts, enforces. Offline: no model calls."""

import time

import app
from app import MAX_ROUNDS, Research, join_round, verify_links


def state() -> Research:
    return Research(hooks=None, deadline=time.monotonic() + 60)


def test_researchers_sent_together_are_one_round():
    s = state()
    assert all(join_round(s) for _ in range(4))  # four parallel calls in one turn
    assert s.rounds == 1 and s.running == 4


def test_follow_up_rounds_are_capped():
    s = state()
    for _ in range(MAX_ROUNDS):
        assert join_round(s) and join_round(s)  # a turn of two researchers
        s.running = 0  # both finished
    assert not join_round(s), "a round past the cap is refused"
    assert s.rounds == MAX_ROUNDS


def test_a_researcher_joins_a_round_still_running():
    s = state()
    for _ in range(MAX_ROUNDS):
        join_round(s)
        s.running = 0
    s.running = 1  # the last round is still going: a call now belongs to it
    assert join_round(s)


def test_links_must_come_from_the_findings():
    findings = "- 11,585 units started ([HPD data](https://data.cityofnewyork.us/d/hg8x-zxpr))"
    report = ("Starts fell ([HPD data](https://data.cityofnewyork.us/d/hg8x-zxpr)). "
              "Experts agree ([a blog](https://example.com/made-up)).")
    checked = verify_links(report, findings)
    assert "(https://data.cityofnewyork.us/d/hg8x-zxpr)" in checked
    assert "example.com" not in checked and "a blog (unverified source removed)" in checked


def test_links_match_through_url_encoding():
    findings = "([Housing](https://web.archive.org/web/2025/https://www.zohranfornyc.com/policies/housing%28nyc%29))"
    report = "Quote ([Housing](https://web.archive.org/web/2025/https://www.zohranfornyc.com/policies/housing(nyc)))"
    assert "unverified" not in verify_links(report, findings)


def test_lead_instructions_are_built_per_run(monkeypatch):
    """The date and roster must not be frozen when the server starts."""
    monkeypatch.setattr(app.time, "strftime", lambda fmt: "2026-12-01")
    text = app.lead_instructions(None, app.lead)
    assert "Today is 2026-12-01" in text
    assert "District 3: Carl Wilson (elected in a special election, 2026-04-28)" in text
    assert "Erik D. Bottcher (left office 2026-02-03)" in text
