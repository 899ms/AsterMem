"""
Provider registry: unified access layer for Embedding and Chat models

Background: separate Embedding and Chat classes used to exist for LM Studio / Bailian /
OpenRouter / Google AI, with the tagging prompt duplicated three times; adding a new provider
required changes in four places.
Design intent: inspired by Asterove Gateway's Provider registry (base_url + api_key + api_type),
all differences live in configuration, with three protocol adapters in code:
  - openai_compatible: LM Studio, Bailian (compatible-mode), OpenRouter, Asterove,
    and any OpenAI-compatible endpoint (POST {base}/embeddings, POST {base}/chat/completions)
  - gemini: Google AI native (:embedContent / :generateContent)
  - anthropic: Anthropic Messages API (POST {base}/messages, Chat only)
Key constraints:
  - API Keys are always read from environment variables (config only stores env var names);
    config files and API responses never echo plaintext keys
  - Single-user self-hosted scenario; no fallback chains or circuit breakers (over-engineering);
    failures raise errors directly for the caller to handle
  - Switching embedding provider changes vector dimensions; the upper layer must trigger
    a full rebuild (see api.py vector-rebuild)

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import re
import time
from typing import List, Optional

import httpx

from .usage_tracker import estimate_tokens, record_usage

# Network config uses proven values: short connect timeout to quickly detect dead connections,
# keep-alive disabled to avoid cross-thread sharing deadlocks
_EMBEDDING_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=5.0, pool=5.0)
_CHAT_TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=5.0, pool=5.0)
_POOL_LIMITS = httpx.Limits(max_connections=5, max_keepalive_connections=0, keepalive_expiry=5)


# ==================== Usage parsing (unified gateway observation point) ====================
# The three protocols have different usage field structures; centralized here to parse into
# (prompt, completion, cached). Parse failures always return 0 to avoid affecting business response handling.

def _parse_openai_usage(data: dict) -> tuple[int, int, int]:
    usage = data.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    # Cache hits: OpenAI uses prompt_tokens_details.cached_tokens, DeepSeek uses prompt_cache_hit_tokens
    details = usage.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)
    return prompt, completion, cached


def _parse_anthropic_usage(data: dict) -> tuple[int, int, int]:
    usage = data.get("usage") or {}
    cached = int(usage.get("cache_read_input_tokens") or 0)
    # Anthropic's input_tokens excludes cache reads; observation layer normalizes to "total input"
    prompt = int(usage.get("input_tokens") or 0) + cached + int(usage.get("cache_creation_input_tokens") or 0)
    completion = int(usage.get("output_tokens") or 0)
    return prompt, completion, cached


def _parse_gemini_usage(data: dict) -> tuple[int, int, int]:
    usage = data.get("usageMetadata") or {}
    prompt = int(usage.get("promptTokenCount") or 0)
    completion = int(usage.get("candidatesTokenCount") or 0) + int(usage.get("thoughtsTokenCount") or 0)
    cached = int(usage.get("cachedContentTokenCount") or 0)
    return prompt, completion, cached


# ==================== Provider catalog ====================
# Built-in provider registry. The catalog only describes
# available items, not installed ones. New instances only install LM Studio;
# other Providers enter config.providers after explicit user or AI addition.
PROVIDER_CATALOG: dict = {
    "lmstudio": {
        "name": "LM Studio (Local)",
        "category": "local",
        "api_type": "openai_compatible",
        "base_url": "http://localhost:1234/v1",
        "api_key_env": "",
        "embedding_model": "Qwen/Qwen3-Embedding-0.6B-GGUF",
        "chat_model": "google/gemma-4-26b-a4b",
        "vlm_model": "zai-org/glm-4.6v-flash",
    },
    "ollama": {
        "name": "Ollama (Local)",
        "category": "local",
        "api_type": "openai_compatible",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "",
        "embedding_model": "nomic-embed-text",
        "chat_model": "llama3.2",
    },
    "moonshot": {
        "name": "Moonshot AI (Kimi)",
        "category": "china",
        "api_type": "openai_compatible",
        "base_url": "https://api.moonshot.cn/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "embedding_model": "",
        "chat_model": "kimi-k2.6",
    },
    "kimi_coding": {
        "name": "Kimi Code Plan",
        "category": "coding",
        "api_type": "openai_compatible",
        "base_url": "https://api.kimi.com/coding/v1",
        "api_key_env": "KIMI_CODING_API_KEY",
        "embedding_model": "",
        "chat_model": "kimi-for-coding",
    },
    "aliyun_coding": {
        "name": "Alibaba Cloud Coding Plan",
        "category": "coding",
        "api_type": "openai_compatible",
        "base_url": "https://coding.dashscope.aliyuncs.com/v1",
        "api_key_env": "ALIYUN_CODING_API_KEY",
        "embedding_model": "",
        "chat_model": "",
    },
    "dashscope": {
        "name": "Alibaba Cloud Model Studio",
        "category": "china",
        "api_type": "openai_compatible",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key_env": "DASHSCOPE_API_KEY",
        "embedding_model": "text-embedding-v3",
        "chat_model": "qwen-plus",
    },
    "xiaomi": {
        "name": "Xiaomi MiMo",
        "category": "china",
        "api_type": "openai_compatible",
        "base_url": "https://api.xiaomimimo.com/v1",
        "api_key_env": "XIAOMI_API_KEY",
        "embedding_model": "",
        "chat_model": "mimo-v2-omni",
    },
    "xiaomi_coding": {
        "name": "Xiaomi MiMo Token Plan",
        "category": "coding",
        "api_type": "openai_compatible",
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "api_key_env": "XIAOMI_CODING_API_KEY",
        "embedding_model": "",
        "chat_model": "mimo-v2-pro",
    },
    "deepseek": {
        "name": "DeepSeek",
        "category": "china",
        "api_type": "openai_compatible",
        "base_url": "https://api.deepseek.com",
        "api_key_env": "DEEPSEEK_API_KEY",
        "embedding_model": "",
        "chat_model": "deepseek-chat",
    },
    "zhipu": {
        "name": "Zhipu AI",
        "category": "china",
        "api_type": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "api_key_env": "ZHIPU_API_KEY",
        "embedding_model": "",
        "chat_model": "glm-4.5",
    },
    "zhipu_coding": {
        "name": "Zhipu GLM Coding Plan",
        "category": "coding",
        "api_type": "openai_compatible",
        "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        "api_key_env": "ZHIPU_CODING_API_KEY",
        "embedding_model": "",
        "chat_model": "",
    },
    "minimax": {
        "name": "MiniMax",
        "category": "china",
        "api_type": "openai_compatible",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key_env": "MINIMAX_API_KEY",
        "embedding_model": "",
        "chat_model": "MiniMax-M2.5",
    },
    "minimax_coding": {
        "name": "MiniMax Token Plan",
        "category": "coding",
        "api_type": "anthropic",
        "base_url": "https://api.minimaxi.com/anthropic/v1",
        "api_key_env": "MINIMAX_CODING_API_KEY",
        "embedding_model": "",
        "chat_model": "MiniMax-M2.5",
    },
    "volces": {
        "name": "Volcengine Ark",
        "category": "china",
        "api_type": "openai_compatible",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api_key_env": "VOLCES_API_KEY",
        "embedding_model": "doubao-embedding-text-240715",
        "chat_model": "doubao-seed-2-0-pro-260215",
    },
    "volces_coding": {
        "name": "Volcengine Coding Plan",
        "category": "coding",
        "api_type": "openai_compatible",
        "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
        "api_key_env": "VOLCES_CODING_API_KEY",
        "embedding_model": "",
        "chat_model": "",
    },
    "anthropic": {
        "name": "Anthropic Claude",
        "category": "global",
        "api_type": "anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "api_key_env": "ANTHROPIC_API_KEY",
        "embedding_model": "",
        "chat_model": "claude-sonnet-4-6",
    },
    "openai": {
        "name": "OpenAI",
        "category": "global",
        "api_type": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "embedding_model": "text-embedding-3-small",
        "chat_model": "gpt-5.5",
    },
    "xai": {
        "name": "xAI (Grok)",
        "category": "global",
        "api_type": "openai_compatible",
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "embedding_model": "",
        "chat_model": "grok-4",
    },
    "google": {
        "name": "Google Gemini",
        "category": "global",
        "api_type": "gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key_env": "GOOGLE_API_KEY",
        "embedding_model": "models/gemini-embedding-001",
        "chat_model": "models/gemini-2.5-pro",
    },
    "openrouter": {
        "name": "OpenRouter",
        "category": "platform",
        "api_type": "openai_compatible",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "embedding_model": "google/gemini-embedding-001",
        "chat_model": "openai/gpt-4.1",
    },
    "pipellm_claude": {
        "name": "Claude (PipeLLM)",
        "category": "platform",
        "api_type": "openai_compatible",
        "base_url": "https://api.pipellm.ai/openai/v1",
        "api_key_env": "PIPELLM_API_KEY",
        "embedding_model": "",
        "chat_model": "claude-sonnet-4-6",
    },
    "siliconflow": {
        "name": "SiliconFlow",
        "category": "platform",
        "api_type": "openai_compatible",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key_env": "SILICONFLOW_API_KEY",
        "embedding_model": "",
        "chat_model": "Qwen/Qwen3-235B-A22B",
    },
    "tokendance": {
        "name": "TokenDance",
        "category": "platform",
        "api_type": "openai_compatible",
        "base_url": "https://tokendance.space/gateway/v1",
        "api_key_env": "TOKENDANCE_API_KEY",
        "embedding_model": "qwen-text-embedding-v4",
        "chat_model": "glm-5.1",
    },
    "asterove": {
        "name": "Asterove",
        "category": "platform",
        "api_type": "openai_compatible",
        "base_url": "https://asterove.com/api/v1",
        "api_key_env": "ASTEROVE_API_KEY",
        "embedding_model": "asterove/standard",
        "chat_model": "asterove/standard",
    },
}

DEFAULT_PROVIDER_IDS = ("lmstudio",)
DEFAULT_PROVIDERS = {provider_id: dict(PROVIDER_CATALOG[provider_id]) for provider_id in DEFAULT_PROVIDER_IDS}


def normalize_config(config: dict) -> bool:
    """
    Background: legacy users' config.yaml still uses the old schema (model.mode + model.local/bailian/...);
    reading new fields directly would yield empty values, silently disabling semantic search.
    Design intent: migrate in-place to the new schema (providers + active) at startup, mapping old modes
    to active providers for seamless upgrades; migration is idempotent, repeated calls have no effect.
    Key constraint: only modifies the dict in memory; whether to write back to file is decided by
    the caller (server.py), which is also responsible for backups.
    Return value: True means migration occurred (caller should write config file).
    """
    changed = False
    if "providers" not in config:
        config["providers"] = {k: dict(v) for k, v in DEFAULT_PROVIDERS.items()}
        changed = True

        old_model = config.get("model", {}) or {}
        old_local = old_model.get("local", {}) or {}
        # Legacy local section allowed custom base_url and model names; preserve user modifications during migration
        if old_local:
            lp = config["providers"]["lmstudio"]
            lp["base_url"] = old_local.get("base_url", lp["base_url"])
            lp["embedding_model"] = old_local.get("embedding_model", lp["embedding_model"])
            lp["chat_model"] = old_local.get("chat_model", lp["chat_model"])
            lp["vlm_model"] = old_local.get("vlm_model", lp.get("vlm_model", ""))

    if "active" not in config:
        mode = (config.get("model", {}) or {}).get("mode", "lmstudio")
        mode_map = {
            "local": "lmstudio",
            "bailian": "dashscope",
            "openrouter": "openrouter",
            "googleai": "google",
        }
        active_id = mode_map.get(mode, mode if mode in PROVIDER_CATALOG else "lmstudio")
        if active_id not in config["providers"] and active_id in PROVIDER_CATALOG:
            config["providers"][active_id] = dict(PROVIDER_CATALOG[active_id])
        config["active"] = {"embedding_provider": active_id, "chat_provider": active_id}
        changed = True

    providers = config.get("providers", {})
    active = config.get("active", {})

    # Unify legacy IDs with the catalog; user-customized models and URLs are preserved.
    for old_id, new_id in {"bailian": "dashscope", "googleai": "google"}.items():
        if old_id not in providers:
            continue
        if new_id not in providers:
            migrated = dict(PROVIDER_CATALOG[new_id])
            migrated.update(providers[old_id])
            migrated["name"] = PROVIDER_CATALOG[new_id]["name"]
            migrated["api_key_env"] = PROVIDER_CATALOG[new_id]["api_key_env"]
            providers[new_id] = migrated
        del providers[old_id]
        for key in ("embedding_provider", "chat_provider"):
            if active.get(key) == old_id:
                active[key] = new_id
        changed = True

    # v2 separates the provider catalog from added items. Legacy auto-expanded unused cards
    # are migrated only once; active, keyed, and customized Providers are all preserved.
    if config.get("provider_catalog_version", 1) < 2:
        active_ids = {active.get("embedding_provider"), active.get("chat_provider")}
        legacy_auto_ids = {"lmstudio", "dashscope", "openrouter", "google", "asterove"}
        for provider_id in list(providers):
            entry = providers[provider_id]
            has_key = bool(resolve_api_key(entry))
            catalog_entry = PROVIDER_CATALOG.get(provider_id, {})
            customized = any(
                entry.get(field) != catalog_entry.get(field)
                for field in ("base_url", "embedding_model", "chat_model", "vlm_model")
            )
            if (provider_id in legacy_auto_ids and provider_id not in active_ids
                    and not has_key and not customized):
                del providers[provider_id]
                changed = True
        config["provider_catalog_version"] = 2
        changed = True

    # v3 switches to global-user-perspective catalog ordering; added items only sync category
    # and legacy display names, without changing URLs, models, or Keys.
    if config.get("provider_catalog_version", 1) < 3:
        legacy_names = {
            "moonshot": {"Kimi (Moonshot AI)"},
            "dashscope": {"Alibaba Bailian"},
        }
        for provider_id, entry in providers.items():
            catalog_entry = PROVIDER_CATALOG.get(provider_id)
            if not catalog_entry:
                continue
            entry["category"] = catalog_entry.get("category", "platform")
            if not entry.get("name") or entry.get("name") in legacy_names.get(provider_id, set()):
                entry["name"] = catalog_entry["name"]
        config["provider_catalog_version"] = 3
        changed = True

    return changed


def resolve_api_key(entry: dict) -> str:
    """
    Background: Provider entries only store environment variable names; actual Keys are resolved at runtime.
    Design intent: centralize resolution logic; if keychain or other sources are needed in the future,
    only this function needs modification.
    Key constraint: empty env name (e.g., local LM Studio) returns empty string;
    caller treats it as "no authentication required".
    """
    env_name = (entry.get("api_key_env") or "").strip()
    if not env_name:
        return ""
    return os.environ.get(env_name, "") or ""


# ==================== Embedding protocol adapters ====================

class EmbeddingModel:
    """Embedding model base class: vector.py and others depend on this interface for type constraints"""

    def embed(self, text: str) -> List[float]:
        raise NotImplementedError

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class OpenAICompatibleEmbedding(EmbeddingModel):
    """
    Background: LM Studio / Bailian compatible-mode / OpenRouter / Asterove embeddings endpoints
    are fully isomorphic (POST {base}/embeddings, Bearer auth).
    Design intent: one class covers all OpenAI-compatible Providers; only base_url / key / model name differ.
    Key constraint: batch splits at 20 items (Bailian's single-batch limit is 25; conservative value
    for compatibility with all upstreams).
    """

    BATCH_SIZE = 20

    def __init__(self, base_url: str, model: str, api_key: str = "", provider_name: str = "provider"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.provider_name = provider_name
        self.provider_id = ""  # Injected by factory for usage attribution and local free-tier detection

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _record(self, prompt_tokens: int, duration_ms: int, caller: str,
                estimated: bool = False, status: str = "success", error: str = None) -> None:
        record_usage(caller=caller, kind="embedding", model=self.model,
                     provider=self.provider_id, provider_name=self.provider_name,
                     prompt_tokens=prompt_tokens, estimated=estimated,
                     duration_ms=duration_ms, status=status, error=error)

    def embed(self, text: str, caller: str = "embedding") -> List[float]:
        start = time.time()
        try:
            with httpx.Client(timeout=_EMBEDDING_TIMEOUT, limits=_POOL_LIMITS) as client:
                resp = client.post(
                    f"{self.base_url}/embeddings",
                    headers=self._headers(),
                    json={"model": self.model, "input": text},
                )
                resp.raise_for_status()
                data = resp.json()
                prompt_tokens, _, _ = _parse_openai_usage(data)
                estimated = prompt_tokens == 0
                if estimated:
                    prompt_tokens = estimate_tokens(text)
                self._record(prompt_tokens, int((time.time() - start) * 1000), caller,
                             estimated=estimated)
                return data["data"][0]["embedding"]
        except Exception as e:
            self._record(0, int((time.time() - start) * 1000), caller,
                         status="error", error=str(e))
            raise ConnectionError(f"{self.provider_name} embedding request failed: {e}")

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        results: List[List[float]] = []
        try:
            with httpx.Client(timeout=_EMBEDDING_TIMEOUT, limits=_POOL_LIMITS) as client:
                for i in range(0, len(texts), self.BATCH_SIZE):
                    batch = texts[i:i + self.BATCH_SIZE]
                    start = time.time()
                    try:
                        resp = client.post(
                            f"{self.base_url}/embeddings",
                            headers=self._headers(),
                            json={"model": self.model, "input": batch},
                        )
                        resp.raise_for_status()
                    except Exception as e:
                        self._record(0, int((time.time() - start) * 1000), "embedding",
                                     status="error", error=str(e))
                        raise
                    data = resp.json()
                    prompt_tokens, _, _ = _parse_openai_usage(data)
                    estimated = prompt_tokens == 0
                    if estimated:
                        prompt_tokens = sum(estimate_tokens(t) for t in batch)
                    self._record(prompt_tokens, int((time.time() - start) * 1000), "embedding",
                                 estimated=estimated)
                    items = sorted(data["data"], key=lambda x: x["index"])
                    results.extend([it["embedding"] for it in items])
            return results
        except Exception as e:
            raise ConnectionError(f"{self.provider_name} batch embedding failed: {e}")

    def is_available(self, max_retries: int = 3) -> bool:
        """
        Background: local LM Studio commonly returns 502 during cold start; startup probing needs
        to tolerate brief unavailability.
        Design intent: lightweight GET /models probe + limited retries; online Providers without
        a Key are immediately marked unavailable to avoid sending meaningless requests upstream.
        """
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=_EMBEDDING_TIMEOUT, limits=_POOL_LIMITS) as client:
                    resp = client.get(f"{self.base_url}/models", headers=self._headers(), timeout=5.0)
                    if resp.status_code == 200:
                        return True
                    if resp.status_code in (502, 503, 504) and attempt < max_retries - 1:
                        time.sleep(2)
                        continue
                    return False
            except Exception:
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return False
        return False

    def test_connection(self) -> tuple[bool, str, int]:
        """Send a real embedding request to verify connectivity; returns (success, message, vector dimension)"""
        try:
            vec = self.embed("connection test", caller="test")
            return True, f"{self.provider_name} connected", len(vec)
        except Exception as e:
            return False, _classify_error(str(e)), 0


class GeminiEmbedding(EmbeddingModel):
    """
    Background: Google AI native API differs from OpenAI protocol (:embedContent, x-goog key param).
    Design intent: the only non-OpenAI protocol adapter; Google native has no batch API, calls are sequential.
    """

    def __init__(self, base_url: str, model: str, api_key: str, provider_name: str = "Google AI"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.provider_name = provider_name
        self.provider_id = ""

    def embed(self, text: str, caller: str = "embedding") -> List[float]:
        if not self.api_key:
            raise ValueError(f"{self.provider_name}: API key not configured")
        start = time.time()
        try:
            with httpx.Client(timeout=_EMBEDDING_TIMEOUT, limits=_POOL_LIMITS) as client:
                resp = client.post(
                    f"{self.base_url}/{self.model}:embedContent",
                    headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                    json={"model": self.model, "content": {"parts": [{"text": text}]}},
                )
                resp.raise_for_status()
                # Gemini embedContent doesn't return usage; estimate by character count with estimated flag
                record_usage(caller=caller, kind="embedding", model=self.model,
                             provider=self.provider_id, provider_name=self.provider_name,
                             prompt_tokens=estimate_tokens(text), estimated=True,
                             duration_ms=int((time.time() - start) * 1000))
                return resp.json()["embedding"]["values"]
        except Exception as e:
            record_usage(caller=caller, kind="embedding", model=self.model,
                         provider=self.provider_id, provider_name=self.provider_name,
                         duration_ms=int((time.time() - start) * 1000),
                         status="error", error=str(e))
            raise ConnectionError(f"{self.provider_name} embedding request failed: {e}")

    def is_available(self) -> bool:
        return bool(self.api_key)

    def test_connection(self) -> tuple[bool, str, int]:
        if not self.api_key:
            return False, "API key not configured", 0
        try:
            vec = self.embed("connection test", caller="test")
            return True, f"{self.provider_name} connected", len(vec)
        except Exception as e:
            return False, _classify_error(str(e)), 0


# ==================== Chat protocol adapters ====================

class OpenAICompatibleChat:
    """
    Background: tagging / smart chunking / AI exploration all need a lightweight chat channel.
    Design intent: covers all OpenAI-compatible upstreams; LM Studio without Key omits Authorization header.
    Key constraint: chunker / task_queue / api.py use .chat / .generate_raw / .generate_tags
    via duck typing; signatures must not change.
    """

    def __init__(self, base_url: str, model: str, api_key: str = "", provider_name: str = "provider"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.provider_name = provider_name
        self.provider_id = ""   # Injected by factory
        self.caller = "chat"    # Instance-level default context; method-level caller kwarg can override

    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _record(self, usage: tuple[int, int, int], duration_ms: int, caller: Optional[str],
                status: str = "success", error: str = None) -> None:
        prompt_tokens, completion_tokens, cached_tokens = usage
        record_usage(caller=caller or self.caller, kind="chat", model=self.model,
                     provider=self.provider_id, provider_name=self.provider_name,
                     prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                     cached_tokens=cached_tokens, duration_ms=duration_ms,
                     status=status, error=error)

    def chat(self, messages: List[dict], temperature: float = 0.3, max_retries: int = 3,
             max_tokens: Optional[int] = None, caller: Optional[str] = None) -> str:
        # Don't hardcode output limit: omit max_tokens by default, let server use model's max; callers can explicitly limit
        payload = {"model": self.model, "messages": messages, "temperature": temperature}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        last_error = None
        start = time.time()
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=_CHAT_TIMEOUT, limits=_POOL_LIMITS) as client:
                    resp = client.post(
                        f"{self.base_url}/chat/completions",
                        headers=self._headers(),
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    self._record(_parse_openai_usage(data), int((time.time() - start) * 1000), caller)
                    return data["choices"][0]["message"]["content"]
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 2
                    print(f"[providers] {self.provider_name} chat failed, retrying in {wait}s ({attempt + 1}/{max_retries}): {e}")
                    time.sleep(wait)
        self._record((0, 0, 0), int((time.time() - start) * 1000), caller,
                     status="error", error=str(last_error))
        raise ConnectionError(f"{self.provider_name} chat connection failed (retried {max_retries} times): {last_error}")

    def generate_raw(self, prompt: str, max_tokens: int = 1000, temperature: float = 0.3,
                     caller: Optional[str] = None) -> str:
        start = time.time()
        try:
            with httpx.Client(timeout=_CHAT_TIMEOUT, limits=_POOL_LIMITS) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._headers(),
                    json={"model": self.model,
                          "messages": [{"role": "user", "content": prompt}],
                          "temperature": temperature, "max_tokens": max_tokens},
                )
                resp.raise_for_status()
                data = resp.json()
                self._record(_parse_openai_usage(data), int((time.time() - start) * 1000), caller)
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            self._record((0, 0, 0), int((time.time() - start) * 1000), caller,
                         status="error", error=str(e))
            print(f"[providers] {self.provider_name} generate_raw failed: {e}")
            return ""

    def is_available(self) -> bool:
        # Online Providers check Key; local (no key_env) tries probing /models
        if self.api_key:
            return True
        try:
            with httpx.Client(timeout=_EMBEDDING_TIMEOUT, limits=_POOL_LIMITS) as client:
                return client.get(f"{self.base_url}/models", timeout=5.0).status_code == 200
        except Exception:
            return False

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.chat([{"role": "user", "content": "Hi"}], temperature=0, max_retries=1, caller="test")
            return True, f"{self.provider_name} chat connected"
        except Exception as e:
            return False, _classify_error(str(e))

    def generate_tags(self, title: str, content: str, existing_tags: List[str] = None,
                      tag_tree: List[str] = None, similar_tags: List[str] = None) -> List[str]:
        return _generate_tags_via_chat(self, title, content, existing_tags, tag_tree, similar_tags)


class AnthropicChat:
    """Anthropic Messages API adapter, for Claude and Anthropic-compatible endpoints."""

    def __init__(self, base_url: str, model: str, api_key: str, provider_name: str = "Anthropic"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.provider_name = provider_name
        self.provider_id = ""
        self.caller = "chat"

    def _record(self, usage: tuple[int, int, int], duration_ms: int, caller: Optional[str],
                status: str = "success", error: str = None) -> None:
        prompt_tokens, completion_tokens, cached_tokens = usage
        record_usage(caller=caller or self.caller, kind="chat", model=self.model,
                     provider=self.provider_id, provider_name=self.provider_name,
                     prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                     cached_tokens=cached_tokens, duration_ms=duration_ms,
                     status=status, error=error)

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
        }

    @staticmethod
    def _payload(messages: List[dict], model: str, temperature: float, max_tokens: int) -> dict:
        system_parts = []
        converted = []
        for message in messages:
            role = message.get("role", "user")
            content = message.get("content", "")
            if role == "system":
                system_parts.append(content)
            else:
                converted.append({
                    "role": "assistant" if role == "assistant" else "user",
                    "content": content,
                })
        payload = {
            "model": model,
            "messages": converted,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        return payload

    def chat(self, messages: List[dict], temperature: float = 0.3, max_retries: int = 3,
             max_tokens: Optional[int] = None, caller: Optional[str] = None) -> str:
        # Anthropic API requires max_tokens field, cannot be omitted; defaults to 64000
        if not self.api_key:
            raise ValueError(f"{self.provider_name}: API key not configured")
        last_error = None
        start = time.time()
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=_CHAT_TIMEOUT, limits=_POOL_LIMITS) as client:
                    resp = client.post(
                        f"{self.base_url}/messages",
                        headers=self._headers(),
                        json=self._payload(messages, self.model, temperature,
                                           max_tokens or 64000),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    self._record(_parse_anthropic_usage(data), int((time.time() - start) * 1000), caller)
                    return "".join(
                        part.get("text", "")
                        for part in data.get("content", [])
                        if part.get("type") == "text"
                    )
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 2)
        self._record((0, 0, 0), int((time.time() - start) * 1000), caller,
                     status="error", error=str(last_error))
        raise ConnectionError(f"{self.provider_name} chat connection failed: {last_error}")

    def generate_raw(self, prompt: str, max_tokens: int = 1000, temperature: float = 0.3,
                     caller: Optional[str] = None) -> str:
        start = time.time()
        try:
            if not self.api_key:
                raise ValueError(f"{self.provider_name}: API key not configured")
            with httpx.Client(timeout=_CHAT_TIMEOUT, limits=_POOL_LIMITS) as client:
                resp = client.post(
                    f"{self.base_url}/messages",
                    headers=self._headers(),
                    json=self._payload([{"role": "user", "content": prompt}],
                                       self.model, temperature, max_tokens),
                )
                resp.raise_for_status()
                data = resp.json()
                self._record(_parse_anthropic_usage(data), int((time.time() - start) * 1000), caller)
                return "".join(
                    part.get("text", "")
                    for part in data.get("content", [])
                    if part.get("type") == "text"
                )
        except Exception as e:
            self._record((0, 0, 0), int((time.time() - start) * 1000), caller,
                         status="error", error=str(e))
            print(f"[providers] {self.provider_name} generate_raw failed: {e}")
            return ""

    def is_available(self) -> bool:
        return bool(self.api_key)

    def test_connection(self) -> tuple[bool, str]:
        try:
            self.chat([{"role": "user", "content": "Hi"}], temperature=0, max_retries=1, caller="test")
            return True, f"{self.provider_name} chat connected"
        except Exception as e:
            return False, _classify_error(str(e))

    def generate_tags(self, title: str, content: str, existing_tags: List[str] = None,
                      tag_tree: List[str] = None, similar_tags: List[str] = None) -> List[str]:
        return _generate_tags_via_chat(self, title, content, existing_tags, tag_tree, similar_tags)


class GeminiChat:
    """Google AI native chat adapter: converts OpenAI-format messages to Gemini contents"""

    def __init__(self, base_url: str, model: str, api_key: str, provider_name: str = "Google AI"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.provider_name = provider_name
        self.provider_id = ""
        self.caller = "chat"

    def _record(self, usage: tuple[int, int, int], duration_ms: int, caller: Optional[str],
                status: str = "success", error: str = None) -> None:
        prompt_tokens, completion_tokens, cached_tokens = usage
        record_usage(caller=caller or self.caller, kind="chat", model=self.model,
                     provider=self.provider_id, provider_name=self.provider_name,
                     prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                     cached_tokens=cached_tokens, duration_ms=duration_ms,
                     status=status, error=error)

    def chat(self, messages: List[dict], temperature: float = 0.3, max_retries: int = 3,
             max_tokens: Optional[int] = None, caller: Optional[str] = None) -> str:
        if not self.api_key:
            raise ValueError(f"{self.provider_name}: API key not configured")

        # OpenAI messages -> Gemini contents; system is prepended to first user message (Gemini compatibility, no separate system role)
        contents = []
        system_text = None
        for msg in messages:
            role, content = msg.get("role", "user"), msg.get("content", "")
            if role == "system":
                system_text = content
            elif role == "assistant":
                contents.append({"role": "model", "parts": [{"text": content}]})
            else:
                contents.append({"role": "user", "parts": [{"text": content}]})
        if system_text and contents and contents[0]["role"] == "user":
            contents[0]["parts"][0]["text"] = f"{system_text}\n\n{contents[0]['parts'][0]['text']}"

        # Don't hardcode output limit: omit maxOutputTokens by default, let Gemini use model's max
        generation_config = {"temperature": temperature}
        if max_tokens is not None:
            generation_config["maxOutputTokens"] = max_tokens
        payload = {"contents": contents, "generationConfig": generation_config}

        last_error = None
        start = time.time()
        for attempt in range(max_retries):
            try:
                with httpx.Client(timeout=_CHAT_TIMEOUT, limits=_POOL_LIMITS) as client:
                    resp = client.post(
                        f"{self.base_url}/{self.model}:generateContent",
                        headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                        json=payload,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    self._record(_parse_gemini_usage(data), int((time.time() - start) * 1000), caller)
                    return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 2
                    print(f"[providers] {self.provider_name} chat failed, retrying in {wait}s ({attempt + 1}/{max_retries}): {e}")
                    time.sleep(wait)
        self._record((0, 0, 0), int((time.time() - start) * 1000), caller,
                     status="error", error=str(last_error))
        raise ConnectionError(f"{self.provider_name} chat connection failed (retried {max_retries} times): {last_error}")

    def generate_raw(self, prompt: str, max_tokens: int = 1000, temperature: float = 0.3,
                     caller: Optional[str] = None) -> str:
        start = time.time()
        try:
            with httpx.Client(timeout=_CHAT_TIMEOUT, limits=_POOL_LIMITS) as client:
                resp = client.post(
                    f"{self.base_url}/{self.model}:generateContent",
                    headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
                    json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                          "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}},
                )
                resp.raise_for_status()
                data = resp.json()
                self._record(_parse_gemini_usage(data), int((time.time() - start) * 1000), caller)
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            self._record((0, 0, 0), int((time.time() - start) * 1000), caller,
                         status="error", error=str(e))
            print(f"[providers] {self.provider_name} generate_raw failed: {e}")
            return ""

    def is_available(self) -> bool:
        return bool(self.api_key)

    def test_connection(self) -> tuple[bool, str]:
        if not self.api_key:
            return False, "API key not configured"
        try:
            self.chat([{"role": "user", "content": "Hi"}], temperature=0, max_retries=1, caller="test")
            return True, f"{self.provider_name} chat connected"
        except Exception as e:
            return False, _classify_error(str(e))

    def generate_tags(self, title: str, content: str, existing_tags: List[str] = None,
                      tag_tree: List[str] = None, similar_tags: List[str] = None) -> List[str]:
        return _generate_tags_via_chat(self, title, content, existing_tags, tag_tree, similar_tags)


# ==================== Shared logic ====================

def _classify_error(error_msg: str) -> str:
    """
    Background: upstream error messages are unfriendly to users (long stack traces, raw HTTP semantics).
    Design intent: centrally categorize common error codes, replacing the old three-copy if/elif;
    unrecognized errors preserve the original message for debugging.
    """
    if "401" in error_msg or "Unauthorized" in error_msg or "API_KEY_INVALID" in error_msg:
        return "API key invalid or expired"
    if "402" in error_msg:
        return "Insufficient account balance"
    if "403" in error_msg or "Forbidden" in error_msg:
        return "API key lacks permission"
    if "404" in error_msg:
        return "Endpoint or model not found"
    if "429" in error_msg:
        return "Rate limited, try again later"
    if "timeout" in error_msg.lower() or "timed out" in error_msg.lower():
        return "Connection timeout"
    return f"Connection failed: {error_msg[:300]}"


def _build_tag_hierarchy(tags: List[str]) -> str:
    """Render a/b/c-format tag list as an indented tree for display in the tagging prompt"""
    tree: dict = {}
    for tag in tags:
        current = tree
        for part in (p.strip() for p in tag.split("/") if p.strip()):
            current = current.setdefault(part, {})

    lines: List[str] = []

    def walk(node: dict, level: int):
        for key, sub in sorted(node.items()):
            prefix = "  " * level + ("├─ " if level > 0 else "")
            lines.append(f"{prefix}{key}")
            if sub:
                walk(sub, level + 1)

    walk(tree, 0)
    return "\n".join(lines) if lines else "(no existing tags)"


def _generate_tags_via_chat(chat_model, title: str, content: str, existing_tags: List[str] = None,
                            tag_tree: List[str] = None, similar_tags: List[str] = None) -> List[str]:
    """
    Background: the old version duplicated prompt assembly + result parsing across three Chat classes;
    changing the prompt required edits in three places.
    Design intent: consolidated into a single implementation; any adapter with .chat() can use it.
    Key constraint: returns at most 4 tags in "level1/level2/level3" format; parse failures return
    an empty list without raising errors — tagging is an enhancement, not a blocker for the main flow.
    """
    content_preview = content[:2000]

    similar_hint = ""
    if similar_tags:
        tag_counts: dict = {}
        for tag in similar_tags:
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
        top = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)[:15]
        joined = ", ".join(f"{t}({c}x)" for t, c in top)
        similar_hint = (
            f"\n[⭐ Tags from Similar Articles (Primary Reference)]\n{joined}\n\n"
            "Please prioritize selecting appropriate tags from the similar articles above!"
        )

    tag_tree_hint = ""
    if tag_tree:
        tag_tree_hint = (
            f"\n[System's Existing Tag Hierarchy]\n{_build_tag_hierarchy(tag_tree[:50])}\n\n"
            "If similar article tags are not applicable, select from the hierarchy above or extend under existing categories."
        )

    existing_hint = ""
    if existing_tags:
        existing_hint = f"\n[Current Content's Existing Tags]: {', '.join(existing_tags)}\nPlease avoid duplicates."

    prompt = f"""Please generate 2-4 hierarchical classification tags for the following content, for knowledge categorization and retrieval.

