"""Layer 1, what they promised: the offline promise corpus, built by offline/build_promises.py.

data/officials.json   every official in scope (mayor + City Council), from NYC Open Data
data/promises.jsonl   one promise per line, each with the archived page it came from
"""

import json
import unicodedata

from . import DATA, error, to_json


def _norm(name: str) -> str:
    """'Tiffany L. Cabán' -> 'tiffany l caban', so 'caban' and 'Cabán' both match."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return " ".join(s.replace(".", " ").split())


def load_officials() -> list[dict]:
    path = DATA / "officials.json"
    return json.loads(path.read_text()) if path.exists() else []


def load_promises() -> list[dict]:
    path = DATA / "promises.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def find_official(query: str) -> list[dict]:
    """Match by name (any part), 'mayor', or 'district 7' / 'D7' / '7'."""
    q = _norm(query)
    officials = load_officials()
    digits = "".join(ch for ch in q if ch.isdigit())
    if digits:  # names have no digits, so any number is a district
        return [o for o in officials if str(o.get("district")) == str(int(digits))]
    if "mayor" in q:
        return [o for o in officials if o["role"] == "Mayor"]
    words = q.split()
    return [o for o in officials if all(w in _norm(o["name"]) for w in words)]


def list_officials() -> str:
    officials = load_officials()
    if not officials:
        return error("No officials loaded.", "Run: uv run offline/build_officials.py")
    counts: dict[str, int] = {}
    for p in load_promises():
        counts[p["official"]] = counts.get(p["official"], 0) + 1
    return to_json({
        "source_url": "https://data.cityofnewyork.us/d/uvw5-9znb",
        "officials": [
            {"name": o["name"], "role": o["role"], "district": o.get("district"), "promises_on_file": counts.get(o["name"], 0)}
            for o in officials
        ],
    })


FIELDS = ("promise", "quote", "category", "specific", "location", "target", "source_url", "snapshot_date")


def get_promises(official: str, topic: str = "", specific_only: bool = False) -> str:
    matches = find_official(official)
    if not matches:
        return error(f"No official in scope matches '{official}'.", "Call list_officials to see names and districts.")
    if len(matches) > 1:
        return to_json({"ambiguous": [{"name": o["name"], "role": o["role"], "district": o.get("district")} for o in matches]})
    person = matches[0]
    promises = [p for p in load_promises() if p["official"] == person["name"]]
    if topic:
        words = [w for w in _norm(topic).split() if len(w) > 2]
        promises = [p for p in promises if any(w in _norm(p["promise"] + " " + p.get("category", "") + " " + p.get("quote", "")) for w in words)]
    if specific_only:
        promises = [p for p in promises if p.get("specific")]
    if not promises:
        return to_json({
            "official": person,
            "promises": [],
            "note": "No archived promises on file for this official" + (f" about '{topic}'" if topic else "")
            + ". Say the promise corpus can't answer this; don't guess what they promised.",
        })
    # Only the fields the agents use: the full records for a busy official overflow the result cap.
    slim = [{k: p[k] for k in FIELDS if p.get(k) not in (None, "")} for p in promises]
    return to_json({"official": person, "count": len(slim), "promises": slim}, limit=30_000)
