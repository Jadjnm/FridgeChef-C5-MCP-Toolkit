"""Repeatable checks of tool routing, arguments and successful results.

Each case runs as a fresh conversation against the selected model and real server, with the
shopping list redirected to a temporary folder so your own list is untouched. A case passes when:
  1. routing: the ordered tool calls match an accepted sequence, including call counts, and
  2. check: successful outputs match the tool schema and the requested items are exact.

Free-text answer faithfulness needs manual review. Every run saves its calls, results and answer
to a JSONL transcript; the automated score does not claim to validate the meaning of prose.

Usage:
    python eval_routing.py            # every case once
    python eval_routing.py --runs 3   # every case three times
    python eval_routing.py --model us.amazon.nova-lite-v1:0   # evaluate another Bedrock model
    python eval_routing.py --provider openai --model gpt-4o-mini --runs 2
    python eval_routing.py --provider openrouter --model openai/gpt-4o-mini --runs 2
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import matching
from server import CookTonightResult, SearchResult, ShoppingListUpdate


@dataclass
class Run:
    calls: list[tuple[str, dict[str, Any]]]  # (tool name, parsed arguments)
    outputs: list[Any]                        # parsed tool outputs (dict) or raw text
    answer: str
    first: Run | None = None                  # the first turn, for multi-turn cases

    def tool_output(self, name: str) -> Any:
        for (called, _), out in zip(self.calls, self.outputs):
            if called == name:
                return out
        return None


@dataclass
class Case:
    prompt: str
    accept: list[tuple[str, ...]]                          # accepted call sequences, including repeats
    check: Callable[[Run], str | None] | None = None       # automated tool check, not prose grading
    note: str = ""
    seed_list: list[str] | None = None                     # shopping list written before the case
    followup: str | None = None                            # second user turn; routing is judged on it


def route_matches(run: Run, case: Case) -> bool:
    return (tuple(name for name, _ in run.calls) in case.accept
            and len(run.calls) == len(run.outputs))


def _plain(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()


def _same_items(expected: list[str] | tuple[str, ...], actual: Any) -> bool:
    # Compare whole ingredient identities (including plurals/aliases), never substrings or cuts.
    if not isinstance(actual, list) or not all(isinstance(i, str) and i.strip() for i in actual):
        return False
    key = lambda s: matching.item_key(_plain(s))
    return {key(i) for i in expected} == {key(i) for i in actual}


def successful_tools(run: Run) -> str | None:
    if len(run.calls) != len(run.outputs):
        return "each tool call must have a result"
    models = {"search_recipes": SearchResult, "cook_tonight": CookTonightResult,
              "add_to_shopping_list": ShoppingListUpdate}
    for name, _ in run.calls:
        try:
            models[name].model_validate(run.tool_output(name))
        except (KeyError, ValueError):
            return f"{name} did not return a valid successful result"
    return None


def fridge_passed(*items: str) -> Callable[[Run], str | None]:
    def check(run: Run) -> str | None:
        failure = successful_tools(run)
        if failure:
            return failure
        args = next((a for n, a in run.calls if n == "cook_tonight"), {})
        if not _same_items(items, args.get("fridge")):
            return "cook_tonight fridge differs from the requested ingredients"
        return None
    return check


def items_passed(*items: str) -> Callable[[Run], str | None]:
    def check(run: Run) -> str | None:
        failure = successful_tools(run)
        if failure:
            return failure
        args = next((a for n, a in run.calls if n == "add_to_shopping_list"), {})
        if not _same_items(items, args.get("items")):
            return "shopping-list arguments differ from the requested items"
        out = run.tool_output("add_to_shopping_list")
        if not _same_items(items, out["added"] + out["already_on_list"]):
            return "shopping-list result does not account for exactly the requested items"
        return None
    return check


def error_relayed(run: Run) -> str | None:
    out = run.tool_output("search_recipes")
    if not (isinstance(out, str) and "No recipes found" in out):
        return f"expected the server's 'No recipes found' error, got {str(out)[:80]!r}"
    return None


def added_runner_up_needs(run: Run) -> str | None:
    if not run.first or tuple(n for n, _ in run.first.calls) != ("cook_tonight",):
        return "the first turn must call cook_tonight exactly once"
    failure = successful_tools(run.first)
    if failure:
        return failure
    first = run.first.tool_output("cook_tonight") if run.first else None
    if not isinstance(first, dict) or not first.get("runners_up"):
        return "the first turn did not call cook_tonight or returned no runners-up"
    need = first["runners_up"][0]["missing_ingredients"]
    if not need:
        return "nothing is missing, so no add call is needed" if run.calls else None
    return items_passed(*need)(run)


SEARCH, COOK, ADD = ("search_recipes",), ("cook_tonight",), ("add_to_shopping_list",)
CASES = [
    Case("What recipes use chickpeas?", [SEARCH], successful_tools),
    Case("Find me something to cook with beef.", [SEARCH], successful_tools),
    Case("What can I make with jackfruit?", [SEARCH], error_relayed,
         note="no recipes exist: must still ask the tool and relay its error"),
    Case("I have eggs, spinach and feta. What should I make tonight?", [COOK], fridge_passed("eggs", "spinach", "feta")),
    Case("My fridge has salmon, lemon and potatoes. Plan dinner for me.", [COOK], fridge_passed("salmon", "lemon", "potatoes")),
    Case("Dinner idea? I've got chicken thighs, rice, garlic, onion, carrots and peas.", [COOK],
         fridge_passed("chicken thighs", "rice", "garlic", "onion", "carrots", "peas")),
    Case("I have chicken. Show me some recipes.", [SEARCH, COOK], successful_tools,
         note="ambiguous: either tool is reasonable"),
    Case("Put milk and bread on my shopping list.", [ADD], items_passed("milk", "bread")),
    Case("What's on my shopping list?", [()],
         note="no tool; manually check the answer against the seeded list", seed_list=["tahini", "pita"]),
    Case("What's the weather in Beirut today?", [()], note="out of scope: no tool"),
    Case("I have chicken, rice, garlic and an onion. What should I cook tonight?", [ADD, ()], added_runner_up_needs,
         followup="Add what the first runner-up needs to my shopping list.",
         note="multi-turn: the follow-up must add exactly the runner-up's missing ingredients"),
]


def _parse(output: Any) -> Any:
    if isinstance(output, list) and len(output) == 1:
        return _parse(output[0])
    if isinstance(output, dict) and "text" in output:
        output = output["text"]
    try:
        return json.loads(output)
    except (TypeError, ValueError):
        return output


async def _turn(agent, turn_input) -> tuple[Run, list[Any]]:
    from agents import Runner
    from agent import turn_context

    result = await Runner.run(agent, turn_input, context=await turn_context(agent), max_turns=8)
    calls, outputs = [], []
    for item in result.new_items:
        if item.type == "tool_call_item":
            raw = item.raw_item
            name = getattr(raw, "name", None) or raw.get("name")
            args = getattr(raw, "arguments", None) or raw.get("arguments") or "{}"
            calls.append((name, json.loads(args)))
        elif item.type == "tool_call_output_item":
            outputs.append(_parse(item.output))
    return Run(calls, outputs, str(result.final_output)), result.to_input_list()


async def run_case(agent, case: Case) -> Run:
    run, history = await _turn(agent, case.prompt)
    if case.followup is None:
        return run
    second, _ = await _turn(agent, history + [{"role": "user", "content": case.followup}])
    second.first = run
    return second


async def main(runs: int, output: Path) -> int:
    from agent import make_agent, make_server, model_id, model_provider

    print(f"Agent eval: {len(CASES)} cases x {runs} run(s), model {model_provider()}/{model_id()}\n")
    routed = checked = total = 0
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as transcript, tempfile.TemporaryDirectory() as data_dir:
        async with make_server(data_dir) as server:
            agent = make_agent(server)
            for case in CASES:
                for attempt in range(runs):
                    total += 1
                    # Every repetition starts with the case's own state, not earlier saves.
                    Path(data_dir, "shopping_list.json").write_text(
                        json.dumps([{"item": i, "for_recipe": "", "added_at": ""}
                                    for i in case.seed_list or []]), encoding="utf-8")
                    try:
                        run = await run_case(agent, case)
                    except Exception as exc:
                        print(f"FAIL  error {type(exc).__name__}  <- {case.prompt}\n      {str(exc)[:160]}")
                        transcript.write(json.dumps({"prompt": case.prompt, "attempt": attempt + 1,
                                                     "error": type(exc).__name__, "automated_pass": False}) + "\n")
                        transcript.flush()
                        continue
                    called = tuple(n for n, _ in run.calls)
                    route_ok = route_matches(run, case)
                    try:
                        reason = case.check(run) if (route_ok and case.check) else None
                    except Exception as exc:  # a broken check is a failure to report, not a crash
                        reason = f"check raised {type(exc).__name__}: {exc}"
                    routed += route_ok
                    checked += route_ok and reason is None
                    status = "PASS" if route_ok and reason is None else "FAIL"
                    detail = reason or (case.note if route_ok else f"expected one of {case.accept}")
                    transcript.write(json.dumps({"prompt": case.prompt, "followup": case.followup,
                                                 "seed_list": case.seed_list or [], "model": model_id(),
                                                 "provider": model_provider(),
                                                 "attempt": attempt + 1, "run": asdict(run),
                                                 "routing_pass": route_ok, "automated_pass": status == "PASS",
                                                 "failure": None if status == "PASS" else detail,
                                                 "answer_review": "pending manual review"}) + "\n")
                    transcript.flush()
                    print(f"{status}  called {called or '(none)'}  <- {case.prompt}" + (f" / then: {case.followup}" if case.followup else "")
                          + (f"\n      {detail}" if detail else ""))
    print(f"\nRouting: {routed}/{total}   Routing + tool checks: {checked}/{total}")
    print(f"Answer faithfulness: pending manual review. Transcripts: {output}")
    return 0 if checked == total else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=1)
    parser.add_argument("--provider", choices=["bedrock", "openai", "openrouter"], help="Override LLM_PROVIDER")
    parser.add_argument("--model", help="Override the selected provider's model id")
    parser.add_argument("--output", type=Path, default=Path("eval-results") / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".jsonl"), help="JSONL transcript file")
    args = parser.parse_args()
    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        from agent import model_provider
        model_env = {"bedrock": "BEDROCK_MODEL_ID", "openai": "OPENAI_MODEL", "openrouter": "OPENROUTER_MODEL"}
        os.environ[model_env[model_provider()]] = args.model
    if args.runs < 1:
        parser.error("--runs must be at least 1")
    sys.exit(asyncio.run(main(args.runs, args.output)))
