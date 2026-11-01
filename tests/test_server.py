"""Offline tests for the Fridge Chef MCP server.

Every test goes through a real MCP client (fastmcp.Client connected in-memory to the server),
so they check the same tool schemas, structured outputs and error results the agent sees.
TheMealDB is replaced by a small fake; set RUN_LIVE=1 to also run the live-API test at the end.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest
from fastmcp import Client
from filelock import FileLock

import server

REAL_GET_JSON = server._get_json

# Minimal fake TheMealDB: four recipes.
MEALS = {
    "1": {"idMeal": "1", "strMeal": "Garlic Chicken Rice", "strCategory": "Chicken", "strArea": "Lebanese",
          "strInstructions": "Fry garlic. Add chicken. Add rice and water. Simmer.",
          "ingredients": [("Chicken Breast", "300g"), ("Rice", "1 cup"), ("Garlic", "4 cloves"),
                          ("Salt", "pinch"), ("Lemon", "1")]},
    "2": {"idMeal": "2", "strMeal": "Chicken Curry", "strCategory": "Chicken", "strArea": "Indian",
          "strInstructions": "Cook everything.",
          "ingredients": [("Chicken", "500g"), ("Coconut Milk", "1 can"), ("Curry Powder", "2 tbsp"),
                          ("Onion", "1"), ("Ginger", "1 thumb")]},
    "3": {"idMeal": "3", "strMeal": "Chicken Soup", "strCategory": "Soup", "strArea": "British",
          "strInstructions": "Simmer chicken in stock with carrots.",
          "ingredients": [("Chicken", "2 legs"), ("Chicken Stock", "1L"), ("Carrots", "2")]},
    "4": {"idMeal": "4", "strMeal": "Onion Soup", "strCategory": "Soup", "strArea": "French",
          "strInstructions": "Caramelise onions in butter.",
          "ingredients": [("Red Onions", "4"), ("Butter", "50g")]},
    "5": {"idMeal": "5", "strMeal": "Plain Rice", "strCategory": "Side", "strArea": "Unknown",
          "strInstructions": "Boil rice in salted water.",
          "ingredients": [("Rice", "1 cup"), ("Water", "2 cups"), ("Salt", "pinch")]},
    "6": {"idMeal": "6", "strMeal": "Beef Fried Rice", "strCategory": "Beef", "strArea": "Chinese",
          "strInstructions": "Fry beef, add vegetables and rice.",
          "ingredients": [("Beef Mince", "300g"), ("Rice", "2 cups"), ("Onion", "1"), ("Garlic", "2 cloves"),
                          ("Peas", "1 cup")]},
}
BY_INGREDIENT = {"chicken": ["1", "2", "3"], "rice": ["1", "5", "6"], "garlic": ["1", "6"],
                 "onion": ["2", "4", "6"], "peas": ["6"]}


CATALOGUE = json.loads((Path(__file__).parent / "data" / "catalogue.json").read_text(encoding="utf-8"))


def fake_get_json(endpoint: str, params: dict[str, str]) -> dict:
    if endpoint == "list.php":  # the real TheMealDB ingredient catalogue, snapshotted
        return {"meals": [{"strIngredient": n} for n in CATALOGUE]}
    if endpoint == "filter.php":
        ids = BY_INGREDIENT.get(params["i"].replace("_", " ").lower(), [])
        return {"meals": [{"idMeal": i, "strMeal": MEALS[i]["strMeal"]} for i in ids] or None}
    if endpoint == "lookup.php":
        m = MEALS.get(params["i"])
        if not m:
            return {"meals": None}
        record = {k: v for k, v in m.items() if k != "ingredients"}
        for n, (name, measure) in enumerate(m["ingredients"], start=1):
            record[f"strIngredient{n}"], record[f"strMeasure{n}"] = name, measure
        return {"meals": [record]}
    raise AssertionError(endpoint)


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path, request):
    monkeypatch.setenv("FRIDGECHEF_DATA_DIR", str(tmp_path))
    server._lookup_meal.cache_clear()  # fake and live recipes must not leak between tests
    server._filter.cache_clear()
    server._catalogue.cache_clear()
    if "live" not in request.keywords:
        monkeypatch.setattr(server, "_get_json", fake_get_json)


@pytest.fixture
async def client():
    async with Client(server.mcp) as c:
        yield c


def saved_items(tmp_path) -> list[str]:
    path = tmp_path / "shopping_list.json"
    return [e["item"] for e in json.loads(path.read_text())] if path.exists() else []


# --- Server surface ---------------------------------------------------------

async def test_exactly_three_documented_tools(client):
    tools = {t.name: t for t in await client.list_tools()}
    assert set(tools) == {"search_recipes", "add_to_shopping_list", "cook_tonight"}
    for t in tools.values():
        assert t.description and t.input_schema["properties"] and t.output_schema
    assert tools["search_recipes"].annotations.read_only_hint is True
    assert tools["add_to_shopping_list"].annotations.read_only_hint is False


# --- Data tool --------------------------------------------------------------

async def test_search_returns_full_ingredient_lists(client):
    res = await client.call_tool("search_recipes", {"ingredient": "  Chicken ", "limit": 5})
    data = res.structured_content
    assert data["ingredient"] == "chicken" and data["count"] == 3 and data["total_matches"] == 3
    assert {i["name"] for i in data["recipes"][0]["ingredients"]} >= {"Chicken Breast", "Rice"}


async def test_search_is_read_only(client, tmp_path):
    await client.call_tool("search_recipes", {"ingredient": "rice"})
    assert not (tmp_path / "shopping_list.json").exists()


async def test_search_includes_family_members_beyond_the_old_cutoff(client, monkeypatch):
    family = server._catalogue().family("cheese")
    assert family.index("Swiss Cheese") >= 12
    monkeypatch.setitem(BY_INGREDIENT, "swiss cheese", ["1"])
    monkeypatch.setitem(BY_INGREDIENT, "cheddar cheese", ["2"])
    result = await client.call_tool("search_recipes", {"ingredient": "cheese", "limit": 1})
    assert result.structured_content["total_matches"] == 2
    assert result.structured_content["count"] == 1
    assert set(server._recipe_ids("cheese")) == {"1", "2"}


def test_separate_processes_preserve_unique_items_and_deduplicate(tmp_path):
    # Delay each read to force overlapping writers; each child uses the test's data directory.
    code = (
        "import json,sys,time,server; "
        "original=server._load_list; "
        "exec('def delayed_read():\\n result=original()\\n time.sleep(0.05)\\n return result'); "
        "server._load_list=delayed_read; "
        "[server.add_to_shopping_list([item, 'shared']) for item in json.loads(sys.argv[1])]"
    )
    batches = [[f"batch{batch}ingredient{i}" for i in range(5)] for batch in range(3)]

    def run(items):
        result = subprocess.run([sys.executable, "-c", code, json.dumps(items)],
                                cwd=Path(server.__file__).parent, env=os.environ.copy(),
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stderr

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(run, batches))
    assert sorted(saved_items(tmp_path)) == sorted([i for batch in batches for i in batch] + ["shared"])
    assert not list(tmp_path.glob("*.tmp"))


def test_file_lock_timeout_is_clear_and_preserves_list(tmp_path, monkeypatch):
    path = tmp_path / "shopping_list.json"
    before = '[{"item": "milk"}]'
    path.write_text(before, encoding="utf-8")
    monkeypatch.setattr(server, "SHOPPING_LOCK_TIMEOUT_S", 0.01)
    with FileLock(str(path) + ".lock"):
        with pytest.raises(server.ToolError, match="busy in another session"):
            server.add_to_shopping_list(["bread"])
    assert path.read_text(encoding="utf-8") == before


def test_failed_replace_preserves_list_and_removes_temporary_file(tmp_path, monkeypatch):
    path = tmp_path / "shopping_list.json"
    before = '[{"item": "milk"}]'
    path.write_text(before, encoding="utf-8")

    def denied(*args):
        raise PermissionError("test write denied")

    monkeypatch.setattr(server.os, "replace", denied)
    with pytest.raises(server.ToolError, match="nothing was added"):
        server.add_to_shopping_list(["bread"])
    assert path.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("query, indexed_name", [("eggplant", "aubergine"), ("eggplant", "egg plants"),
                                               ("yoghurt", "yogurt")])
async def test_search_common_aliases_reach_catalogue_recipes(client, monkeypatch, query, indexed_name):
    monkeypatch.setitem(BY_INGREDIENT, indexed_name, ["1"])
    result = await client.call_tool("search_recipes", {"ingredient": query})
    assert result.structured_content["recipes"][0]["id"] == "1"


async def test_staple_only_fridge_is_clear_error_and_preserves_list(client, monkeypatch, tmp_path):
    # Search can return real recipes, yet none uses a non-staple fridge ingredient.
    monkeypatch.setitem(BY_INGREDIENT, "salt", ["5"])
    monkeypatch.setitem(BY_INGREDIENT, "pepper", ["5"])
    path = tmp_path / "shopping_list.json"
    before = '[{"item": "milk", "for_recipe": "", "added_at": ""}]'
    path.write_text(before, encoding="utf-8")
    result = await client.call_tool("cook_tonight", {"fridge": ["salt", "pepper"]}, raise_on_error=False)
    assert result.is_error and "non-staple" in result.content[0].text
    assert path.read_text(encoding="utf-8") == before


async def test_zero_use_candidate_cannot_beat_recipe_that_uses_the_fridge(client, monkeypatch):
    # A zero-use recipe scores 0; the useful recipe scores below 0 due to purchases.
    # Filter before ranking rather than rejecting only the highest-scoring recipe.
    monkeypatch.setitem(MEALS, "7", {"idMeal": "7", "strMeal": "Salt Water",
                                  "ingredients": [("Salt", "pinch"), ("Water", "1 cup")]})
    monkeypatch.setitem(MEALS, "8", {"idMeal": "8", "strMeal": "Rice with vegetables",
                                  "ingredients": [(n, "1") for n in
                                                  ("Rice", "Lemon", "Ginger", "Cumin", "Coriander", "Carrots")]})
    monkeypatch.setitem(BY_INGREDIENT, "rice", ["7", "8"])
    result = await client.call_tool("cook_tonight", {"fridge": ["rice"]})
    data = result.structured_content
    assert data["recipe"]["id"] == "8" and data["uses_from_fridge"] == ["rice"]
    assert data["runners_up"] == []
    assert set(data["shopping_list"]["added"]) == {"lemon", "ginger", "cumin", "coriander", "carrots"}


async def test_search_blank_ingredient_is_clear_error(client):
    res = await client.call_tool("search_recipes", {"ingredient": "   "}, raise_on_error=False)
    assert res.is_error and "food name" in res.content[0].text


async def test_search_non_food_input_is_echoed_in_error(client):
    res = await client.call_tool("search_recipes", {"ingredient": "123"}, raise_on_error=False)
    assert res.is_error and "got '123'" in res.content[0].text


async def test_search_no_matches_is_clear_error(client):
    res = await client.call_tool("search_recipes", {"ingredient": "unobtainium"}, raise_on_error=False)
    assert res.is_error and "No recipes found" in res.content[0].text


async def test_search_api_unavailable_is_clear_error(client, monkeypatch):
    def down(url, **kwargs):
        raise httpx.ConnectTimeout("timed out")
    # Use the real _get_json so its httpx failure handling is what gets exercised.
    monkeypatch.setattr(server, "_get_json", REAL_GET_JSON)
    monkeypatch.setattr(server.httpx, "get", down)
    res = await client.call_tool("search_recipes", {"ingredient": "chicken"}, raise_on_error=False)
    assert res.is_error and "TheMealDB is unavailable" in res.content[0].text


async def test_search_limit_is_validated(client):
    res = await client.call_tool("search_recipes", {"ingredient": "chicken", "limit": 50}, raise_on_error=False)
    assert res.is_error and "10" in res.content[0].text      # "less than or equal to 10"


async def test_search_also_using_ranks_matches_that_use_more_of_the_fridge(client):
    # TheMealDB lists "chicken" matches alphabetically; also_using puts the one with onion first.
    plain = (await client.call_tool("search_recipes", {"ingredient": "chicken", "limit": 1})).structured_content
    ranked = (await client.call_tool("search_recipes", {"ingredient": "chicken", "limit": 1, "also_using": ["onion"]})
              ).structured_content
    assert plain["recipes"][0]["name"] == "Garlic Chicken Rice"
    assert ranked["recipes"][0]["name"] == "Chicken Curry" and ranked["ranked_by"] == ["onion"]


# --- Action tool ------------------------------------------------------------

async def test_add_persists_and_dedupes(client, tmp_path):
    first = (await client.call_tool("add_to_shopping_list", {"items": ["Lemon", "lemon", "Parsley"]})).structured_content
    assert first["added"] == ["lemon", "parsley"] and first["total_items"] == 2
    second = (await client.call_tool("add_to_shopping_list", {"items": ["parsley", "feta"]})).structured_content
    assert second["added"] == ["feta"] and second["already_on_list"] == ["parsley"]
    assert saved_items(tmp_path) == ["lemon", "parsley", "feta"]
    third = (await client.call_tool("add_to_shopping_list", {"items": ["Lemons"]})).structured_content
    assert third["added"] == [] and third["already_on_list"] == ["lemons"]   # "lemons" is "lemon"


async def test_add_empty_items_is_clear_error(client, tmp_path):
    res = await client.call_tool("add_to_shopping_list", {"items": ["", "  "]}, raise_on_error=False)
    assert res.is_error and "non-blank" in res.content[0].text
    assert not (tmp_path / "shopping_list.json").exists()


def test_add_concurrent_writes_lose_nothing(tmp_path):
    # Parallel tool calls run in worker threads; without the lock some of these adds get lost.
    items = [f"item{i}" for i in range(40)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda i: server.add_to_shopping_list([i]), items))
    assert sorted(saved_items(tmp_path)) == sorted(items)


@pytest.mark.parametrize("content", ["{not json", '{"a": 1}', '["milk"]', '[{"item": 3}]'])
async def test_add_corrupted_list_is_clear_error(client, tmp_path, content):
    # Broken JSON and valid JSON of the wrong shape both get the same clear error, not a TypeError.
    (tmp_path / "shopping_list.json").write_text(content)
    res = await client.call_tool("add_to_shopping_list", {"items": ["milk"]}, raise_on_error=False)
    assert res.is_error and "corrupted" in res.content[0].text


# --- Orchestration tool -----------------------------------------------------

async def test_cook_tonight_picks_best_and_adds_only_missing(client, tmp_path):
    data = (await client.call_tool("cook_tonight", {"fridge": ["chicken", "rice", "garlic"]})).structured_content
    assert data["recipe"]["name"] == "Garlic Chicken Rice"
    assert data["uses_from_fridge"] == ["chicken", "rice", "garlic"]
    assert data["missing_ingredients"] == ["lemon"]
    assert data["assumed_staples"] == ["salt"]          # assumed, not bought, and said so
    assert data["shopping_list"]["added"] == ["lemon"]
    assert data["instructions"].startswith("Fry garlic")
    assert saved_items(tmp_path) == ["lemon"]
    # Plain Rice needs nothing but leaves the chicken unused, so the chicken dishes rank above it.
    assert [r["name"] for r in data["runners_up"]] == ["Chicken Soup", "Chicken Curry"]
    assert data["runners_up"][0]["missing_ingredients"] == ["chicken stock", "carrots"]
    assert data["no_recipes_for"] == []


async def test_cook_tonight_prefers_fewest_purchases(client):
    # Plain Rice needs nothing; the others need a protein and more.
    data = (await client.call_tool("cook_tonight", {"fridge": ["rice"]})).structured_content
    assert data["recipe"]["name"] == "Plain Rice"
    assert data["missing_ingredients"] == []


async def test_cook_tonight_avoids_buying_a_different_protein(client):
    # Regression from a live eval run: Beef Fried Rice uses 4 of these items but needs beef;
    # Garlic Chicken Rice uses the chicken you have and needs only a lemon.
    data = (await client.call_tool("cook_tonight", {"fridge": ["chicken", "rice", "onion", "garlic", "peas"]})
            ).structured_content
    assert data["recipe"]["name"] == "Garlic Chicken Rice"
    # Beef Fried Rice uses the most fridge items but buys beef and ignores the chicken: third.
    assert [r["name"] for r in data["runners_up"]] == ["Chicken Curry", "Beef Fried Rice"]


async def test_cook_tonight_uses_your_own_protein(client):
    # Generic beef covers mince, so it must not be charged as a purchase.
    data = (await client.call_tool("cook_tonight", {"fridge": ["beef", "rice", "onion", "garlic", "peas"]})
            ).structured_content
    assert data["recipe"]["name"] == "Beef Fried Rice"
    assert data["missing_ingredients"] == []


async def test_cook_tonight_does_not_count_stock_as_chicken(client):
    data = (await client.call_tool("cook_tonight", {"fridge": ["chicken", "carrots"]})).structured_content
    assert data["recipe"]["name"] == "Chicken Soup"
    assert data["missing_ingredients"] == ["chicken stock"]


async def test_cook_tonight_nothing_missing_skips_action(client, tmp_path):
    data = (await client.call_tool("cook_tonight", {"fridge": ["chicken", "rice", "garlic", "lemon"]})).structured_content
    assert data["missing_ingredients"] == [] and data["shopping_list"] is None
    assert not (tmp_path / "shopping_list.json").exists()


@pytest.mark.parametrize("part", ["lemon zest", "lemon juice"])
async def test_cook_tonight_buys_whole_lemon_when_only_a_part_is_available(client, tmp_path, part):
    data = (await client.call_tool("cook_tonight", {"fridge": ["chicken", "rice", "garlic", part]})).structured_content
    assert data["recipe"]["name"] == "Garlic Chicken Rice"
    assert part not in data["uses_from_fridge"]
    assert data["missing_ingredients"] == ["lemon"]
    assert data["shopping_list"]["added"] == ["lemon"]
    assert saved_items(tmp_path) == ["lemon"]


@pytest.mark.parametrize("first, alias", [("lemon", "lemons"), ("yoghurt", "yogurt")])
async def test_cook_tonight_alias_duplicates_preserve_ranking_and_purchases(client, monkeypatch, first, alias):
    meal = dict(MEALS["1"], ingredients=MEALS["1"]["ingredients"] + [("Yogurt", "1 tbsp")])
    monkeypatch.setitem(MEALS, "1", meal)
    base = ["chicken", "rice", "garlic", first]
    single = (await client.call_tool("cook_tonight", {"fridge": base})).structured_content
    server._list_path().unlink(missing_ok=True)
    repeated = (await client.call_tool("cook_tonight", {"fridge": base + [alias]})).structured_content
    assert repeated == single
    assert alias not in repeated["uses_from_fridge"]


@pytest.mark.parametrize("part", ["egg white", "egg yolk"])
async def test_cook_tonight_buys_whole_egg_when_only_a_part_is_available(client, tmp_path, monkeypatch, part):
    monkeypatch.setitem(MEALS, "7", {"idMeal": "7", "strMeal": "Egg Rice",
                                  "ingredients": [("Egg", "1"), ("Rice", "1 cup")]})
    monkeypatch.setitem(BY_INGREDIENT, "rice", ["7"])
    data = (await client.call_tool("cook_tonight", {"fridge": ["rice", part]})).structured_content
    assert data["recipe"]["name"] == "Egg Rice"
    assert data["uses_from_fridge"] == ["rice"]
    assert data["missing_ingredients"] == ["egg"]
    assert saved_items(tmp_path) == ["egg"]


async def test_cook_tonight_skips_unknown_items(client):
    data = (await client.call_tool("cook_tonight", {"fridge": ["unobtainium", "onion"]})).structured_content
    assert data["recipe"]["name"] == "Onion Soup"
    assert data["no_recipes_for"] == ["unobtainium"]


async def test_cook_tonight_too_many_items_is_clear_error(client):
    fridge = [f"item{i}" for i in range(server.MAX_FRIDGE_ITEMS + 1)]
    res = await client.call_tool("cook_tonight", {"fridge": fridge}, raise_on_error=False)
    assert res.is_error and str(server.MAX_FRIDGE_ITEMS) in res.content[0].text


async def test_cook_tonight_api_unavailable_is_fatal(client, monkeypatch, tmp_path):
    def down(url, **kwargs):
        raise httpx.ConnectError("no route")
    monkeypatch.setattr(server, "_get_json", REAL_GET_JSON)
    monkeypatch.setattr(server.httpx, "get", down)
    res = await client.call_tool("cook_tonight", {"fridge": ["chicken"]}, raise_on_error=False)
    assert res.is_error and "TheMealDB is unavailable" in res.content[0].text
    assert not (tmp_path / "shopping_list.json").exists()


async def test_cook_tonight_no_recipes_is_clear_error(client, tmp_path):
    res = await client.call_tool("cook_tonight", {"fridge": ["unobtainium", "kryptonite"]}, raise_on_error=False)
    assert res.is_error and "No recipe uses any of" in res.content[0].text
    assert not (tmp_path / "shopping_list.json").exists()


async def test_cook_tonight_empty_fridge_is_clear_error(client):
    res = await client.call_tool("cook_tonight", {"fridge": []}, raise_on_error=False)
    assert res.is_error and "at least one ingredient" in res.content[0].text


# --- Resources --------------------------------------------------------------

async def test_resources(client):
    await client.call_tool("add_to_shopping_list", {"items": ["milk"]})
    listing = json.loads((await client.read_resource("shopping-list://current"))[0].text)
    assert [e["item"] for e in listing] == ["milk"]
    recipe = json.loads((await client.read_resource("recipe://1"))[0].text)
    assert recipe["name"] == "Garlic Chicken Rice" and recipe["instructions"]


# --- Live API (opt-in) ------------------------------------------------------

@pytest.mark.live
@pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to hit the real TheMealDB")
async def test_live_cook_tonight(client):
    data = (await client.call_tool("cook_tonight", {"fridge": ["chicken", "rice", "garlic"]})).structured_content
    assert data["recipe"]["name"] and data["uses_from_fridge"]


async def test_search_reports_total_beyond_limit(client):
    data = (await client.call_tool("search_recipes", {"ingredient": "chicken", "limit": 1})).structured_content
    assert data["count"] == 1 and data["total_matches"] == 3


async def test_cook_tonight_charges_a_repeated_ingredient_once(client, monkeypatch):
    MEALS["7"] = {"idMeal": "7", "strMeal": "Double Lemon Rice", "strCategory": "Side", "strArea": "Greek",
                  "strInstructions": "Cook rice with lemon.",
                  "ingredients": [("Rice", "1 cup"), ("Lemon", "1"), ("Lemon", "zest of 1")]}
    monkeypatch.setitem(BY_INGREDIENT, "rice", ["7"])
    try:
        data = (await client.call_tool("cook_tonight", {"fridge": ["rice"]})).structured_content
    finally:
        del MEALS["7"]
    assert data["missing_ingredients"] == ["lemon"]


async def test_search_plural_also_searches_singular(client, monkeypatch):
    # TheMealDB indexes "Eggs" and "Egg" separately; a plural query should find both sets.
    monkeypatch.setitem(BY_INGREDIENT, "eggs", ["1"])
    monkeypatch.setitem(BY_INGREDIENT, "egg", ["1", "2"])
    data = (await client.call_tool("search_recipes", {"ingredient": "eggs"})).structured_content
    assert data["total_matches"] == 2                      # "1" appears in both and is counted once
