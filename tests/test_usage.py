"""
AI Usage Gateway (usage_tracker + pricing + /api/usage) Tests.

Background: All AI calls go through a unified gateway that records metadata (caller/model/
tokens/duration); cost is calculated by the pricing module at query time.
Design intent:
  - Tracker and pricing are pure unit tests (independent tmp db, no global singletons);
  - Adapter instrumentation uses fake httpx.Client to verify the "call succeeds → usage
    persisted" closed loop;
  - /api/usage goes through conftest's session-level TestClient for real HTTP semantics.
Key constraint: No test may make real network requests.
"""

import pytest

from memory.pricing import calculate_cost_usd, resolve_pricing, usd_rate
from memory.usage_tracker import UsageTracker, estimate_tokens
from memory.providers import (
    OpenAICompatibleChat,
    _parse_anthropic_usage,
    _parse_gemini_usage,
    _parse_openai_usage,
)


# ==================== Tracker ====================

@pytest.fixture
def tracker(tmp_path):
    return UsageTracker(str(tmp_path / "ai_usage.db"), max_records=10)


def test_tracker_record_and_query(tracker):
    tracker.record(caller="chat", kind="chat", model="deepseek-chat", provider="deepseek",
                   prompt_tokens=100, completion_tokens=20, cached_tokens=30, duration_ms=500)
    tracker.record(caller="embedding", kind="embedding", model="text-embedding-v3",
                   provider="dashscope", prompt_tokens=50)

    result = tracker.get_logs()
    assert result["total"] == 2
    latest = result["logs"][0]
    assert latest["caller"] == "embedding"
    assert latest["total_tokens"] == 50  # Auto-summed when total not explicitly provided

    chat_only = tracker.get_logs(caller="chat")
    assert chat_only["total"] == 1
    assert chat_only["logs"][0]["cached_tokens"] == 30


def test_tracker_error_row(tracker):
    tracker.record(caller="chat", kind="chat", model="m", status="error", error="x" * 500)
    row = tracker.get_logs(status="error")["logs"][0]
    assert row["status"] == "error"
    assert len(row["error"]) <= 300  # Error message truncated


def test_tracker_fifo(tracker):
    for i in range(15):
        tracker.record(caller="chat", kind="chat", model=f"m{i}", prompt_tokens=1)
    result = tracker.get_logs(limit=100)
    assert result["total"] == 10  # max_records=10
    assert result["logs"][0]["model"] == "m14"  # Most recent kept


def test_tracker_clear(tracker):
    tracker.record(caller="chat", kind="chat", model="m", prompt_tokens=1)
    tracker.clear()
    assert tracker.get_logs()["total"] == 0


def test_tracker_aggregate(tracker):
    tracker.record(caller="chat", kind="chat", model="a", provider="p",
                   prompt_tokens=100, completion_tokens=10)
    tracker.record(caller="chat", kind="chat", model="a", provider="p",
                   prompt_tokens=100, completion_tokens=10, status="error", error="boom")
    tracker.record(caller="tagging", kind="chat", model="a", provider="p",
                   prompt_tokens=50, completion_tokens=5)

    agg = tracker.aggregate()
    assert agg["totals"]["calls"] == 3
    assert agg["totals"]["errors"] == 1
    assert agg["totals"]["total_tokens"] == 275
    callers = {row["caller"] for row in agg["by_caller"]}
    assert callers == {"chat", "tagging"}


def test_estimate_tokens():
    assert estimate_tokens("") == 1
    assert estimate_tokens("a" * 400) == 100


# ==================== Pricing ====================

def test_pricing_builtin():
    p = resolve_pricing("deepseek-chat", {})
    assert p["source"] == "builtin" and p["currency"] == "CNY"
    # 1M input (incl. 500K cache hit) + 100K output: 0.5*1 + 0.5*0.02 + 0.1*2 = 0.71 CNY → USD
    cost = calculate_cost_usd(1_000_000, 100_000, 500_000, p, rate=7.25)
    assert cost == pytest.approx(0.71 / 7.25)


def test_pricing_alias_dot_hyphen():
    # Built-in table key is claude-sonnet-4-6; dot notation should also match
    p = resolve_pricing("Claude-Sonnet-4.6", {})
    assert p is not None and p["currency"] == "USD"


def test_pricing_usd_no_conversion():
    # USD-priced models do not go through exchange rate conversion
    p = resolve_pricing("gpt-5.5", {})
    assert p["currency"] == "USD"
    cost = calculate_cost_usd(1_000_000, 0, 0, p, rate=7.0)
    assert cost == pytest.approx(p["input"])


