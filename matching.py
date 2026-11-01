"""Ingredient matching and recipe scoring for cook_tonight. Pure functions, no I/O.

The matching rests on TheMealDB's own ingredient catalogue (about 1,000 names, which are exactly
the names recipes use). One rule decides whether two names are the same food:

    They share a HEAD NOUN, and no extra word is itself a food.

  "cream" ~ "Double Cream"      head "cream"; "double" is not a food
  "cream" !~ "Coconut Cream"    "coconut" is a food (it heads names in the catalogue)
  "cream" !~ "Cream Cheese"     different head ("cheese")
  "chicken" ~ "Chicken Breast"  cuts and preparation words ("breast", "minced") are never heads

Whole citrus and eggs can supply their parts; juice, zest, whites and yolks cannot supply the whole.
Search and scoring both use this directional coverage rule.

Which words are foods comes from the catalogue, not from a hand-written list. What stays hand-written
is short and explicit: cut/preparation words, a few same-head different products (sweet potatoes,
spring onions, red pepper vs pepper), protein sources, and pantry staples. See docs/families.md
for what common fridge items resolve to.

Public API: Catalogue (family, covers, is_staple), normalise, search_forms, item_key, unique_items, purchase_cost, assess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_IRREGULAR = {"leaves": "leaf", "loaves": "loaf", "halves": "half"}
# One spelling per food, for names the catalogue lists both ways (US and UK spellings, synonyms).
_ALIASES = {"eggplant": "aubergine", "yoghurt": "yogurt", "chili": "chilli", "zucchini": "courgette"}


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _stem(word: str) -> str:
    """Fold plurals and y/ie spellings: chillies, chilli -> chilli; peaches -> peach; eggs -> egg."""
    w = word.lower()
    if w in _IRREGULAR:
        return _IRREGULAR[w]
    if w.endswith("ies"):
        w = w[:-3] + "i"
    elif w.endswith(("ches", "shes", "sses", "xes", "oes")):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith(("ss", "us")):  # not "couscous", "asparagus"
        w = w[:-1]
    return w[:-1] + "i" if w.endswith("y") else w


def _tokens(text: str) -> list[str]:
    # The catalogue uses both "Egg Plants" and "Aubergine". Use the same identity
    # for search, matching and shopping-list deduplication, while retaining display names.
    text = re.sub(r"\begg[\s-]+plants?\b", "aubergine", text.lower())
    text = re.sub(r"\bscallions?\b", "spring onion", text)
    words = [_stem(w) for w in re.findall(r"[a-z0-9]+", text)]
    return [_ALIASES.get(w, w) for w in words]


def _stems(*words: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in words)


# --- The short hand-written part -----------------------------------------------------------------
# Explicit meat forms are compatible only when they agree; "ground" and "minced"
# are synonyms, but ground beef cannot stand in for brisket (or breast for thigh).
_PROTEIN_CUTS = _stems(
    "breast", "thigh", "drumstick", "wing", "leg", "fillet", "steak", "chop", "shoulder", "loin",
    "tenderloin", "sirloin", "rib", "shank", "brisket", "belly", "cutlet", "cheek", "neck",
)
_MINCED_FORMS = _stems("mince", "minced", "ground")
# Never a head noun: meat cuts, parts and preparation words. "Lamb Mince" is lamb, "Garlic Clove" garlic.
NON_HEAD = _PROTEIN_CUTS | _MINCED_FORMS | _stems(
    "clove", "chopped", "diced", "sliced", "grated", "beaten", "crushed", "melted", "chilled",
    "cubed", "shredded", "dried", "fresh", "frozen", "cooked", "raw", "peeled", "of",
)
# Catalogue heads that, as modifiers, name a variety rather than another food: "Cherry Tomatoes".
VARIETY_FOODS = _stems("cherry", "plum", "baby", "new", "chestnut")
_COLOURS = _stems("red", "white", "yellow", "green", "brown", "black", "orange")
# Same head noun, different product: modifier words that make it so, per head.
DIFFERENT_PRODUCTS = {
    _stem("pepper"): _COLOURS | _stems("bell", "cayenne", "sichuan", "padron", "roasted", "mixed", "sweet",
                                       "habanero", "jalapeno", "romano", "scotch", "bonnet"),
    _stem("wine"): _COLOURS,
    _stem("vinegar"): _COLOURS,
    _stem("potato"): _stems("sweet"),
    _stem("onion"): _stems("spring", "green"),
    _stem("cream"): _stems("ice", "sour"),
    _stem("sugar"): _stems("icing"),
    _stem("milk"): _stems("condensed", "evaporated", "sour", "soya"),
    _stem("bean"): _stems("green", "french", "runner", "broad"),
    _stem("rice"): _stems("wild"),
    _stem("egg"): _stems("flax"),
    _stem("pea"): _stems("split", "snow", "sugar", "snap", "black", "eyed", "chick"),
    _stem("butter"): _stems("palm"),
}
# A trailing form word that keeps the base food: "Feta Cheese" is feta, "Lemon Juice" is lemon.
_CITRUS = _stems("lemon", "lime", "orange", "grapefruit")
FORM_WORDS = {
    _stem("cheese"): _stems("feta", "parmesan", "cheddar", "mozzarella", "halloumi", "ricotta", "gouda", "brie",
                            "gruyere", "pecorino", "mascarpone", "stilton", "emmental", "manchego"),
    _stem("juice"): _CITRUS,
    _stem("zest"): _CITRUS,
}
# Kinds of a head that are also foods in their own right: "Kidney Beans" are beans, not kidney.
KINDS = {_stem("bean"): _stems("kidney", "butter")}
# Category words whose members are named by kind: recipes say "Spaghetti", not "Pasta".
CATEGORIES = {
    _stem("pasta"): _stems("spaghetti", "penne", "linguine", "tagliatelle", "fettuccine", "fusilli", "farfalle",
                           "rigatoni", "macaroni", "lasagne", "pappardelle", "orzo", "conchiglie", "bucatini",
                           "paccheri", "tortellini", "ravioli", "vermicelli"),
}
# Proteins, matched on the head noun. TheMealDB's own types are too noisy to use for this
# ("Bok Choi" and "Pistachio" are typed Meat; "Chicken" has no type).
PROTEIN_SOURCES = _stems(
    "chicken", "beef", "pork", "lamb", "mutton", "veal", "goat", "venison", "rabbit", "turkey", "duck",
    "fish", "salmon", "tuna", "cod", "haddock", "hake", "trout", "mackerel", "sardine", "anchovy", "herring",
    "halibut", "monkfish", "snapper", "tilapia", "prawn", "shrimp", "squid", "octopus", "mussel", "clam",
    "scallop", "oyster", "crab", "lobster", "conch", "chorizo", "bacon", "ham", "pancetta", "sausage",
    "kielbasa", "tofu", "oxtail",
)
# Assumed in every kitchen, so never bought and never counted as "using" the fridge. Variants
# ("Sea Salt", "Extra Virgin Olive Oil") count too, by the same matching rule.
PANTRY_STAPLES = ("salt", "black pepper", "pepper", "water", "oil", "olive oil", "vegetable oil",
                  "canola oil", "sunflower oil", "sugar")
_STAPLE_ADJECTIVES = _stems("sea", "kosher", "table", "fine", "coarse", "flaky", "extra", "virgin", "light",
                            "caster", "granulated", "cold", "warm", "hot", "boiling", "freshly", "cracked", "ground")

# --- Scoring --------------------------------------------------------------------------------------
# Each fridge item a recipe uses is worth +1. Buying a side ingredient costs PURCHASE_COST. Buying a
# main protein you don't have costs PROTEIN_COST, and so does leaving a protein in the fridge unused:
# either way you are not cooking the meal your fridge suggests. Checked against tests/test_benchmark.py, which includes held-out
# fridges that were never used to choose these numbers.
PURCHASE_COST = 0.25
PROTEIN_COST = 1.5


# Parts of an egg, which are heads only after "egg": "Egg Yolks" and "Egg White" are egg, "White Wine" is wine.
_EGG_PARTS = _stems("yolk", "white")


def _head(tokens: list[str]) -> str:
    for i in range(len(tokens) - 1, -1, -1):
        t = tokens[i]
        if t in NON_HEAD or (t in _EGG_PARTS and i > 0 and tokens[i - 1] == "egg"):
            continue
        return t
    return tokens[-1] if tokens else ""


def search_forms(phrase: str) -> list[str]:
    """Spellings to try on TheMealDB for a name that is not in the catalogue ("eggs" -> eggs, egg)."""
    head, _, last = normalise(phrase).rpartition(" ")
    forms = [last]
    if last.endswith("ies"):
        forms += [last[:-3] + "i", last[:-3] + "y"]
    elif last.endswith(("ches", "shes", "sses", "xes", "oes")):
        forms.append(last[:-2])
    elif last.endswith("s") and not last.endswith(("ss", "us")):
        forms.append(last[:-1])
    else:
        forms += [last + "s", last + "es"]
    return list(dict.fromkeys(f"{head} {f}".strip() for f in forms))


def item_key(text: str) -> frozenset[str]:
    """Identity for de-duplicating a shopping list: 'Lemons' and 'lemon' are the same item."""
    return frozenset(_tokens(text))


def unique_items(items: Iterable[str]) -> list[str]:
    """Normalize and deduplicate ingredients, keeping the first spelling for display."""
    first = {}
    for item in items:
        if item and item.strip():
            name = normalise(item)
            first.setdefault(item_key(name), name)
    return list(first.values())


class Catalogue:
    """TheMealDB's ingredient names, and the matching rule built on them."""

    def __init__(self, names: Iterable[str]):
        self.names = list(dict.fromkeys(n.strip() for n in names if n and n.strip()))
        self._tok = {n: _tokens(n) for n in self.names}
        # A word is a food if some catalogue name has it as its head noun ("coconut", "peanut", "onion").
        self.food_words = {_head(t) for t in self._tok.values() if t} - VARIETY_FOODS

    def _variant(self, general: list[str], specific: list[str]) -> bool:
        """True when `specific` is `general` plus words that only say which kind, cut or form."""
        g, s = set(general), set(specific)
        if not g or not g <= s:
            return False
        head_g, head_s = _head(general), _head(specific)
        extra = s - g
        if head_s != head_g:
            # "Feta Cheese" for "feta", "Lemon Juice" for "lemon": a form word after the base food.
            return head_s in FORM_WORDS and head_g in FORM_WORDS[head_s] and extra == {head_s}
        if extra & DIFFERENT_PRODUCTS.get(head_g, frozenset()):
            return False
        # Kinds of the head are not "other foods": "Cheddar Cheese" is still cheese.
        kinds = FORM_WORDS.get(head_g, frozenset()) | KINDS.get(head_g, frozenset())
        return not (extra - NON_HEAD - kinds) & self.food_words

    def _match(self, a: list[str], b: list[str]) -> bool:
        # Coverage is directional for separated parts: juice cannot supply a whole fruit,
        # nor whites a whole egg. Whole foods can still supply their parts below.
        if _head(a) in {_stem("juice"), _stem("zest")} and set(a) & _CITRUS:
            if _head(a) != _head(b):
                return False
        if _head(a) == _head(b) == "egg":
            parts = set(a) & _EGG_PARTS
            if parts and parts != set(b) & _EGG_PARTS:
                return False
        if _head(a) == _head(b) and _head(a) in PROTEIN_SOURCES:
            forms = [{"mince" if t in _MINCED_FORMS else t for t in tokens
                      if t in _PROTEIN_CUTS or t in _MINCED_FORMS} for tokens in (a, b)]
            if all(forms) and forms[0] != forms[1]:
                return False
        if self._variant(a, b) or self._variant(b, a):
            return True
        for x, y in ((a, b), (b, a)):  # "pasta" covers "Penne Rigate" and "Spaghetti"
            if len(set(x)) == 1 and x[0] in CATEGORIES and set(y) & CATEGORIES[x[0]]:
                return True
        # Siblings: "Ground Beef" and "Minced Beef" are the same food, cut or prepared differently.
        # Only cut/preparation words may differ, so "Bay Leaf" and "Grape Leaves" are not siblings.
        common = [t for t in a if t in set(b)]
        if not common or not _head(common) == _head(a) == _head(b):
            return False
        differing = (set(a) | set(b)) - set(common)
        if differing & DIFFERENT_PRODUCTS.get(_head(common), frozenset()):
            return False
        return differing <= NON_HEAD

    def covers(self, have: str, need: str) -> bool:
        """True when a fridge item can stand in for a recipe ingredient."""
        return self._match(_tokens(have), _tokens(need))

    def family(self, item: str) -> list[str]:
        """Catalogue names this item covers, the closest (fewest extra words) first."""
        t = _tokens(item)
        hits = [n for n in self.names if self._match(t, self._tok[n])]
        return sorted(hits, key=lambda n: (len(set(self._tok[n]) ^ set(t)), n.lower()))

    def is_staple(self, ingredient: str) -> bool:
        t = _tokens(ingredient)
        for base in PANTRY_STAPLES:
            b = _tokens(base)
            if set(t) == set(b):
                return True
            extra = set(t) - set(b)
            if set(b) <= set(t) and _head(t) == _head(b) and extra <= _STAPLE_ADJECTIVES:
                return True
        return False


