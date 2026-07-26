# -*- coding: utf-8 -*-
"""
Model Pricing and Cost Calculation

Background: Users want to see "how much they spent" on the usage page, but AsterMem
supports 20+ providers whose prices cannot all be known in advance, and local models
(LM Studio / Ollama) are free.
Design intent: a two-layer pricing model — a built-in pricing table (manually maintained
from official pricing pages) covers common models; users can override or supplement via config `pricing.overrides`;
costs are never persisted, calculated on-the-fly at query time using current prices.
Key constraints:
  - Lookup priority: user overrides → built-in table (exact → lowercase/dot-hyphen alias)
    → local provider records 0 → OpenRouter reference price (pricing_openrouter.py,
    auto-generated) → None
  - None means unpriced; UI only shows tokens and prompts user to set a price
  - Price unit is uniformly "per 1M tokens", currency CNY/USD; costs displayed in USD,
    CNY prices converted to USD using pricing.usd_to_cny exchange rate

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import re
from typing import Any, Dict, Optional

from .pricing_openrouter import OPENROUTER_PRICING

DEFAULT_USD_TO_CNY = 7.25

# Built-in pricing table: model name → {input, cached_input, output, currency}
# input/cached_input/output are prices per 1M tokens; cached_input is KV cache hit price,
# same as input when upstream doesn't distinguish. Data source: official provider pricing pages.
PRICING_TABLE: Dict[str, Dict[str, Any]] = {
    # ── Kimi (Moonshot AI) ──
    "kimi-k3": {"input": 21.00, "cached_input": 2.10, "output": 75.00, "currency": "CNY"},
    "kimi-k2.6": {"input": 6.50, "cached_input": 1.10, "output": 27.00, "currency": "CNY"},
    "kimi-k2.5": {"input": 4.00, "cached_input": 0.70, "output": 21.00, "currency": "CNY"},
    "kimi-for-coding": {"input": 6.50, "cached_input": 1.10, "output": 27.00, "currency": "CNY"},
    # ── Xiaomi MiMo ──
    "mimo-v2.5-pro": {"input": 7.00, "cached_input": 1.40, "output": 21.00, "currency": "CNY"},
    "mimo-v2-pro": {"input": 7.00, "cached_input": 1.40, "output": 21.00, "currency": "CNY"},
    "mimo-v2.5": {"input": 2.80, "cached_input": 0.56, "output": 14.00, "currency": "CNY"},
    "mimo-v2-omni": {"input": 2.80, "cached_input": 0.56, "output": 14.00, "currency": "CNY"},
    "mimo-v2-flash": {"input": 0.70, "cached_input": 0.07, "output": 2.10, "currency": "CNY"},
    # ── Anthropic Claude ──
    "claude-sonnet-4-6": {"input": 3.00, "cached_input": 0.30, "output": 15.00, "currency": "USD"},
    "claude-opus-4": {"input": 15.00, "cached_input": 1.50, "output": 75.00, "currency": "USD"},
    "claude-haiku-3-5": {"input": 0.80, "cached_input": 0.08, "output": 4.00, "currency": "USD"},
    # ── Alibaba Cloud DashScope ──
    "qwen3.7-max": {"input": 12.00, "cached_input": 2.40, "output": 36.00, "currency": "CNY"},
    "qwen3.6-max-preview": {"input": 9.00, "output": 54.00, "currency": "CNY"},
    "qwen3-max": {"input": 2.50, "output": 10.00, "currency": "CNY"},
    "qwen-max": {"input": 2.40, "output": 9.60, "currency": "CNY"},
    "qwen3.6-plus": {"input": 2.00, "cached_input": 0.40, "output": 12.00, "currency": "CNY"},
    "qwen3.6-flash": {"input": 1.20, "cached_input": 0.24, "output": 7.20, "currency": "CNY"},
    "qwen3.5-plus": {"input": 0.80, "output": 4.80, "currency": "CNY"},
    "qwen-plus": {"input": 0.80, "output": 2.00, "currency": "CNY"},
    "qwen3.5-flash": {"input": 0.20, "output": 2.00, "currency": "CNY"},
    "qwen-flash": {"input": 0.15, "output": 1.50, "currency": "CNY"},
    "qwen-turbo": {"input": 0.30, "output": 0.60, "currency": "CNY"},
    "qwen-long": {"input": 0.50, "output": 2.00, "currency": "CNY"},
    "qwen3-235b-a22b": {"input": 2.00, "output": 8.00, "currency": "CNY"},
    "qwen3-32b": {"input": 2.00, "output": 8.00, "currency": "CNY"},
    "qwen3-coder-plus": {"input": 4.00, "output": 16.00, "currency": "CNY"},
    "text-embedding-v3": {"input": 0.50, "output": 0.0, "currency": "CNY"},
    "text-embedding-v4": {"input": 0.50, "output": 0.0, "currency": "CNY"},
    "qwen-text-embedding-v4": {"input": 0.50, "output": 0.0, "currency": "CNY"},
    # ── DeepSeek Official ──
    "deepseek-v4-pro": {"input": 3.00, "cached_input": 0.025, "output": 6.00, "currency": "CNY"},
    "deepseek-v4-flash": {"input": 1.00, "cached_input": 0.20, "output": 2.00, "currency": "CNY"},
    "deepseek-chat": {"input": 1.00, "cached_input": 0.02, "output": 2.00, "currency": "CNY"},
    "deepseek-reasoner": {"input": 0.75, "cached_input": 0.025, "output": 6.00, "currency": "CNY"},
    "deepseek-v3": {"input": 2.00, "output": 8.00, "currency": "CNY"},
    "deepseek-r1": {"input": 4.00, "output": 16.00, "currency": "CNY"},
    # ── Zhipu GLM ──
    "glm-5.1": {"input": 6.00, "cached_input": 1.30, "output": 24.00, "currency": "CNY"},
    "glm-5-turbo": {"input": 5.00, "cached_input": 1.20, "output": 22.00, "currency": "CNY"},
    "glm-5": {"input": 4.00, "cached_input": 1.00, "output": 18.00, "currency": "CNY"},
    "glm-4.7": {"input": 2.00, "cached_input": 0.40, "output": 8.00, "currency": "CNY"},
    "glm-4.5": {"input": 2.00, "cached_input": 0.40, "output": 8.00, "currency": "CNY"},
    "glm-4.5-air": {"input": 0.80, "cached_input": 0.16, "output": 2.00, "currency": "CNY"},
    "glm-4.7-flash": {"input": 0.0, "output": 0.0, "currency": "CNY"},
    "glm-4-flash": {"input": 0.0, "output": 0.0, "currency": "CNY"},
    "embedding-3": {"input": 0.50, "output": 0.0, "currency": "CNY"},
    # ── MiniMax ──
    "minimax-m2.5": {"input": 2.00, "cached_input": 0.40, "output": 16.00, "currency": "CNY"},
    # ── Volcengine Doubao ──
    "doubao-seed-2-0-pro-260215": {"input": 4.00, "cached_input": 0.80, "output": 16.00, "currency": "CNY"},
    "doubao-embedding-text-240715": {"input": 0.50, "output": 0.0, "currency": "CNY"},
    # ── OpenAI ──
    "gpt-5.6-sol": {"input": 5.00, "cached_input": 0.50, "output": 30.00, "currency": "USD"},
    "gpt-5.5": {"input": 5.00, "cached_input": 0.50, "output": 30.00, "currency": "USD"},
    "gpt-4.1": {"input": 2.00, "cached_input": 0.50, "output": 8.00, "currency": "USD"},
    "gpt-4o": {"input": 2.50, "cached_input": 1.25, "output": 10.00, "currency": "USD"},
    "gpt-4o-mini": {"input": 0.15, "cached_input": 0.075, "output": 0.60, "currency": "USD"},
    "text-embedding-3-small": {"input": 0.02, "output": 0.0, "currency": "USD"},
    "text-embedding-3-large": {"input": 0.13, "output": 0.0, "currency": "USD"},
    # ── xAI ──
    "grok-4.5": {"input": 2.00, "cached_input": 0.30, "output": 6.00, "currency": "USD"},
    "grok-4": {"input": 3.00, "cached_input": 0.75, "output": 15.00, "currency": "USD"},
    # ── Google Gemini ──
    "gemini-2.5-pro": {"input": 1.25, "output": 10.00, "currency": "USD"},
    "gemini-2.5-flash": {"input": 0.30, "output": 2.50, "currency": "USD"},
    "gemini-embedding-001": {"input": 0.15, "output": 0.0, "currency": "USD"},
    # ── Asterove (self-hosted gateway) ──
    "bunny": {"input": 0.0, "output": 0.0, "currency": "CNY"},
    "auto": {"input": 3.25, "cached_input": 0.55, "output": 13.50, "currency": "CNY"},
    "asterove/standard": {"input": 6.50, "cached_input": 1.10, "output": 27.00, "currency": "CNY"},
    "premium": {"input": 13.00, "cached_input": 2.20, "output": 54.00, "currency": "CNY"},
    "asterove-test-1": {"input": 2.00, "cached_input": 0.50, "output": 15.00, "currency": "USD"},
}


def _alias_keys(model: str) -> list:
    """Equivalent forms of a model name: lowercase, strip provider prefix, swap dots/hyphens in version numbers"""
    keys = []
    for name in (model, model.split("/")[-1] if "/" in model else model):
        lower = name.lower()
        for k in (name, lower):
            if k not in keys:
                keys.append(k)
        dot_to_hyphen = re.sub(r"(\d+)\.(\d+)", r"\1-\2", lower)
        hyphen_to_dot = re.sub(r"(\d+)-(\d+)", r"\1.\2", lower)
        for k in (dot_to_hyphen, hyphen_to_dot):
            if k not in keys:
                keys.append(k)
    return keys


def _builtin_lookup(model: str) -> Optional[Dict[str, Any]]:
    lower_table = {k.lower(): v for k, v in PRICING_TABLE.items()}
    for key in _alias_keys(model):
        if key in PRICING_TABLE:
            return PRICING_TABLE[key]
        if key.lower() in lower_table:
            return lower_table[key.lower()]
    return None


def _openrouter_lookup(model: str) -> Optional[Dict[str, Any]]:
    """OpenRouter reference price (keys are all lowercase, including full id and unique suffix)."""
    for key in _alias_keys(model):
        entry = OPENROUTER_PRICING.get(key.lower())
        if entry is not None:
            return entry
    return None


def resolve_pricing(model: str, config: Optional[dict] = None,
                    provider_id: str = "") -> Optional[Dict[str, Any]]:
    """
    Resolve effective pricing for a model, with source field (override / builtin / local / openrouter).
    Returns None for unpriced models (UI only shows tokens).
    """
    if not model and not provider_id:
        return None
    config = config or {}

    overrides = ((config.get("pricing") or {}).get("overrides") or {})
    for key in _alias_keys(model or ""):
        if key in overrides and isinstance(overrides[key], dict):
            entry = overrides[key]
            return {
                "input": float(entry.get("input", 0) or 0),
                "cached_input": float(entry.get("cached_input", entry.get("input", 0)) or 0),
                "output": float(entry.get("output", 0) or 0),
                "currency": entry.get("currency", "USD"),
                "source": "override",
            }

    builtin = _builtin_lookup(model or "")
    if builtin is not None:
        return {
            "input": builtin["input"],
            "cached_input": builtin.get("cached_input", builtin["input"]),
            "output": builtin["output"],
            "currency": builtin.get("currency", "CNY"),
            "source": "builtin",
        }

    # Local providers (LM Studio / Ollama) are free. Check category in current config;
    # fall back to catalog lookup if the provider has been deleted.
    if provider_id:
        entry = ((config.get("providers") or {}).get(provider_id)) or {}
        category = entry.get("category")
        if category is None:
            from .providers import PROVIDER_CATALOG
            category = (PROVIDER_CATALOG.get(provider_id) or {}).get("category")
        if category == "local":
            return {"input": 0.0, "cached_input": 0.0, "output": 0.0,
                    "currency": "CNY", "source": "local"}

    openrouter = _openrouter_lookup(model or "")
    if openrouter is not None:
        return {
            "input": openrouter["input"],
            "cached_input": openrouter.get("cached_input", openrouter["input"]),
            "output": openrouter["output"],
            "currency": "USD",
            "source": "openrouter",
        }

    return None


def usd_rate(config: Optional[dict] = None) -> float:
    try:
        return float(((config or {}).get("pricing") or {}).get("usd_to_cny", DEFAULT_USD_TO_CNY))
    except (TypeError, ValueError):
        return DEFAULT_USD_TO_CNY


def calculate_cost_usd(prompt_tokens: int, completion_tokens: int, cached_tokens: int,
                       pricing: Optional[Dict[str, Any]], rate: float = DEFAULT_USD_TO_CNY) -> Optional[float]:
    """Calculate cost in USD using current pricing. CNY prices are converted via exchange rate; returns None if unpriced."""
    if pricing is None:
        return None
    cached = min(max(cached_tokens or 0, 0), max(prompt_tokens or 0, 0))
    uncached = max((prompt_tokens or 0) - cached, 0)
    cost = (
        cached * pricing.get("cached_input", pricing["input"])
        + uncached * pricing["input"]
        + (completion_tokens or 0) * pricing["output"]
    ) / 1_000_000
    if pricing.get("currency") == "CNY":
        cost /= rate
    return cost