def test_pricing_override_wins():
    config = {"pricing": {"overrides": {"deepseek-chat": {"input": 100, "output": 200}}}}
    p = resolve_pricing("deepseek-chat", config)
    assert p["source"] == "override"
    assert calculate_cost_usd(1_000_000, 0, 0, p) == pytest.approx(100)


def test_pricing_local_free():
    config = {"providers": {"lmstudio": {"category": "local"}}}
    p = resolve_pricing("some-local-model", config, "lmstudio")
    assert p["source"] == "local"
    assert calculate_cost_usd(999, 999, 0, p) == 0


def test_pricing_unknown_returns_none():
    assert resolve_pricing("totally-unknown-model", {}) is None
    assert calculate_cost_usd(100, 100, 0, None) is None


def test_usd_rate_from_config():
    assert usd_rate({"pricing": {"usd_to_cny": 7.5}}) == 7.5
    assert usd_rate({}) == 7.25


# ==================== Usage Parsing ====================

def test_parse_openai_usage():
    data = {"usage": {"prompt_tokens": 100, "completion_tokens": 20,
                      "prompt_tokens_details": {"cached_tokens": 30}}}
    assert _parse_openai_usage(data) == (100, 20, 30)
    # DeepSeek-style cache field
    data2 = {"usage": {"prompt_tokens": 10, "completion_tokens": 2, "prompt_cache_hit_tokens": 4}}
    assert _parse_openai_usage(data2) == (10, 2, 4)
    assert _parse_openai_usage({}) == (0, 0, 0)


def test_parse_anthropic_usage():
    data = {"usage": {"input_tokens": 70, "output_tokens": 20,
                      "cache_read_input_tokens": 25, "cache_creation_input_tokens": 5}}
    # Unified metric: prompt = input + cache_read + cache_creation
    assert _parse_anthropic_usage(data) == (100, 20, 25)


def test_parse_gemini_usage():
    data = {"usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 8,
                              "thoughtsTokenCount": 2, "cachedContentTokenCount": 10}}
    assert _parse_gemini_usage(data) == (40, 10, 10)


# ==================== Adapter Instrumentation Closed Loop ====================

class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _FakeClient:
    """Stub httpx.Client: returns fixed chat/completions response"""

    payload: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return _FakeResponse(type(self).payload)


def test_chat_adapter_records_usage(tmp_path, monkeypatch):
    import memory.providers as providers_mod
    import memory.usage_tracker as tracker_mod

    saved = tracker_mod._usage_tracker
    try:
        tracker = tracker_mod.init_usage_tracker(str(tmp_path / "u.db"))
        _FakeClient.payload = {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 3},
        }
        monkeypatch.setattr(providers_mod.httpx, "Client", _FakeClient)

        chat = OpenAICompatibleChat("http://fake/v1", "test-model", provider_name="Fake")
        chat.provider_id = "fakeprov"
        chat.caller = "chunking"

        assert chat.chat([{"role": "user", "content": "hi"}]) == "hello"
        # Method-level caller overrides instance default
        assert chat.chat([{"role": "user", "content": "hi"}], caller="profile") == "hello"

        logs = tracker.get_logs(limit=10)["logs"]
        assert len(logs) == 2
        assert logs[1]["caller"] == "chunking" and logs[0]["caller"] == "profile"
        assert logs[0]["prompt_tokens"] == 12 and logs[0]["completion_tokens"] == 3
        assert logs[0]["provider"] == "fakeprov" and logs[0]["model"] == "test-model"
    finally:
        tracker_mod._usage_tracker = saved


