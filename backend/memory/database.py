"""
SQLite database operations

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import sqlite3
import json
import os
from datetime import datetime
from typing import Optional, List
from contextlib import contextmanager

from .models import Memory, MemoryHistory, Trunk

# Whoosh search (optional)
try:
    from .whoosh_search import WhooshSearch
    WHOOSH_AVAILABLE = True
except ImportError:
    WHOOSH_AVAILABLE = False
    WhooshSearch = None


class Database:
    """SQLite database manager"""
    
    def __init__(self, db_path: str, whoosh_dir: str = None):
        self.db_path = db_path
        self.whoosh_search = None
        
        # Ensure directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()
        
        # Initialize Whoosh search
        if WHOOSH_AVAILABLE and whoosh_dir:
            try:
                self.whoosh_search = WhooshSearch(whoosh_dir, title_resolver=self.get_document_title)
                print("✅ Whoosh full-text search enabled")
                if self.whoosh_search.trunk_index_stale:
                    self._refill_trunk_index()
            except Exception as e:
                print(f"⚠️ Whoosh initialization failed: {e}")
    
    def get_document_title(self, document_id: str) -> str:
        """Get the title of the memory a chunk belongs to; the index layer uses this to add document context back to chunk-level retrieval."""
        memory = self.get_memory(document_id)
        return memory.title if memory else ""
    
    def _refill_trunk_index(self) -> None:
        """
        Refill trunk index after schema upgrade.

        The full-text index uses local tokenization, so refilling makes no external calls
        and can be done automatically at startup without requiring the user to manually
        trigger a rebuild from the settings page. Titles are batch-fetched to avoid per-row queries.
        """
        try:
            trunks = self.get_all_trunks(status="ready", limit=1000000)
            if not trunks:
                self.whoosh_search.trunk_index_stale = False
                return
            
            titles = {m.id: m.title for m in self.list_memories(limit=1000000)}
            payload = []
            for trunk in trunks:
                data = trunk.to_dict()
                data["document_title"] = titles.get(trunk.document_id, "")
                payload.append(data)
            
            count = self.whoosh_search.rebuild_trunk_index(payload)
            print(f"✅ Trunk full-text index rebuilt ({count} chunks)")
        except Exception as e:
            print(f"⚠️ Trunk full-text index rebuild failed: {e}")
    
    @contextmanager
    def get_connection(self):
        """Get a database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    def _init_db(self):
        """Initialize database tables"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Memories main table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    tags TEXT,
                    priority INTEGER DEFAULT 5,
                    version INTEGER DEFAULT 1,
                    source TEXT DEFAULT 'api',
                    status TEXT DEFAULT 'active',
                    file_path TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    trunk_ids TEXT,
                    trunk_status TEXT DEFAULT 'not_chunked'
                )
            """)
            
            # History versions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS memory_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    title TEXT,
                    content TEXT,
                    tags TEXT,
                    priority INTEGER,
                    changed_at TEXT,
                    FOREIGN KEY (memory_id) REFERENCES memories(id)
                )
            """)
            
            # Trunks table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trunks (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    "order" INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    tags TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT,
                    updated_at TEXT,
                    FOREIGN KEY (document_id) REFERENCES memories(id)
                )
            """)
            
            # Create indices
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_status 
                ON memories(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_source 
                ON memories(source)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_memory_history_memory_id 
                ON memory_history(memory_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trunks_document 
                ON trunks(document_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trunks_status 
                ON trunks(status)
            """)
            
            # Meta tags index table (supports fast filtering and association lookups)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chunk_meta_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chunk_id TEXT NOT NULL,
                    tag_type TEXT NOT NULL,
                    tag_value TEXT NOT NULL,
                    confidence REAL DEFAULT 1.0,
                    created_at TEXT,
                    FOREIGN KEY (chunk_id) REFERENCES trunks(id)
                )
            """)
            
            # Meta tags indices
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meta_tags_type_value 
                ON chunk_meta_tags(tag_type, tag_value)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_meta_tags_chunk_id 
                ON chunk_meta_tags(chunk_id)
            """)
            
            # ========== Entities table ==========
            # Stores entities extracted from trunks (people, places, organizations, concepts, etc.)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    normalized_name TEXT,
                    description TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    mention_count INTEGER DEFAULT 1,
                    UNIQUE(name, entity_type)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entities_type 
                ON entities(entity_type)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entities_name 
                ON entities(name)
            """)
            
            # ========== Entity-Trunk links table ==========
            # Records which trunks mention which entities
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS entity_trunk_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id INTEGER NOT NULL,
                    trunk_id TEXT NOT NULL,
                    context TEXT,
                    role TEXT,
                    created_at TEXT,
                    FOREIGN KEY (entity_id) REFERENCES entities(id),
                    FOREIGN KEY (trunk_id) REFERENCES trunks(id),
                    UNIQUE(entity_id, trunk_id)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_trunk_entity 
                ON entity_trunk_links(entity_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_trunk_trunk 
                ON entity_trunk_links(trunk_id)
            """)
            
            # ========== Entity relations table (knowledge graph triples) ==========
            # Stores (subject, relation, object) triples
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS entity_relations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    subject_id INTEGER NOT NULL,
                    relation_type TEXT NOT NULL,
                    object_id INTEGER NOT NULL,
                    source_trunk_id TEXT,
                    confidence REAL DEFAULT 1.0,
                    created_at TEXT,
                    FOREIGN KEY (subject_id) REFERENCES entities(id),
                    FOREIGN KEY (object_id) REFERENCES entities(id),
                    FOREIGN KEY (source_trunk_id) REFERENCES trunks(id)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_relations_subject 
                ON entity_relations(subject_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_entity_relations_object 
                ON entity_relations(object_id)
            """)
            
            # ========== Timeline index table ==========
            # Records temporal information for trunks, used for timeline queries
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trunk_timeline (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trunk_id TEXT NOT NULL,
                    time_type TEXT NOT NULL,
                    time_value TEXT NOT NULL,
                    time_normalized TEXT,
                    time_precision TEXT,
                    created_at TEXT,
                    FOREIGN KEY (trunk_id) REFERENCES trunks(id)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trunk_timeline_trunk 
                ON trunk_timeline(trunk_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_trunk_timeline_normalized 
                ON trunk_timeline(time_normalized)
            """)
            
            # ========== Time events table (full timeline feature) ==========
            # Stores time events extracted from trunks, supports calendar/todo/timeline views
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS time_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trunk_id TEXT NOT NULL,
                    
                    -- Original text info
                    original_text TEXT NOT NULL,
                    text_start INTEGER,
                    text_end INTEGER,
                    
                    -- Event description
                    event_summary TEXT,
                    
                    -- Time info
                    absolute_time TEXT NOT NULL,
                    time_precision TEXT DEFAULT 'day',
                    is_range INTEGER DEFAULT 0,
                    range_end TEXT,
                    
                    -- Event status
                    event_type TEXT DEFAULT 'todo',
                    status TEXT DEFAULT 'pending',
                    completed_at TEXT,
                    
                    -- Metadata
                    source_type TEXT DEFAULT 'text',
                    anchor_time TEXT,
                    created_at TEXT,
                    updated_at TEXT,
                    
                    FOREIGN KEY (trunk_id) REFERENCES trunks(id)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_time_events_trunk 
                ON time_events(trunk_id)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_time_events_absolute_time 
                ON time_events(absolute_time)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_time_events_status 
                ON time_events(status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_time_events_type 
                ON time_events(event_type)
            """)
            
            # ========== User profile layer (P1-0) ==========
            # Profile versions: Dream produces candidate versions, only active after activation; active version is immutable during dreams
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT DEFAULT 'candidate',
                    origin TEXT NOT NULL,
                    dream_id INTEGER,
                    created_at TEXT,
                    activated_at TEXT
                )
            """)
            # Claims table: sources is a JSON array; source_kind determines whether sources point to memories or lower-level claims
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version_id INTEGER NOT NULL,
                    tier TEXT NOT NULL,
                    text TEXT NOT NULL,
                    sources TEXT NOT NULL,
                    source_kind TEXT DEFAULT 'memory',
                    confidence REAL DEFAULT 1.0,
                    status TEXT DEFAULT 'active',
                    cloned_from INTEGER,
                    created_at TEXT,
                    updated_at TEXT,
                    verified_at TEXT,
                    FOREIGN KEY (version_id) REFERENCES profile_versions(id)
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_profile_claims_version
                ON profile_claims(version_id, tier, status)
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_profile_claims_verified
                ON profile_claims(verified_at)
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_dreams (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    status TEXT DEFAULT 'pending',
                    scope TEXT,
                    instructions TEXT,
                    input_version_id INTEGER,
                    output_version_id INTEGER,
                    usage_tokens INTEGER DEFAULT 0,
                    trigger_reason TEXT,
                    error TEXT,
                    created_at TEXT,
                    ended_at TEXT
                )
            """)
            # Audit and traceability decision log: every judgment made by the automated system must be reviewable
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    claim_text TEXT,
                    detail TEXT,
                    created_at TEXT
                )
            """)
            # Profile layer key-value state (last run time, dream trigger suggestions, etc.)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)
            # Profile fields: AI auto-fills (source=distilled),
            # user-edited fields (source=manual) are not overwritten by AI
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_fields (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    source TEXT DEFAULT 'manual',
                    updated_at TEXT
                )
            """)
            # Field version history: old values are archived on change, max 10 entries per field
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS profile_field_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    source TEXT,
                    archived_at TEXT
                )
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_profile_field_history_key
                ON profile_field_history(key, id DESC)
            """)

            # Write-time arbitration audit log: every arbitration decision (including
            # keep_both) is recorded with the LLM's reasoning; archived memories are
            # soft-deleted and restorable, this log is how the user traces "why"
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS arbitration_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    new_memory_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_ids TEXT,
                    archived_ids TEXT,
                    reason TEXT,
                    created_at TEXT
                )
            """)

            # Migration: add new columns to existing tables (if not present)
            self._migrate_add_column(cursor, "memories", "trunk_ids", "TEXT")
            self._migrate_add_column(cursor, "memories", "trunk_status", "TEXT DEFAULT 'not_chunked'")
            
            # Trunk table meta-related column migrations
            self._migrate_add_column(cursor, "trunks", "content_type", "TEXT DEFAULT 'text'")
            self._migrate_add_column(cursor, "trunks", "meta", "TEXT")
            self._migrate_add_column(cursor, "trunks", "meta_tags", "TEXT")
            self._migrate_add_column(cursor, "trunks", "meta_status", "TEXT DEFAULT 'pending'")
            
            # Trunk table image-related column migrations
            self._migrate_add_column(cursor, "trunks", "image_url", "TEXT")
            self._migrate_add_column(cursor, "trunks", "image_description", "TEXT")
            self._migrate_add_column(cursor, "trunks", "image_ocr", "TEXT")
            self._migrate_add_column(cursor, "trunks", "image_exif", "TEXT")
    
    def _migrate_add_column(self, cursor, table: str, column: str, column_type: str):
        """Safely add a new column to a table (if it doesn't exist)"""
        try:
            cursor.execute(f"SELECT {column} FROM {table} LIMIT 1")
        except sqlite3.OperationalError:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")
    
    def add_memory(self, memory: Memory) -> Memory:
        """Add a memory"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO memories 
                (id, title, content, tags, priority, version, source, status, file_path, created_at, updated_at, trunk_ids, trunk_status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                memory.id,
                memory.title,
                memory.content,
                json.dumps(memory.tags, ensure_ascii=False),
                memory.priority,
                memory.version,
                memory.source,
                memory.status,
                memory.file_path,
                memory.created_at.isoformat() if memory.created_at else None,
                memory.updated_at.isoformat() if memory.updated_at else None,
                json.dumps(memory.trunk_ids, ensure_ascii=False),
                memory.trunk_status,
            ))
            
        return memory
    
    def update_memory(self, memory: Memory, save_history: bool = True) -> Memory:
        """Update a memory"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # If history needs to be saved, first get the old version
            if save_history:
                old_memory = self.get_memory(memory.id)
                if old_memory:
                    self._save_history(cursor, old_memory)
            
            # Update the memory
            memory.version += 1
            memory.updated_at = datetime.now()
            
            cursor.execute("""
                UPDATE memories 
                SET title = ?, content = ?, tags = ?, priority = ?, 
                    version = ?, status = ?, file_path = ?, updated_at = ?,
                    trunk_ids = ?, trunk_status = ?
                WHERE id = ?
            """, (
                memory.title,
                memory.content,
                json.dumps(memory.tags, ensure_ascii=False),
                memory.priority,
                memory.version,
                memory.status,
                memory.file_path,
                memory.updated_at.isoformat(),
                json.dumps(memory.trunk_ids, ensure_ascii=False),
                memory.trunk_status,
                memory.id,
            ))
            
        return memory
    
    def _save_history(self, cursor, memory: Memory):
        """Save a history version"""
        cursor.execute("""
            INSERT INTO memory_history 
            (memory_id, version, title, content, tags, priority, changed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            memory.id,
            memory.version,
            memory.title,
            memory.content,
            json.dumps(memory.tags, ensure_ascii=False),
            memory.priority,
            datetime.now().isoformat(),
        ))
    
    def add_arbitration_log(self, new_memory_id: str, action: str,
                            target_ids: list, archived_ids: list, reason: str) -> int:
        """Record a write-time arbitration decision (white-box audit trail)"""
        with self.get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO arbitration_log
                   (new_memory_id, action, target_ids, archived_ids, reason, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (new_memory_id, action,
                 json.dumps(target_ids or [], ensure_ascii=False),
                 json.dumps(archived_ids or [], ensure_ascii=False),
                 reason or "", datetime.now().isoformat()),
            )
            return cursor.lastrowid

    def list_arbitration_logs(self, limit: int = 50, offset: int = 0) -> List[dict]:
        """List arbitration decisions, most recent first"""
        with self.get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM arbitration_log ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            logs = []
            for r in rows:
                log = dict(r)
                log["target_ids"] = json.loads(log.get("target_ids") or "[]")
                log["archived_ids"] = json.loads(log.get("archived_ids") or "[]")
                logs.append(log)
            return logs

    def delete_memory(self, memory_id: str, hard_delete: bool = False) -> bool:
        """Delete a memory"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if hard_delete:
                # Hard delete
                cursor.execute("DELETE FROM memory_history WHERE memory_id = ?", (memory_id,))
                cursor.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            else:
                # Soft delete (archive)
                cursor.execute("""
                    UPDATE memories SET status = 'archived', updated_at = ?
                    WHERE id = ?
                """, (datetime.now().isoformat(), memory_id))
            
            return cursor.rowcount > 0
    
    def get_memory(self, memory_id: str) -> Optional[Memory]:
        """Get a single memory"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM memories WHERE id = ?", (memory_id,))
            row = cursor.fetchone()
            
            if row:
                return Memory.from_dict(dict(row))
            return None
    
    def get_memory_history(self, memory_id: str) -> List[MemoryHistory]:
        """Get memory history versions"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM memory_history 
                WHERE memory_id = ? 
                ORDER BY version DESC
            """, (memory_id,))
            rows = cursor.fetchall()
            
            return [MemoryHistory.from_dict(dict(row)) for row in rows]
    
    def list_memories(
        self, 
        status: Optional[str] = None,
        source: Optional[str] = None,
        tags: Optional[List[str]] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Memory]:
        """List memories (supports tag filtering at both memory and trunk levels)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT * FROM memories m WHERE 1=1"
            params = []
            
            if status:
                query += " AND m.status = ?"
                params.append(status)
            
            if source:
                query += " AND m.source = ?"
                params.append(source)
            
            if tags:
                for tag in tags:
                    # Supports prefix matching: matches exact tags or sub-tags starting with the tag
                    # Checks both memory-level and trunk-level tags
                    query += """ AND (
                        (m.tags LIKE ? OR m.tags LIKE ?)
                        OR EXISTS (
                            SELECT 1 FROM trunks t 
                            WHERE t.document_id = m.id 
                            AND (t.tags LIKE ? OR t.tags LIKE ?)
                        )
                    )"""
                    params.append(f'%"{tag}"%')  # memory exact match
                    params.append(f'%"{tag}/%')  # memory prefix match
                    params.append(f'%"{tag}"%')  # trunk exact match
                    params.append(f'%"{tag}/%')  # trunk prefix match
            
            query += " ORDER BY m.updated_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            
            return [Memory.from_dict(dict(row)) for row in rows]
    
    def search_keyword(self, query: str, limit: int = 20) -> List[tuple]:
        """Keyword search (supports CJK, prefers Whoosh)"""
        results = []
        seen_ids = set()
        
        # Method 1: Prefer Whoosh search (good CJK tokenization)
        if self.whoosh_search:
            try:
                whoosh_results = self.whoosh_search.search(query, limit=limit, status="active")
                for memory_id, score in whoosh_results:
                    memory = self.get_memory(memory_id)
                    if memory and memory.id not in seen_ids:
                        results.append((memory, score))
                        seen_ids.add(memory.id)
            except Exception as e:
                print(f"Whoosh search error: {e}")
        
        # Method 2: LIKE search as fallback (or supplement)
        if len(results) < limit:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                
                # Split query into multiple keywords
                keywords = query.strip().split()
                if not keywords:
                    keywords = [query.strip()]
                
                # Build LIKE conditions
                like_conditions = []
                like_params = []
                for kw in keywords:
                    if kw:
                        like_conditions.append("(title LIKE ? OR content LIKE ? OR tags LIKE ?)")
                        pattern = f"%{kw}%"
                        like_params.extend([pattern, pattern, pattern])
                
                if like_conditions:
                    sql = f"""
                        SELECT * FROM memories 
                        WHERE status = 'active' 
                        AND ({" AND ".join(like_conditions)})
                        LIMIT ?
                    """
                    like_params.append(limit)
                    cursor.execute(sql, like_params)
                    
                    for row in cursor.fetchall():
                        memory = Memory.from_dict(dict(row))
                        if memory.id not in seen_ids:
                            # Calculate a simple relevance score
                            text = f"{memory.title} {memory.content}".lower()
                            match_count = sum(1 for kw in keywords if kw.lower() in text)
                            score = match_count * 2  # Simple score
                            results.append((memory, score))
                            seen_ids.add(memory.id)
        
        # Sort by score
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]
    
    def sync_to_whoosh(self, memory: Memory):
        """Sync a single memory to the Whoosh index"""
        if self.whoosh_search:
            self.whoosh_search.update_document(
                memory_id=memory.id,
                title=memory.title,
                content=memory.content,
                tags=memory.tags,
                priority=memory.priority,
                status=memory.status,
            )
    
    def delete_from_whoosh(self, memory_id: str):
        """Delete a memory from the Whoosh index"""
        if self.whoosh_search:
            self.whoosh_search.delete_document(memory_id)
    
    def rebuild_whoosh_index(self) -> int:
        """Rebuild the Whoosh index"""
        if not self.whoosh_search:
            return 0
        
        memories = self.get_all_memories()
        memory_dicts = [m.to_dict() for m in memories]
        return self.whoosh_search.rebuild_index(memory_dicts)
    
    def get_all_tags(self) -> List[str]:
        """Get all tags (including both Memory and Trunk levels)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            all_tags = set()
            
            # 1. Memory-level tags
            cursor.execute("SELECT tags FROM memories WHERE status = 'active'")
            for row in cursor.fetchall():
                tags = row["tags"]
                if tags:
                    tag_list = json.loads(tags)
                    all_tags.update(tag_list)
            
            # 2. Trunk-level tags
            cursor.execute("SELECT tags FROM trunks WHERE status = 'ready'")
            for row in cursor.fetchall():
                tags = row["tags"]
                if tags:
                    tag_list = json.loads(tags)
                    all_tags.update(tag_list)
            
            return sorted(list(all_tags))
    
    def get_stats(self) -> dict:
        """Get statistics"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("SELECT COUNT(*) as total FROM memories")
            total = cursor.fetchone()["total"]
            
            cursor.execute("SELECT COUNT(*) as active FROM memories WHERE status = 'active'")
            active = cursor.fetchone()["active"]
            
            cursor.execute("SELECT COUNT(*) as archived FROM memories WHERE status = 'archived'")
            archived = cursor.fetchone()["archived"]
            
            cursor.execute("SELECT COUNT(*) as api_count FROM memories WHERE source = 'api'")
            api_count = cursor.fetchone()["api_count"]
            
            cursor.execute("SELECT COUNT(*) as user_count FROM memories WHERE source = 'user'")
            user_count = cursor.fetchone()["user_count"]
            
            # Trunk statistics
            cursor.execute("SELECT COUNT(*) as trunk_total FROM trunks")
            trunk_total = cursor.fetchone()["trunk_total"]
            
            cursor.execute("SELECT COUNT(*) as trunk_ready FROM trunks WHERE status = 'ready'")
            trunk_ready = cursor.fetchone()["trunk_ready"]
            
            return {
                "total": total,
                "active": active,
                "archived": archived,
                "api_count": api_count,
                "user_count": user_count,
                "trunk_total": trunk_total,
                "trunk_ready": trunk_ready,
            }
    
    # ==================== Trunk Operations ====================
    
    def add_trunk(self, trunk: Trunk) -> Trunk:
        """Add a Trunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO trunks 
                (id, document_id, "order", content, content_type, summary, tags, 
                 meta, meta_tags, meta_status, status, 
                 image_url, image_description, image_ocr, image_exif,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trunk.id,
                trunk.document_id,
                trunk.order,
                trunk.content,
                trunk.content_type,
                trunk.summary,
                json.dumps(trunk.tags, ensure_ascii=False),
                json.dumps(trunk.meta, ensure_ascii=False) if trunk.meta else None,
                json.dumps(trunk.meta_tags, ensure_ascii=False) if trunk.meta_tags else None,
                trunk.meta_status,
                trunk.status,
                trunk.image_url,
                trunk.image_description,
                trunk.image_ocr,
                json.dumps(trunk.image_exif, ensure_ascii=False) if trunk.image_exif else None,
                trunk.created_at.isoformat() if trunk.created_at else None,
                trunk.updated_at.isoformat() if trunk.updated_at else None,
            ))
            
        return trunk
    
    def update_trunk(self, trunk: Trunk) -> Trunk:
        """Update a Trunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            trunk.updated_at = datetime.now()
            
            cursor.execute("""
                UPDATE trunks 
                SET content = ?, content_type = ?, summary = ?, tags = ?, 
                    meta = ?, meta_tags = ?, meta_status = ?, status = ?,
                    image_url = ?, image_description = ?, image_ocr = ?, image_exif = ?,
                    updated_at = ?
                WHERE id = ?
            """, (
                trunk.content,
                trunk.content_type,
                trunk.summary,
                json.dumps(trunk.tags, ensure_ascii=False),
                json.dumps(trunk.meta, ensure_ascii=False) if trunk.meta else None,
                json.dumps(trunk.meta_tags, ensure_ascii=False) if trunk.meta_tags else None,
                trunk.meta_status,
                trunk.status,
                trunk.image_url,
                trunk.image_description,
                trunk.image_ocr,
                json.dumps(trunk.image_exif, ensure_ascii=False) if trunk.image_exif else None,
                trunk.updated_at.isoformat(),
                trunk.id,
            ))
            
        return trunk
    
    def get_trunk(self, trunk_id: str) -> Optional[Trunk]:
        """Get a single Trunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trunks WHERE id = ?", (trunk_id,))
            row = cursor.fetchone()
            
            if row:
                return Trunk.from_dict(dict(row))
            return None
    
    def get_trunks_by_document(self, document_id: str) -> List[Trunk]:
        """Get all Trunks of a document (ordered)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trunks 
                WHERE document_id = ? 
                ORDER BY "order" ASC
            """, (document_id,))
            rows = cursor.fetchall()
            
            return [Trunk.from_dict(dict(row)) for row in rows]
    
    def delete_trunks_by_document(self, document_id: str) -> int:
        """Delete all Trunks of a document"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trunks WHERE document_id = ?", (document_id,))
            return cursor.rowcount
    
    def delete_trunk(self, trunk_id: str) -> bool:
        """Delete a single Trunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trunks WHERE id = ?", (trunk_id,))
            return cursor.rowcount > 0
    
    def get_all_trunks(self, status: Optional[str] = None, limit: int = 1000) -> List[Trunk]:
        """Get all Trunks"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if status:
                cursor.execute("""
                    SELECT * FROM trunks WHERE status = ? 
                    ORDER BY created_at DESC LIMIT ?
                """, (status, limit))
            else:
                cursor.execute("""
                    SELECT * FROM trunks 
                    ORDER BY created_at DESC LIMIT ?
                """, (limit,))
            
            rows = cursor.fetchall()
            return [Trunk.from_dict(dict(row)) for row in rows]
    
    def get_pending_trunks(self, limit: int = 50) -> List[Trunk]:
        """Get pending Trunks"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trunks 
                WHERE status = 'pending' 
                ORDER BY created_at ASC LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [Trunk.from_dict(dict(row)) for row in rows]
    
    def update_memory_trunk_status(self, memory_id: str, trunk_status: str, trunk_ids: List[str] = None):
        """Update a memory's trunk status"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            if trunk_ids is not None:
                cursor.execute("""
                    UPDATE memories 
                    SET trunk_status = ?, trunk_ids = ?, updated_at = ?
                    WHERE id = ?
                """, (
                    trunk_status,
                    json.dumps(trunk_ids, ensure_ascii=False),
                    datetime.now().isoformat(),
                    memory_id,
                ))
            else:
                cursor.execute("""
                    UPDATE memories 
                    SET trunk_status = ?, updated_at = ?
                    WHERE id = ?
                """, (
                    trunk_status,
                    datetime.now().isoformat(),
                    memory_id,
                ))
    
    def get_memories_needing_chunking(self, limit: int = 10,
                                      include_stalled: bool = False) -> List[Memory]:
        """
        Get memories that need chunking.
        
        Args:
            include_stalled: Whether to include memories stuck in 'chunking' state.
                The chunking queue is an in-process memory queue, so it's always empty
                at process startup. Any 'chunking' state at that point is a leftover
                from a previous interrupted run — these memories have no chunks and are
                invisible to chunk-level retrieval.
                Set to True only during startup recovery; setting True at runtime would
                reprocess tasks that are already in progress.
        """
        statuses = ["not_chunked"]
        if include_stalled:
            statuses.append("chunking")
        placeholders = ",".join("?" * len(statuses))
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT * FROM memories
                WHERE status = 'active'
                AND (trunk_status IN ({placeholders}) OR trunk_status IS NULL)
                ORDER BY created_at DESC LIMIT ?
            """, (*statuses, limit))
            rows = cursor.fetchall()
            return [Memory.from_dict(dict(row)) for row in rows]
    
    def get_all_memories(self) -> List[Memory]:
        """Get all memories"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM memories")
            rows = cursor.fetchall()
            return [Memory.from_dict(dict(row)) for row in rows]
    
    # ==================== Meta Tag Operations ====================
    
    def add_meta_tags(self, chunk_id: str, meta_tags: List[dict]) -> int:
        """
        Add meta tags to a chunk.
        
        Args:
            chunk_id: trunk ID
            meta_tags: List of tags, each containing tag_type, tag_value, confidence (optional)
        
        Returns:
            Number of tags added
        """
        if not meta_tags:
            return 0
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            
            count = 0
            for tag in meta_tags:
                cursor.execute("""
                    INSERT INTO chunk_meta_tags 
                    (chunk_id, tag_type, tag_value, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    chunk_id,
                    tag.get("tag_type", "general"),
                    tag.get("tag_value", ""),
                    tag.get("confidence", 1.0),
                    now,
                ))
                count += 1
            
            return count
    
    def get_meta_tags_by_chunk(self, chunk_id: str) -> List[dict]:
        """Get all meta tags for a chunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT tag_type, tag_value, confidence 
                FROM chunk_meta_tags 
                WHERE chunk_id = ?
            """, (chunk_id,))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def search_chunks_by_meta_tag(
        self, 
        tag_type: Optional[str] = None, 
        tag_value: Optional[str] = None,
        limit: int = 50
    ) -> List[str]:
        """
        Search chunks by meta tag.
        
        Args:
            tag_type: Tag type (e.g. entity_person, theme, etc.)
            tag_value: Tag value
            limit: Maximum number of results
        
        Returns:
            List of matching chunk_ids
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            query = "SELECT DISTINCT chunk_id FROM chunk_meta_tags WHERE 1=1"
            params = []
            
            if tag_type:
                query += " AND tag_type = ?"
                params.append(tag_type)
            
            if tag_value:
                query += " AND tag_value LIKE ?"
                params.append(f"%{tag_value}%")
            
            query += " LIMIT ?"
            params.append(limit)
            
            cursor.execute(query, params)
            return [row["chunk_id"] for row in cursor.fetchall()]
    
    def delete_meta_tags_by_chunk(self, chunk_id: str) -> int:
        """Delete all meta tags for a chunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chunk_meta_tags WHERE chunk_id = ?", (chunk_id,))
            return cursor.rowcount
    
    def get_all_meta_tags(self) -> List[str]:
        """Get all unique meta tag values"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT tag_value FROM chunk_meta_tags")
            return [row["tag_value"] for row in cursor.fetchall()]
    
    def get_trunks_needing_meta(self, limit: int = 50) -> List[Trunk]:
        """Get Trunks that need meta extraction"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trunks 
                WHERE meta_status = 'pending' AND status = 'ready'
                ORDER BY created_at ASC LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()
            return [Trunk.from_dict(dict(row)) for row in rows]
    
    # ==================== Entity Operations ====================
    
    def upsert_entity(self, name: str, entity_type: str, description: str = None) -> int:
        """
        Insert or update an entity.
        
        Returns:
            Entity ID
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            
            # Check if already exists
            cursor.execute("""
                SELECT id, mention_count FROM entities 
                WHERE name = ? AND entity_type = ?
            """, (name, entity_type))
            row = cursor.fetchone()
            
            if row:
                # Update mention count
                cursor.execute("""
                    UPDATE entities 
                    SET mention_count = mention_count + 1, 
                        last_seen_at = ?
                    WHERE id = ?
                """, (now, row["id"]))
                return row["id"]
            else:
                # Insert new entity
                cursor.execute("""
                    INSERT INTO entities 
                    (name, entity_type, normalized_name, description, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (name, entity_type, name.lower(), description, now, now))
                return cursor.lastrowid
    
    def link_entity_to_trunk(self, entity_id: int, trunk_id: str, context: str = None, role: str = None):
        """Link an entity to a trunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO entity_trunk_links 
                    (entity_id, trunk_id, context, role, created_at)
                    VALUES (?, ?, ?, ?, ?)
                """, (entity_id, trunk_id, context, role, datetime.now().isoformat()))
            except sqlite3.IntegrityError:
                # Link already exists, ignore
                pass
    
    def add_entity_relation(
        self, 
        subject_id: int, 
        relation_type: str, 
        object_id: int, 
        source_trunk_id: str = None,
        confidence: float = 1.0
    ):
        """Add an entity relation (knowledge graph triple)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO entity_relations 
                (subject_id, relation_type, object_id, source_trunk_id, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (subject_id, relation_type, object_id, source_trunk_id, confidence, datetime.now().isoformat()))
    
    def get_entity_by_name(self, name: str, entity_type: str = None) -> Optional[dict]:
        """Get an entity by name"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if entity_type:
                cursor.execute("""
                    SELECT * FROM entities WHERE name = ? AND entity_type = ?
                """, (name, entity_type))
            else:
                cursor.execute("SELECT * FROM entities WHERE name = ?", (name,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_trunks_by_entity(self, entity_id: int) -> List[Trunk]:
        """Get all trunks that mention a given entity"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT t.* FROM trunks t
                JOIN entity_trunk_links etl ON t.id = etl.trunk_id
                WHERE etl.entity_id = ?
                ORDER BY t.created_at DESC
            """, (entity_id,))
            return [Trunk.from_dict(dict(row)) for row in cursor.fetchall()]
    
    def get_related_entities(self, entity_id: int) -> List[dict]:
        """Get entities related to a given entity (via co-occurrence or relations)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Method 1: Via knowledge graph relations
            cursor.execute("""
                SELECT e.*, er.relation_type, 'relation' as link_type
                FROM entities e
                JOIN entity_relations er ON (e.id = er.object_id OR e.id = er.subject_id)
                WHERE (er.subject_id = ? OR er.object_id = ?) AND e.id != ?
            """, (entity_id, entity_id, entity_id))
            relation_entities = [dict(row) for row in cursor.fetchall()]
            
            # Method 2: Via trunk co-occurrence (appearing in the same trunk)
            cursor.execute("""
                SELECT e.*, COUNT(*) as co_occurrence, 'co_occurrence' as link_type
                FROM entities e
                JOIN entity_trunk_links etl1 ON e.id = etl1.entity_id
                JOIN entity_trunk_links etl2 ON etl1.trunk_id = etl2.trunk_id
                WHERE etl2.entity_id = ? AND e.id != ?
                GROUP BY e.id
                ORDER BY co_occurrence DESC
                LIMIT 20
            """, (entity_id, entity_id))
            cooccur_entities = [dict(row) for row in cursor.fetchall()]
            
            # Merge results (deduplicate by normalized name, merge same-name entities)
            name_groups = {}
            for e in relation_entities + cooccur_entities:
                # Normalize name: remove parenthetical content, convert to lowercase
                raw_name = e["name"]
                normalized = raw_name.split('（')[0].split('(')[0].strip().lower()
                
                if normalized not in name_groups:
                    name_groups[normalized] = {
                        **e,
                        "merged_ids": [e["id"]],
                        "total_mention_count": e.get("mention_count", 0)
                    }
                else:
                    # Merge: accumulate mention_count, keep shorter name
                    name_groups[normalized]["merged_ids"].append(e["id"])
                    name_groups[normalized]["total_mention_count"] += e.get("mention_count", 0)
                    # Prefer shorter name
                    if len(raw_name) < len(name_groups[normalized]["name"]):
                        name_groups[normalized]["name"] = raw_name
                        name_groups[normalized]["entity_type"] = e["entity_type"]
            
            result = list(name_groups.values())
            
            # Sort by mention_count descending
            result.sort(key=lambda x: x.get("total_mention_count", x.get("mention_count", 0)), reverse=True)
            
            return result
    
    def get_all_entities(self, entity_type: str = None, limit: int = 100) -> List[dict]:
        """Get all entities"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if entity_type:
                cursor.execute("""
                    SELECT * FROM entities 
                    WHERE entity_type = ?
                    ORDER BY mention_count DESC LIMIT ?
                """, (entity_type, limit))
            else:
                cursor.execute("""
                    SELECT * FROM entities 
                    ORDER BY mention_count DESC LIMIT ?
                """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_entity_relations(self, entity_id: int = None) -> List[dict]:
        """Get entity relations"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if entity_id:
                cursor.execute("""
                    SELECT er.*, 
                           s.name as subject_name, s.entity_type as subject_type,
                           o.name as object_name, o.entity_type as object_type
                    FROM entity_relations er
                    JOIN entities s ON er.subject_id = s.id
                    JOIN entities o ON er.object_id = o.id
                    WHERE er.subject_id = ? OR er.object_id = ?
                """, (entity_id, entity_id))
            else:
                cursor.execute("""
                    SELECT er.*, 
                           s.name as subject_name, s.entity_type as subject_type,
                           o.name as object_name, o.entity_type as object_type
                    FROM entity_relations er
                    JOIN entities s ON er.subject_id = s.id
                    JOIN entities o ON er.object_id = o.id
                    LIMIT 100
                """)
            return [dict(row) for row in cursor.fetchall()]
    
    # ==================== Timeline Operations ====================
    
    def add_trunk_timeline(self, trunk_id: str, time_type: str, time_value: str, 
                          time_normalized: str = None, time_precision: str = None):
        """Add temporal information to a trunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trunk_timeline 
                (trunk_id, time_type, time_value, time_normalized, time_precision, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (trunk_id, time_type, time_value, time_normalized, time_precision, datetime.now().isoformat()))
    
    def get_trunks_by_time_range(self, start_date: str, end_date: str) -> List[Trunk]:
        """Get trunks within a time range"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT t.* FROM trunks t
                JOIN trunk_timeline tt ON t.id = tt.trunk_id
                WHERE tt.time_normalized >= ? AND tt.time_normalized <= ?
                ORDER BY tt.time_normalized
            """, (start_date, end_date))
            return [Trunk.from_dict(dict(row)) for row in cursor.fetchall()]
    
    def get_timeline_entries(self, trunk_id: str = None) -> List[dict]:
        """Get timeline entries"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if trunk_id:
                cursor.execute("""
                    SELECT * FROM trunk_timeline WHERE trunk_id = ?
                """, (trunk_id,))
            else:
                cursor.execute("""
                    SELECT * FROM trunk_timeline ORDER BY time_normalized LIMIT 100
                """)
            return [dict(row) for row in cursor.fetchall()]
    
    def delete_entity_links_by_trunk(self, trunk_id: str):
        """Delete all entity links for a trunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM entity_trunk_links WHERE trunk_id = ?", (trunk_id,))
            cursor.execute("DELETE FROM trunk_timeline WHERE trunk_id = ?", (trunk_id,))
    
    # ==================== Knowledge Graph Statistics ====================
    
    def get_knowledge_graph_stats(self) -> dict:
        """Get knowledge graph statistics"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Entity statistics
            cursor.execute("SELECT entity_type, COUNT(*) as count FROM entities GROUP BY entity_type")
            entity_stats = {row["entity_type"]: row["count"] for row in cursor.fetchall()}
            
            # Relation statistics
            cursor.execute("SELECT relation_type, COUNT(*) as count FROM entity_relations GROUP BY relation_type")
            relation_stats = {row["relation_type"]: row["count"] for row in cursor.fetchall()}
            
            # Totals
            cursor.execute("SELECT COUNT(*) FROM entities")
            total_entities = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM entity_relations")
            total_relations = cursor.fetchone()[0]
            
            cursor.execute("SELECT COUNT(*) FROM entity_trunk_links")
            total_links = cursor.fetchone()[0]
            
            return {
                "total_entities": total_entities,
                "total_relations": total_relations,
                "total_links": total_links,
                "entities_by_type": entity_stats,
                "relations_by_type": relation_stats
            }
    
    def get_entity_cooccurrences(self, min_count: int = 1, limit: int = 500) -> List[dict]:
        """
        Get entity co-occurrences (entity pairs mentioned in the same trunk).
        
        Returns:
            List where each element contains entity1_id, entity2_id, co_count
        """
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    etl1.entity_id as entity1_id,
                    etl2.entity_id as entity2_id,
                    COUNT(DISTINCT etl1.trunk_id) as co_count
                FROM entity_trunk_links etl1
                JOIN entity_trunk_links etl2 
                    ON etl1.trunk_id = etl2.trunk_id 
                    AND etl1.entity_id < etl2.entity_id
                GROUP BY etl1.entity_id, etl2.entity_id
                HAVING co_count >= ?
                ORDER BY co_count DESC
                LIMIT ?
            """, (min_count, limit))
            
            return [dict(row) for row in cursor.fetchall()]
    
    # ==================== Time Event Operations ====================
    
    def add_time_event(self, event: dict) -> int:
        """Add a time event"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()
            
            cursor.execute("""
                INSERT INTO time_events 
                (trunk_id, original_text, text_start, text_end, event_summary,
                 absolute_time, time_precision, is_range, range_end,
                 event_type, status, source_type, anchor_time, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event.get('trunk_id'),
                event.get('original_text', ''),
                event.get('text_start'),
                event.get('text_end'),
                event.get('event_summary'),
                event.get('absolute_time'),
                event.get('time_precision', 'day'),
                1 if event.get('is_range') else 0,
                event.get('range_end'),
                event.get('event_type', 'todo'),
                event.get('status', 'pending'),
                event.get('source_type', 'text'),
                event.get('anchor_time'),
                now,
                now
            ))
            return cursor.lastrowid
    
    def get_time_event(self, event_id: int) -> Optional[dict]:
        """Get a single time event"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM time_events WHERE id = ?", (event_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def get_time_events_by_trunk(self, trunk_id: str) -> List[dict]:
        """Get all time events for a trunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM time_events 
                WHERE trunk_id = ? 
                ORDER BY absolute_time
            """, (trunk_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_time_events(self, 
                        start_date: str = None, 
                        end_date: str = None,
                        status: str = None,
                        event_type: str = None,
                        source_type: str = None,
                        limit: int = 100,
                        offset: int = 0) -> List[dict]:
        """Get time events list (with filtering support)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            conditions = []
            params = []
            
            if start_date:
                conditions.append("absolute_time >= ?")
                params.append(start_date)
            if end_date:
                conditions.append("absolute_time <= ?")
                params.append(end_date)
            if status:
                conditions.append("status = ?")
                params.append(status)
            if event_type:
                conditions.append("event_type = ?")
                params.append(event_type)
            if source_type:
                conditions.append("source_type = ?")
                params.append(source_type)
            
            where_clause = " AND ".join(conditions) if conditions else "1=1"
            
            cursor.execute(f"""
                SELECT te.*, t.document_id, m.title as document_title
                FROM time_events te
                LEFT JOIN trunks t ON te.trunk_id = t.id
                LEFT JOIN memories m ON t.document_id = m.id
                WHERE {where_clause}
                ORDER BY te.absolute_time
                LIMIT ? OFFSET ?
            """, params + [limit, offset])
            
            return [dict(row) for row in cursor.fetchall()]
    
    def get_time_events_for_calendar(self, year: int, month: int) -> List[dict]:
        """Get time events for calendar view (by month)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Calculate month range
            start_date = f"{year:04d}-{month:02d}-01"
            if month == 12:
                end_date = f"{year+1:04d}-01-01"
            else:
                end_date = f"{year:04d}-{month+1:02d}-01"
            
            cursor.execute("""
                SELECT te.*, t.document_id, m.title as document_title
                FROM time_events te
                LEFT JOIN trunks t ON te.trunk_id = t.id
                LEFT JOIN memories m ON t.document_id = m.id
                WHERE te.absolute_time >= ? AND te.absolute_time < ?
                ORDER BY te.absolute_time
            """, (start_date, end_date))
            
            return [dict(row) for row in cursor.fetchall()]
    
    def update_time_event(self, event_id: int, updates: dict) -> bool:
        """Update a time event"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Build update statement
            set_parts = []
            params = []
            
            allowed_fields = ['event_summary', 'absolute_time', 'time_precision', 
                            'is_range', 'range_end', 'event_type', 'status', 'completed_at']
            
            for field in allowed_fields:
                if field in updates:
                    set_parts.append(f"{field} = ?")
                    params.append(updates[field])
            
            if not set_parts:
                return False
            
            set_parts.append("updated_at = ?")
            params.append(datetime.now().isoformat())
            params.append(event_id)
            
            cursor.execute(f"""
                UPDATE time_events SET {', '.join(set_parts)} WHERE id = ?
            """, params)
            
            return cursor.rowcount > 0
    
    def complete_time_event(self, event_id: int) -> bool:
        """Mark a time event as completed"""
        return self.update_time_event(event_id, {
            'status': 'completed',
            'completed_at': datetime.now().isoformat()
        })
    
    def uncomplete_time_event(self, event_id: int) -> bool:
        """Undo completion of a time event"""
        return self.update_time_event(event_id, {
            'status': 'pending',
            'completed_at': None
        })
    
    def delete_time_event(self, event_id: int) -> bool:
        """Delete a time event"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM time_events WHERE id = ?", (event_id,))
            return cursor.rowcount > 0
    
    def delete_time_events_by_trunk(self, trunk_id: str) -> int:
        """Delete all time events for a trunk"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM time_events WHERE trunk_id = ?", (trunk_id,))
            return cursor.rowcount
    
    def get_time_events_stats(self) -> dict:
        """Get time event statistics"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Status statistics
            cursor.execute("""
                SELECT status, COUNT(*) as count 
                FROM time_events 
                GROUP BY status
            """)
            status_stats = {row["status"]: row["count"] for row in cursor.fetchall()}
            
            # Type statistics
            cursor.execute("""
                SELECT event_type, COUNT(*) as count 
                FROM time_events 
                GROUP BY event_type
            """)
            type_stats = {row["event_type"]: row["count"] for row in cursor.fetchall()}
            
            # Source statistics
            cursor.execute("""
                SELECT source_type, COUNT(*) as count 
                FROM time_events 
                GROUP BY source_type
            """)
            source_stats = {row["source_type"]: row["count"] for row in cursor.fetchall()}
            
            # Total
            cursor.execute("SELECT COUNT(*) FROM time_events")
            total = cursor.fetchone()[0]
            
            # Upcoming count (future pending items)
            cursor.execute("""
                SELECT COUNT(*) FROM time_events 
                WHERE status = 'pending' AND absolute_time >= date('now')
            """)
            upcoming = cursor.fetchone()[0]
            
            # Expired count
            cursor.execute("""
                SELECT COUNT(*) FROM time_events 
                WHERE status = 'pending' AND absolute_time < date('now')
            """)
            expired = cursor.fetchone()[0]
            
            return {
                "total": total,
                "upcoming": upcoming,
                "expired": expired,
                "by_status": status_stats,
                "by_type": type_stats,
                "by_source": source_stats
            }

