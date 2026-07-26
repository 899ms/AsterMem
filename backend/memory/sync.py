"""
Data sync manager

Handles synchronization across MD files, SQLite, and Chroma

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import threading
import copy
from typing import Optional, List, Dict, Any
from datetime import datetime

from .models import Memory, generate_memory_id
from .database import Database
from .storage import MemoryStorage
from .vector import VectorStore


def _run_background(func, *args, **kwargs):
    """Run in a background thread without blocking the caller"""
    def _task():
        try:
            func(*args, **kwargs)
        except Exception as e:
            print(f"[WARN] Background task failed ({func.__name__}): {e}")
    t = threading.Thread(target=_task, daemon=True)
    t.start()


class SyncManager:
    """Sync manager"""
    
    def __init__(
        self,
        database: Database,
        storage: MemoryStorage,
        vector_store: Optional[VectorStore] = None
    ):
        self.database = database
        self.storage = storage
        self.vector_store = vector_store
    
    def add_memory(
        self,
        title: str,
        content: str,
        tags: Optional[List[str]] = None,
        priority: int = 5,
        source: str = "api"
    ) -> Memory:
        """Add memory (synced to all stores)"""
        # Create memory object
        memory = Memory(
            id=generate_memory_id(),
            title=title,
            content=content,
            tags=tags or [],
            priority=priority,
            source=source,
        )
        
        # 1. Save to MD file
        self.storage.save_memory(memory)
        
        # 2. Save to SQLite
        self.database.add_memory(memory)
        
        # 3. Add to vector store (background, non-blocking for MCP response)
        if self.vector_store:
            mem_copy = copy.deepcopy(memory)
            _run_background(self.vector_store.add_memory, mem_copy)
        
        # 4. Sync to Whoosh search index
        self.database.sync_to_whoosh(memory)
        
        return memory
    
    def update_memory(
        self,
        memory_id: str,
        title: Optional[str] = None,
        content: Optional[str] = None,
        tags: Optional[List[str]] = None,
        priority: Optional[int] = None,
        status: Optional[str] = None
    ) -> Optional[Memory]:
        """Update memory"""
        # Get existing memory
        memory = self.database.get_memory(memory_id)
        if not memory:
            return None
        
        # Update fields
        if title is not None:
            memory.title = title
        if content is not None:
            memory.content = content
        if tags is not None:
            memory.tags = tags
        if priority is not None:
            memory.priority = priority
        if status is not None:
            memory.status = status
        
        # 1. Update SQLite (saves history)
        memory = self.database.update_memory(memory)
        
        # 2. Update MD file
        self.storage.save_memory(memory)
        
        # 3. Update vector store (background, non-blocking for MCP response)
        if self.vector_store:
            mem_copy = copy.deepcopy(memory)
            _run_background(self.vector_store.update_memory, mem_copy)
        
        # 4. Sync to Whoosh search index
        self.database.sync_to_whoosh(memory)
        
        return memory
    
    def delete_memory(self, memory_id: str, hard_delete: bool = False) -> bool:
        """Delete memory"""
        memory = self.database.get_memory(memory_id)
        if not memory:
            return False
        
        if hard_delete:
            # Hard delete: remove data from all stores
            self.storage.delete_memory(memory)
            if self.vector_store:
                self.vector_store.delete_memory(memory_id)
            # Delete from Whoosh index
            self.database.delete_from_whoosh(memory_id)
        
        # Delete from database (soft or hard delete)
        return self.database.delete_memory(memory_id, hard_delete)
    
    def get_memory(self, memory_id: str) -> Optional[Memory]:
        """Get memory"""
        return self.database.get_memory(memory_id)
    
    def list_memories(
        self,
        status: Optional[str] = None,
        source: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Memory]:
        """List memories"""
        return self.database.list_memories(status, source, tags, limit, offset)
    
    def sync_user_files(self) -> Dict[str, Any]:
        """Sync user directory MD files to the database"""
        result = {
            "added": 0,
            "updated": 0,
            "errors": []
        }
        
        # Scan user directory
        user_memories = self.storage.scan_user_memories()
        
        for memory in user_memories:
            try:
                # Check if it exists in the database
                existing = self.database.get_memory(memory.id)
                
                if existing:
                    # Compare update times; update database if file is newer
                    if memory.updated_at and existing.updated_at:
                        if memory.updated_at > existing.updated_at:
                            # Update
                            existing.title = memory.title
                            existing.content = memory.content
                            existing.tags = memory.tags
                            existing.priority = memory.priority
                            self.database.update_memory(existing, save_history=True)
                            if self.vector_store:
                                self.vector_store.update_memory(existing)
                            result["updated"] += 1
                else:
                    # Add new
                    self.database.add_memory(memory)
                    if self.vector_store:
                        self.vector_store.add_memory(memory)
                    result["added"] += 1
                    
            except Exception as e:
                result["errors"].append(f"{memory.id}: {str(e)}")
        
        return result
    
    def full_sync(self) -> Dict[str, Any]:
        """Full sync: scan all MD files and update the database"""
        result = {
            "scanned": 0,
            "added": 0,
            "updated": 0,
            "errors": []
        }
        
        # Scan all MD files
        all_memories = self.storage.scan_all_memories()
        result["scanned"] = len(all_memories)
        
        for memory in all_memories:
            try:
                existing = self.database.get_memory(memory.id)
                
                if existing:
                    # Update
                    existing.title = memory.title
                    existing.content = memory.content
                    existing.tags = memory.tags
                    existing.priority = memory.priority
                    self.database.update_memory(existing, save_history=False)
                    result["updated"] += 1
                else:
                    # Add new
                    self.database.add_memory(memory)
                    result["added"] += 1
                    
            except Exception as e:
                result["errors"].append(f"{memory.id}: {str(e)}")
        
        # Rebuild vector index
        if self.vector_store:
            active_memories = self.database.list_memories(status="active", limit=10000)
            vector_count = self.vector_store.rebuild_index(active_memories)
            result["vector_indexed"] = vector_count
        
        # Rebuild Whoosh index
        whoosh_count = self.database.rebuild_whoosh_index()
        if whoosh_count > 0:
            result["whoosh_indexed"] = whoosh_count
        
        return result
    
    def export_memories(
        self,
        export_path: str,
        status: Optional[str] = None,
        tags: Optional[List[str]] = None
    ) -> str:
        """Export memories"""
        memories = self.database.list_memories(status=status, tags=tags, limit=10000)
        return self.storage.export_memories(memories, export_path)
    
    def import_memories(self, import_path: str) -> Dict[str, Any]:
        """Import memories"""
        result = {
            "imported": 0,
            "errors": []
        }
        
        try:
            memories = self.storage.import_memories(import_path)
            
            for memory in memories:
                try:
                    # Save to all stores
                    self.storage.save_memory(memory)
                    self.database.add_memory(memory)
                    if self.vector_store:
                        self.vector_store.add_memory(memory)
                    result["imported"] += 1
                except Exception as e:
                    result["errors"].append(f"{memory.id}: {str(e)}")
                    
        except Exception as e:
            result["errors"].append(f"Import failed: {str(e)}")
        
        return result
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics"""
        stats = self.database.get_stats()
        stats["tags"] = self.database.get_all_tags()
        
        if self.vector_store:
            stats["vector_count"] = self.vector_store.get_count()
        
        return stats

