"""Fridge Chef MCP server (EECE 503P/798S, Assignment C5).

Three tools, one per category from class:

* search_recipes        Data tool: read-only lookup of recipes that use an ingredient (TheMealDB).
* add_to_shopping_list  Action tool: writes items to the persistent shopping list.
* cook_tonight          Orchestration tool: calls the other two. Searches recipes for what is in
                        the fridge, picks the best fit, then adds only what is missing to the list.

Two pure lookups are exposed as resources: shopping-list://current and recipe://{meal_id}.
Ingredient matching and recipe scoring live in matching.py.

Run over stdio (what the agent host uses):  python server.py
Set FRIDGECHEF_TRACE=1 to log every MCP request this server receives, and its reply, to stderr.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
from contextlib import suppress
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import httpx
from filelock import FileLock, Timeout as LockTimeout
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import Middleware
from pydantic import BaseModel, Field

import matching

MEALDB_BASE = "https://www.themealdb.com/api/json/v1/1"  # free public test key "1"
HTTP_TIMEOUT_S = 10.0
# Each search expands ingredient families and ranks against the other fridge ingredients.
# Worker counts bound concurrency; total requests and elapsed time depend on the API and cache.
SEARCH_WORKERS = 4
LOOKUP_WORKERS = 5
MAX_FRIDGE_ITEMS = 8  # enforced by the tool schema, so a longer list is a clear validation error
SHOPPING_LOCK_TIMEOUT_S = 5.0

# FastMCP runs sync tools in worker threads and agents may call tools in parallel: this keeps the
# shopping list's read-modify-write atomic within the server process. A file lock below also
# protects the shared JSON list when separate agent sessions start separate server processes.
_list_lock = threading.Lock()

mcp = FastMCP(
    name="fridge-chef",
    instructions=(
        "Recipe tools. Use search_recipes to look up recipes for one ingredient, "
        "add_to_shopping_list to save items the user needs to buy, and cook_tonight when the "
        "user lists what is in their fridge and wants one dinner idea plus a shopping list."
    ),
)


# ---------------------------------------------------------------------------
# Typed outputs
# ---------------------------------------------------------------------------

class Ingredient(BaseModel):
    name: str
    measure: str = ""


class Recipe(BaseModel):
    id: str
    name: str
    category: str | None = None
    area: str | None = None
    ingredients: list[Ingredient]
    source_url: str | None = None


class SearchResult(BaseModel):
    ingredient: str
    total_matches: int = Field(description="How many recipes on TheMealDB use this ingredient.")
    count: int = Field(description="How many of them are returned here (at most `limit`).")
    ranked_by: list[str] = Field(description="The also_using ingredients used to rank the matches, if any.")
    recipes: list[Recipe]


class ShoppingListUpdate(BaseModel):
    added: list[str]
    already_on_list: list[str]
    total_items: int
    list_path: str


class RunnerUp(BaseModel):
    name: str
    uses_from_fridge: list[str]
    missing_ingredients: list[str]


class CookTonightResult(BaseModel):
    recipe: Recipe
    instructions: str
    uses_from_fridge: list[str]
    missing_ingredients: list[str]
    assumed_staples: list[str] = Field(
        description="Ingredients of this recipe assumed to be in every kitchen, so not added to the list."
    )
    shopping_list: ShoppingListUpdate | None = Field(
        description="Result of the add_to_shopping_list step, or null when nothing was missing."
    )
    candidates_considered: int
    runners_up: list[RunnerUp] = Field(description="The next best candidates, best first.")
    no_recipes_for: list[str] = Field(description="Fridge items that matched no recipe and were skipped.")


class UpstreamUnavailable(ToolError):
    """TheMealDB could not be reached. Unlike 'no recipes found', this is never skippable."""


# ---------------------------------------------------------------------------
# TheMealDB access and the shopping-list file
# ---------------------------------------------------------------------------

def _get_json(endpoint: str, params: dict[str, str]) -> dict[str, Any]:
    """GET a TheMealDB endpoint. Any network or HTTP failure becomes a clear ToolError."""
    try:
        resp = httpx.get(f"{MEALDB_BASE}/{endpoint}", params=params, timeout=HTTP_TIMEOUT_S)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise UpstreamUnavailable(
            f"TheMealDB is unavailable right now ({type(exc).__name__}). "
            "Try again in a moment; nothing was changed."
        ) from exc


@lru_cache(maxsize=512)  # TheMealDB's catalogue is static; cook_tonight reuses these across searches
def _filter(form: str) -> tuple[str, ...]:
    """Ids of recipes TheMealDB lists under one ingredient spelling (in its own, alphabetical, order)."""
    meals = _get_json("filter.php", {"i": form.replace(" ", "_")}).get("meals") or []
    return tuple(m["idMeal"] for m in meals)


@lru_cache(maxsize=1)
def _catalogue() -> matching.Catalogue:
    """TheMealDB's ingredient catalogue (about 1,000 names), fetched once."""
    meals = _get_json("list.php", {"i": "list"}).get("meals") or []
    return matching.Catalogue(m["strIngredient"] for m in meals)


