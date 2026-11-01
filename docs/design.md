# Design

## Tool boundaries

`search_recipes` is read-only. It queries TheMealDB and returns full ingredient lists so callers can compare recipes without a separate details tool.

`add_to_shopping_list` owns persistence. It normalizes item names, skips duplicates, and writes the shared JSON shopping list.

`cook_tonight` composes those same functions: search for each fridge ingredient, rank the candidates and save the selected meal's missing ingredients. Matching and scoring are pure functions in `matching.py`.

## Resources and context

The host reads `shopping-list://current` through its MCP client once at the start of each turn. Reading it before every model call would let a newly added item appear as though it was already present at the start of the turn.

`recipe://{meal_id}` provides a full recipe and instructions for other MCP clients. This host does not need it because its tools already return the information required by the chat flow.

## Ingredient matching

Matching uses TheMealDB's ingredient catalogue. Names generally match when they share a head noun and their modifiers do not identify another food or a different product.

| Available ingredient | Recipe ingredient | Covered? |
|---|---|---|
| cream | Double Cream | Yes |
| cream | Coconut Cream | No |
| chicken | Chicken Breast | Yes |
| chicken | Chicken Stock | No |
| ground beef | Minced Beef | Yes |
| ground beef | Beef Brisket | No |
| lemon | Lemon Juice | Yes |
| lemon juice | Lemon | No |
| eggs | Egg Yolks | Yes |
| egg white | Egg | No |

Aliases include eggplant/aubergine, yoghurt/yogurt, chili/chilli, zucchini/courgette and scallions/spring onions. Explicit meat cuts must agree, while ground and minced meat are equivalent. Whole citrus and eggs can supply their parts; separated parts cannot supply the whole or another part.

The same coverage rule drives catalogue-family searches and scoring. Equivalent fridge entries such as lemon/lemons count once, preserving the first spelling for display. [Ingredient families](families.md) lists common examples and exclusions.

## Candidate selection and scoring

Each search considers all names in an ingredient's catalogue family. The optional `also_using` argument ranks recipe IDs by overlap with the other fridge ingredients before fetching the returned recipes. The orchestrator takes up to five candidates per fridge ingredient by default, deduplicates recipes and ranks that pool.

- Each fridge item used contributes +1.
- Each missing side ingredient costs 0.25.
- Buying a different main protein costs 1.5; another cut of an available protein costs 0.25.
- Leaving a fridge protein unused costs 1.5.
- Pantry staples are assumed, not purchased or counted as fridge usage.

Candidates using no non-staple fridge ingredient are excluded. If none remain, the tool returns an error before changing the shopping list. The selected result includes alternatives, assumed staples and inputs with no recipe matches.

## Persistence and failures

A thread lock and file lock protect the shopping-list read-modify-write across concurrent sessions. A unique sibling temporary file is atomically replaced into place. Failed writes preserve the original list and remove temporary files; lock acquisition times out after five seconds.

An unknown fridge ingredient is skipped and reported. An unavailable recipe API is fatal. Empty input and more than eight fridge entries are rejected, and no action is taken when the selected recipe needs no purchases.

Catalogue, filter and recipe responses are cached. Worker pools bound concurrency. HTTP requests have a ten-second timeout, while the host waits up to ninety seconds for an MCP result; the client limit does not guarantee server cancellation.

## Limitations

- Matching is heuristic and does not check quantities, dietary restrictions or every preparation difference.
- Generic names can be ambiguous: peppers resolves to a spice family; red peppers is more specific.
- The best candidate in the sampled pool is not necessarily the best recipe in the entire database.
- Recipe quality and availability depend on TheMealDB.
- The local shopping list is shared by sessions, and there is no remove-item tool.