def test_generate_tags_attributed_to_tagging(tmp_path, monkeypatch):
    import memory.providers as providers_mod
    import memory.usage_tracker as tracker_mod

    saved = tracker_mod._usage_tracker
    try:
        tracker = tracker_mod.init_usage_tracker(str(tmp_path / "u.db"))
        _FakeClient.payload = {
            "choices": [{"message": {"content": "tech/programming, life/health"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
        monkeypatch.setattr(providers_mod.httpx, "Client", _FakeClient)

        chat = OpenAICompatibleChat("http://fake/v1", "m", provider_name="Fake")
        chat.caller = "chunking"  # Even though host default is chunking
        tags = chat.generate_tags("title", "content")
        assert tags == ["tech/programming", "life/health"]

        row = tracker.get_logs()["logs"][0]
        assert row["caller"] == "tagging"  # Tag generation is always attributed to tagging
    finally:
        tracker_mod._usage_tracker = saved


def test_record_usage_noop_without_tracker(monkeypatch):
    """record_usage must not raise when tracker is uninitialized (some unit tests construct adapters directly)"""
    import memory.usage_tracker as tracker_mod

    monkeypatch.setattr(tracker_mod, "_usage_tracker", None)
    tracker_mod.record_usage(caller="chat", kind="chat", model="m")  # Should not raise


# ==================== /api/usage ====================

@pytest.fixture
def seeded_usage(app_bundle):
    """Write known data to the session app stack's usage db; clear after use to avoid polluting other tests"""
    _app, _config, _path, services = app_bundle
    tracker = services["usage_tracker"]
    tracker.clear()
    tracker.record(caller="chat", kind="chat", model="deepseek-chat", provider="deepseek",
                   provider_name="DeepSeek", prompt_tokens=1_000_000, completion_tokens=100_000,
                   duration_ms=800)
    tracker.record(caller="embedding", kind="embedding", model="mystery-embed", provider="someprov",
                   provider_name="Some", prompt_tokens=2_000, duration_ms=90)
    yield tracker
    tracker.clear()


def test_usage_requires_auth(anon_client):
    assert anon_client.get("/api/usage/summary").status_code == 401
    assert anon_client.get("/api/usage/logs").status_code == 401
    assert anon_client.delete("/api/usage/logs").status_code == 401
    assert anon_client.get("/api/usage/pricing").status_code == 401


def test_usage_summary(client, seeded_usage):
    resp = client.get("/api/usage/summary?days=0")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["totals"]["calls"] == 2
    assert body["totals"]["total_tokens"] == 1_102_000
    # deepseek-chat pricing: 1.0*1 + 0.1*2 = 1.2 CNY → USD; mystery-embed is unpriced
    assert body["totals"]["cost_usd"] == pytest.approx(1.2 / 7.25)
    assert body["totals"]["has_unpriced"] is True
    assert "mystery-embed" in body["unpriced_models"]

    callers = {row["caller"]: row for row in body["by_caller"]}
    assert callers["chat"]["cost_usd"] == pytest.approx(1.2 / 7.25)
    assert callers["embedding"]["unpriced"] is True

    kinds = {row["kind"] for row in body["by_kind"]}
    assert kinds == {"chat", "embedding"}
    assert len(body["by_day"]) == 1


def test_usage_logs_endpoint(client, seeded_usage):
    resp = client.get("/api/usage/logs?limit=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    newest = body["logs"][0]
    assert newest["caller"] == "embedding"
    assert newest["cost_usd"] is None  # Unpriced model has None per-row cost

    only_chat = client.get("/api/usage/logs?caller=chat").json()
    assert only_chat["total"] == 1
    assert only_chat["logs"][0]["cost_usd"] == pytest.approx(1.2 / 7.25)


def test_usage_pricing_override_flow(client, seeded_usage, app_bundle):
    _app, config, _path, _services = app_bundle

    # Add pricing for unpriced model (USD / 1M tokens)
    resp = client.put("/api/usage/pricing", json={"model": "mystery-embed", "input": 0.5, "output": 0})
    assert resp.status_code == 200, resp.text
    assert config["pricing"]["overrides"]["mystery-embed"]["input"] == 0.5

    pricing = client.get("/api/usage/pricing").json()
    row = next(m for m in pricing["models"] if m["model"] == "mystery-embed")
    assert row["pricing"]["source"] == "override"

    summary = client.get("/api/usage/summary?days=0").json()
    assert summary["totals"]["has_unpriced"] is False
    # 2000 tokens * 0.5 USD/1M = 0.001 USD (override pricing defaults to USD)
    assert summary["totals"]["cost_usd"] == pytest.approx(1.2 / 7.25 + 0.001)

    # Delete override pricing (both input/output empty)
    resp = client.put("/api/usage/pricing", json={"model": "mystery-embed"})
    assert resp.status_code == 200
    assert "mystery-embed" not in config["pricing"]["overrides"]


def test_usage_clear_endpoint(client, seeded_usage):
    assert client.delete("/api/usage/logs").status_code == 200
    assert client.get("/api/usage/logs").json()["total"] == 0
    assert client.get("/api/usage/summary?days=0").json()["totals"]["calls"] == 0
