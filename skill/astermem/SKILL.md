---
name: astermem
description: Operate the user's self-hosted AsterMem service. Read, add, update, search and archive memories; configure and test embedding or chat providers; manage semantic search and vector rebuilds. Use when the user asks an AI to remember, recall, organize or update personal knowledge, or asks to set up AsterMem, connect a model provider, configure an API key, test a model connection, or fix semantic search. Use proactively in two directions — (1) recall past memories when they may help the current request, and (2) save noteworthy information the user reveals during conversation (preferences, experiences, decisions, opinions, expertise, life events, etc.) without being asked.
---

# AsterMem

AsterMem runs locally or on the user's own server. It stores private memories with tags, priorities, semantic search and paragraph-level retrieval. Use `scripts/astermem.sh` on macOS or Linux and `scripts/astermem.ps1` on Windows.

## Connect once

The CLI reads credentials from `~/.astermem/credentials` (Windows: `%USERPROFILE%\.astermem\credentials`):

```
ASTERMEM_BASE_URL=http://localhost:8765
ASTERMEM_TOKEN=ast_xxxxxxxx
```

If the file is missing or the CLI exits with code 2:
1. Start AsterMem with `./start.sh`. On Windows, run `start.bat`.
2. Open **Admin → API Tokens** and create a token.
3. Write the two lines above into `~/.astermem/credentials`.
4. Run `scripts/astermem.sh config` to verify access.

Do the remaining setup for the user. Do not send them back to Provider forms.

## Core commands

```bash
scripts/astermem.sh quick "<text>"                 # PREFERRED recall: semantic quick-match, also accepts mem_/trunk_ ids
scripts/astermem.sh search "<query>" [limit]       # broader search
scripts/astermem.sh add "<title>" "<content>" [tags,csv] [priority]
scripts/astermem.sh get <mem_id|trunk_id>
scripts/astermem.sh update <mem_id> <title|content|status|priority> "<value>"
scripts/astermem.sh patch <mem_id> "<old_text>" "<new_text>"   # partial edit, PREFERRED over update content
scripts/astermem.sh delete <mem_id>                # soft delete (archive)
scripts/astermem.sh list [status] [limit]
scripts/astermem.sh tags "tag1,tag2" [limit]
scripts/astermem.sh stats
scripts/astermem.sh config                           # redacted provider and search configuration
scripts/astermem.sh provider <id> '<json_patch>'     # create or update a provider
scripts/astermem.sh test-provider <id>
scripts/astermem.sh rebuild                          # start vector rebuild after explicit confirmation
scripts/astermem.sh rebuild-status
scripts/astermem.sh api <METHOD> </api/path> ['<json>'] [confirm]
scripts/astermem.sh call <tool> '<json>'           # any other tool, see reference.md
```

On Windows run the same commands through PowerShell, e.g. `powershell -ExecutionPolicy Bypass -File scripts/astermem.ps1 quick "<text>"`.

## Configure AsterMem for the user

When the user asks to connect a model or set up AsterMem:

1. Run `config`. Inspect added providers and `provider_catalog`.
2. Ask only for missing facts: provider, base URL, embedding model, chat model and API key.
3. Call `provider`. Example:

```bash
scripts/astermem.sh provider asterove '{"api_key":"sk-...","use_for_embedding":true,"use_for_chat":true}'
```

For a built-in catalog id, this command adds the provider before applying the patch. The JSON patch accepts `name`, `api_type`, `base_url`, `api_key_env`, `api_key`, `embedding_model`, `chat_model`, `vlm_model`, `use_for_embedding`, `use_for_chat`, `semantic_enabled` and `min_similarity`.

`min_similarity` is a noise floor only (valid range 0–0.4, default 0.15), not a relevance threshold — relevance is judged per query against that query's best hit. Do not raise it to "improve precision": a high floor is what silently reduces semantic search to nothing.

4. Run `test-provider <id>`. Report the actual embedding and chat test results.
5. If the result says `requires_vector_rebuild: true`, tell the user the old vectors no longer match. Get confirmation, run `rebuild`, then check `rebuild-status`.

