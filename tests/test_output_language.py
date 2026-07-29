"""
Output language unit tests

Background: the UI locale used to live only in the browser, so AI-generated text (chunk summaries,
semantic tags, follow-up suggestions) came back in whatever language the model chose. A user
reading a Chinese UI got English tags, sometimes English and Chinese within one batch.
Design intent: assert both halves of the contract — that the resolved language reaches the prompts
users actually see, and that the call sites which parse replies with code keep their English keys
and verbatim keywords. No network: prompts are captured from fake chat models.
"""

import pytest

from memory import output_language as ol
from memory.chunker import Chunker
from memory.models import Trunk
from memory.providers import _generate_tags_via_chat


@pytest.fixture(autouse=True)
def unbound_language():
    """Every test starts from an unconfigured backend and leaves nothing bound behind."""
    ol.bind(None)
    yield
    ol.bind(None)


class _CapturingChat:
    """Records the last prompt it was handed, so tests can assert on prompt text."""

    def __init__(self, reply=""):
        self.reply = reply
        self.prompts = []

    def chat(self, messages, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return self.reply

    @property
    def last_prompt(self):
        return self.prompts[-1]


# ---------- Locale normalization ----------

@pytest.mark.parametrize("raw,expected", [
    ("zh-CN", "zh-CN"),
    ("zh_TW", "zh-TW"),
    ("zh-Hant", "zh-TW"),
    ("zh-HK", "zh-TW"),
    ("zh", "zh-CN"),
    ("en-GB", "en"),
    ("ja", "ja"),
    ("  ru  ", "ru"),
    ("auto", ol.AUTO),
    ("", ol.AUTO),
    ("klingon", ol.AUTO),
    (None, ol.AUTO),
    (42, ol.AUTO),
])
def test_normalize_maps_locales_onto_supported_codes(raw, expected):
    assert ol.normalize(raw) == expected


def test_every_supported_language_has_a_name():
    """A code without a name would crash directive(); the frontend list is the contract."""
    frontend_locales = {"de", "en", "es", "fr", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"}
    assert set(ol.LANGUAGE_NAMES) == frontend_locales


# ---------- Directive ----------

def test_auto_produces_no_directive():
    """The historical behaviour has to stay reachable: output follows the memory's own language."""
    assert ol.directive(ol.AUTO) == ""
    assert ol.directive(ol.AUTO, json_mode=True) == ""
    assert ol.directive("klingon") == ""


def test_directive_names_the_target_language():
    assert "Simplified Chinese" in ol.directive("zh-CN")
    assert "Traditional Chinese" in ol.directive("zh-TW")


def test_json_directive_protects_field_names():
    """Translating a JSON key breaks extraction, so the instruction must carve keys out."""
    text = ol.directive("ja", json_mode=True)
    assert "Japanese" in text
    assert "field names" in text
    assert "English" in text


def test_bind_makes_the_language_readable_without_plumbing():
    assert ol.current() == ol.AUTO
    config = {"output_language": "ko"}
    ol.bind(config)
    assert ol.current() == "ko"
    assert "Korean" in ol.current_directive()
    # Bound by reference: a language saved from the settings page reaches long-lived chunkers
    # and extractors with no refresh step.
    config["output_language"] = "fr"
    assert ol.current() == "fr"


def test_unbound_backend_behaves_like_auto():
    assert ol.current() == ol.AUTO
    assert ol.current_directive() == ""


# ---------- Prompt injection ----------

def test_chunk_summary_prompt_requests_the_configured_language():
    """An English source with a Chinese reader is exactly the case that used to break."""
    ol.bind({"output_language": "zh-CN"})
    chat = _CapturingChat(reply="a summary")
    Chunker(chat_model=chat).generate_trunk_summary(
        Trunk(id="t1", document_id="d1", order=0, content="a list of things to do")
    )
    assert "Simplified Chinese" in chat.last_prompt


def test_chunk_summary_prompt_stays_clean_on_auto():
    chat = _CapturingChat(reply="summary")
    Chunker(chat_model=chat).generate_trunk_summary(
        Trunk(id="t1", document_id="d1", order=0, content="some content")
    )
    assert "Output Language" not in chat.last_prompt


def test_tag_prompt_requests_the_configured_language():
    ol.bind({"output_language": "ja"})
    chat = _CapturingChat(reply="work/task/todo")
    tags = _generate_tags_via_chat(chat, "a title", "some body text")
    assert "Japanese" in chat.last_prompt
    assert tags == ["work/task/todo"]


def test_metadata_prompt_keeps_json_keys_english():
    """The reply is parsed into intent:/sentiment: tags, so keys must survive translation."""
    from memory.meta_extractor import TextMetaExtractor

    ol.bind({"output_language": "zh-CN"})
    captured = {}

    class _FakeClient:
        def post(self, url, headers=None, json=None):
            captured["prompt"] = json["messages"][-1]["content"]
            raise RuntimeError("stop after capturing the prompt")

    extractor = TextMetaExtractor(base_url="http://x/v1", model="m")
    extractor.client = _FakeClient()
    extractor.extract("a passage long enough to get past the minimum length guard")

    assert "Simplified Chinese" in captured["prompt"]
    assert "field names" in captured["prompt"]
