"""Unit tests for matching.py, run against a snapshot of TheMealDB's real ingredient catalogue.

No server or network calls. Cases cover ingredient identities, product differences and recipe scoring.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from matching import PROTEIN_COST, PURCHASE_COST, Catalogue, assess, item_key, purchase_cost, search_forms

CATALOGUE = Catalogue(json.loads((Path(__file__).parent / "data" / "catalogue.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize("have, need, expected", [
    # Same head noun, modifier only says which kind, cut or form.
    ("chicken", "Chicken Breast", True),
    ("chicken breast", "Chicken", True),
    ("onion", "Red Onions", True),
    ("eggs", "Egg", True),
    ("tomatoes", "Cherry Tomatoes", True),
    ("beef", "Ground Beef", True),
    ("ground beef", "Beef", True),
    ("cream", "Double Cream", True),
    ("cheese", "Cheddar Cheese", True),
    ("mushrooms", "Chestnut Mushroom", True),
    ("feta", "Feta Cheese", True),
    ("feta cheese", "Feta", True),
    ("lemon", "Lemon Juice", True),
    ("lime", "Lime Zest", True),
    # Whole foods supply their parts; separated parts cannot supply the whole or another part.
    ("lemon", "Lemon Zest", True),
    ("lemon juice", "Lemon", False),
    ("lemon zest", "Lemons", False),
    ("lemon juice", "Lemon Zest", False),
    ("lemon zest", "Lemon Juice", False),
    ("lemon juice", "Lemon Juice", True),
    ("lemon zest", "Lemon Zest", True),
    ("lime juice", "Lime", False),
    ("orange zest", "Orange", False),
    ("egg white", "Egg", False),
    ("egg yolks", "Eggs", False),
    ("egg white", "Egg Yolks", False),
    ("egg yolk", "Egg White", False),
    ("egg whites", "Egg White", True),
    ("egg yolks", "Egg Yolk", True),
    ("red pepper", "Red Peppers", True),
    # Plurals and spellings.
    ("potato", "Potatoes", True),
    ("peach", "Peaches", True),
    ("radishes", "Radish", True),
    ("bay leaves", "Bay Leaf", True),
    ("chilli", "Chillies", True),
    ("chillies", "Chilli", True),
    ("chilli", "Green Chilli", True),
    ("anchovy", "Anchovies", True),
    ("eggplant", "Aubergine", True),
    ("aubergine", "Egg Plants", True),
    ("eggplants", "Egg Plant", True),
    ("egg plants", "Eggplants", True),
    ("yoghurt", "Yogurt", True),
    ("yogurt", "Yoghurts", True),
    # Siblings: the same food cut or prepared differently.
    ("ground beef", "Minced Beef", True),
    ("minced beef", "Ground Beef", True),
    ("ground beef", "Beef Brisket", False),
    ("beef brisket", "Ground Beef", False),
    ("minced beef", "Beef Steak", False),
    ("beef steak", "Minced Beef", False),
    ("chicken breast", "Chicken Thigh", False),
    ("chicken thigh", "Chicken Breast", False),
    # Different head noun: a different product.
    ("chicken", "Chicken Stock", False),
    ("chicken stock", "Chicken", False),
    ("rice", "Rice Vinegar", False),
    ("egg noodles", "Eggs", False),
    ("red wine vinegar", "Red Wine", False),
    ("cream", "Cream Cheese", False),
    ("goat", "Goat Cheese", False),
    ("apple", "Apple Juice", False),
    # A modifier that is itself a food (from the catalogue): a different product.
    ("milk", "Coconut Milk", False),
    ("coconut milk", "Milk", False),
    ("cream", "Coconut Cream", False),
    ("peanut butter", "Butter", False),
    ("oil", "Sesame Seed Oil", False),
    # Same head, named as a different product.
    ("pepper", "Cayenne Pepper", False),
    ("pepper", "Red Pepper", False),
    ("red pepper", "Pepper", False),
    ("wine", "White Wine", False),
    ("cream", "Ice Cream", False),
    ("cream", "Sour Cream", False),
    ("potato", "Sweet Potatoes", False),
    ("onion", "Spring Onions", False),
    ("onion", "Green Onions", False),
    ("green beans", "Beans", False),
    # Not siblings: the differing words name kinds, not cuts.
    ("bay leaf", "Grape Leaves", False),
    ("soy sauce", "Hoisin Sauce", False),
    ("chicken breast", "Duck Breast", False),
    ("beef mince", "Lamb Mince", False),
    # Categories: recipes name the pasta shape.
    ("pasta", "Spaghetti", True),
    ("pasta", "Penne Rigate", True),
    # Egg parts, bean kinds that are also foods, and regional spellings.
    ("eggs", "Egg Yolks", True),
    ("egg", "Egg White", True),
    ("egg", "Egg Noodles", False),
    ("wine", "White Wine", False),        # "white" is an egg part only after "egg"
    ("white wine", "Wine", False),
    ("beans", "Kidney Beans", True),
    ("beans", "Butter Beans", True),
    ("beef", "Beef Kidney", False),
    ("chili", "Chilli", True),
    ("chilies", "Green Chilli", True),
    ("zucchini", "Courgettes", True),
    ("scallions", "Spring Onions", True),
])
def test_covers(have, need, expected):
    assert CATALOGUE.covers(have, need) is expected


@pytest.mark.parametrize("item, includes, excludes", [
    ("potato", {"Potatoes", "New Potatoes", "Baby New Potatoes"}, {"Sweet Potatoes", "Potato Starch"}),
    ("cream", {"Cream", "Double Cream", "Heavy Cream", "Single Cream"},
     {"Coconut Cream", "Ice Cream", "Sour Cream", "Cream Cheese"}),
    ("mushroom", {"Mushrooms", "Chestnut Mushroom"}, set()),
    ("apple", {"Apples", "Cooking Apples"}, {"Apple Cider Vinegar"}),
    ("chicken", {"Chicken", "Chicken Thighs", "Ground Chicken"}, {"Chicken Stock", "Chicken Liver"}),
    ("eggplant", {"Egg Plants", "Aubergine"}, set()),
    ("yoghurt", {"Yogurt"}, set()),
    ("ground beef", {"Ground Beef", "Minced Beef"}, {"Beef Brisket", "Beef Steak"}),
    ("lemon juice", {"Lemon Juice"}, {"Lemon", "Lemons", "Lemon Zest"}),
    ("lemon zest", {"Lemon Zest"}, {"Lemon", "Lemons", "Lemon Juice"}),
    ("egg white", {"Egg White"}, {"Egg", "Eggs", "Egg Yolks"}),
    ("egg yolk", {"Egg Yolks"}, {"Egg", "Eggs", "Egg White"}),
])
def test_family(item, includes, excludes):
    family = set(CATALOGUE.family(item))
    assert includes <= family and not (excludes & family)


def test_family_of_a_compound_is_just_itself():
    assert CATALOGUE.family("chicken stock") == ["Chicken Stock"]
    assert CATALOGUE.family("bay leaf") == ["Bay Leaf", "Bay Leaves"]


@pytest.mark.parametrize("ingredient, fridge, cost", [
    ("ground beef", [], PROTEIN_COST),
    ("chicken breast", [], PROTEIN_COST),
    ("prawns", [], PROTEIN_COST),
    ("conchs", [], PROTEIN_COST),
    ("lamb shoulder", [], PROTEIN_COST),
    ("chicken stock", [], PURCHASE_COST),
    ("soy sauce", [], PURCHASE_COST),
    ("beef steak", ["beef mince"], PURCHASE_COST),       # same protein, different cut
    ("duck breast", ["chicken breast"], PROTEIN_COST),   # same cut, different protein
    ("chicken", ["chicken stock"], PROTEIN_COST),        # stock is not a protein you have
])
def test_purchase_cost(ingredient, fridge, cost):
    assert purchase_cost(ingredient, fridge) == cost


@pytest.mark.parametrize("name, expected", [
    ("Salt", True), ("Sea Salt", True), ("Extra Virgin Olive Oil", True), ("Black Pepper", True),
    ("Ground Black Pepper", True), ("Ground Red Pepper", False),
    ("Celery Salt", False), ("Red Pepper", False), ("Icing Sugar", False), ("Sesame Seed Oil", False),
])
def test_is_staple(name, expected):
    assert CATALOGUE.is_staple(name) is expected


@pytest.mark.parametrize("phrase, forms", [
    ("eggs", ["eggs", "egg"]),
    ("jackfruit", ["jackfruit", "jackfruits", "jackfruites"]),
    ("chillies", ["chillies", "chilli", "chilly"]),
    ("couscous", ["couscous", "couscouss", "couscouses"]),
])
def test_search_forms_for_names_outside_the_catalogue(phrase, forms):
    assert search_forms(phrase) == forms


def test_item_key_treats_plural_as_same_item():
    assert item_key("Lemons") == item_key("lemon")
    assert item_key("red onion") != item_key("onion")
    assert item_key("eggplants") == item_key("Egg Plants") == item_key("aubergine")
    assert item_key("yoghurt") == item_key("yogurt")


def test_staples_in_the_fridge_do_not_count_as_used():
    fit = assess(["Water", "Salt", "Rice"], ["water", "salt", "rice"], CATALOGUE)
    assert fit.uses_from_fridge == ["rice"]


def test_assess_charges_a_repeated_ingredient_once():
    fit = assess(["Rice", "Lemon", "Lemon", "Salt"], ["rice"], CATALOGUE)
    assert fit.missing == ["lemon"] and fit.assumed_staples == ["salt"] and fit.uses_from_fridge == ["rice"]
    assert fit.score == (1 - PURCHASE_COST, 1)


def test_leaving_the_fridge_protein_unused_costs_like_buying_one():
    # A yogurt dip must not outrank a meal that uses the available lamb.
    dip = assess(["Yogurt", "Garlic", "Cucumber"], ["lamb", "yogurt", "garlic"], CATALOGUE)
    burger = assess(["Lamb Mince", "Yogurt", "Garlic", "Cumin", "Mint", "Buns"], ["lamb", "yogurt", "garlic"], CATALOGUE)
    assert burger.score > dip.score


@pytest.mark.parametrize("first, alias", [("lemon", "lemons"), ("yoghurt", "yogurt")])
def test_aliases_in_fridge_do_not_inflate_score(first, alias):
    ingredients = ["Rice", first]
    single = assess(ingredients, ["rice", first], CATALOGUE)
    repeated = assess(ingredients, ["rice", first, alias], CATALOGUE)
    assert repeated == single
    assert repeated.uses_from_fridge == ["rice", first]


def test_deduplicating_fridge_keeps_different_products_and_parts():
    from matching import unique_items
    assert unique_items([" Lemon ", "lemons", "lemon juice", "egg", "egg white", " "]) == [
        "lemon", "lemon juice", "egg", "egg white"]