def _recipe_ids(ingredient: str) -> list[str]:
    """Ids of recipes using any catalogue name the ingredient covers.

    "potato" searches "Potatoes", "New Potatoes", ... (the same rule cook_tonight scores with).
    A name outside the catalogue falls back to its spellings ("jackfruit" -> jackfruit, jackfruits).
    """
    names = _catalogue().family(ingredient) or matching.search_forms(ingredient)
    with ThreadPoolExecutor(max_workers=LOOKUP_WORKERS) as pool:
        id_lists = list(pool.map(_filter, names))
    return list(dict.fromkeys(i for ids in id_lists for i in ids))


@lru_cache(maxsize=256)  # recipes do not change; also saves cook_tonight a second lookup
def _lookup_meal(meal_id: str) -> dict[str, Any]:
    meals = _get_json("lookup.php", {"i": meal_id}).get("meals")
    if not meals:
        raise ToolError(f"No recipe with id '{meal_id}' exists on TheMealDB.")
    return meals[0]


def _parse_meal(meal: dict[str, Any]) -> Recipe:
    ingredients = []
    for i in range(1, 21):
        name = (meal.get(f"strIngredient{i}") or "").strip()
        if name:
            ingredients.append(Ingredient(name=name, measure=(meal.get(f"strMeasure{i}") or "").strip()))
    return Recipe(
        id=meal["idMeal"],
        name=meal["strMeal"],
        category=meal.get("strCategory"),
        area=meal.get("strArea"),
        ingredients=ingredients,
        source_url=meal.get("strSource") or meal.get("strYoutube") or None,
    )


def _list_path() -> Path:
    data_dir = Path(os.getenv("FRIDGECHEF_DATA_DIR") or Path(__file__).parent / "data")
    return data_dir / "shopping_list.json"


def _load_list() -> list[dict[str, str]]:
    path = _list_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = None
    # Valid JSON of the wrong shape is just as unusable as broken JSON.
    if not isinstance(data, list) or not all(isinstance(e, dict) and isinstance(e.get("item"), str) for e in data):
        raise ToolError(f"The shopping list file at {path} is corrupted; fix or delete it.")
    return data


# ---------------------------------------------------------------------------
# Data tool
# ---------------------------------------------------------------------------

def search_recipes(
    ingredient: Annotated[str, Field(description="One ingredient, e.g. 'chicken' or 'chickpeas'.")],
    limit: Annotated[int, Field(ge=1, le=10, description="Maximum recipes to return.")] = 5,
    also_using: Annotated[list[str] | None, Field(
        description="Optional other ingredients: matches that also use more of these are returned first.",
    )] = None,
) -> SearchResult:
    """Find recipes that use ONE ingredient and return each recipe's full ingredient list.

    TheMealDB lists matches alphabetically, so pass also_using (e.g. the rest of the user's fridge)
    to get the most relevant matches first. Read-only: it never changes the shopping list.
    Raises an error if the ingredient is blank, nothing matches, or TheMealDB is down.
    """
    query = matching.normalise(ingredient)
    if not any(c.isalpha() for c in query):
        raise ToolError(f"ingredient must be a food name such as 'chicken'; got {ingredient!r}.")

    ids = _recipe_ids(query)
    if not ids:
        raise ToolError(
            f"No recipes found that use '{ingredient}'. Try a simpler or more common name "
            "(e.g. 'chicken' instead of 'free-range chicken thighs')."
        )
    others = [o for o in dict.fromkeys(matching.normalise(a) for a in also_using or [] if a.strip()) if o != query]
    if others:
        other_ids = [set(_recipe_ids(o)) for o in others]
        ids.sort(key=lambda i: -sum(i in s for s in other_ids))  # stable: ties keep TheMealDB's order
    # filter.php only returns ids, so fetch the full record for each hit, in parallel.
    with ThreadPoolExecutor(max_workers=LOOKUP_WORKERS) as pool:
        recipes = [_parse_meal(m) for m in pool.map(_lookup_meal, ids[:limit])]
    return SearchResult(ingredient=query, total_matches=len(ids), count=len(recipes), ranked_by=others, recipes=recipes)


