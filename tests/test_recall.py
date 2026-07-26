"""
Adaptive Recall Tests

Background: Semantic recall previously relied on an absolute threshold for relevance.
After production config was set to 0.69, recall became permanently empty, search silently
degraded to keyword-only (exploration could only retrieve a single isolated result).
Design intent: Lock down three bottom lines of the new strategy — relative anchor cutoff,
guaranteed non-empty results, floor cannot be fatal; and ensure legacy configs auto-downgrade
so the same failure cannot recur after upgrade.
"""

import pytest

from memory.recall import (
    DEFAULT_NOISE_FLOOR,
    MAX_NOISE_FLOOR,
    adaptive_cutoff,
    candidate_pool_size,
    clamp_noise_floor,
    migrate_recall_config,
)
from memory.search import SearchEngine


def scores(*values):
    """Construct (id, score) candidates matching vector store return format"""
    return [(f"t{i}", v) for i, v in enumerate(values)]


def kept_scores(result):
    return [score for _, score in result]


def test_keeps_results_close_to_best_hit():
    """Results close enough to the best hit are kept; clearly trailing ones are cut off"""
    result = adaptive_cutoff(
        scores(0.62, 0.55, 0.40, 0.12),
        lambda item: item[1],
        limit=10,
        relative_ratio=0.55,
        noise_floor=0.1,
        min_keep=1,
    )
    assert kept_scores(result) == [0.62, 0.55, 0.40]


def test_never_returns_empty_when_candidates_exist():
    """
    Positive case for the historical failure: when the highest score in the entire store
    is only 0.35, results must still be returned — not zero results as with an absolute threshold.
    """
    result = adaptive_cutoff(
        scores(0.35, 0.34, 0.28, 0.26),
        lambda item: item[1],
        limit=8,
        noise_floor=DEFAULT_NOISE_FLOOR,
    )
    assert len(result) >= 3


def test_min_keep_survives_steep_score_drop():
    """Even with a cliff-like score drop, min_keep results are still returned for upstream comparison"""
    result = adaptive_cutoff(
        scores(0.80, 0.20, 0.19),
        lambda item: item[1],
        limit=10,
        relative_ratio=0.55,
        noise_floor=0.1,
        min_keep=3,
    )
    assert kept_scores(result) == [0.80, 0.20, 0.19]


def test_noise_floor_still_filters_garbage():
    """Guaranteed non-empty does not mean no floor: results below the noise floor are always discarded"""
    result = adaptive_cutoff(
        scores(0.05, 0.04, 0.02),
        lambda item: item[1],
        limit=10,
        noise_floor=0.15,
        min_keep=3,
    )
    assert result == []


def test_limit_is_respected():
    result = adaptive_cutoff(
        scores(0.9, 0.88, 0.86, 0.84),
        lambda item: item[1],
        limit=2,
        noise_floor=0.1,
    )
    assert len(result) == 2


def test_empty_candidates():
    assert adaptive_cutoff([], lambda item: item[1], limit=5) == []


def test_candidate_pool_is_wider_than_limit():
    """Relative anchor must be chosen accurately; candidate pool must be wider than return limit"""
    assert candidate_pool_size(8) > 8
    assert candidate_pool_size(1) >= 20


@pytest.mark.parametrize(
    "raw,expected",
    [
        (0.69, MAX_NOISE_FLOOR),  # Fatal value that occurred in production
        (1.0, MAX_NOISE_FLOOR),
        (0.25, 0.25),
        (-0.5, 0.0),
        (None, DEFAULT_NOISE_FLOOR),
        ("abc", DEFAULT_NOISE_FLOOR),
    ],
)
def test_clamp_noise_floor(raw, expected):
    assert clamp_noise_floor(raw) == expected


def test_search_engine_clamps_config_floor():
    """When config is manually set to a fatal value, the search engine must not entirely fail"""
    engine = SearchEngine(database=None, min_similarity=0.69)
    assert engine.min_similarity == MAX_NOISE_FLOOR

    engine.set_min_similarity(0.95)
    assert engine.min_similarity == MAX_NOISE_FLOOR


def test_migrate_legacy_threshold():
    config = {"search": {"semantic": {"enabled": True, "min_similarity": 0.69}}}
    assert migrate_recall_config(config) is True
    assert config["search"]["semantic"]["min_similarity"] == DEFAULT_NOISE_FLOOR


def test_migration_is_idempotent_and_leaves_sane_values():
    config = {"search": {"semantic": {"enabled": True, "min_similarity": 0.2}}}
    assert migrate_recall_config(config) is False
    assert config["search"]["semantic"]["min_similarity"] == 0.2
    assert migrate_recall_config({}) is False


def test_question_query_favours_semantic():
    """Queries like 'who am I' were previously treated as short keyword queries with lower semantic weight"""
    engine = SearchEngine(database=None)
    keyword_w, semantic_w = engine._calculate_dynamic_weights("who am I")
    assert semantic_w > keyword_w


def test_short_keyword_query_favours_keyword():
    engine = SearchEngine(database=None)
    keyword_w, semantic_w = engine._calculate_dynamic_weights("Alex Rivera")
    assert keyword_w > semantic_w


def test_rrf_weights_stay_within_one_order_of_magnitude():
    """
    When weights are too skewed, the other path's ranking becomes completely ineffective,
    and RRF degrades to single-path retrieval.
    """
    engine = SearchEngine(database=None)
    for query in ["who am I", "Alex Rivera", "decision style and emotional handling", "help me review my recent startup decisions and team arrangements"]:
        keyword_w, semantic_w = engine._calculate_dynamic_weights(query)
        assert max(keyword_w, semantic_w) / min(keyword_w, semantic_w) <= 3
