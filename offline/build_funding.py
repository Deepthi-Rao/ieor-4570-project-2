"""Build data/funding/: Council discretionary funding CSVs, read by sources/funding.py.

Downloads NYC Open Data's "City Council Discretionary Funding" (4d7f-74pe, FY2009–FY2021).
It has no council-member column and stops before this term, so for current-term questions add
the Council's own discretionary funding files (FY2026, FY2027) as extra URLs:

    uv run offline/build_funding.py
    uv run offline/build_funding.py https://council.nyc.gov/.../fy2026-discretionary.csv

Every file's origin goes into data/funding/sources.json, so each row can be cited.
"""

import gzip
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

OUT = Path(__file__).parent.parent / "data" / "funding"
OPEN_DATA = {
    "discretionary_fy2009_2021.csv": ("https://data.cityofnewyork.us/api/views/4d7f-74pe/rows.csv?accessType=DOWNLOAD",
                                      "https://data.cityofnewyork.us/d/4d7f-74pe"),
}


def download(url: str, path: Path) -> None:
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_bytes():
                f.write(chunk)
    # Gzip it: DuckDB reads .csv.gz directly, and it keeps the repo far under GitHub's 100 MB limit.
    with path.open("rb") as src, gzip.open(f"{path}.gz", "wb") as dst:
        shutil.copyfileobj(src, dst)
    path.unlink()
    print(f"{path.name}.gz: {Path(f'{path}.gz').stat().st_size / 1e6:.1f} MB")


def main(extra_urls: list[str]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sources_file = OUT / "sources.json"
    sources = json.loads(sources_file.read_text()) if sources_file.exists() else {}
    for name, (url, cite) in OPEN_DATA.items():
        download(url, OUT / name)
        sources[name] = cite
    for url in extra_urls:
        name = Path(urlparse(url).path).name or "council_funding.csv"
        if not name.endswith(".csv"):
            print(f"Skipping {url}: save Excel files as CSV first, then put them in {OUT}")
            continue
        download(url, OUT / name)
        sources[name] = url
    sources_file.write_text(json.dumps(sources, indent=2) + "\n")


if __name__ == "__main__":
    main(sys.argv[1:])
