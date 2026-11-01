"""Live benchmark for cook_tonight's choices (set RUN_LIVE=1; uses the real TheMealDB).

Two kinds of case:

* TUNING cases: regression fridges used to develop the scoring rules. They confirm known
  requirements but do not, on their own, establish generalisation.
* HELD-OUT cases: everyday fridges written after the rules were final and never used to change
  them. Each is judged by a general sanity rule, not an expected dish.

The recipe data is TheMealDB's, so a case can break if they change their catalogue.
"""

from __future__ import annotations

import os

import pytest

import matching
import server

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to hit the real TheMealDB"),
]

# (fridge, items the chosen dish must use, the wrong pick this case was added for)
TUNING = [
    (["chicken thighs", "rice", "garlic", "onion", "carrots", "peas"],
     ["chicken thighs", "rice", "garlic", "onion", "carrots", "peas"], "a beef dish needing ground beef"),
    (["eggs", "spinach", "feta"], ["eggs", "spinach", "feta"], "a cheddar snack that skipped the spinach"),
    (["tofu", "soy sauce", "ginger"], ["tofu"], "pork buns that ignored the tofu"),
    (["beef", "rice", "onion", "garlic"], ["beef"], "'beef' charged as a protein to buy"),
    (["chicken", "rice", "garlic", "onion"], ["chicken", "rice", "garlic", "onion"],
     "congee using 3 of 4: only the alphabetically first 5 matches were scored"),
    (["chilli", "chicken"], ["chilli", "chicken"], "conch stew that ignored the chicken"),
    (["mushrooms", "cream", "pasta"], ["cream", "pasta"], "a strawberry dessert ('pasta' missed 'Fettuccine')"),
    (["lamb", "yogurt", "garlic"], ["lamb"], "a yogurt dip that ignored the lamb"),
    (["eggs", "bacon", "spaghetti", "parmesan"], ["eggs", "bacon", "spaghetti"],
     "carbonara that told the user to buy egg yolks"),
]

HELD_OUT = [
    ["salmon", "rice", "soy sauce"],
    ["pork", "onion", "garlic", "potatoes"],
    ["chicken", "lemon", "garlic", "potatoes"],
    ["beef", "mushrooms", "onion"],
    ["prawns", "garlic", "pasta"],
    ["eggs", "potato", "onion"],
    ["lamb", "rice", "onion"],
    ["tofu", "rice", "broccoli"],
    ["chickpeas", "tomatoes", "onion"],
    ["cod", "potatoes", "peas"],
]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("FRIDGECHEF_DATA_DIR", str(tmp_path))


@pytest.mark.parametrize("fridge, must_use, wrong_pick", TUNING, ids=[" ".join(c[0][:3]) for c in TUNING])
def test_tuning_case(fridge, must_use, wrong_pick):
    result = server.cook_tonight(fridge)
    assert set(must_use) <= set(result.uses_from_fridge), \
        f"{result.recipe.name} uses {result.uses_from_fridge}; this case was added for: {wrong_pick}"


@pytest.mark.parametrize("fridge", HELD_OUT, ids=[" ".join(f) for f in HELD_OUT])
def test_held_out_fridge_is_sensible(fridge):
    result = server.cook_tonight(fridge)
    proteins = [f for f in fridge if matching._head(matching._tokens(f)) in matching.PROTEIN_SOURCES]
    assert all(p in result.uses_from_fridge for p in proteins), f"{result.recipe.name} ignores {proteins}"
    assert len(result.uses_from_fridge) >= 2, f"{result.recipe.name} uses only {result.uses_from_fridge}"
    assert not result.no_recipes_for, f"skipped {result.no_recipes_for}"


def test_stock_is_not_chicken():
    # A recipe that needs plain chicken must put it on the list when the fridge only has stock.
    result = server.cook_tonight(["chicken stock", "rice"])
    needs_chicken = any(i.name.strip().lower() in ("chicken", "chickens") for i in result.recipe.ingredients)
    assert not needs_chicken or "chicken" in result.missing_ingredients


@pytest.mark.parametrize("ingredient", ["potato", "mushroom", "apple", "peach", "chili", "zucchini"])
def test_ingredient_spellings_find_recipes(ingredient):
    # Common singulars and regional spellings must resolve to recipes.
    assert server.search_recipes(ingredient, 1).total_matches > 0