# ---------------------------------------------------------------------------
# Action tool
# ---------------------------------------------------------------------------

def add_to_shopping_list(
    items: Annotated[list[str], Field(description="Items to buy, e.g. ['onion', 'garlic'].")],
    for_recipe: Annotated[str | None, Field(description="Optional recipe these items are for.")] = None,
) -> ShoppingListUpdate:
    """Add items to the saved shopping list, skipping any that are already on it.

    This changes state: the list is written to data/shopping_list.json and persists between runs.
    Raises an error if no non-blank item is given.
    """
    cleaned = [matching.normalise(i) for i in items if i and i.strip()]
    if not cleaned:
        raise ToolError("items must contain at least one non-blank item to add.")

    path = _list_path()
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _list_lock, FileLock(str(path) + ".lock", timeout=SHOPPING_LOCK_TIMEOUT_S):
            current = _load_list()
            on_list = {matching.item_key(entry["item"]) for entry in current}  # "lemons" == "lemon"
            added, skipped = [], []
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for item in cleaned:
                if matching.item_key(item) in on_list:
                    skipped.append(item)
                    continue
                current.append({"item": item, "for_recipe": for_recipe or "", "added_at": now})
                on_list.add(matching.item_key(item))
                added.append(item)

            # A unique sibling file avoids collisions; replacement keeps readers on complete JSON.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=path.name + ".", suffix=".tmp", delete=False) as stream:
                tmp = Path(stream.name)
                json.dump(current, stream, indent=2)
            os.replace(tmp, path)
    except LockTimeout:
        raise ToolError("The shopping list is busy in another session; try again. Nothing was added.") from None
    except OSError as exc:
        raise ToolError(f"Could not update the shopping list ({type(exc).__name__}); nothing was added.") from None
    finally:
        if tmp is not None:
            with suppress(OSError):
                tmp.unlink(missing_ok=True)
    return ShoppingListUpdate(added=added, already_on_list=skipped, total_items=len(current), list_path=str(path))


# ---------------------------------------------------------------------------
# Orchestration tool
# ---------------------------------------------------------------------------

