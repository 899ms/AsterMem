"""
Whoosh full-text search engine (with Chinese support)

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
from typing import Callable, List, Tuple, Optional

from whoosh import index
from whoosh.fields import Schema, TEXT, ID, NUMERIC, KEYWORD
from whoosh.qparser import MultifieldParser, OrGroup
from whoosh.analysis import Tokenizer, Token
from whoosh.writing import AsyncWriter

import jieba


class JiebaTokenizer(Tokenizer):
    """Jieba tokenizer"""
    
    def __call__(self, value, positions=False, chars=False, keeporiginal=False,
                 removestops=True, start_pos=0, start_char=0, tokenize=True,
                 mode='', **kwargs):
        t = Token(positions, chars, removestops=removestops, mode=mode, **kwargs)
        
        # Tokenize with jieba
        words = jieba.cut_for_search(value)
        
        pos = start_pos
        char_pos = start_char
        
        for word in words:
            word = word.strip()
            if not word:
                continue
            
            t.original = t.text = word
            t.boost = 1.0
            
            if positions:
                t.pos = pos
                pos += 1
            
            if chars:
                t.startchar = char_pos
                t.endchar = char_pos + len(word)
                char_pos = t.endchar
            
            yield t


class JiebaAnalyzer:
    """Jieba analyzer"""
    
    def __call__(self, value, **kwargs):
        tokenizer = JiebaTokenizer()
        return tokenizer(value, **kwargs)


class WhooshSearch:
    """Whoosh search engine"""
    
    def __init__(self, index_dir: str, title_resolver: Optional[Callable[[str], str]] = None):
        self.index_dir = index_dir
        self.trunk_index_dir = os.path.join(index_dir, "trunks")
        self.analyzer = JiebaAnalyzer()
        # Injected by Database: chunks only store document_id, so the owning memory title
        # is resolved from it when indexing
        self.title_resolver = title_resolver
        # Set after a trunk schema change to tell callers the index must be refilled
        # (see _init_trunk_index)
        self.trunk_index_stale = False
        
        # Define the Memory schema
        self.schema = Schema(
            id=ID(stored=True, unique=True),
            title=TEXT(stored=True, analyzer=self.analyzer),
            content=TEXT(stored=True, analyzer=self.analyzer),
            tags=TEXT(stored=True, analyzer=self.analyzer),
            priority=NUMERIC(stored=True),
            status=KEYWORD(stored=True),
        )
        
        # Define the Trunk schema
        # title is the owning memory's title: chunks often lose their subject once split
        # apart ("## Decision style - relies on intuition" carries no personal name), and
        # without the title in the index a name query can never match at the chunk level.
        self.trunk_schema = Schema(
            id=ID(stored=True, unique=True),
            document_id=ID(stored=True),
            title=TEXT(stored=True, analyzer=self.analyzer),
            content=TEXT(stored=True, analyzer=self.analyzer),
            summary=TEXT(stored=True, analyzer=self.analyzer),
            tags=TEXT(stored=True, analyzer=self.analyzer),
            order=NUMERIC(stored=True),
            status=KEYWORD(stored=True),
        )
        self.TRUNK_SEARCH_FIELDS = ["title", "content", "summary", "tags"]
        
        # Initialize the indexes
        self._init_index()
        self._init_trunk_index()
    
    def _init_index(self):
        """Initialize the Memory index"""
        if not os.path.exists(self.index_dir):
            os.makedirs(self.index_dir)
        
        if index.exists_in(self.index_dir):
            self.ix = index.open_dir(self.index_dir)
        else:
            self.ix = index.create_in(self.index_dir, self.schema)
    
    def _init_trunk_index(self):
        """
        Initialize the Trunk index.

        An existing index keeps the old schema stored on disk, so once a field is added,
        queries parsed with the new schema fail outright with "no field named ...". Here we
        compare the field sets and, when the index is stale, rebuild it as an empty index
        and set trunk_index_stale so Database refills it at startup (local tokenization,
        no external calls).
        """
        if not os.path.exists(self.trunk_index_dir):
            os.makedirs(self.trunk_index_dir)
        
        if index.exists_in(self.trunk_index_dir):
            self.trunk_ix = index.open_dir(self.trunk_index_dir)
            missing = set(self.trunk_schema.names()) - set(self.trunk_ix.schema.names())
            if missing:
                print(f"Trunk index schema is outdated (missing fields {sorted(missing)}), rebuilding...")
                self.trunk_ix.close()
                self.trunk_ix = index.create_in(self.trunk_index_dir, self.trunk_schema)
                self.trunk_index_stale = True
        else:
            self.trunk_ix = index.create_in(self.trunk_index_dir, self.trunk_schema)

    def _resolve_title(self, document_id: str) -> str:
        """Resolve the title of the memory a chunk belongs to; fall back to an empty title when the resolver is missing or the lookup fails."""
        if not self.title_resolver or not document_id:
            return ""
        try:
            return self.title_resolver(document_id) or ""
        except Exception as e:
            print(f"Failed to resolve document title ({document_id}): {e}")
            return ""
    
    def add_document(self, memory_id: str, title: str, content: str, 
                     tags: List[str], priority: int = 5, status: str = "active"):
        """Add a document to the index"""
        try:
            writer = AsyncWriter(self.ix)
            writer.update_document(
                id=memory_id,
                title=title,
                content=content,
                tags=" ".join(tags),
                priority=priority,
                status=status,
            )
            writer.commit()
            return True
        except Exception as e:
            print(f"Whoosh failed to add document: {e}")
            return False
    
    def update_document(self, memory_id: str, title: str, content: str,
                        tags: List[str], priority: int = 5, status: str = "active"):
        """Update a document"""
        return self.add_document(memory_id, title, content, tags, priority, status)
    
    def delete_document(self, memory_id: str):
        """Delete a document"""
        try:
            writer = AsyncWriter(self.ix)
            writer.delete_by_term("id", memory_id)
            writer.commit()
            return True
        except Exception as e:
            print(f"Whoosh failed to delete document: {e}")
            return False
    
    def search(self, query: str, limit: int = 20, 
               status: Optional[str] = "active") -> List[Tuple[str, float]]:
        """
        Search documents
        
        Args:
            query: Search query
            limit: Maximum number of results to return
            status: Status filter (None means no filtering)
        
        Returns:
            List of (memory_id, score) tuples
        """
        results = []
        
        try:
            with self.ix.searcher() as searcher:
                # Multi-field search
                parser = MultifieldParser(
                    ["title", "content", "tags"], 
                    schema=self.schema,
                    group=OrGroup  # Combine with OR
                )
                
                # Parse the query
                q = parser.parse(query)
                
                # Search
                hits = searcher.search(q, limit=limit * 2)  # Fetch extras, filtered below
                
                for hit in hits:
                    # Status filter
                    if status and hit.get("status") != status:
                        continue
                    
                    memory_id = hit["id"]
                    score = hit.score
                    results.append((memory_id, score))
                    
                    if len(results) >= limit:
                        break
        
        except Exception as e:
            print(f"Whoosh search failed: {e}")
        
        return results
    
    def rebuild_index(self, memories: List[dict]) -> int:
        """
        Rebuild the index
        
        Args:
            memories: List of memories; each item must contain id, title, content, tags, priority, status
        
        Returns:
            Number of indexed documents
        """
        try:
            # Clear and rebuild the index
            if os.path.exists(self.index_dir):
                import shutil
                shutil.rmtree(self.index_dir)
            
            os.makedirs(self.index_dir)
            self.ix = index.create_in(self.index_dir, self.schema)
            
            # Add documents in bulk
            writer = self.ix.writer()
            count = 0
            
            for mem in memories:
                if mem.get("status") == "active":
                    writer.add_document(
                        id=mem["id"],
                        title=mem.get("title", ""),
                        content=mem.get("content", ""),
                        tags=" ".join(mem.get("tags", [])),
                        priority=mem.get("priority", 5),
                        status=mem.get("status", "active"),
                    )
                    count += 1
            
            writer.commit()
            return count
        
        except Exception as e:
            print(f"Whoosh failed to rebuild the index: {e}")
            return 0
    
    def get_doc_count(self) -> int:
        """Get the number of indexed documents"""
        try:
            with self.ix.searcher() as searcher:
                return searcher.doc_count()
        except:
            return 0
    
    # ==================== Trunk index methods ====================
    
    def _ensure_trunk_index(self):
        """Ensure the trunk index exists, recreating it if missing"""
        if not os.path.exists(self.trunk_index_dir) or not index.exists_in(self.trunk_index_dir):
            print(f"Trunk index missing, rebuilding...")
            if not os.path.exists(self.trunk_index_dir):
                os.makedirs(self.trunk_index_dir)
            self.trunk_ix = index.create_in(self.trunk_index_dir, self.trunk_schema)
            print("✅ Trunk full-text index rebuilt")
    
    def add_trunk(self, trunk_id: str, document_id: str, content: str,
                  summary: str = "", tags: List[str] = None, 
                  order: int = 0, status: str = "ready", title: Optional[str] = None):
        """
        Add a trunk to the index.

        When title is omitted the owning memory's title is resolved automatically, so
        callers do not each have to query the database.
        """
        fields = dict(
            id=trunk_id,
            document_id=document_id,
            title=title if title is not None else self._resolve_title(document_id),
            content=content,
            summary=summary or "",
            tags=" ".join(tags or []),
            order=order,
            status=status,
        )
        
        try:
            # Make sure the index exists
            self._ensure_trunk_index()
            
            writer = AsyncWriter(self.trunk_ix)
            writer.update_document(**fields)
            writer.commit()
            return True
        except Exception as e:
            # If the error is a missing index, try to recover
            if "No such file or directory" in str(e) or "does not exist" in str(e).lower():
                try:
                    print(f"Detected a missing index, attempting recovery...")
                    self._ensure_trunk_index()
                    # Retry the add
                    writer = AsyncWriter(self.trunk_ix)
                    writer.update_document(**fields)
                    writer.commit()
                    print(f"✅ Recovery succeeded, trunk added to the index")
                    return True
                except Exception as retry_e:
                    print(f"Whoosh failed to add trunk (after recovery): {retry_e}")
                    return False
            print(f"Whoosh failed to add trunk: {e}")
            return False
    
    def update_trunk(self, trunk_id: str, document_id: str, content: str,
                     summary: str = "", tags: List[str] = None,
                     order: int = 0, status: str = "ready", title: Optional[str] = None):
        """Update the trunk index"""
        return self.add_trunk(trunk_id, document_id, content, summary, tags, order, status, title)
    
    def delete_trunk(self, trunk_id: str):
        """Delete a trunk from the index"""
        try:
            writer = AsyncWriter(self.trunk_ix)
            writer.delete_by_term("id", trunk_id)
            writer.commit()
            return True
        except Exception as e:
            print(f"Whoosh failed to delete trunk: {e}")
            return False
    
    def delete_trunks_by_document(self, document_id: str):
        """Delete the index entries of all trunks under a document"""
        try:
            writer = AsyncWriter(self.trunk_ix)
            writer.delete_by_term("document_id", document_id)
            writer.commit()
            return True
        except Exception as e:
            print(f"Whoosh failed to delete document trunks: {e}")
            return False
    
    def search_trunks(self, query: str, limit: int = 20,
                      status: Optional[str] = "ready") -> List[Tuple[str, float]]:
        """
        Search trunks
        
        Args:
            query: Search query
            limit: Maximum number of results to return
            status: Status filter (None means no filtering)
        
        Returns:
            List of (trunk_id, score) tuples
        """
        results = []
        
        try:
            with self.trunk_ix.searcher() as searcher:
                # Multi-field search: title, content, summary, tags
                # The title gets no extra boost: it repeats in every chunk of the same
                # memory, so boosting it would let title hits outrank body hits. The title
                # exists to make chunks recallable, not to drive ranking.
                parser = MultifieldParser(
                    self.TRUNK_SEARCH_FIELDS,
                    schema=self.trunk_schema,
                    group=OrGroup
                )
                
                q = parser.parse(query)
                hits = searcher.search(q, limit=limit * 2)
                
                for hit in hits:
                    if status and hit.get("status") != status:
                        continue
                    
                    trunk_id = hit["id"]
                    score = hit.score
                    results.append((trunk_id, score))
                    
                    if len(results) >= limit:
                        break
        
        except Exception as e:
            print(f"Whoosh trunk search failed: {e}")
        
        return results
    
    def rebuild_trunk_index(self, trunks: List[dict]) -> int:
        """
        Rebuild the trunk index
        
        Args:
            trunks: List of trunks; each item must contain id, document_id, content, summary, tags, order, status
        
        Returns:
            Number of indexed documents
        """
        try:
            # Clear and rebuild the index
            if os.path.exists(self.trunk_index_dir):
                import shutil
                shutil.rmtree(self.trunk_index_dir)
            
            os.makedirs(self.trunk_index_dir)
            self.trunk_ix = index.create_in(self.trunk_index_dir, self.trunk_schema)
            
            # Add documents in bulk
            writer = self.trunk_ix.writer()
            count = 0
            
            for trunk in trunks:
                if trunk.get("status") == "ready":
                    document_id = trunk.get("document_id", "")
                    writer.add_document(
                        id=trunk["id"],
                        document_id=document_id,
                        title=trunk.get("document_title") or self._resolve_title(document_id),
                        content=trunk.get("content", ""),
                        summary=trunk.get("summary", ""),
                        tags=" ".join(trunk.get("tags", [])),
                        order=trunk.get("order", 0),
                        status=trunk.get("status", "ready"),
                    )
                    count += 1
            
            writer.commit()
            self.trunk_index_stale = False
            return count
        
        except Exception as e:
            print(f"Whoosh failed to rebuild the trunk index: {e}")
            return 0
    
    def get_trunk_count(self) -> int:
        """Get the number of indexed trunks"""
        try:
            with self.trunk_ix.searcher() as searcher:
                return searcher.doc_count()
        except:
            return 0


# Global instance
_whoosh_search: Optional[WhooshSearch] = None


def get_whoosh_search(index_dir: str = None) -> Optional[WhooshSearch]:
    """Get the Whoosh search instance"""
    global _whoosh_search
    
    if _whoosh_search is None and index_dir:
        _whoosh_search = WhooshSearch(index_dir)
    
    return _whoosh_search


def init_whoosh_search(index_dir: str) -> WhooshSearch:
    """Initialize Whoosh search"""
    global _whoosh_search
    _whoosh_search = WhooshSearch(index_dir)
    return _whoosh_search
