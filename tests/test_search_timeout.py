"""
Recall Timeout Tests (search.timeout_seconds)

Background: The memory service sits on the Agent conversation's critical path; a hung
recall backend (embedding provider down, index lock) must never block the turn.
Coverage: hard deadline returns partial/empty results, fast path survives a slow
sibling, timeout=0 preserves sequential legacy behavior. Slow paths are simulated by
monkeypatching the internal recall methods — no real backends involved.
"""

import time

from memory.models import Trunk, TrunkSearchResult
from memory.search import SearchEngine


def _fast_keyword_result():
    trunk = Trunk(id="trunk_fast0001", document_id="mem_fast0001", order=0,
                  content="fast keyword hit", status="ready")
    return [TrunkSearchResult(trunk=trunk, document_title="Fast doc",
                              score=0.9, match_type="keyword")]


def _make_engine(timeout):
    # database isn't touched: both recall paths get monkeypatched per test
    return SearchEngine(database=None, vector_store=None, whoosh_search=object(),
                        semantic_enabled=False, timeout_seconds=timeout)


def test_hung_path_returns_within_deadline():
    engine = _make_engine(timeout=0.5)

    def hung(query, limit):
        time.sleep(3)
        return _fast_keyword_result()

    engine._trunk_keyword_search = hung
    start = time.time()
    result = engine.search_trunks("anything", mode="keyword")
    elapsed = time.time() - start

    assert elapsed < 2, f"search blocked for {elapsed:.1f}s despite 0.5s timeout"
    assert result["results"] == []
    assert result["debug"].get("keyword_timed_out") is True


def test_fast_path_survives_slow_sibling():
    """Hybrid mode: semantic path hangs, keyword results still come back (partial results)"""
    engine = SearchEngine(database=None, vector_store=object(), whoosh_search=object(),
                          semantic_enabled=True, timeout_seconds=0.5)

    engine._trunk_keyword_search = lambda query, limit: _fast_keyword_result()

    def hung_semantic(query, limit, min_score):
        time.sleep(3)
        return []

    engine._trunk_semantic_search = hung_semantic

    start = time.time()
    result = engine.search_trunks("anything", mode="hybrid")
    elapsed = time.time() - start

    assert elapsed < 2
    assert len(result["results"]) == 1
    assert result["results"][0]["trunk"]["id"] == "trunk_fast0001"
    assert result["debug"].get("semantic_timed_out") is True
    assert "keyword_timed_out" not in result["debug"]


def test_path_error_is_isolated():
    """One path raising doesn't kill the other path's results"""
    engine = SearchEngine(database=None, vector_store=object(), whoosh_search=object(),
                          semantic_enabled=True, timeout_seconds=0.5)
    engine._trunk_keyword_search = lambda query, limit: _fast_keyword_result()

    def broken_semantic(query, limit, min_score):
        raise RuntimeError("embedding provider exploded")

    engine._trunk_semantic_search = broken_semantic
    result = engine.search_trunks("anything", mode="hybrid")
    assert len(result["results"]) == 1
    assert "embedding provider exploded" in result["debug"].get("semantic_error", "")


def test_timeout_zero_disables_guard():
    """timeout_seconds=0: sequential legacy path, no worker threads, results intact"""
    engine = _make_engine(timeout=0)
    engine._trunk_keyword_search = lambda query, limit: _fast_keyword_result()
    result = engine.search_trunks("anything", mode="keyword")
    assert len(result["results"]) == 1
    assert result["debug"].get("keyword_count") == 1
