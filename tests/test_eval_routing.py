"""Offline regressions for evaluator false passes; no host, credentials or model calls."""
from copy import deepcopy
import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

import eval_routing as evaluation


REPORT = {
    "recipe": {"id": "1", "name": "Lemon Rice", "ingredients": [{"name": "Rice"}]},
    "instructions": "Cook the rice.", "uses_from_fridge": ["rice"],
    "missing_ingredients": ["lemon"], "assumed_staples": [], "shopping_list": None,
    "candidates_considered": 2,
    "runners_up": [{"name": "Other Rice", "uses_from_fridge": ["rice"],
                    "missing_ingredients": ["lemon"]}], "no_recipes_for": [],
}


def action(items):
    return evaluation.Run([("add_to_shopping_list", {"items": items})],
                          [{"added": items, "already_on_list": [], "total_items": len(items),
                            "list_path": "test-list.json"}], "Answer requires manual review.")


@pytest.mark.parametrize("output", ["Tool error: unavailable", None, {}, {"recipe": {"name": "Invented"}}])
def test_dinner_error_or_malformed_output_cannot_pass(output):
    run = evaluation.Run([("cook_tonight", {"fridge": ["rice"]})], [output], "Invented dinner.")
    assert evaluation.fridge_passed("rice")(run) is not None


@pytest.mark.parametrize("actual", [["rice", "beef"], ["rice vinegar"], []])
def test_dinner_arguments_must_match_whole_requested_items(actual):
    run = evaluation.Run([("cook_tonight", {"fridge": actual})], [REPORT], "Rice.")
    assert evaluation.fridge_passed("rice")(run) is not None


def test_valid_dinner_output_and_exact_arguments_pass_tool_checks():
    run = evaluation.Run([("cook_tonight", {"fridge": ["rice"]})], [REPORT], "Rice.")
    assert evaluation.fridge_passed("rice")(run) is None


@pytest.mark.parametrize("items", [["milk chocolate", "breadcrumbs"], ["milk", "bread", "beef"], ["milk"]])
def test_shopping_check_rejects_substrings_missing_and_extra_items(items):
    assert evaluation.items_passed("milk", "bread")(action(items)) is not None


def test_shopping_check_accepts_plural_and_alias_identities():
    assert evaluation.items_passed("lemon", "yogurt")(action(["lemons", "yoghurt"])) is None


def test_shopping_result_must_account_for_requested_items():
    run = action(["milk", "bread"])
    run.outputs[0]["added"] = []
    assert evaluation.items_passed("milk", "bread")(run) is not None


def test_extra_runner_up_purchases_cannot_pass():
    run = action(["lemon", "beef", "milk"])
    run.first = evaluation.Run([("cook_tonight", {"fridge": ["rice"]})], [REPORT], "Rice.")
    assert evaluation.added_runner_up_needs(run) is not None


def test_exact_runner_up_purchases_pass():
    run = action(["lemons"])
    run.first = evaluation.Run([("cook_tonight", {"fridge": ["rice"]})], [REPORT], "Rice.")
    assert evaluation.added_runner_up_needs(run) is None


def test_no_add_call_is_needed_for_runner_up_without_missing_items():
    first = deepcopy(REPORT)
    first["runners_up"][0]["missing_ingredients"] = []
    run = evaluation.Run([], [], "Nothing to buy.",
                         first=evaluation.Run([("cook_tonight", {})], [first], "Rice."))
    assert evaluation.added_runner_up_needs(run) is None


def test_duplicate_calls_are_not_hidden_by_routing_check():
    run = action(["milk"])
    case = evaluation.Case("Add milk", [evaluation.ADD])
    assert evaluation.route_matches(run, case)
    run.calls *= 2
    run.outputs *= 2
    assert not evaluation.route_matches(run, case)


def test_missing_tool_result_fails_routing():
    run = action(["milk"])
    run.outputs.clear()
    assert not evaluation.route_matches(run, evaluation.Case("Add milk", [evaluation.ADD]))


def test_expected_failure_case_checks_server_error_not_keywords_in_answer():
    run = evaluation.Run([("search_recipes", {"ingredient": "jackfruit"})],
                         ["No recipes found that use jackfruit"], "Manual review required.")
    assert evaluation.error_relayed(run) is None
    run.outputs = [{"recipes": []}]
    assert evaluation.error_relayed(run) is not None


def test_list_answer_is_saved_for_manual_review_without_claiming_faithfulness(tmp_path, monkeypatch, capsys):
    class Server:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def fake_case(agent, case):
        return evaluation.Run([], [], "No tahini or pita is on your list.")

    monkeypatch.setitem(sys.modules, "agent", SimpleNamespace(
        make_server=lambda *args: Server(), make_agent=lambda server: object(), model_id=lambda: "test",
        model_provider=lambda: "test-provider"))
    monkeypatch.setattr(evaluation, "CASES", [evaluation.Case("What's on my list?", [()], seed_list=["tahini", "pita"])])
    monkeypatch.setattr(evaluation, "run_case", fake_case)
    output = tmp_path / "eval.jsonl"
    assert asyncio.run(evaluation.main(1, output)) == 0  # routing is correct; answer review is separate
    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["run"]["answer"] == "No tahini or pita is on your list."
    assert record["answer_review"] == "pending manual review"
    assert record["provider"] == "test-provider"
    assert "Answer faithfulness: pending manual review" in capsys.readouterr().out
