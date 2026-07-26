"""
MCP Tool Functions

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import time
import functools
from datetime import datetime
from typing import Optional, List, Callable

from .sync import SyncManager, _run_background
from .search import SearchEngine


def log_performance(func):
    """Performance logging decorator"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.time()
        try:
            result = func(*args, **kwargs)
            elapsed = time.time() - start
            if elapsed > 0.5:  # Only log slow requests exceeding 0.5s
                print(f"[SLOW] {func.__name__} took {elapsed:.3f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start
            print(f"[ERROR] {func.__name__} failed, took {elapsed:.3f}s: {e}")
            raise
    return wrapper


class MemoryTools:
    """MCP Memory Toolkit"""
    
    def __init__(self, sync_manager: SyncManager, search_engine: SearchEngine):
        self.sync = sync_manager
        self.search = search_engine
        # Chunking callbacks (set externally to avoid circular dependencies)
        self._on_document_changed: Optional[Callable[[str], None]] = None
        self._on_document_updated: Optional[Callable[[str], None]] = None
    
    def set_chunking_callbacks(
        self,
        on_document_changed: Callable[[str], None],
        on_document_updated: Callable[[str], None]
    ):
        """
        Set chunking callbacks
        
        Args:
            on_document_changed: Callback when a document is added (triggers chunking)
            on_document_updated: Callback when document content is updated (triggers re-chunking)
        """
        self._on_document_changed = on_document_changed
        self._on_document_updated = on_document_updated
    
    @log_performance
    def add_memory(
        self,
        title: str,
        content: str,
        tags: Optional[List[str]] = None,
        priority: int = 5
    ) -> str:
        """
        Add a new memory
        
        Args:
            title: Memory title
            content: Memory content (supports Markdown format)
            tags: List of tags
            priority: Priority (1-10), default 5
        
        Returns:
            Operation result message
        """
        try:
            memory = self.sync.add_memory(
                title=title,
                content=content,
                tags=tags or [],
                priority=priority,
                source="api"
            )
            
            # Trigger chunking
            if self._on_document_changed:
                try:
                    self._on_document_changed(memory.id)
                except Exception as e:
                    print(f"[WARN] Chunking trigger failed: {e}")
            
            tags_str = ", ".join(memory.tags) if memory.tags else "none"
            return f"Added: {memory.title} | {memory.id}"
        except Exception as e:
            return f"Error: Failed to add memory - {str(e)}"
    
    @log_performance
    def update_memory(
        self,
        memory_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        priority: Optional[int] = None,
        status: Optional[str] = None
    ) -> str:
        """
        Update a memory
        
        Args:
            memory_id: Memory ID
            title: New title
            content: New content
            tags: New tag list
            priority: New priority
            status: New status (active/archived)
        
        Returns:
            Operation result message
        """
        try:
            # Check if content was updated (requires re-chunking)
            content_changed = content is not None
            
            memory = self.sync.update_memory(
                memory_id=memory_id,
                title=title,
                content=content,
                tags=tags,
                priority=priority,
                status=status
            )
            
            if memory:
                # If content changed, trigger re-chunking
                if content_changed and self._on_document_updated:
                    try:
                        self._on_document_updated(memory_id)
                    except Exception as e:
                        print(f"[WARN] Re-chunking trigger failed: {e}")
                
                return f"Updated: {memory.title} v{memory.version} | {memory.id}"
            else:
                return f"Error: Memory {memory_id} not found"
        except Exception as e:
            return f"Error: Failed to update memory - {str(e)}"
    
    @log_performance
    def delete_memory(self, memory_id: str) -> str:
        """
        Delete a memory (archive)
        
        Args:
            memory_id: Memory ID
        
        Returns:
            Operation result message
        """
        try:
            success = self.sync.delete_memory(memory_id, hard_delete=False)
            if success:
                return f"Archived: {memory_id}"
            else:
                return f"Error: Memory {memory_id} not found"
        except Exception as e:
            return f"Error: Failed to delete memory - {str(e)}"
    
    def get_memory(self, memory_id: str) -> str:
        """
        Get a single memory (returns trunk-level details)
        
        Args:
            memory_id: Memory ID
        
        Returns:
            Memory details, including all trunks
        """
        try:
            memory = self.sync.get_memory(memory_id)
            
            if not memory:
                return f"Error: Memory {memory_id} not found"
            
            tags_str = ", ".join(memory.tags) if memory.tags else ""
            
            lines = [
                f"📄 {memory.title}",
                f"ID: {memory.id} | P{memory.priority} | v{memory.version}",
            ]
            if tags_str:
                lines.append(f"Tags: {tags_str}")
            lines.append("")
            
            # Get trunk list
            trunks = self.sync.database.get_trunks_by_document(memory_id)
            
            if trunks:
                lines.append(f"{len(trunks)} trunks total:")
                lines.append("-" * 40)
                
                for trunk in trunks:
                    trunk_tags = f" [{', '.join(trunk.tags)}]" if trunk.tags else ""
                    status_icon = "✅" if trunk.status == "ready" else "⏳"
                    
                    lines.append(f"\n{status_icon} [Trunk {trunk.order + 1}]{trunk_tags}")
                    lines.append(f"trunk_id: {trunk.id}")
                    lines.append(f"{trunk.content}")
                    
                    # Show related trunks
                    if trunk.status == "ready" and self.search.vector_store:
                        try:
                            related = self.search.vector_store.find_related_trunks(
                                trunk_id=trunk.id, 
                                limit=2, 
                                current_document_id=memory_id
                            )
                            if related:
                                for rel_id, rel_score, is_same_doc in related:
                                    if is_same_doc:
                                        continue  # Skip same-document results
                                    rel_trunk = self.sync.database.get_trunk(rel_id)
                                    if rel_trunk:
                                        rel_memory = self.sync.get_memory(rel_trunk.document_id)
                                        rel_title = rel_memory.title if rel_memory else "Unknown"
                                        rel_preview = rel_trunk.content[:80].replace("\n", " ")
                                        if len(rel_trunk.content) > 80:
                                            rel_preview += "..."
                                        lines.append(f"   ↳ Related[{rel_title}]: {rel_preview}")
                        except Exception:
                            pass
            else:
                # If no trunks, show original content
                status_hint = ""
                if memory.trunk_status == "chunking":
                    status_hint = "\n⏳ Chunking in progress..."
                elif memory.trunk_status == "not_chunked":
                    status_hint = "\n⚠️ Not yet chunked"
                
                lines.append(f"Original content:{status_hint}")
                lines.append("-" * 40)
                lines.append(memory.content)
            
            # Next steps: document-level related memories
            try:
                related = self.search.get_related(memory_id, 3)
                if related:
                    lines.append("\n[Next Steps]")
                    rel_str = ", ".join(f"{r.memory.title}({r.memory.id})" for r in related)
                    lines.append(f"- Related memories: {rel_str}, use get_memory to expand")
            except Exception:
                pass

            return "\n".join(lines)
            
        except Exception as e:
            return f"Error: Failed to get memory - {str(e)}"

    # ==================== Next Step Hints (retrieval navigation for the Agent) ====================

    def _next_step_hints(self, trunk_results, seen_doc_ids) -> List[str]:
        """
        Generate retrieval navigation based on current results: semantically similar
        but not yet shown memory IDs and usable tags. Helps the Agent avoid guessing
        what to search next.
        """
        lines: List[str] = []
        try:
            # 1) Use vector neighbors of matched trunks to find related memories not yet shown
            related_docs: dict = {}
            if self.search.vector_store:
                for r in trunk_results[:3]:
                    trunk = r["trunk"]
                    try:
                        neighbors = self.search.vector_store.find_related_trunks(
                            trunk_id=trunk["id"], limit=3,
                            current_document_id=trunk["document_id"],
                        )
                    except Exception:
                        continue
                    for rel_id, _score, is_same_doc in neighbors:
                        if is_same_doc:
                            continue
                        rel_trunk = self.sync.database.get_trunk(rel_id)
                        if not rel_trunk or rel_trunk.document_id in seen_doc_ids \
                                or rel_trunk.document_id in related_docs:
                            continue
                        rel_memory = self.sync.get_memory(rel_trunk.document_id)
                        if rel_memory:
                            related_docs[rel_trunk.document_id] = rel_memory.title
                    if len(related_docs) >= 3:
                        break

            # 2) Tags from matched trunks (deduplicated in order of appearance)
            tags: List[str] = []
            for r in trunk_results:
                for tag in (r["trunk"].get("tags") or []):
                    if tag not in tags:
                        tags.append(tag)

            hints = []
            if related_docs:
                docs_str = ", ".join(
                    f"{title}({doc_id})" for doc_id, title in list(related_docs.items())[:3]
                )
                hints.append(f"- Semantically similar but not shown: {docs_str}, use get_memory to expand")
            if tags:
                hints.append(
                    f"- Tags from matched content: {', '.join(tags[:5])}, "
                    f"use list_memories_by_tag to broaden the search"
                )
            if len(seen_doc_ids) > 1:
                hints.append("- Results span multiple memories; use get_memory to read the full document if you need complete context")
            # Persistent reminder: a single search usually covers only one angle
            hints.append(
                "- A single search usually covers only one angle. Don't assume you've found everything: "
                "first assess whether these results suffice, then try different keywords / synonyms / "
                "related tags for more rounds until you have enough information"
            )
            lines.append("\n[Next Steps]")
            lines.extend(hints)
        except Exception:
            pass
        return lines

    def _no_result_hints(self, query: str) -> str:
        """Hints when search returns no results: guide the Agent on what to try next"""
        lines = [
            f"No memories found related to \"{query}\"",
            "",
            "[Next Steps]",
            "- Try shorter keywords or synonyms (long phrases dilute keyword weight; 2-6 word core terms recommended)",
        ]
        try:
            tags = self.sync.database.get_all_tags()
            if tags:
                # Tags are hierarchical (e.g. personal/habits/routine); listing all would
                # flood with siblings. Take one representative per top-level group to show
                # the overall distribution of the memory store
                picked, seen_roots = [], set()
                for tag in tags:
                    root = tag.split("/")[0]
                    if root not in seen_roots:
                        seen_roots.add(root)
                        picked.append(tag)
                    if len(picked) >= 10:
                        break
                lines.append(f"- Existing tag groups in memory store: {', '.join(picked)}, use list_memories_by_tag to browse")
        except Exception:
            pass
        lines.append("- Or use list_memories to browse recent memories by time")
        return "\n".join(lines)

    @log_performance
    def search_memories(
        self,
        query: str,
        limit: int = 10,
        min_score: Optional[float] = None
    ) -> str:
        """
        Search memories (returns trunk-level details)
        
        Returns matched trunk content directly; no need to call get_memory again.
        Note: a single search usually covers only one angle — try different keywords,
        synonyms, or related tags across multiple rounds until you have enough information.
        
        Args:
            query: Search keywords or natural language description
            limit: Maximum number of results
            min_score: Noise floor; leave empty to use the server-side adaptive recall strategy
        
        Returns:
            Matched trunk content (trunk level)
        """
        try:
            # Prefer trunk-level search
            trunk_result = self.search.search_trunks(
                query=query,
                mode="auto",
                limit=limit,
                min_score=min_score
            )
            
            trunk_results = trunk_result.get("results", [])
            
            if not trunk_results:
                return self._no_result_hints(query)
            
            lines = [f"Search results ({len(trunk_results)} related trunks)\n"]
            
            seen_docs = set()
            
            for i, r in enumerate(trunk_results, 1):
                trunk = r["trunk"]
                score = r["score"]
                doc_title = r.get("document_title", "Unknown")
                doc_id = trunk["document_id"]
                trunk_id = trunk["id"]
                
                # Show document title (on first occurrence)
                if doc_id not in seen_docs:
                    seen_docs.add(doc_id)
                    lines.append(f"\n📄 {doc_title} (doc: {doc_id})")
                    lines.append("-" * 40)
                
                # Show trunk content
                content = trunk["content"]
                tags = trunk.get("tags", [])
                tags_str = f" [{', '.join(tags)}]" if tags else ""
                
                lines.append(f"\n[Trunk {trunk['order']+1}]{tags_str} ({score:.0%})")
                lines.append(f"trunk_id: {trunk_id}")
                lines.append(f"{content}")
                
                # Get related trunks (expand for the first 2 results)
                if i <= 2 and self.search.vector_store:
                    try:
                        related = self.search.vector_store.find_related_trunks(
                            trunk_id=trunk_id, 
                            limit=2, 
                            current_document_id=doc_id
                        )
                        if related:
                            for rel_id, rel_score, is_same_doc in related:
                                rel_trunk = self.sync.database.get_trunk(rel_id)
                                if rel_trunk:
                                    rel_preview = rel_trunk.content[:100].replace("\n", " ")
                                    if len(rel_trunk.content) > 100:
                                        rel_preview += "..."
                                    same_doc_mark = "[same doc]" if is_same_doc else ""
                                    lines.append(f"   ↳ Related{same_doc_mark}: {rel_preview}")
                    except Exception:
                        pass
            
            lines.extend(self._next_step_hints(trunk_results, seen_docs))
            return "\n".join(lines)
            
        except Exception as e:
            return f"Error: Search failed - {str(e)}"
    
    @log_performance
    def list_memories(
        self,
        status: Optional[str] = None,
        source: Optional[str] = None,
        limit: int = 20
    ) -> str:
        """
        List memories
        
        Args:
            status: Status filter (active/archived)
            source: Source filter (api/user)
            limit: Maximum number of results
        
        Returns:
            Memory list
        """
        try:
            memories = self.sync.list_memories(
                status=status,
                source=source,
                limit=limit
            )
            
            if not memories:
                return "No memories yet"
            
            lines = [f"Memory list ({len(memories)} total)\n"]
            
            for memory in memories:
                tags_str = ", ".join(memory.tags) if memory.tags else ""
                tags_display = f" [{tags_str}]" if tags_str else ""
                
                lines.append(f"- {memory.title}{tags_display} P{memory.priority} | {memory.id}")
            
            return "\n".join(lines)
        except Exception as e:
            return f"Error: Failed to get list - {str(e)}"
    
    def list_memories_by_tag(
        self,
        tags: List[str],
        limit: int = 20
    ) -> str:
        """
        Filter memories by tags
        
        Args:
            tags: List of tags
            limit: Maximum number of results
        
        Returns:
            Memory list
        """
        try:
            memories = self.sync.list_memories(
                status="active",
                tags=tags,
                limit=limit
            )
            
            if not memories:
                return f"No memories found with tags {tags}"
            
            lines = [f"Tags {tags} ({len(memories)} total)\n"]
            
            for memory in memories:
                lines.append(f"- {memory.title} P{memory.priority} | {memory.id}")
            
            return "\n".join(lines)
        except Exception as e:
            return f"Error: Filter failed - {str(e)}"
    
    def get_related_memories(
        self,
        memory_id: str,
        limit: int = 5
    ) -> str:
        """
        Get related memories
        
        Args:
            memory_id: Memory ID
            limit: Maximum number of results
        
        Returns:
            Related memory list
        """
        try:
            # First get the original memory
            memory = self.sync.get_memory(memory_id)
            if not memory:
                return f"Error: Memory {memory_id} not found"
            
            # Get related memories
            related = self.search.get_related(memory_id, limit)
            
            if not related:
                return f"No memories found related to {memory.title}"
            
            lines = [f"Related memories ({len(related)} total)\n"]
            
            for r in related:
                lines.append(f"- {r.memory.title} {r.score:.0%} | {r.memory.id}")
            
            return "\n".join(lines)
        except Exception as e:
            return f"Error: Failed to get related memories - {str(e)}"
    
    def search_trunks(
        self,
        query: str,
        mode: str = "auto",
        limit: int = 10,
        min_score: Optional[float] = None
    ) -> str:
        """
        Search trunks (trunk-level search, finer granularity)
        
        Note: a single search usually covers only one angle — try different keywords,
        synonyms, or related tags across multiple rounds until you have enough information.
        
        Args:
            query: Search keywords or natural language description
            mode: Search mode (auto/keyword/semantic/hybrid)
            limit: Maximum number of results
            min_score: Noise floor; leave empty to use the server-side adaptive recall strategy
        
        Returns:
            Search results
        """
        try:
            result = self.search.search_trunks(
                query=query,
                mode=mode,
                limit=limit,
                min_score=min_score
            )
            
            results = result["results"]
            debug = result["debug"]
            
            if not results:
                return self._no_result_hints(query)
            
            lines = [f"Trunk search results ({len(results)} total, mode: {debug.get('mode', 'unknown')})\n"]
            
            for i, r in enumerate(results, 1):
                trunk = r["trunk"]
                score = r["score"]
                doc_title = r["document_title"]
                summary = trunk.get("summary") or trunk["content"][:60] + "..."
                tags = trunk.get("tags", [])
                tags_str = f" [{', '.join(tags)}]" if tags else ""
                
                lines.append(f"{i}. [{doc_title}] Trunk {trunk['order']+1}{tags_str} {score:.0%}")
                lines.append(f"   {summary}")
                lines.append(f"   trunk_id: {trunk['id']} | doc_id: {trunk['document_id']}")
                if i < len(results):
                    lines.append("")
            
            seen_docs = {r["trunk"]["document_id"] for r in results}
            lines.extend(self._next_step_hints(results, seen_docs))
            return "\n".join(lines)
        except Exception as e:
            return f"Error: Trunk search failed - {str(e)}"
    
    @log_performance
    def get_trunk(self, trunk_id: str) -> str:
        """
        Get details of a single trunk
        
        Args:
            trunk_id: Trunk ID
        
        Returns:
            Trunk details
        """
        try:
            trunk = self.sync.database.get_trunk(trunk_id)
            
            if not trunk:
                return f"Error: Trunk {trunk_id} not found"
            
            # Get parent document
            memory = self.sync.get_memory(trunk.document_id)
            doc_title = memory.title if memory else "Unknown document"
            
            tags_str = ", ".join(trunk.tags) if trunk.tags else "none"
            summary = trunk.summary or "No summary"
            
            return (
                f"Trunk Details\n\n"
                f"Parent document: {doc_title}\n"
                f"Trunk order: {trunk.order + 1}\n"
                f"Trunk ID: {trunk.id}\n"
                f"Document ID: {trunk.document_id}\n"
                f"Status: {trunk.status}\n"
                f"Tags: {tags_str}\n"
                f"Summary: {summary}\n\n"
                f"--- Content ---\n{trunk.content}"
            )
        except Exception as e:
            return f"Error: Failed to get trunk - {str(e)}"
    
    def get_related_trunks(
        self,
        trunk_id: str,
        limit: int = 5
    ) -> str:
        """
        Get related trunks
        
        Args:
            trunk_id: Trunk ID
            limit: Maximum number of results
        
        Returns:
            Related trunk list
        """
        try:
            # First get the original trunk
            trunk = self.sync.database.get_trunk(trunk_id)
            if not trunk:
                return f"Error: Trunk {trunk_id} not found"
            
            # Get parent document
            memory = self.sync.get_memory(trunk.document_id)
            doc_title = memory.title if memory else "Unknown document"
            
            # Get related trunks
            if not self.search.vector_store:
                return "Error: Semantic search is not enabled"
            
            related = self.search.vector_store.find_related_trunks(
                trunk_id=trunk_id,
                limit=limit,
                current_document_id=trunk.document_id
            )
            
            if not related:
                return f"No related trunks found"
            
            lines = [f"Related trunks (source: {doc_title} Trunk {trunk.order+1})\n"]
            
            for rel_trunk_id, score, is_same_doc in related:
                rel_trunk = self.sync.database.get_trunk(rel_trunk_id)
                if not rel_trunk:
                    continue
                
                rel_memory = self.sync.get_memory(rel_trunk.document_id)
                rel_doc_title = rel_memory.title if rel_memory else "Unknown"
                
                same_doc_mark = "[same doc]" if is_same_doc else ""
                summary = rel_trunk.summary or rel_trunk.content[:50] + "..."
                
                lines.append(f"- {same_doc_mark}[{rel_doc_title}] Trunk {rel_trunk.order+1} {score:.0%}")
                lines.append(f"  {summary}")
                lines.append(f"  trunk_id: {rel_trunk_id}")
            
            return "\n".join(lines)
        except Exception as e:
            return f"Error: Failed to get related trunks - {str(e)}"
    
    def get_stats(self) -> str:
        """
        Get statistics
        
        Returns:
            Statistics
        """
        try:
            stats = self.sync.get_stats()
            
            tags_str = ", ".join(stats.get("tags", [])[:10])
            if len(stats.get("tags", [])) > 10:
                tags_str += f" ... {len(stats['tags'])} total"
            
            lines = [
                "Memory Statistics\n",
                f"Total memories: {stats['total']}",
                f"Active memories: {stats['active']}",
                f"Archived: {stats['archived']}",
                f"API created: {stats['api_count']}",
                f"User created: {stats['user_count']}",
            ]
            
            if "vector_count" in stats:
                lines.append(f"Vector index: {stats['vector_count']}")
            
            if tags_str:
                lines.append(f"\nCommon tags: {tags_str}")
            
            return "\n".join(lines)
        except Exception as e:
            return f"Error: Failed to get statistics - {str(e)}"
    
    def patch_memory(
        self,
        memory_id: str,
        old_text: str,
        new_text: str
    ) -> str:
        """
        Partially modify memory content (text match and replace)
        
        Args:
            memory_id: Memory ID
            old_text: Original text to replace (must match uniquely)
            new_text: Replacement text
        
        Returns:
            Operation result message
        """
        try:
            # 1. Get memory
            memory = self.sync.get_memory(memory_id)
            if not memory:
                return (
                    f"❌ Memory {memory_id} not found\n\n"
                    f"💡 Suggestion: Use search_memories or list_memories to find the correct ID"
                )
            
            content = memory.content
            
            # 2. Check match status
            count = content.count(old_text)
            
            if count == 0:
                # No match found
                # Try fuzzy hints
                lines = content.split("\n")
                preview_lines = lines[:10] if len(lines) > 10 else lines
                preview = "\n".join(preview_lines)
                if len(lines) > 10:
                    preview += f"\n... ({len(lines)} lines total)"
                
                return (
                    f"❌ No matching content found\n\n"
                    f"💡 Possible reasons:\n"
                    f"  1. Content has been modified\n"
                    f"  2. Whitespace/newline mismatch\n"
                    f"  3. Special character differences\n\n"
                    f"💡 Suggestion: Use get_memory to fetch the latest content\n\n"
                    f"Current content preview:\n{preview}"
                )
            
            if count > 1:
                # Multiple matches found
                # Find all match positions
                matches = []
                start = 0
                while True:
                    pos = content.find(old_text, start)
                    if pos == -1:
                        break
                    
                    # Calculate line number
                    line_num = content[:pos].count("\n") + 1
                    
                    # Get context (20 characters before and after)
                    ctx_start = max(0, pos - 20)
                    ctx_end = min(len(content), pos + len(old_text) + 20)
                    context = content[ctx_start:ctx_end].replace("\n", "↵")
                    
                    # Mark match position
                    if ctx_start > 0:
                        context = "..." + context
                    if ctx_end < len(content):
                        context = context + "..."
                    
                    matches.append(f"  - Line {line_num}: \"{context}\"")
                    start = pos + 1
                
                matches_str = "\n".join(matches[:5])
                if len(matches) > 5:
                    matches_str += f"\n  ... {len(matches)} total"
                
                return (
                    f"❌ Found {count} matches, cannot determine which to replace\n\n"
                    f"Match positions:\n{matches_str}\n\n"
                    f"💡 Suggestion: Expand old_text to include more context for a unique match\n"
                    f"   For example, include a few lines before and after"
                )
            
            # 3. Unique match, perform replacement
            new_content = content.replace(old_text, new_text, 1)
            
            # 4. Update memory
            updated = self.sync.update_memory(
                memory_id=memory_id,
                content=new_content
            )
            
            if updated:
                # Generate replacement preview
                pos = content.find(old_text)
                line_num = content[:pos].count("\n") + 1
                
                # Get context after replacement
                new_pos = new_content.find(new_text)
                ctx_start = max(0, new_pos - 30)
                ctx_end = min(len(new_content), new_pos + len(new_text) + 30)
                preview = new_content[ctx_start:ctx_end].replace("\n", "↵")
                if ctx_start > 0:
                    preview = "..." + preview
                if ctx_end < len(new_content):
                    preview = preview + "..."
                
                return (
                    f"✅ Replaced (line {line_num})\n\n"
                    f"Memory: {updated.title} v{updated.version}\n"
                    f"ID: {updated.id}\n\n"
                    f"After replacement: {preview}"
                )
            else:
                return f"❌ Update failed"
                
        except Exception as e:
            return f"❌ patch_memory failed: {str(e)}"
    
    def quick_match(self, text: str, top_k: int = 6) -> str:
        """
        Quick match memories (recommended: use first for initial conversations or short user input)
        
        Returns trunk-level details, auto-expands on high-confidence matches
        
        Args:
            text: Short text (keywords, questions, etc.)
            top_k: Number of most relevant trunks to return, default 6
        
        Returns:
            Most relevant trunk content, auto-expands details on high-confidence matches
        """
        try:
            # Current time
            now = datetime.now()
            time_str = now.strftime("%Y-%m-%d %H:%M %A")
            
            # Clean input
            text = text.strip()
            if not text:
                return f"Current time: {time_str}\n\nPlease provide search content"
            
            # Clamp top_k range
            top_k = max(1, min(top_k, 10))
            
            # If input looks like a memory_id, fetch directly
            if text.startswith("mem_") and len(text) == 12:
                return f"Current time: {time_str}\n\n" + self.get_memory(text)
            
            # If input looks like a trunk_id, fetch directly
            if text.startswith("trunk_"):
                return f"Current time: {time_str}\n\n" + self.get_trunk(text)
            
            # Use trunk-level search (relevance is handled by server-side adaptive recall, no hardcoded thresholds on the caller side)
            trunk_result = self.search.search_trunks(
                query=text,
                mode="auto",
                limit=top_k
            )
            
            trunk_results = trunk_result.get("results", [])
            
            if not trunk_results:
                return f"Current time: {time_str}\n\n" + self._no_result_hints(text)
            
            # Check for high-confidence matches (>0.7)
            top_result = trunk_results[0]
            top_score = top_result["score"]
            
            lines = [f"Current time: {time_str}\n"]
            
            if top_score >= 0.7:
                # High-confidence match: expand details
                lines.append(f"[High match {top_score:.0%}]\n")
            else:
                lines.append(f"[Related trunks: {len(trunk_results)}]\n")
            
            seen_docs = set()
            
            for i, r in enumerate(trunk_results, 1):
                trunk = r["trunk"]
                score = r["score"]
                doc_title = r.get("document_title", "Unknown")
                doc_id = trunk["document_id"]
                trunk_id = trunk["id"]
                
                # Show document title (on first occurrence)
                if doc_id not in seen_docs:
                    seen_docs.add(doc_id)
                    lines.append(f"\n📄 {doc_title}")
                
                # Show trunk content
                content = trunk["content"]
                tags = trunk.get("tags", [])
                tags_str = f" [{', '.join(tags[:3])}]" if tags else ""
                
                lines.append(f"\n[Trunk {trunk['order']+1}]{tags_str} ({score:.0%})")
                lines.append(f"trunk_id: {trunk_id}")
                
                # High-confidence match: expand all; normal match: show first 200 chars
                if top_score >= 0.7 or i == 1:
                    lines.append(f"{content}")
                else:
                    preview = content[:200].replace("\n", " ")
                    if len(content) > 200:
                        preview += "..."
                    lines.append(f"{preview}")
                
                # Get related trunks (first result only)
                if i == 1 and self.search.vector_store:
                    try:
                        related = self.search.vector_store.find_related_trunks(
                            trunk_id=trunk_id, 
                            limit=2, 
                            current_document_id=doc_id
                        )
                        if related:
                            for rel_id, rel_score, is_same_doc in related:
                                rel_trunk = self.sync.database.get_trunk(rel_id)
                                if rel_trunk:
                                    rel_preview = rel_trunk.content[:80].replace("\n", " ")
                                    if len(rel_trunk.content) > 80:
                                        rel_preview += "..."
                                    same_doc_mark = "[same doc]" if is_same_doc else ""
                                    lines.append(f"   ↳ Related{same_doc_mark}: {rel_preview}")
                    except Exception:
                        pass
            
            lines.extend(self._next_step_hints(trunk_results, seen_docs))
            return "\n".join(lines)
            
        except Exception as e:
            return f"Error: Quick match failed - {str(e)}"
    
    # ==================== Trunk-level Update Tools ====================
    
    def get_memory_trunks(self, memory_id: str) -> str:
        """
        Get all trunks of a document
        
        Args:
            memory_id: Memory ID
        
        Returns:
            Trunk list
        """
        try:
            memory = self.sync.get_memory(memory_id)
            if not memory:
                return f"❌ Memory {memory_id} not found"
            
            trunks = self.sync.database.get_trunks_by_document(memory_id)
            
            if not trunks:
                return (
                    f"📄 {memory.title}\n"
                    f"ID: {memory_id}\n\n"
                    f"⚠️ This document has not been chunked yet, or chunking is in progress\n"
                    f"Status: {memory.trunk_status}"
                )
            
            lines = [
                f"📄 {memory.title}",
                f"ID: {memory_id}",
                f"{len(trunks)} trunks total\n",
            ]
            
            for trunk in trunks:
                summary = trunk.summary or trunk.content[:60].replace("\n", " ") + "..."
                tags_str = f" [{', '.join(trunk.tags)}]" if trunk.tags else ""
                status_icon = "✅" if trunk.status == "ready" else "⏳"
                
                lines.append(f"{status_icon} Trunk {trunk.order + 1}{tags_str}")
                lines.append(f"   {summary}")
                lines.append(f"   trunk_id: {trunk.id}")
                lines.append("")
            
            return "\n".join(lines)
            
        except Exception as e:
            return f"❌ Failed to get trunk list: {str(e)}"
    
    def update_trunk(
        self,
        trunk_id: str,
        content: Optional[str] = None,
        summary: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> str:
        """
        Update a single trunk
        
        Args:
            trunk_id: Trunk ID (starts with trunk_)
            content: New trunk content
            summary: New summary
            tags: New tag list
        
        Returns:
            Operation result message
        """
        try:
            trunk = self.sync.database.get_trunk(trunk_id)
            if not trunk:
                return f"❌ Trunk {trunk_id} not found"
            
            # Update fields
            if content is not None:
                trunk.content = content
            if summary is not None:
                trunk.summary = summary
            if tags is not None:
                trunk.tags = tags
            
            # Save updates
            self.sync.database.update_trunk(trunk)
            
            # Update vector (run in background, don't block MCP response)
            if content is not None and self.search.vector_store:
                import copy
                trunk_copy = copy.deepcopy(trunk)
                _run_background(self.search.vector_store.update_trunk, trunk_copy)
            
            # Sync to Whoosh (update_trunk accepts scalar fields, consistent with task_queue call pattern)
            if self.sync.database.whoosh_search:
                try:
                    self.sync.database.whoosh_search.update_trunk(
                        trunk_id=trunk.id,
                        document_id=trunk.document_id,
                        content=trunk.content,
                        summary=trunk.summary or "",
                        tags=trunk.tags,
                        order=trunk.order,
                        status=trunk.status or "ready",
                    )
                except Exception:
                    import traceback
                    print(f"[WARN] Failed to update Whoosh index trunk={trunk.id}:\n{traceback.format_exc()}")
            
            # Get parent document
            memory = self.sync.get_memory(trunk.document_id)
            doc_title = memory.title if memory else "Unknown document"
            
            return (
                f"✅ Trunk updated\n\n"
                f"Parent document: {doc_title}\n"
                f"Trunk order: {trunk.order + 1}\n"
                f"Trunk ID: {trunk.id}\n"
                f"Summary: {trunk.summary or 'none'}"
            )
            
        except Exception as e:
            return f"❌ Failed to update trunk: {str(e)}"
    
    def patch_trunk(
        self,
        trunk_id: str,
        old_text: str,
        new_text: str
    ) -> str:
        """
        Partially modify trunk content (text match and replace)
        
        Args:
            trunk_id: Trunk ID (starts with trunk_)
            old_text: Original text to replace (must match uniquely)
            new_text: Replacement text
        
        Returns:
            Operation result message
        """
        try:
            trunk = self.sync.database.get_trunk(trunk_id)
            if not trunk:
                return f"❌ Trunk {trunk_id} not found"
            
            content = trunk.content
            count = content.count(old_text)
            
            if count == 0:
                preview = content[:200].replace("\n", "↵")
                if len(content) > 200:
                    preview += "..."
                return (
                    f"❌ No matching content found\n\n"
                    f"💡 Suggestion: Use get_trunk to fetch the latest content\n\n"
                    f"Current content preview:\n{preview}"
                )
            
            if count > 1:
                return (
                    f"❌ Found {count} matches, cannot determine which to replace\n\n"
                    f"💡 Suggestion: Expand old_text to include more context for a unique match"
                )
            
            # Perform replacement
            new_content = content.replace(old_text, new_text, 1)
            trunk.content = new_content
            
            # Save updates
            self.sync.database.update_trunk(trunk)
            
            # Update vector (run in background, don't block MCP response)
            if self.search.vector_store:
                import copy
                trunk_copy = copy.deepcopy(trunk)
                _run_background(self.search.vector_store.update_trunk, trunk_copy)
            
            # Sync to Whoosh (update_trunk accepts scalar fields, consistent with task_queue call pattern)
            if self.sync.database.whoosh_search:
                try:
                    self.sync.database.whoosh_search.update_trunk(
                        trunk_id=trunk.id,
                        document_id=trunk.document_id,
                        content=trunk.content,
                        summary=trunk.summary or "",
                        tags=trunk.tags,
                        order=trunk.order,
                        status=trunk.status or "ready",
                    )
                except Exception:
                    import traceback
                    print(f"[WARN] Failed to update Whoosh index trunk={trunk.id}:\n{traceback.format_exc()}")
            
            # Also update parent document content
            memory = self.sync.get_memory(trunk.document_id)
            if memory:
                # Get all trunks to reassemble content
                all_trunks = self.sync.database.get_trunks_by_document(trunk.document_id)
                new_doc_content = "\n\n".join([t.content for t in sorted(all_trunks, key=lambda x: x.order)])
                self.sync.update_memory(trunk.document_id, content=new_doc_content)
            
            # Get parent document
            doc_title = memory.title if memory else "Unknown document"
            pos = content.find(old_text)
            line_num = content[:pos].count("\n") + 1
            
            return (
                f"✅ Trunk updated (line {line_num})\n\n"
                f"Parent document: {doc_title}\n"
                f"Trunk order: {trunk.order + 1}\n"
                f"Trunk ID: {trunk.id}"
            )
            
        except Exception as e:
            return f"❌ Failed to patch trunk: {str(e)}"

