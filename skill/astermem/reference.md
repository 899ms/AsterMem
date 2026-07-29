# AsterMem Agent API Reference

All agent access uses a single endpoint:

```
POST {ASTERMEM_BASE_URL}/api/agent/call
Authorization: Bearer {ASTERMEM_TOKEN}
Content-Type: application/json

{"tool": "<tool_name>", "arguments": { ... }}
```

Response: `{"success": true, "tool": "...", "result": <string or object>}`.
Errors: `401` invalid/missing token, `404` unknown tool, `400` missing tool field.

## Tools

### Retrieval

Do not assume a single search is complete: one query usually covers one angle. Judge whether the results answer the question; if not, retry with different keywords, synonyms or tags surfaced by the previous round, and only stop when coverage feels sufficient. Search responses include a `[Next Steps]` section with concrete follow-up leads.

| Tool | Arguments | Notes |
|---|---|---|
| `quick_match` | `text` (str), `top_k` (int, default 6) | Preferred recall. Accepts natural language, or a `mem_`/`trunk_` id for direct lookup. Returns matched paragraphs with ids and related items. |
| `search_memories` | `query`, `limit` (10), `min_score` (optional) | Document-level search (keyword + semantic hybrid). Leave `min_score` unset: relevance is decided per query relative to the best hit, so a fixed threshold only hurts recall. |
| `search_trunks` | `query`, `mode` (`auto`), `limit`, `min_score` (optional) | Paragraph-level search. Same guidance on `min_score`. |
| `get_memory` | `memory_id` | Full document + all paragraphs. |
| `get_trunk` | `trunk_id` | One paragraph with full text. |
| `get_related_memories` | `memory_id`, `limit` (5) | Similar documents. |
| `get_related_trunks` | `trunk_id`, `limit` (5) | Similar paragraphs. |
| `get_memory_trunks` | `memory_id` | List paragraphs of a document. |
| `list_memories` | `status` (`active`/`archived`), `source` (`api`/`user`), `limit` (20) | Titles only. |
| `list_memories_by_tag` | `tags` (list or csv), `limit` (20) | |
| `get_memory_stats` | — | Totals, tag distribution. |
| `get_profile` | `level` (`core`/`standard`/`full`, default `standard`), `with_sources` (bool) | One-call user profile: user-maintained fields + AI-distilled claims (long-term / recent / topic map). Call once at session start for instant user context. |

### Writing

| Tool | Arguments | Notes |
|---|---|---|
| `add_memory` | `title`, `content`, `tags` (list), `priority` (5) | Content is Markdown. Auto-chunked into trunks in background. |
| `update_memory` | `memory_id`, then any of `title`/`content`/`tags`/`priority`/`status` | Omitted fields unchanged. Bumps version. |
| `patch_memory` | `memory_id`, `old_text`, `new_text` | Exact unique-match replacement inside document. Fails if `old_text` not found or ambiguous. |
| `patch_trunk` | `trunk_id`, `old_text`, `new_text` | Same, scoped to one paragraph. |
| `update_trunk` | `trunk_id`, `content`/`summary`/`tags` | Whole-paragraph update. |
| `delete_memory` | `memory_id` | Soft delete (status → archived). Restore via `update_memory status=active`. |

### Configuration

| Tool | Arguments | Notes |
|---|---|---|
| `get_system_config` | `include_catalog` (default false) | Returns added providers, active selections and search settings, with `provider_catalog_ids` listing every selectable id. API keys are redacted to `has_api_key`. Pass `include_catalog: true` only to compare catalog entries in detail — it expands the reply with a full record per provider. |
| `configure_provider` | `provider_id`, plus editable fields | Adds a catalog provider or updates an added provider. Accepts `name`, `api_type`, `base_url`, `api_key_env`, `api_key`, `embedding_model`, `chat_model`, `vlm_model`, `use_for_embedding`, `use_for_chat`, `semantic_enabled`, `min_similarity`. |
| `test_provider` | `provider_id` | Makes real embedding and chat requests to the provider. |
| `rebuild_vector_index` | `confirm: true` | Starts a background rebuild. Required after changing the active embedding provider. |
| `get_vector_rebuild_status` | none | Returns phase, progress, errors and completion state. |

