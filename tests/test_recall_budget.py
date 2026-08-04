"""
Recall Budget Tests (search.recall_budget)

Background: With AI as the only reader, an oversized trunk silently inflates every
conversation turn's token cost. RecallBudget caps per-trunk and total characters
injected per search response; cut content degrades to a hint pointing at get_trunk.
Coverage: pure clip logic, and MemoryTools.search_memories / quick_match integration
via a stub search engine (no network, no index dependency).
"""

from memory.tools import MemoryTools, RecallBudget


def _trunk_result(trunk_id, doc_id, content, score=0.9, order=0):
    return {
        "trunk": {
            "id": trunk_id, "document_id": doc_id, "order": order,
            "content": content, "tags": [], "summary": None,
        },
        "score": score,
        "document_title": f"Doc {doc_id}",
    }


class StubSearch:
    """Returns canned trunk results; no vector store"""

    vector_store = None

    def __init__(self, results):
        self._results = results

    def search_trunks(self, query, mode="auto", limit=10, min_score=None):
        return {"results": self._results[:limit], "debug": {"mode": "keyword"}}


class StubSync:
    class database:  # noqa: N801 - attribute-style stub
        @staticmethod
        def get_all_tags():
            return []


# ---------- RecallBudget unit behavior ----------

def test_budget_disabled_passthrough():
    budget = RecallBudget(0, 0)
    text = "x" * 100000
    assert budget.clip(text) == text


def test_budget_per_trunk_cap():
    budget = RecallBudget(max_chars_per_trunk=50, max_total_chars=0)
    clipped = budget.clip("a" * 200)
    assert clipped.startswith("a" * 50)
    assert RecallBudget.TRUNCATE_HINT in clipped
    # Short content untouched
    assert budget.clip("short") == "short"


def test_budget_total_cap():
    budget = RecallBudget(max_chars_per_trunk=0, max_total_chars=100)
    first = budget.clip("a" * 80)   # fits
    second = budget.clip("b" * 80)  # only ~20 chars of room left
    third = budget.clip("c" * 80)   # budget exhausted
    assert first == "a" * 80
    assert RecallBudget.TRUNCATE_HINT in second
    assert len(second) < 80 + len(RecallBudget.TRUNCATE_HINT) + 1
    assert third == RecallBudget.TRUNCATE_HINT


# ---------- MemoryTools integration ----------

def _make_tools(results, budget_cfg):
    tools = MemoryTools(
        StubSync(), StubSearch(results),
        config={"search": {"recall_budget": budget_cfg}},
    )
    return tools


def test_search_memories_applies_budget():
    long_content = "This memo about deployment " * 100  # ~2700 chars
    results = [
        _trunk_result("trunk_aaa11111", "mem_doc00001", long_content),
        _trunk_result("trunk_bbb22222", "mem_doc00002", long_content, score=0.8),
    ]
    tools = _make_tools(results, {"max_chars_per_trunk": 200, "max_total_chars": 300})
    output = tools.search_memories("deployment")
    assert RecallBudget.TRUNCATE_HINT in output
    # Both trunk ids still listed: the Agent can always drill down
    assert "trunk_aaa11111" in output and "trunk_bbb22222" in output
    # Full 2700-char content must not appear
    assert long_content not in output


def test_search_memories_without_budget_returns_full_content():
    long_content = "Full fidelity content block. " * 50
    tools = _make_tools([_trunk_result("trunk_ccc33333", "mem_doc00003", long_content)], {})
    output = tools.search_memories("fidelity")
    assert long_content in output
    assert RecallBudget.TRUNCATE_HINT not in output


def test_quick_match_applies_budget():
    long_content = "Quick match verbose answer body. " * 100
    results = [_trunk_result("trunk_ddd44444", "mem_doc00004", long_content, score=0.95)]
    tools = _make_tools(results, {"max_chars_per_trunk": 150, "max_total_chars": 0})
    output = tools.quick_match("verbose answer")
    assert RecallBudget.TRUNCATE_HINT in output
    assert long_content not in output
