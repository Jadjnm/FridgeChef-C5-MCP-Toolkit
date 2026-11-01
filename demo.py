"""Paced live demo for the screencast, with one labelled step per Enter.

Each step is labelled with what it demonstrates, so the recording shows which tool is the Data,
Action and Orchestration tool. It uses the real agent and server, with a fresh shopping list in a
temporary folder, so every recording starts from the same state and your own list is untouched.

Usage:
    python demo.py            # press Enter before each step (for recording)
    python demo.py --auto     # run straight through with short pauses (for a dry run)
    python demo.py --no-trace # hide the server's side of each request
"""

from __future__ import annotations

import argparse
import asyncio
import json
import tempfile
import time
from typing import Any

from agent import ask, make_agent, make_server, model_id, model_provider, read_shopping_list

# (label shown on screen, message to the agent, or None for "read the list resource")
STEPS: list[tuple[str, str | None]] = [
    ("DATA tool: search_recipes (read-only lookup)", "What recipes can I make with chickpeas?"),
    ("ORCHESTRATION tool: cook_tonight (searches, scores, then adds what's missing)",
     "I have chicken, rice, garlic and an onion. What should I cook tonight?"),
    ("RESOURCE: shopping-list://current, read by the host over MCP", None),
    ("MEMORY: a follow-up answered from the conversation", "What about the first runner-up instead? Just tell me what it needs."),
    ("ACTION tool: add_to_shopping_list (changes state, skips duplicates)", "Add lemons and coriander to my shopping list."),
    ("RESOURCE as context: the host injects the list every turn", "What's on my shopping list now?"),
    ("FAILURE CASE: a real ingredient with no recipes, reported clearly", "What can I make with jackfruit?"),
]


def banner(n: int, label: str) -> None:
    print(f"\n\033[1;35m=== Step {n}/{len(STEPS)}: {label} ===\033[0m")


async def main(auto: bool, trace: bool) -> None:
    with tempfile.TemporaryDirectory() as data_dir:
        async with make_server(data_dir, trace=trace) as server:
            tools = await server.list_tools()
            print(f"Fridge Chef: MCP server 'fridge-chef' connected over stdio. Model: {model_provider()}/{model_id()}")
            for t in tools:
                print(f"  - {t.name}: {(t.description or '').splitlines()[0]}")
            agent = make_agent(server)
            history: list[Any] = []
            for n, (label, message) in enumerate(STEPS, start=1):
                if auto:
                    time.sleep(1)
                else:
                    input(f"\n\033[2m[Enter for step {n}: {label.split(':')[0]}]\033[0m")
                banner(n, label)
                if message is None:
                    items = await read_shopping_list(server)
                    print("  <- resource shopping-list://current")
                    print(json.dumps([e["item"] for e in items], indent=2))
                else:
                    history = await ask(agent, message, history)
            print("\n\033[1;35m=== End of demo ===\033[0m")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--auto", action="store_true", help="run without waiting for Enter")
    parser.add_argument("--no-trace", action="store_true", help="hide the server-side request log")
    args = parser.parse_args()
    asyncio.run(main(args.auto, not args.no_trace))
