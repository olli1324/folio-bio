"""Headless CLI for the Folio research engine.

    python run.py "EGFR inhibitors for non-small-cell lung cancer"

Each pipeline stage prints as it completes; the finished briefing prints as
Markdown at the end. Useful for testing the engine without the web layer.
"""

from __future__ import annotations

import asyncio
import os
import sys

# Make `engine` importable no matter where this is invoked from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv  # noqa: E402

from engine.pipeline import run_pipeline  # noqa: E402
from engine.schema import Stage  # noqa: E402


async def main() -> int:
    load_dotenv()

    query = " ".join(sys.argv[1:]).strip()
    if not query:
        query = input("Research query (target protein or disease area): ").strip()
    if not query:
        print("No query given.", file=sys.stderr)
        return 1

    exit_code = 0
    async for event in run_pipeline(query):
        print(f"[{event.stage.value:>11}] {event.message}")

        if event.stage is Stage.DONE:
            markdown = event.detail.get("briefing_markdown", "")
            if markdown:
                print("\n" + "-" * 70 + "\n")
                print(markdown)
        elif event.stage is Stage.ERROR:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
