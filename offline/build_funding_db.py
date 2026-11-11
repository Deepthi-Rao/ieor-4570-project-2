"""Build data/funding.duckdb from the CSVs in data/funding/, for sources/funding.py to open read-only.

Parsing ~107,000 gzipped CSV rows takes ~300 MB, too much to do inside a 512 MB Cloud Run instance
that's also running the agents. This does it once, offline: it keeps only the columns a search
returns, plus one lowercase, accent-free column of every field to search in. Rerun it after adding
or rebuilding a CSV (build_funding.py, build_schedule_c.py).

    uv run offline/build_funding_db.py
"""

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).parent.parent))
from sources.funding import DB, FUNDING, KEEP  # noqa: E402


def main() -> None:
    files = sorted(FUNDING.glob("*.csv")) + sorted(FUNDING.glob("*.csv.gz"))
    if not files:
        sys.exit("No CSVs in data/funding/: run build_funding.py or build_schedule_c.py first.")
    tmp = DB.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = duckdb.connect(str(tmp))
    paths = ", ".join("'" + str(f).replace("'", "''") + "'" for f in files)
    source = f"read_csv_auto([{paths}], union_by_name = true, filename = true, all_varchar = true)"
    columns = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()]
    keep = [c for c in columns if c in KEEP]
    everything = ", ".join(f'"{c}"' for c in columns if c != "filename")
    con.execute(f"""CREATE TABLE funding AS
                    SELECT {", ".join(f'"{c}"' for c in keep)},
                           strip_accents(lower(concat_ws(' ', {everything}))) AS search_text
                    FROM {source}""")
    # Store just the file name: the build machine's paths mean nothing on Cloud Run.
    con.execute("UPDATE funding SET filename = regexp_extract(filename, '[^/]+$')")
    rows = con.execute("SELECT count(*) FROM funding").fetchone()[0]
    con.close()
    tmp.replace(DB)
    print(f"Wrote {DB.name}: {rows} awards from {len(files)} files, {DB.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
