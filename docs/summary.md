# Part 1: MCP and the Fetch Server

## Key MCP concepts

Ksenia Se's article presents the Model Context Protocol as an open standard, released by Anthropic in November 2024, for connecting AI assistants to data and tools. Without it, each application needs a connector per data source: N applications and M tools mean N×M integrations. With one shared protocol, each side is implemented once, turning N×M into N+M. Servers offer three primitives, tools, resources and prompt templates, which clients discover at runtime (the specification adds who controls each: model, host and user). It cites over 1,000 community servers by February 2025 and adopters like Block and Replit. It frames MCP as complementary to RAG and agent frameworks: "not an orchestration engine or agent brain." It admits limits: the overhead of many servers, a local-first design, models that still choose tools poorly, and security that is still a work in progress.

The article's text never defines the Host (its diagram is an image). In the MCP specification, the **Host** is the user-facing LLM application (e.g. my Agents SDK script). It creates one **Client** per server, and each client keeps a 1:1 stateful JSON-RPC session (over stdio or HTTP) with a **Server** that exposes capabilities. The host decides what the model sees; the server never talks to the model.

## Critique of the Fetch server

**Well-defined interface: mostly met.** There is one tool, `fetch` (plus a user-triggered prompt), with four typed parameters (`url`, `max_length`=5000, `start_index`=0, `raw`=false) and read-only, idempotent annotations. But its description tells the model it now has internet access, steering rather than documenting.

**Isolated functionality: partly met.** Retrieval, robots.txt policy, HTML-to-Markdown conversion and pagination all happen in one call. The `raw` flag is a mode switch, the "and" smell from class: `fetch_markdown` and `fetch_raw` would be clearer. Single-purpose tools matter because the model chooses tools by their descriptions: one job per tool means fewer wrong calls, simpler tests, and read and write permissions granted separately.

**Predictable behaviour: strong, with one gap.** Connection and HTTP errors, robots.txt refusals and private IP addresses become explicit tool errors, and truncated output tells the model which `start_index` to request next. But some failures (e.g. "Page failed to be simplified") arrive as normal content wrapped in `<error>` tags, so clients must parse text to detect them.

My toolkit avoids both: no mode flags, all failures as tool errors.

---

*Sources (not part of the word count).* Article: Ksenia Se, "What Is MCP, and Why Is Everyone – Suddenly! – Talking About It?", Hugging Face, March 2025 (https://huggingface.co/blog/Kseniase/mcp). The Hugging Face page is the introduction; the details above come from the full text on Turing Post (https://www.turingpost.com/p/mcp). Server: the reference Fetch server, https://github.com/modelcontextprotocol/servers/tree/main/src/fetch, source and README as read on 2026-10-31 (main branch).
