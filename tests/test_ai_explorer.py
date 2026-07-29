"""
AI exploration guard tests

Background: exploration streams chat completions itself instead of going through the provider
adapter, and it used to fill in http://localhost:1234 plus an invented model name whenever no chat
provider was selected. A server install therefore failed with "All connection attempts failed",
which reads as an outage at an address the operator never meant to run a model on.
Design intent: verify the constructor invents no endpoint and that each public generator refuses
before opening a connection, naming the missing selection instead.
Key constraint: the refusal must come before any network use, so these tests pass no search engine —
reaching one would mean the guard ran too late.
"""

import asyncio
import json

from memory.ai_explorer import AIExplorer
from memory.providers import PROVIDER_CATALOG, normalize_config


def _drain(agen) -> list:
    async def run():
        return [json.loads(line) for line in [chunk async for chunk in agen]]

    return asyncio.run(run())


def _unconfigured() -> AIExplorer:
    config = {}
    normalize_config(config)
    return AIExplorer(search_engine=None, config=config)


def test_unconfigured_explorer_invents_no_endpoint():
    explorer = _unconfigured()

    assert explorer.configured is False
    assert explorer.base_url == ""
    assert explorer.model == ""


def test_explore_names_the_missing_provider_instead_of_failing_to_connect():
    events = _drain(_unconfigured().explore("anything"))

    assert [e["type"] for e in events] == ["error"]
    assert events[0]["code"] == "llm_error"
    assert "Settings" in events[0]["vars"]["detail"]


def test_drill_down_and_generate_memory_refuse_the_same_way():
    explorer = _unconfigured()

    for events in (_drain(explorer.drill_down("trunk_1")),
                   _drain(explorer.generate_memory([], "anything"))):
        assert [e["type"] for e in events] == ["error"]
        assert events[0]["code"] == "llm_error"


def test_selected_provider_is_used_as_configured():
    config = {}
    normalize_config(config)
    config["providers"]["openai"] = dict(PROVIDER_CATALOG["openai"])
    config["active"]["chat_provider"] = "openai"

    explorer = AIExplorer(search_engine=None, config=config)

    assert explorer.configured is True
    assert explorer.base_url == PROVIDER_CATALOG["openai"]["base_url"].rstrip("/")
    assert explorer.model == PROVIDER_CATALOG["openai"]["chat_model"]
