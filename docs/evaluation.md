# Evaluation

The project is checked at three levels: tool behavior, recipe selection and live agent interactions.

## Tool tests

`pytest -q` runs 206 offline tests. TheMealDB is replaced with a fake API backed by a snapshot of its ingredient catalogue. MCP tests use a real in-memory FastMCP client to check schemas, structured results, resources and error responses.

Coverage includes invalid inputs, unavailable APIs, corrupted shopping lists, duplicate items, concurrent writes from threads and separate processes, ingredient aliases, directional matching of food parts, recipe scoring and provider selection. Provider tests use fake credentials and do not call a model.

Latest recorded result: **206 passed**.

## Recipe benchmark

Set `RUN_LIVE=1` to enable 27 tests against TheMealDB. These include nine regression fridges, ten additional fridges introduced after the initial scoring rules, and checks of ingredient spellings and product distinctions. The additional fridges were not used to tune the scoring weights.

The benchmark checks that selected meals use the fridge's protein and at least two available ingredients. It does not establish that the chosen meal is optimal or suitable for every diet.

Latest recorded result: **27 passed**. Live tests depend on TheMealDB availability and catalogue contents.

## Agent evaluation

```bash
python eval_routing.py --runs 2
python eval_routing.py --provider openrouter --model openai/gpt-4o-mini --runs 2
python eval_routing.py --provider bedrock --model openai.gpt-oss-120b-1:0 --runs 2
```

There are 11 cases, run twice with a fresh conversation and reset shopping list. Cases cover recipe searches, dinner planning, explicit shopping requests, an empty search result, a seeded list, an out-of-scope request and a two-turn runner-up purchase.

Automated checks compare ordered tool calls and counts, validate output schemas, and check exact fridge or shopping-list arguments for the relevant cases. Search cases currently check routing and output shape; matching the requested search ingredient still needs inspection. Final answers are reviewed separately against the tool outputs, including purchases, list contents, recipe claims and errors.

Recorded results on 2026-11-01:

| Model | Routing | Tool checks | Answers with no material discrepancy found |
|---|---|---|---|
| Claude Haiku 4.5, Bedrock (default) | 22/22 | 22/22 | 22/22 |
| Amazon Nova Lite, Bedrock | 22/22 | 21/22 | 21/22 |
| OpenAI gpt-oss-120b, Bedrock | 22/22 | 22/22 | 20/22 |
| OpenAI GPT-4o-mini, OpenRouter | 22/22 completed | 22/22 completed | 22/22 completed |

GPT-4o-mini's result combines 21 successful conversations with one case retried after an account-budget block. It was not an uninterrupted 22/22 run. The blocked attempts were retained separately and are not counted as model errors.

Nova Lite selected the wrong runner-up in one follow-up. gpt-oss-120b produced two misleading claims about assumed staples and unused fridge ingredients. GPT-4o-mini exceeded the requested answer length on one search response. These observations separate correct tool use from factual accuracy and presentation quality.

The answer counts are manual assessments on this small test set, not proof of perfect reliability. Raw transcripts are local development artifacts and are not bundled with the source. Running the evaluator creates new JSONL records under the Git-ignored `eval-results/` directory, initially marked as awaiting answer review.