`configure_provider` writes API keys to the local `.env`. The key is never returned by a configuration read. If `requires_vector_rebuild` is true, confirm with the user before calling `rebuild_vector_index`.

`get_system_config` nests its fields. Read them at these exact paths — there are no flat `active_embedding_provider` or `semantic_enabled` keys, and a guessed path yields `null` rather than an error:

```json
{
  "providers": {
    "<id>": { "name": "", "api_type": "", "base_url": "", "api_key_env": "",
              "embedding_model": "", "chat_model": "", "has_api_key": false }
  },
  "provider_catalog_ids": ["anthropic", "openai", "..."],
  "active": { "embedding_provider": "<id>", "chat_provider": "<id>" },
  "search": { "semantic": { "enabled": false, "min_similarity": 0.15, "min_similarity_max": 0.4 } },
  "output_language": "en",
  "server": { "port": 8765, "api_log_max": 1000 }
}
```

An empty `providers` with an empty `active` means nothing is configured yet. Memory totals and index state are not part of this reply; read `memory_count` and `vector_index_built` from `get_memory_stats`.

## Full REST API

All protected `/api/*` routes accept either the browser session cookie or the same Bearer Token used by `/api/agent/call`. The Token must contain the required scope.

Use `scripts/astermem.sh api <METHOD> </api/path> ['<json>']`. Important route groups:

| Capability | Routes | Scope |
|---|---|---|
| Memories and search | `/api/memories*`, `/api/search`, `/api/quick-match` | `read` or `write` by method |
| Tags | `/api/tags*` | `read` or `write`; delete requires `destructive` |
| Import and export | `/api/import*`, `/api/export`, `/api/smart-import/*` | `write` or `read` |
| Explore | `/api/explore/search`, `/drill`, `/generate-memory` | `read` |
| Graph and timeline | `/api/knowledge-graph/*`, `/api/timeline/*`, `/api/visualize/*` | `read` or `write` |
| Providers and indexes | `/api/config`, `/api/providers/*`, `/api/vector-*`, `/api/trunk-index-*` | `config` |
| Account and Tokens | `/api/auth/credentials`, `/api/auth/login-protection`, `/api/tokens*` | `admin` |
| Logs | `/api/logs*` | `admin`; clearing requires `destructive` |
| Dangerous operations | `/api/clear-database`, `/api/restart`, DELETE routes | `destructive` |

For a destructive request, send `X-AsterMem-Confirm: METHOD /api/path`. The CLI adds it when the last argument is `confirm`.

Multipart endpoints such as `/api/import` and `/api/import-image` need direct `curl -F` or PowerShell multipart upload. Binary endpoints such as `/api/export` and `/api/skill/download` should write to a file.

## Deployment facts an agent may need

- Server entry: `python3.11 server.py` (repo root). Port: `config.yaml` → `server.port` (randomly assigned 8000–9000 on first boot).
- Data lives in `./data/` (SQLite + Markdown files + Chroma vectors + Whoosh index). Backups = copy that directory.
- The built-in catalog includes Anthropic, OpenAI, xAI (Grok), Google Gemini, Kimi, Alibaba Bailian, DeepSeek, MiniMax, Zhipu AI, Xiaomi MiMo, Volcengine, OpenRouter, SiliconFlow, TokenDance, PipeLLM, Asterove, LM Studio, Ollama and their coding plans. `configure_provider` adds and configures a catalog entry in one call. Remove an inactive provider with `PUT /api/config` and `{"remove_providers":["provider_id"]}`. Interrupted vector rebuilds resume on the next boot.
