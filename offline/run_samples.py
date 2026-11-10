"""Run the README's sample queries two ways and save both answers to samples/:

  - baseline: the same model (gemini-3.5-flash), no tools and no system prompt
  - agent:    the full Promise Tracker (Lead, Researchers, Writer, Reviewer)

    uv run offline/run_samples.py          # all samples
    uv run offline/run_samples.py 2        # just sample 2
"""

import asyncio
import json
import sys
import time
from pathlib import Path

import litellm

sys.path.insert(0, str(Path(__file__).parent.parent))
from app import AgentContexts, ToolCallHooks, investigate  # noqa: E402

OUT = Path(__file__).parent.parent / "samples"
SAMPLES = [
    "Is Mayor Mamdani on track to build 200,000 affordable homes?",
    "Which of Mayor Mamdani's housing promises are specific enough to check against data?",
    "Did Council Member Lincoln Restler steer discretionary funding to parks in District 33 this year?",
]


async def baseline(question: str) -> str:
    r = await litellm.acompletion(model="vertex_ai/gemini-3.5-flash", vertex_location="global", timeout=120,
                                  messages=[{"role": "user", "content": question}])
    return r.choices[0].message.content


async def agent(question: str) -> tuple[str, str, list[dict], float]:
    hooks = ToolCallHooks(AgentContexts(), lambda event: None)
    start = time.monotonic()
    kind, text = await investigate([{"role": "user", "content": question}], hooks)
    return kind, text, hooks.tool_calls, time.monotonic() - start


async def main(only: int | None) -> None:
    OUT.mkdir(exist_ok=True)
    for n, question in enumerate(SAMPLES, 1):
        if only and n != only:
            continue
        print(f"Sample {n}: {question}")
        base, (kind, text, calls, seconds) = await asyncio.gather(baseline(question), agent(question))
        agents = sorted({c["agent"] for c in calls})
        (OUT / f"sample_{n}.md").write_text(
            f"# Sample {n}\n\n**Question:** {question}\n\n"
            f"## Agent ({kind}, {seconds:.0f} s, {len(calls)} tool calls by {', '.join(agents)})\n\n{text}\n\n"
            f"## Baseline: gemini-3.5-flash, no tools, no system prompt\n\n{base}\n"
        )
        (OUT / f"sample_{n}_tool_calls.json").write_text(json.dumps(calls, indent=2, ensure_ascii=False))
        print(f"  agent: {kind}, {seconds:.0f} s, {len(calls)} tool calls")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else None))