def purchase_cost(ingredient: str, fridge: list[str] = ()) -> float:
    """PROTEIN_COST for a main protein you do not have, else PURCHASE_COST.

    A protein of the same kind as a protein in the fridge (beef steak when you have beef mince) is
    only a side purchase. Only fridge items that are themselves proteins count, so "chicken stock"
    in the fridge does not make buying chicken cheap.
    """
    if _head(_tokens(ingredient)) not in PROTEIN_SOURCES:
        return PURCHASE_COST
    fridge_proteins = {_head(_tokens(f)) for f in fridge} & PROTEIN_SOURCES
    return PURCHASE_COST if set(_tokens(ingredient)) & fridge_proteins else PROTEIN_COST


@dataclass(frozen=True)
class Assessment:
    uses_from_fridge: list[str]
    missing: list[str]
    assumed_staples: list[str]
    score: tuple[float, int]  # (fridge items used - purchase cost, fridge items used) for sorting


def assess(ingredients: list[str], fridge: list[str], catalogue: Catalogue) -> Assessment:
    """How well a recipe (its ingredient names) fits a fridge (normalised item names)."""
    fridge = unique_items(fridge)
    names = list(dict.fromkeys(normalise(i) for i in ingredients))  # a recipe may repeat an ingredient
    have = lambda name: any(catalogue.covers(f, name) for f in fridge)  # noqa: E731
    staples = [n for n in names if catalogue.is_staple(n)]
    real = [n for n in names if n not in staples]
    used = [f for f in fridge if any(catalogue.covers(f, n) for n in real)]  # salt "uses" nothing
    missing = [n for n in real if not have(n)]
    cost = sum(purchase_cost(m, fridge) for m in missing)
    unused_proteins = [f for f in fridge if _head(_tokens(f)) in PROTEIN_SOURCES and f not in used]
    cost += PROTEIN_COST * len(unused_proteins)
    return Assessment(used, missing, [n for n in staples if not have(n)], (len(used) - cost, len(used)))
