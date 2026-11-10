"""The promise corpus: every promise is quotable, citable and from before the official's election. Offline."""

import json
from urllib.parse import urlparse

from sources.promises import get_promises, load_officials, load_promises

ELECTION_DAY = "2025-11-04"


def host(url: str) -> str:
    return (urlparse(url).hostname or "").removeprefix("www.")


def test_every_promise_comes_from_its_officials_own_site_before_their_election():
    officials = {o["name"]: o for o in load_officials()}
    promises = load_promises()
    assert len(promises) > 56, "more than the mayor's"
    for p in promises:
        o = officials[p["official"]]
        assert p["quote"].strip() and p["promise"].strip()
        assert host(p["original_url"]) in {host(u) for u in o.get("campaign_urls", [])}, p["original_url"]
        assert p["source_url"].startswith("https://web.archive.org/web/")
        election = o.get("election_day", ELECTION_DAY.replace("-", ""))
        assert p["snapshot_date"].replace("-", "") <= election, (p["official"], p["snapshot_date"])


def test_only_checked_sites_were_used():
    used = {p["official"] for p in load_promises()}
    for o in load_officials():
        if o["name"] in used and o["role"] != "Mayor":
            assert o.get("campaign_urls_checked"), f"{o['name']}'s campaign site was never checked"


def test_a_district_means_whoever_holds_it_now():
    assert json.loads(get_promises("District 3"))["official"]["name"] == "Carl Wilson"


def test_older_pages_are_flagged():
    result = json.loads(get_promises("Julie Won"))
    if any(p["snapshot_date"] < "2025" for p in result["promises"]):
        assert "earlier campaign" in result["note"]


def test_no_translated_pages():
    """'/es/priorities' repeats the English page's promises in Spanish."""
    import re
    for p in load_promises():
        assert not re.match(r"^/[a-z]{2}(-[A-Z]{2})?/", urlparse(p["original_url"]).path), p["original_url"]