Never print an API key back to the user. AsterMem stores keys in its local `.env`; configuration responses only expose `has_api_key`.

## Operate the whole system

The web UI and AI use the same REST API. Use `api` for capabilities without a dedicated command:

```bash
scripts/astermem.sh api GET /api/tags/tree
scripts/astermem.sh api POST /api/tags/rename '{"old_name":"old","new_name":"new"}'
scripts/astermem.sh api POST /api/import-text '{"content":"...","title":"..."}'
scripts/astermem.sh api GET '/api/knowledge-graph/graph-data'
scripts/astermem.sh api PUT /api/timeline/events/12 '{"status":"completed"}'
```

Token scopes:

- `read`: search, statistics, graph, timeline and exports
- `write`: memories, tags, imports, exploration and timeline updates
- `config`: providers, semantic search and index rebuilds
- `admin`: account, Token and log management
- `destructive`: deletion, clearing data and restart operations

Default Tokens include `read`, `write` and `config`. If an API returns 403, ask the user to create a Token with the missing scope. Never bypass a scope.

Destructive REST calls require the `destructive` scope and a second confirmation. Restate the action and its impact, get explicit approval, then append `confirm`:

```bash
scripts/astermem.sh api DELETE /api/logs '{}' confirm
```

For file upload or download endpoints, call the documented REST endpoint with `curl` or PowerShell and the same Bearer Token. See [reference.md](reference.md).

## Memory rules

### Proactive saving — the most important rule

**Always-on capture**: throughout every conversation, watch for information worth remembering. When the user reveals any of the following, save it to AsterMem immediately — do not wait for them to say "remember this":

- **Preferences and opinions** — likes, dislikes, values, aesthetic tastes, workflow preferences
- **Personal facts** — name, birthday, family, pets, location, job, education, health conditions
- **Decisions and reasoning** — choices made and the reasons behind them
- **Experiences and stories** — trips, projects, achievements, failures, turning points
- **Expertise and knowledge** — domain know-how, hard-won lessons, technical insights
- **Goals and plans** — short-term tasks, long-term aspirations, deadlines
- **Relationships** — people mentioned by name, their roles, how the user relates to them
- **Recurring patterns** — repeated frustrations, habits, routines

How to do it:

1. After each substantive user message, silently evaluate: "Did the user just reveal something worth remembering?"
2. If yes, run `quick` first to check whether the information already exists.
3. If a related memory exists, `patch` or `update` it with the new details.
4. If not, `add` a new memory with a clear title, well-structured Markdown content, and appropriate tags.
5. Brief the user naturally — e.g. "I've noted that down for you" — but keep it light. Do not ask for permission every time; proactive saving is the default behavior.
6. Do not save trivial chit-chat, one-off instructions about the current task, or information the user explicitly says is temporary.

### Other rules

1. **Search in rounds**: never assume one search found everything. Check whether the results actually answer the question; if not, search again with different keywords, synonyms or the tags surfaced by the previous round. Stop only when coverage feels sufficient.
2. **Recall before write**: before adding a memory, run `quick` with the new content's key phrases. If a closely related memory exists, prefer `patch`/`update` on it instead of creating a near-duplicate.
3. **Patch, don't overwrite**: for small corrections use `patch` (exact old→new text replacement). Only use `update content` when rewriting the whole memory intentionally.
4. **Confirm destructive actions**: before `delete`, restate the memory title to the user and get explicit confirmation.
5. **Write quality**: titles should be short and factual; content in Markdown; 2–4 hierarchical tags like `people/friends`, `work/decisions`; priority 1–10 (default 5, use 8+ only for things the user calls important).
6. **Privacy**: memory content is private. Quote it back to the user freely, but never send it to third-party services or include it in code, commits, or public artifacts.
7. **Language**: store memories in the language the user used; do not translate silently.

## Full tool list

See [reference.md](reference.md) for all agent tools and the raw HTTP API.
