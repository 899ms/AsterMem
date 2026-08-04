"""
Search engine - integrating keyword search and semantic search

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any, Callable, Dict, List, Optional

from .models import Memory, SearchResult, TrunkSearchResult
from .database import Database
from .recall import (
    DEFAULT_MIN_KEEP,
    DEFAULT_NOISE_FLOOR,
    DEFAULT_RELATIVE_RATIO,
    adaptive_cutoff,
    candidate_pool_size,
    clamp_noise_floor,
)
from .vector import VectorStore
from .whoosh_search import WhooshSearch

# Recall timeout: the memory system sits on the conversation's critical path; a hung
# search backend (embedding service down, index lock) must never block the Agent's turn.
# On timeout the slow path contributes nothing and whatever the other path returned is used.
DEFAULT_SEARCH_TIMEOUT = 5.0


class SearchEngine:
    """Search engine"""
    
    def __init__(
        self, 
        database: Database,
        vector_store: Optional[VectorStore] = None,
        whoosh_search: Optional[WhooshSearch] = None,
        semantic_enabled: bool = False,
        min_similarity: float = DEFAULT_NOISE_FLOOR,
        relative_ratio: float = DEFAULT_RELATIVE_RATIO,
        min_keep: int = DEFAULT_MIN_KEEP,
        timeout_seconds: float = DEFAULT_SEARCH_TIMEOUT,
    ):
        self.database = database
        self.vector_store = vector_store
        self.whoosh_search = whoosh_search
        self.semantic_enabled = semantic_enabled
        # min_similarity only serves as the "noise floor"; relevance is determined by
        # recall.adaptive_cutoff's relative judgment. Config values are clamped to a safe
        # range so misconfiguration won't zero out semantic recall entirely.
        self.min_similarity = clamp_noise_floor(min_similarity)
        self.relative_ratio = relative_ratio
        self.min_keep = min_keep
        # <= 0 disables the guard (used by tests that assert exact sequential behavior)
        self.timeout_seconds = float(timeout_seconds or 0)

    def _run_paths_with_timeout(
        self,
        paths: Dict[str, Callable[[], list]],
        debug_info: Dict[str, Any],
    ) -> Dict[str, list]:
        """
        Run recall paths concurrently under a shared deadline.

        Each path that finishes in time contributes its results; a path that exceeds
        the deadline (or raises) contributes an empty list and is flagged in debug_info
        ("<name>_timed_out" / "<name>_error"). The worker thread of a timed-out path is
        abandoned, not joined — same trade-off as an HTTP client timeout.
        """
        results: Dict[str, list] = {name: [] for name in paths}
        if not paths:
            return results

        if self.timeout_seconds <= 0:
            # Guard disabled: run sequentially, preserving legacy behavior
            for name, fn in paths.items():
                start = time.time()
                try:
                    results[name] = fn()
                    debug_info[f"{name}_count"] = len(results[name])
                except Exception as e:
                    debug_info[f"{name}_error"] = str(e)
                debug_info[f"{name}_time_ms"] = int((time.time() - start) * 1000)
            return results

        deadline = time.time() + self.timeout_seconds
        executor = ThreadPoolExecutor(max_workers=len(paths))
        try:
            futures = {name: executor.submit(fn) for name, fn in paths.items()}
            for name, future in futures.items():
                start = time.time()
                remaining = max(0.0, deadline - time.time())
                try:
                    results[name] = future.result(timeout=remaining)
                    debug_info[f"{name}_count"] = len(results[name])
                except FutureTimeoutError:
                    debug_info[f"{name}_timed_out"] = True
                    print(f"[WARN] Search path '{name}' exceeded {self.timeout_seconds}s timeout, returning partial results")
                except Exception as e:
                    debug_info[f"{name}_error"] = str(e)
                debug_info[f"{name}_time_ms"] = int((time.time() - start) * 1000)
        finally:
            # wait=False: never block the caller on an abandoned worker
            executor.shutdown(wait=False)
        return results
    
    def set_semantic_enabled(self, enabled: bool):
        """Set whether semantic search is enabled"""
        self.semantic_enabled = enabled

    def set_min_similarity(self, value: float):
        """Hot-update noise floor (settings page changes take effect immediately, no restart needed)"""
        self.min_similarity = clamp_noise_floor(value)
    
    def search(
        self,
        query: str,
        mode: str = "auto",  # auto / keyword / semantic / hybrid
        limit: int = 10,
        tags: Optional[List[str]] = None,
        min_score: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Unified search interface
        
        Args:
            query: Search query
            mode: Search mode
                - auto: Automatic selection (based on configuration)
                - keyword: Keyword search only
                - semantic: Semantic search only
                - hybrid: Hybrid search
            limit: Number of results to return
            tags: Tag filter
            min_score: Minimum similarity score
        
        Returns:
            Search results and debug information
        """
        start_time = time.time()
        
        if min_score is None:
            min_score = self.min_similarity
        
        # Determine actual search mode
        if mode == "auto":
            if self.semantic_enabled and self.vector_store:
                mode = "hybrid"
            else:
                mode = "keyword"
        
        results = []
        debug_info = {
            "query": query,
            "mode": mode,
            "keyword_time_ms": 0,
            "semantic_time_ms": 0,
            "total_time_ms": 0,
        }
        
        # Run recall paths concurrently under a shared deadline; a hung path degrades
        # to empty results instead of blocking the conversation (see _run_paths_with_timeout)
        paths: Dict[str, Callable[[], list]] = {}
        if mode in ("keyword", "hybrid"):
            paths["keyword"] = lambda: self._keyword_search(query, limit * 2)
        if mode in ("semantic", "hybrid") and self.semantic_enabled and self.vector_store:
            paths["semantic"] = lambda: self._semantic_search(query, limit * 2, min_score)
        
        path_results = self._run_paths_with_timeout(paths, debug_info)
        for path_result in path_results.values():
            results.extend(path_result)
        
        # Merge and deduplicate (pass query for dynamic weights)
        merged_results = self._merge_results(results, limit, query=query)
        
        # Tag filtering
        if tags:
            merged_results = [
                r for r in merged_results
                if any(t in r.memory.tags for t in tags)
            ]
        
        debug_info["total_time_ms"] = int((time.time() - start_time) * 1000)
        debug_info["result_count"] = len(merged_results)
        
        return {
            "results": [r.to_dict() for r in merged_results],
            "debug": debug_info
        }
    
    def _keyword_search(self, query: str, limit: int) -> List[SearchResult]:
        """Keyword search"""
        results = []
        
        try:
            matches = self.database.search_keyword(query, limit)
            
            for memory, score in matches:
                # Calculate keyword match count
                keywords = query.lower().split()
                text = f"{memory.title} {memory.content}".lower()
                keyword_matches = sum(1 for k in keywords if k in text)
                
                results.append(SearchResult(
                    memory=memory,
                    score=min(score / 10, 1.0),  # Normalize score
                    match_type="keyword",
                    keyword_matches=keyword_matches
                ))
        except Exception as e:
            print(f"Keyword search error: {e}")
        
        return results
    
    def _semantic_search(
        self, 
        query: str, 
        limit: int,
        min_score: float
    ) -> List[SearchResult]:
        """
        Semantic search.

        The vector store only ranks candidates by similarity; "what counts as relevant"
        is delegated to adaptive_cutoff for relative judgment. Cutoff happens before
        database lookups to fill Memory objects, avoiding unnecessary IO for noise candidates.
        """
        results = []
        
        if not self.vector_store:
            return results
        
        candidates = self.vector_store.search(query, candidate_pool_size(limit), 0.0)
        matches = adaptive_cutoff(
            candidates,
            lambda item: item[1],
            limit=limit,
            relative_ratio=self.relative_ratio,
            noise_floor=min_score,
            min_keep=self.min_keep,
        )
        
        for memory_id, score in matches:
            memory = self.database.get_memory(memory_id)
            if memory and memory.status == "active":
                results.append(SearchResult(
                    memory=memory,
                    score=score,
                    match_type="semantic",
                    keyword_matches=0
                ))
        
        return results
    
    # Question patterns: these queries typically use different wording than the source text, requiring semantic understanding to bridge the gap
    QUESTION_PATTERNS = [
        r'什么', r'啥', r'怎么', r'怎样', r'咋', r'如何', r'哪里', r'哪儿', r'哪个',
        r'哪些', r'哪位', r'谁', r'为什么', r'为啥', r'干嘛', r'多少', r'多久',
        r'几个', r'几时', r'何时', r'是否', r'能否', r'可以吗', r'行吗', r'好吗',
        r'介绍', r'讲讲', r'说说', r'总结',
        r'吗$', r'呢$', r'吧$', r'\?$', r'？$',
        r'\bwho\b', r'\bwhat\b', r'\bwhere\b', r'\bwhen\b', r'\bwhy\b',
        r'\bhow\b', r'\bwhich\b',
    ]

    def _calculate_dynamic_weights(self, query: str) -> tuple:
        """
        Dynamically calculate RRF fusion weights based on query characteristics
        
        Strategy:
        - Questions: wording diverges significantly from source text, semantic-dominant
        - Short queries (1-2 words): typically searching for specific names or terms, keyword-dominant
        - Medium queries (3-5 words): slightly semantic-heavy
        - Long queries (6+ words): describing concepts, semantic-dominant
        
        Key constraint: RRF relies on rank fusion; when weights are too disparate, the lower-weighted
        path's rankings have no effect, effectively degrading to single-path retrieval. Weight differences
        are thus kept within 3x to ensure both paths can influence the final ordering.
        
        Returns:
            (keyword_weight, semantic_weight)
        """
        if any(re.search(p, query.lower()) for p in self.QUESTION_PATTERNS):
            return (1.0, 3.0)
        
        # Count words (supports mixed Chinese and English)
        words = query.split()
        word_count = 0
        for w in words:
            # For pure Chinese text, estimate word count as character count / 2
            chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', w))
            if chinese_chars > 0:
                word_count += max(1, chinese_chars // 2)
            else:
                word_count += 1
        
        if word_count <= 2:
            return (2.0, 1.0)
        elif word_count <= 5:
            return (1.0, 2.0)
        else:
            return (1.0, 3.0)
    
    def _merge_results(
        self, 
        results: List[SearchResult], 
        limit: int,
        k: int = 60,
        query: str = ""
    ) -> List[SearchResult]:
        """
        Merge and deduplicate search results (using RRF reciprocal rank fusion + dynamic weights)
        
        Score = 1 / (k + rank)
        Weights are dynamically adjusted based on query length
        """
        if not results:
            return []
            
        # Separate results by source
        keyword_results = []
        semantic_results = []
        
        for r in results:
            if r.match_type == "keyword":
                keyword_results.append(r)
            else:
                semantic_results.append(r)
        
        # If only one type of results exists, return directly
        if not keyword_results:
            return sorted(semantic_results, key=lambda x: x.score, reverse=True)[:limit]
        if not semantic_results:
            return sorted(keyword_results, key=lambda x: x.score, reverse=True)[:limit]
            
        # Calculate RRF scores
        rrf_scores = {}
        seen_objects = {}
        
        # Dynamic weights: adjust based on query length
        W_KEYWORD, W_SEMANTIC = self._calculate_dynamic_weights(query)
        
        # Process keyword results
        # Sort by original score first
        keyword_results.sort(key=lambda x: x.score, reverse=True)
        for rank, r in enumerate(keyword_results):
            memory_id = r.memory.id
            if memory_id not in rrf_scores:
                rrf_scores[memory_id] = 0.0
                seen_objects[memory_id] = r
            
            rrf_scores[memory_id] += W_KEYWORD * (1.0 / (k + rank + 1))
            
        # Process semantic results
        semantic_results.sort(key=lambda x: x.score, reverse=True)
        for rank, r in enumerate(semantic_results):
            memory_id = r.memory.id
            if memory_id not in rrf_scores:
                rrf_scores[memory_id] = 0.0
                seen_objects[memory_id] = r
            else:
                # If already exists (matched in both), mark as hybrid
                seen_objects[memory_id].match_type = "hybrid"
                seen_objects[memory_id].score = max(seen_objects[memory_id].score, r.score) # Keep higher original score for display
            
            rrf_scores[memory_id] += W_SEMANTIC * (1.0 / (k + rank + 1))
            
        # Sort by RRF score
        sorted_ids = sorted(rrf_scores.keys(), key=lambda mid: rrf_scores[mid], reverse=True)
        
        final_results = []
        for mid in sorted_ids[:limit]:
            final_results.append(seen_objects[mid])
            
        return final_results
    
    def get_related(self, memory_id: str, limit: int = 5) -> List[SearchResult]:
        """Get related memories"""
        results = []
        
        if not self.semantic_enabled or not self.vector_store:
            return results
        
        matches = self.vector_store.find_related(memory_id, limit)
        
        for mid, score in matches:
            memory = self.database.get_memory(mid)
            if memory and memory.status == "active":
                results.append(SearchResult(
                    memory=memory,
                    score=score,
                    match_type="semantic"
                ))
        
        return results
    
    def search_trunks(
        self,
        query: str,
        mode: str = "auto",
        limit: int = 10,
        min_score: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Trunk-level search
        
        Args:
            query: Search query
            mode: Search mode
                - auto: Automatic selection
                - keyword: Keyword search only
                - semantic: Semantic search only
                - hybrid: Hybrid search
            limit: Number of results to return
            min_score: Minimum similarity score
        
        Returns:
            Search results and debug information
        """
        start_time = time.time()
        
        if min_score is None:
            min_score = self.min_similarity
        
        # Determine actual search mode
        if mode == "auto":
            if self.semantic_enabled and self.vector_store:
                mode = "hybrid"
            else:
                mode = "keyword"
        
        results = []
        debug_info = {
            "query": query,
            "mode": mode,
            "level": "trunk",
            "keyword_time_ms": 0,
            "semantic_time_ms": 0,
            "total_time_ms": 0,
        }
        
        # Run recall paths concurrently under a shared deadline; a hung path degrades
        # to empty results instead of blocking the conversation (see _run_paths_with_timeout)
        paths: Dict[str, Callable[[], list]] = {}
        if mode in ("keyword", "hybrid") and self.whoosh_search:
            paths["keyword"] = lambda: self._trunk_keyword_search(query, limit * 2)
        if mode in ("semantic", "hybrid") and self.semantic_enabled and self.vector_store:
            paths["semantic"] = lambda: self._trunk_semantic_search(query, limit * 2, min_score)
        
        path_results = self._run_paths_with_timeout(paths, debug_info)
        for path_result in path_results.values():
            results.extend(path_result)
        
        # Merge and deduplicate (pass query for dynamic weights)
        merged_results = self._merge_trunk_results(results, limit, query=query)
        
        debug_info["total_time_ms"] = int((time.time() - start_time) * 1000)
        debug_info["result_count"] = len(merged_results)
        
        return {
            "results": [r.to_dict() for r in merged_results],
            "debug": debug_info
        }
    
    def _trunk_keyword_search(self, query: str, limit: int) -> List[TrunkSearchResult]:
        """Trunk keyword search (with meta tag enhancement)"""
        results = []
        
        if not self.whoosh_search:
            return results
        
        try:
            matches = self.whoosh_search.search_trunks(query, limit)
            
            for trunk_id, score in matches:
                trunk = self.database.get_trunk(trunk_id)
                if trunk and trunk.status == "ready":
                    memory = self.database.get_memory(trunk.document_id)
                    document_title = memory.title if memory else "Unknown document"
                    
                    # Calculate keyword match count
                    keywords = query.lower().split()
                    text = trunk.content.lower()
                    keyword_matches = sum(1 for k in keywords if k in text)
                    
                    # Meta tag enhancement: boost score if meta tags also match
                    meta_boost = 0.0
                    if trunk.meta_tags:
                        meta_text = ' '.join(trunk.meta_tags).lower()
                        meta_matches = sum(1 for k in keywords if k in meta_text)
                        if meta_matches > 0:
                            # Meta matches can add up to 30% score boost
                            meta_boost = min(meta_matches * 0.1, 0.3)
                    
                    final_score = min((score / 10) + meta_boost, 1.0)
                    
                    results.append(TrunkSearchResult(
                        trunk=trunk,
                        score=final_score,
                        match_type="keyword",
                        document_title=document_title,
                        keyword_matches=keyword_matches
                    ))
        except Exception as e:
            print(f"Trunk keyword search error: {e}")
        
        return results
    
    def _trunk_semantic_search(
        self,
        query: str,
        limit: int,
        min_score: float
    ) -> List[TrunkSearchResult]:
        """
        Trunk semantic search (with meta tag weighting).

        Same as document-level: vector store only ranks; relevance is determined by adaptive_cutoff's relative judgment.
        """
        results = []
        
        if not self.vector_store:
            return results
        
        try:
            candidates = self.vector_store.search_trunks(query, candidate_pool_size(limit), 0.0)
            matches = adaptive_cutoff(
                candidates,
                lambda item: item[1],
                limit=limit,
                relative_ratio=self.relative_ratio,
                noise_floor=min_score,
                min_keep=self.min_keep,
            )
            
            for trunk_id, score in matches:
                trunk = self.database.get_trunk(trunk_id)
                if trunk and trunk.status == "ready":
                    memory = self.database.get_memory(trunk.document_id)
                    document_title = memory.title if memory else "Unknown document"
                    
                    # Meta tag weighting: check if query terms appear in meta tags
                    meta_boost = 0.0
                    if trunk.meta_tags:
                        keywords = query.lower().split()
                        meta_text = ' '.join(trunk.meta_tags).lower()
                        meta_matches = sum(1 for k in keywords if k in meta_text)
                        if meta_matches > 0:
                            # Meta matches can add up to 20% score boost
                            meta_boost = min(meta_matches * 0.07, 0.2)
                    
                    final_score = min(score + meta_boost, 1.0)
                    
                    results.append(TrunkSearchResult(
                        trunk=trunk,
                        score=final_score,
                        match_type="semantic",
                        document_title=document_title
                    ))
        except Exception as e:
            print(f"Trunk semantic search error: {e}")
        
        return results
    
    def _merge_trunk_results(
        self,
        results: List[TrunkSearchResult],
        limit: int,
        k: int = 60,
        query: str = ""
    ) -> List[TrunkSearchResult]:
        """Merge and deduplicate Trunk search results (using RRF + dynamic weights)"""
        if not results:
            return []
            
        # Separate results
        keyword_results = []
        semantic_results = []
        
        for r in results:
            if r.match_type == "keyword":
                keyword_results.append(r)
            else:
                semantic_results.append(r)
        
        if not keyword_results:
            return sorted(semantic_results, key=lambda x: x.score, reverse=True)[:limit]
        if not semantic_results:
            return sorted(keyword_results, key=lambda x: x.score, reverse=True)[:limit]
            
        # Calculate RRF
        rrf_scores = {}
        seen_objects = {}
        
        # Dynamic weights: adjust based on query length
        W_KEYWORD, W_SEMANTIC = self._calculate_dynamic_weights(query)
        
        # Keyword ranking
        keyword_results.sort(key=lambda x: x.score, reverse=True)
        for rank, r in enumerate(keyword_results):
            trunk_id = r.trunk.id
            if trunk_id not in rrf_scores:
                rrf_scores[trunk_id] = 0.0
                seen_objects[trunk_id] = r
            
            rrf_scores[trunk_id] += W_KEYWORD * (1.0 / (k + rank + 1))
            
        # Semantic ranking
        semantic_results.sort(key=lambda x: x.score, reverse=True)
        for rank, r in enumerate(semantic_results):
            trunk_id = r.trunk.id
            if trunk_id not in rrf_scores:
                rrf_scores[trunk_id] = 0.0
                seen_objects[trunk_id] = r
            else:
                # Hybrid hit
                seen_objects[trunk_id].match_type = "hybrid"
                # Keep higher original score and keyword match count
                seen_objects[trunk_id].score = max(seen_objects[trunk_id].score, r.score)
                seen_objects[trunk_id].keyword_matches = max(
                    seen_objects[trunk_id].keyword_matches,
                    r.keyword_matches
                )
            
            rrf_scores[trunk_id] += W_SEMANTIC * (1.0 / (k + rank + 1))
            
        # Sort
        sorted_ids = sorted(rrf_scores.keys(), key=lambda tid: rrf_scores[tid], reverse=True)
        
        final_results = []
        for tid in sorted_ids[:limit]:
            final_results.append(seen_objects[tid])
            
        return final_results
    
    def compare_trunk_search(
        self,
        query: str,
        limit: int = 10
    ) -> Dict[str, Any]:
        """Trunk comparison search mode"""
        start_time = time.time()
        
        # Keyword search
        keyword_results = []
        keyword_time = 0
        keyword_error = None
        
        if self.whoosh_search:
            keyword_start = time.time()
            try:
                keyword_results = self._trunk_keyword_search(query, limit)
                keyword_time = int((time.time() - keyword_start) * 1000)
            except Exception as e:
                keyword_error = str(e)
        
        # Semantic search
        semantic_results = []
        semantic_time = 0
        semantic_error = None
        
        if self.semantic_enabled and self.vector_store:
            semantic_start = time.time()
            try:
                semantic_results = self._trunk_semantic_search(query, limit, self.min_similarity)
                semantic_time = int((time.time() - semantic_start) * 1000)
            except Exception as e:
                semantic_error = str(e)
        
        # Hybrid search (pass query for dynamic weights)
        hybrid_start = time.time()
        hybrid_results = self._merge_trunk_results(
            keyword_results + semantic_results,
            limit,
            query=query
        )
        hybrid_time = int((time.time() - hybrid_start) * 1000)
        
        # Calculate current weights (for debug display)
        w_keyword, w_semantic = self._calculate_dynamic_weights(query)
        
        return {
            "keyword": {
                "results": [r.to_dict() for r in keyword_results],
                "time_ms": keyword_time,
                "count": len(keyword_results),
                "error": keyword_error
            },
            "semantic": {
                "results": [r.to_dict() for r in semantic_results],
                "time_ms": semantic_time,
                "count": len(semantic_results),
                "error": semantic_error,
                "enabled": self.semantic_enabled
            },
            "hybrid": {
                "results": [r.to_dict() for r in hybrid_results],
                "time_ms": keyword_time + semantic_time + hybrid_time,
                "count": len(hybrid_results),
                "weights": {"keyword": w_keyword, "semantic": w_semantic}
            },
            "total_time_ms": int((time.time() - start_time) * 1000)
        }
    
    def compare_search(
        self,
        query: str,
        limit: int = 10
    ) -> Dict[str, Any]:
        """Comparison search mode"""
        start_time = time.time()
        
        # Keyword search
        keyword_start = time.time()
        keyword_results = self._keyword_search(query, limit)
        keyword_time = int((time.time() - keyword_start) * 1000)
        
        # Semantic search
        semantic_results = []
        semantic_time = 0
        semantic_error = None
        
        if self.semantic_enabled and self.vector_store:
            semantic_start = time.time()
            try:
                semantic_results = self._semantic_search(query, limit, self.min_similarity)
                semantic_time = int((time.time() - semantic_start) * 1000)
            except Exception as e:
                semantic_error = str(e)
        
        # Hybrid search (pass query for dynamic weights)
        hybrid_start = time.time()
        hybrid_results = self._merge_results(
            keyword_results + semantic_results, 
            limit,
            query=query
        )
        hybrid_time = int((time.time() - hybrid_start) * 1000)
        
        # Calculate current weights (for debug display)
        w_keyword, w_semantic = self._calculate_dynamic_weights(query)
        
        return {
            "keyword": {
                "results": [r.to_dict() for r in keyword_results],
                "time_ms": keyword_time,
                "count": len(keyword_results)
            },
            "semantic": {
                "results": [r.to_dict() for r in semantic_results],
                "time_ms": semantic_time,
                "count": len(semantic_results),
                "error": semantic_error,
                "enabled": self.semantic_enabled
            },
            "hybrid": {
                "results": [r.to_dict() for r in hybrid_results],
                "time_ms": keyword_time + semantic_time + hybrid_time,
                "count": len(hybrid_results),
                "weights": {"keyword": w_keyword, "semantic": w_semantic}
            },
            "total_time_ms": int((time.time() - start_time) * 1000)
        }
    
    def search_by_meta_tag(
        self,
        tag_type: Optional[str] = None,
        tag_value: Optional[str] = None,
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        Search trunks precisely by meta tags
        
        Args:
            tag_type: Tag type (e.g. person, location, theme, etc.)
            tag_value: Tag value (supports fuzzy matching)
            limit: Number of results to return
        
        Returns:
            Search results
        """
        start_time = time.time()
        results = []
        
        try:
            # Search matching chunk_ids from database
            chunk_ids = self.database.search_chunks_by_meta_tag(
                tag_type=tag_type,
                tag_value=tag_value,
                limit=limit
            )
            
            for chunk_id in chunk_ids:
                trunk = self.database.get_trunk(chunk_id)
                if trunk and trunk.status == "ready":
                    memory = self.database.get_memory(trunk.document_id)
                    document_title = memory.title if memory else "Unknown document"
                    
                    results.append(TrunkSearchResult(
                        trunk=trunk,
                        score=1.0,  # Exact match
                        match_type="meta",
                        document_title=document_title
                    ))
        except Exception as e:
            print(f"Meta tag search error: {e}")
        
        return {
            "results": [r.to_dict() for r in results],
            "count": len(results),
            "total_time_ms": int((time.time() - start_time) * 1000)
        }
    
    def get_related_by_meta(
        self,
        trunk_id: str,
        limit: int = 10
    ) -> List[TrunkSearchResult]:
        """
        Find related trunks via meta tags
        
        Args:
            trunk_id: Current trunk ID
            limit: Number of results to return
        
        Returns:
            List of related trunks
        """
        results = []
        
        try:
            # Get current trunk's meta tags
            trunk = self.database.get_trunk(trunk_id)
            if not trunk or not trunk.meta_tags:
                return results
            
            # Count meta tag overlap between other trunks and the current trunk
            related_scores = {}
            
            for tag in trunk.meta_tags:
                # Parse tag type and value
                if ':' in tag:
                    parts = tag.split(':', 1)
                    tag_type = parts[0]
                    tag_value = parts[1]
                else:
                    tag_type = None
                    tag_value = tag
                
                # Search for trunks containing the same tag
                matching_ids = self.database.search_chunks_by_meta_tag(
                    tag_type=tag_type,
                    tag_value=tag_value,
                    limit=50
                )
                
                for match_id in matching_ids:
                    if match_id != trunk_id:  # Exclude self
                        related_scores[match_id] = related_scores.get(match_id, 0) + 1
            
            # Sort by relevance
            sorted_ids = sorted(
                related_scores.items(),
                key=lambda x: x[1],
                reverse=True
            )[:limit]
            
            # Build results
            for match_id, score in sorted_ids:
                related_trunk = self.database.get_trunk(match_id)
                if related_trunk and related_trunk.status == "ready":
                    memory = self.database.get_memory(related_trunk.document_id)
                    document_title = memory.title if memory else "Unknown document"
                    
                    results.append(TrunkSearchResult(
                        trunk=related_trunk,
                        score=score / len(trunk.meta_tags),  # Normalize score
                        match_type="meta_related",
                        document_title=document_title,
                        is_same_document=(related_trunk.document_id == trunk.document_id)
                    ))
        except Exception as e:
            print(f"Meta relation search error: {e}")
        
        return results

