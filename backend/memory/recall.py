"""
Adaptive recall strategy

Background: Semantic recall originally relied on a single global absolute threshold
(min_similarity) to decide "what score counts as relevant". This number is not
portable — switching to a different embedding model on the same set of memories shifts
the entire cosine score distribution, immediately invalidating tuned thresholds; users
also have no way to know what value to enter on the settings page. In practice, a
threshold of 0.69 caused semantic recall to consistently return 0 results, silently
degrading retrieval to keyword-only.

Design intent: Relevance is now judged relatively. The best hit for the current query
serves as an anchor, and results sufficiently close to it are retained. The absolute
score is only used to filter out pure noise and to guarantee that at least some results
are always returned. This makes the threshold independent of the model and data scale,
eliminating the need for user tuning.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from typing import Callable, List, Sequence, TypeVar

T = TypeVar("T")

# Relative anchor ratio: keep results whose score is at least (best hit × this ratio).
# Too low brings in weakly relevant content; too high may cut valid recalls when query
# wording differs significantly from the original text.
DEFAULT_RELATIVE_RATIO = 0.55

# Noise floor: results below this absolute score are treated as noise regardless of rank.
DEFAULT_NOISE_FLOOR = 0.15

# Minimum keep count: even if all results fall below the relative anchor, at least this many are returned for upstream judgment.
DEFAULT_MIN_KEEP = 3

# Upper bound for the noise floor. A floor set too high causes recall to return nothing
# (exactly the failure historically caused by 0.69), so any configured value is clamped
# to this range first.
MAX_NOISE_FLOOR = 0.4


def clamp_noise_floor(value: float) -> float:
    """Clamp a floor value from any source to the safe range, preventing misconfiguration from disabling semantic recall entirely."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return DEFAULT_NOISE_FLOOR
    return min(max(value, 0.0), MAX_NOISE_FLOOR)


def migrate_recall_config(config: dict) -> bool:
    """
    Migrate the legacy min_similarity to the safe range under new semantics.

    Under the old semantics this value was an absolute threshold; users tended to raise
    it higher and higher to filter noise (in production a value of 0.69 was observed,
    while the embedding model's max score for the most precise query was only 0.62,
    causing semantic recall to return nothing). Under the new semantics it serves only
    as a noise floor, so values outside the safe range must be corrected once, otherwise
    recall remains completely broken after upgrade.

    Returns True if the config was modified; the caller is responsible for writing it back.
    """
    semantic = (config.get("search") or {}).get("semantic")
    if not isinstance(semantic, dict):
        return False

    value = semantic.get("min_similarity")
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    if value <= MAX_NOISE_FLOOR:
        return False

    semantic["min_similarity"] = DEFAULT_NOISE_FLOOR
    print(
        f"[MIGRATE] Semantic recall threshold {value} used the legacy absolute threshold semantics; "
        f"under the new adaptive recall it would block all results. Downgraded to noise floor {DEFAULT_NOISE_FLOOR}"
    )
    return True


def adaptive_cutoff(
    candidates: Sequence[T],
    score_of: Callable[[T], float],
    *,
    limit: int,
    relative_ratio: float = DEFAULT_RELATIVE_RATIO,
    noise_floor: float = DEFAULT_NOISE_FLOOR,
    min_keep: int = DEFAULT_MIN_KEEP,
) -> List[T]:
    """
    Truncate the candidate set using a relative anchor.

    Args:
        candidates: Candidate results in any order
        score_of: Callable to extract the similarity score from a candidate
        limit: Maximum number of results to return
        relative_ratio: Retention ratio relative to the best hit
        noise_floor: Noise floor; results below this score are discarded outright
        min_keep: Minimum keep count (still subject to noise_floor)

    Returns:
        Results sorted by descending score, truncated
    """
    if not candidates:
        return []

    ordered = sorted(candidates, key=score_of, reverse=True)
    above_floor = [c for c in ordered if score_of(c) >= noise_floor]
    if not above_floor:
        return []

    cutoff = score_of(above_floor[0]) * relative_ratio
    kept = [c for c in above_floor if score_of(c) >= cutoff]

    # When query wording differs significantly from the original text, overall scores
    # are suppressed and the relative anchor may keep only one result. The minimum keep
    # ensures upstream consumers (exploration narratives, follow-up expansion) still have
    # enough results for lateral comparison.
    if len(kept) < min_keep:
        kept = above_floor[:min_keep]

    return kept[:limit]


def candidate_pool_size(limit: int) -> int:
    """
    Relative cutoff needs a sufficiently wide candidate pool to find an accurate anchor;
    fetching only `limit` items degrades the cutoff to a hard cut at `limit`. This returns
    how many items the vector store should actually retrieve.
    """
    return max(limit * 3, 20)
