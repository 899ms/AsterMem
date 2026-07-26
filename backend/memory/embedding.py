"""
Embedding compatibility layer (implementation has been migrated to providers.py)

Background: this file used to hold separate Embedding/Chat classes for each upstream
provider (1000+ lines). They have since been consolidated into protocol adapters plus a
configuration registry in providers.py.
Design intent: Keep this module as a backward-compatible import entry point (vector.py /
chunker.py / task_queue.py / api.py all import from here), avoiding a mass update of all
call sites at once. New code should import directly from memory.providers.
Key constraint: Do not add new implementations to this file; all Provider logic must
reside in providers.py only.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from .providers import (  # noqa: F401
    EmbeddingModel,
    OpenAICompatibleEmbedding,
    GeminiEmbedding,
    OpenAICompatibleChat,
    AnthropicChat,
    GeminiChat,
    get_embedding_model,
    get_chat_model,
    test_provider,
    normalize_config,
    resolve_api_key,
    DEFAULT_PROVIDERS,
    PROVIDER_CATALOG,
)
