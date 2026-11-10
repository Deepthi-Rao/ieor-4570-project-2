"""Layer 3, what actually happened: NYC Open Data through the Socrata APIs.

search_datasets finds a dataset and its columns; query_open_data runs SoQL against it. No key is
needed. Set SOCRATA_APP_TOKEN to raise the rate limit.
"""

import os
from urllib.parse import urlencode

import httpx

from . import error, to_json

DOMAIN = "data.cityofnewyork.us"
CATALOG = "https://api.us.socrata.com/api/catalog/v1"
MAX_ROWS = 50


def _headers() -> dict:
    token = os.environ.get("SOCRATA_APP_TOKEN")
    return {"X-App-Token": token} if token else {}


def search_datasets(query: str, limit: int = 6) -> str:
    try:
        r = httpx.get(CATALOG, params={"domains": DOMAIN, "q": query, "limit": limit, "only": "dataset"}, timeout=30)
        r.raise_for_status()
    except Exception as e:
        return error(f"Open Data catalog search failed: {type(e).__name__}: {e}")
    datasets = []
    for item in r.json().get("results", []):
        res = item["resource"]
        datasets.append({
            "dataset_id": res["id"],
            "name": res["name"],
            "description": (res.get("description") or "")[:300],
            "columns": res.get("columns_field_name", [])[:40],
            "data_updated_at": res.get("data_updated_at"),
            "source_url": f"https://{DOMAIN}/d/{res['id']}",
        })
    if not datasets:
        return error(f"No NYC Open Data dataset matches '{query}'.", "Try broader words, e.g. 'housing' not 'affordable units District 7'.")
    return to_json({"datasets": datasets, "note": "Check data_updated_at: a dataset last updated before 2026 can't show what happened this term."})


def query_open_data(dataset_id: str, where: str = "", select: str = "", group: str = "", order: str = "", limit: int = 20) -> str:
    params = {"$limit": max(1, min(int(limit), MAX_ROWS))}
    for key, value in (("$where", where), ("$select", select), ("$group", group), ("$order", order)):
        if value:
            params[key] = value
    url = f"https://{DOMAIN}/resource/{dataset_id}.json"
    try:
        r = httpx.get(url, params=params, headers=_headers(), timeout=60)
    except Exception as e:
        return error(f"Open Data query failed: {type(e).__name__}: {e}")
    if r.status_code != 200:
        # Socrata explains bad SoQL in the body: pass it on so the model can fix its query.
        return error(f"Open Data returned {r.status_code}: {r.text[:400]}", "Check column names with search_datasets; text values need single quotes.")
    rows = r.json()
    return to_json({
        "dataset_id": dataset_id,
        "source_url": f"https://{DOMAIN}/d/{dataset_id}",
        "api_url": f"{url}?{urlencode(params)}",
        "row_count": len(rows),
        "rows": rows,
    })