def cook_tonight(
    fridge: Annotated[list[str], Field(
        max_length=MAX_FRIDGE_ITEMS,
        description=f"What the user has (at most {MAX_FRIDGE_ITEMS} items), e.g. ['chicken', 'rice', 'garlic'].",
    )],
    recipes_per_ingredient: Annotated[int, Field(ge=1, le=10)] = 5,
) -> CookTonightResult:
    """Pick tonight's dinner from what is in the fridge, then put only what is missing on the list.

    Calls search_recipes for each fridge item (ranked by the rest of the fridge), scores every candidate (see matching.py: +1 per
    fridge item used, -0.25 per side ingredient, -1.5 per protein to buy and -1.5 per unused fridge
    protein; pantry staples are assumed), and calls add_to_shopping_list for the winner's missing ingredients. Also reports
    the runners-up, the staples it assumed and any fridge items that matched no recipe.
    Raises an error without writing if the fridge list is empty, no recipe uses a non-staple
    fridge ingredient, or TheMealDB is down.
    """
    items = matching.unique_items(fridge)
    if not items:
        raise ToolError("fridge must list at least one ingredient you have.")

    # Step 1: Data tool, once per fridge item, in parallel (read back in fridge order). Each search
    # ranks its matches by how much of the rest of the fridge they use, so the candidate pool holds
    # the best fits rather than the alphabetically first. "No recipes" skips that item; "API down" is fatal.
    candidates: dict[str, Recipe] = {}
    unmatched = []
    with ThreadPoolExecutor(max_workers=SEARCH_WORKERS) as pool:
        futures = [(item, pool.submit(search_recipes, item, recipes_per_ingredient, [o for o in items if o != item]))
                   for item in items]
        for item, future in futures:
            try:
                result = future.result()
            except UpstreamUnavailable:
                raise
            except ToolError:
                unmatched.append(item)
                continue
            for r in result.recipes:
                candidates.setdefault(r.id, r)
    if not candidates:
        raise ToolError(f"No recipe uses any of: {', '.join(items)}. Try more common ingredient names.")

    # Step 2: score every candidate against the fridge and rank them.
    catalogue = _catalogue()
    fit = {r.id: matching.assess([i.name for i in r.ingredients], items, catalogue) for r in candidates.values()}
    usable = [r for r in candidates.values() if fit[r.id].uses_from_fridge]
    if not usable:
        raise ToolError("No recipe uses a non-staple ingredient from your fridge; nothing was added to the shopping list.")
    ranked = sorted(usable, key=lambda r: fit[r.id].score, reverse=True)
    best = ranked[0]

    # Step 3: Action tool, only when there is something to buy.
    missing = fit[best.id].missing
    update = add_to_shopping_list(missing, for_recipe=best.name) if missing else None

    return CookTonightResult(
        recipe=best,
        instructions=(_lookup_meal(best.id).get("strInstructions") or "").strip(),
        uses_from_fridge=fit[best.id].uses_from_fridge,
        missing_ingredients=missing,
        assumed_staples=fit[best.id].assumed_staples,
        shopping_list=update,
        candidates_considered=len(candidates),
        runners_up=[
            RunnerUp(name=r.name, uses_from_fridge=fit[r.id].uses_from_fridge, missing_ingredients=fit[r.id].missing)
            for r in ranked[1:3]
        ],
        no_recipes_for=unmatched,
    )


# Register the plain functions as MCP tools. They stay plain callables, so cook_tonight composes
# the exact same code the agent can call directly.
mcp.tool(search_recipes, annotations={"readOnlyHint": True, "openWorldHint": True})
mcp.tool(add_to_shopping_list, annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True})
mcp.tool(cook_tonight, annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True})


# ---------------------------------------------------------------------------
# Resources (pure lookups)
# ---------------------------------------------------------------------------

@mcp.resource("shopping-list://current", mime_type="application/json")
def current_shopping_list() -> str:
    """The saved shopping list as JSON."""
    return json.dumps(_load_list(), indent=2)


@mcp.resource("recipe://{meal_id}", mime_type="application/json")
def recipe_by_id(meal_id: str) -> str:
    """Full recipe, including cooking instructions, for a TheMealDB id."""
    meal = _lookup_meal(meal_id)
    recipe = _parse_meal(meal).model_dump()
    recipe["instructions"] = (meal.get("strInstructions") or "").strip()
    return json.dumps(recipe, indent=2)


# ---------------------------------------------------------------------------
# Optional trace: the server's side of the round trip
# ---------------------------------------------------------------------------

class TraceRequests(Middleware):
    """Log each tool call and resource read this server receives, and what it sends back."""

    @staticmethod
    def _log(text: str) -> None:
        print(f"\033[32m[server] {text}\033[0m", file=sys.stderr, flush=True)

    async def on_call_tool(self, context, call_next):
        self._log(f"received tools/call {context.message.name}({json.dumps(context.message.arguments)})")
        try:
            result = await call_next(context)
        except Exception as exc:
            self._log(f"replying with error: {exc}")
            raise
        reply = json.dumps(result.structured_content) if result.structured_content else str(result.content)
        self._log(f"replying {reply[:160]}{' ...' if len(reply) > 160 else ''}")
        return result

    async def on_read_resource(self, context, call_next):
        self._log(f"received resources/read {context.message.uri}")
        return await call_next(context)


if os.getenv("FRIDGECHEF_TRACE") == "1":
    mcp.add_middleware(TraceRequests())


if __name__ == "__main__":
    mcp.run(show_banner=False)