[Title] {title}

[Content]
{content_preview}
{similar_hint}
{tag_tree_hint}
{existing_hint}

[Tag Format Requirements]
1. Use hierarchical format: level1/level2/level3 (up to three levels)
2. Examples: technology/programming/Python, lifestyle/health/exercise
3. Separate each level with /
4. ⭐ Highest priority: reuse tags from similar articles; then select from existing hierarchy; create new tags only when none fit
5. Only output tags, separated by commas, no other content

[Tags]"""

    try:
        # Tagging always attributes to the "tagging" context, regardless of the host adapter's default caller
        response = chat_model.chat([{"role": "user", "content": prompt}], caller="tagging").strip()
        if response.startswith(("标签:", "标签：", "【标签】")):
            response = response.split("】")[-1] if "】" in response else response[3:]

        tags: List[str] = []
        for tag in re.split(r"[,，、\n]+", response):
            tag = tag.strip().strip("\"'").strip("`")
            if tag and len(tag) <= 30 and not tag.startswith("#"):
                tag = tag.replace("\\", "/").replace("->", "/").replace(">", "/")
                tag = "/".join(p.strip() for p in tag.split("/") if p.strip())
                if tag:
                    tags.append(tag)
        return tags[:4]
    except Exception as e:
        print(f"[providers] Auto-tagging failed: {e}")
        return []


# ==================== Registry factory ====================

def get_provider_entry(config: dict, provider_id: str) -> Optional[dict]:
    """Get Provider entry by id; returns None if not found, caller decides error handling"""
    return (config.get("providers") or {}).get(provider_id)


def _build_embedding(
    entry: dict,
    provider_id: str,
    api_key_override: Optional[str] = None,
) -> Optional[EmbeddingModel]:
    api_key = resolve_api_key(entry) if api_key_override is None else api_key_override
    name = entry.get("name", provider_id)
    embedding_model = entry.get("embedding_model") or ""
    if not embedding_model:
        return None
    if entry.get("api_type") == "gemini":
        model = GeminiEmbedding(entry["base_url"], embedding_model, api_key, name)
    else:
        model = OpenAICompatibleEmbedding(entry["base_url"], embedding_model, api_key, name)
    model.provider_id = provider_id
    return model


def _build_chat(entry: dict, provider_id: str, api_key_override: Optional[str] = None,
                caller: str = "chat"):
    api_key = resolve_api_key(entry) if api_key_override is None else api_key_override
    name = entry.get("name", provider_id)
    chat_model = entry.get("chat_model") or ""
    if not chat_model:
        return None
    if entry.get("api_type") == "gemini":
        model = GeminiChat(entry["base_url"], chat_model, api_key, name)
    elif entry.get("api_type") == "anthropic":
        model = AnthropicChat(entry["base_url"], chat_model, api_key, name)
    else:
        model = OpenAICompatibleChat(entry["base_url"], chat_model, api_key, name)
    model.provider_id = provider_id
    model.caller = caller
    return model


def get_embedding_model(config: dict) -> Optional[EmbeddingModel]:
    """
    Background: server / api / task_queue all use this factory to get the currently active embedding model.
    Design intent: active.embedding_provider is the sole switch; configuration errors return None
    with logging, allowing semantic search to gracefully degrade to keyword search instead of crashing on startup.
    """
    normalize_config(config)
    provider_id = (config.get("active") or {}).get("embedding_provider", "lmstudio")
    entry = get_provider_entry(config, provider_id)
    if not entry:
        print(f"[providers] embedding provider '{provider_id}' not found in registry")
        return None
    return _build_embedding(entry, provider_id)


def get_chat_model(config: dict, caller: str = "chat"):
    """
    Same as get_embedding_model; returns the currently active chat model; returns None when chat_model is not configured.
    caller is the default usage attribution context (chunking / profile / meta-extract, etc.);
    individual calls can override with method-level caller kwarg.
    """
    normalize_config(config)
    provider_id = (config.get("active") or {}).get("chat_provider", "lmstudio")
    entry = get_provider_entry(config, provider_id)
    if not entry:
        print(f"[providers] chat provider '{provider_id}' not found in registry")
        return None
    return _build_chat(entry, provider_id, caller=caller)


def test_provider(
    config: dict,
    provider_id: str,
    entry_override: Optional[dict] = None,
    api_key_override: Optional[str] = None,
) -> dict:
    """
    Background: the settings page needs a unified "test connection" entry point, replacing the
    old four test-connection-* endpoints.
    Design intent: tests both embedding (returns dimension) and chat (if chat_model is configured),
    giving the settings page a complete availability profile in one call.
    """
    entry = get_provider_entry(config, provider_id)
    if not entry:
        return {"success": False, "message": f"Provider '{provider_id}' not found"}
    if entry_override:
        entry = {**entry, **entry_override}

    result: dict = {"provider": provider_id, "success": False}
    emb = _build_embedding(entry, provider_id, api_key_override)
    if emb is not None:
        ok, msg, dim = emb.test_connection()
        result.update({"success": ok, "message": msg, "dimension": dim})

    chat = _build_chat(entry, provider_id, api_key_override)
    if chat is not None:
        chat_ok, chat_msg = chat.test_connection()
        result.update({"chat_success": chat_ok, "chat_message": chat_msg})
        if emb is None:
            result.update({"success": chat_ok, "message": chat_msg})
    if emb is None and chat is None:
        result["message"] = "No embedding or chat model configured"
    return result
