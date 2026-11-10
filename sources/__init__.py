"""Data sources for the Promise Tracker. Each module wraps one source from the data plan.

Every function returns a JSON string with a `source_url` (or a `source_url` per row), so the
Writer can cite the exact page, dataset or API call behind every fact. Errors come back as
{"error": ...} strings, never exceptions: the model can't see an exception, but it can read an error.
"""

import json
from pathlib import Path

DATA = Path(__file__).parent.parent / "data"

# Results go into the model's context. Cap them so one big table can't crowd out everything else.
MAX_RESULT_CHARS = 12_000


def to_json(payload) -> str:
    text = json.dumps(payload, default=str, ensure_ascii=False)
    if len(text) > MAX_RESULT_CHARS:
        text = text[:MAX_RESULT_CHARS] + ' ... [truncated: narrow the query to see the rest]"'
    return text


def error(message: str, how_to_fix: str = "") -> str:
    return json.dumps({"error": message, "how_to_fix": how_to_fix} if how_to_fix else {"error": message})
