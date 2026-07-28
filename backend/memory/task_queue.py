"""
Background task queue

Handles async tasks such as document chunking, summary generation, and vectorization

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import asyncio
import threading
import time
from typing import Optional, List, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from queue import Queue, Empty


class TaskType(Enum):
    """Task types"""
    CHUNK_DOCUMENT = "chunk_document"      # Chunk a document
    PROCESS_TRUNK = "process_trunk"        # Process a single trunk (summary + tags + vectorization)
    VECTORIZE_TRUNK = "vectorize_trunk"    # Vectorize a trunk
    EXTRACT_META = "extract_meta"          # Extract meta metadata


class TaskStatus(Enum):
    """Task statuses"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Task:
    """Task definition"""
    id: str
    task_type: TaskType
    payload: dict           # Task data
    status: TaskStatus = TaskStatus.PENDING
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3


class ChunkingTaskQueue:
    """
    Document chunking task queue
    
    Uses a single thread to avoid concurrency issues
    """
    
    def __init__(self):
        self.queue: Queue = Queue()
        self.processing = False
        self.current_task: Optional[Task] = None
        self.completed_tasks: List[Task] = []
        self.failed_tasks: List[Task] = []
        self._worker_thread: Optional[threading.Thread] = None
        self._stop_flag = False
        
        # Callbacks (set externally)
        self.on_chunk_document: Optional[Callable] = None
        self.on_process_trunk: Optional[Callable] = None
        self.on_vectorize_trunk: Optional[Callable] = None
        self.on_extract_meta: Optional[Callable] = None
        
        # Statistics
        self.stats = {
            "total_tasks": 0,
            "completed_tasks": 0,
            "failed_tasks": 0,
            "processing": False,
        }
    
    def start(self):
        """Start the background worker thread"""
        if self._worker_thread and self._worker_thread.is_alive():
            return
        
        self._stop_flag = False
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()
        print("✅ Task queue started")
    
    def stop(self):
        """Stop the background worker thread"""
        self._stop_flag = True
        if self._worker_thread:
            self._worker_thread.join(timeout=5)
            self._worker_thread = None
        print("⏹️ Task queue stopped")
    
    def _worker_loop(self):
        """Worker thread main loop"""
        while not self._stop_flag:
            try:
                # Try to get a task (blocks for 1 second)
                try:
                    task = self.queue.get(timeout=1)
                except Empty:
                    continue
                
                self._process_task(task)
                
            except Exception as e:
                print(f"Task processing error: {e}")
                time.sleep(1)
    
    def _process_task(self, task: Task):
        """Process a single task"""
        self.processing = True
        self.current_task = task
        self.stats["processing"] = True
        
        task.status = TaskStatus.PROCESSING
        task.started_at = datetime.now()
        
        try:
            if task.task_type == TaskType.CHUNK_DOCUMENT:
                if self.on_chunk_document:
                    self.on_chunk_document(task.payload)
            
            elif task.task_type == TaskType.PROCESS_TRUNK:
                if self.on_process_trunk:
                    self.on_process_trunk(task.payload)
            
            elif task.task_type == TaskType.VECTORIZE_TRUNK:
                if self.on_vectorize_trunk:
                    self.on_vectorize_trunk(task.payload)
            
            elif task.task_type == TaskType.EXTRACT_META:
                if self.on_extract_meta:
                    self.on_extract_meta(task.payload)
            
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now()
            self.completed_tasks.append(task)
            self.stats["completed_tasks"] += 1
            
        except Exception as e:
            task.error = str(e)
            task.retry_count += 1
            
            if task.retry_count < task.max_retries:
                # Retry
                task.status = TaskStatus.PENDING
                self.queue.put(task)
                print(f"Task {task.id} failed, will retry ({task.retry_count}/{task.max_retries}): {e}")
            else:
                task.status = TaskStatus.FAILED
                task.completed_at = datetime.now()
                self.failed_tasks.append(task)
                self.stats["failed_tasks"] += 1
                print(f"Task {task.id} permanently failed: {e}")
        
        finally:
            self.processing = False
            self.current_task = None
            self.stats["processing"] = False
    
    def add_chunk_task(self, memory_id: str) -> Task:
        """Add a document chunking task"""
        task = Task(
            id=f"chunk_{memory_id}_{int(time.time())}",
            task_type=TaskType.CHUNK_DOCUMENT,
            payload={"memory_id": memory_id}
        )
        self.queue.put(task)
        self.stats["total_tasks"] += 1
        return task
    
    def add_process_trunk_task(self, trunk_id: str, document_id: str) -> Task:
        """Add a trunk processing task (summary + tags + vectorization)"""
        task = Task(
            id=f"process_{trunk_id}_{int(time.time())}",
            task_type=TaskType.PROCESS_TRUNK,
            payload={"trunk_id": trunk_id, "document_id": document_id}
        )
        self.queue.put(task)
        self.stats["total_tasks"] += 1
        return task
    
    def add_vectorize_trunk_task(self, trunk_id: str) -> Task:
        """Add a trunk vectorization task"""
        task = Task(
            id=f"vectorize_{trunk_id}_{int(time.time())}",
            task_type=TaskType.VECTORIZE_TRUNK,
            payload={"trunk_id": trunk_id}
        )
        self.queue.put(task)
        self.stats["total_tasks"] += 1
        return task
    
    def add_extract_meta_task(self, trunk_id: str, document_id: str) -> Task:
        """Add a meta extraction task"""
        task = Task(
            id=f"meta_{trunk_id}_{int(time.time())}",
            task_type=TaskType.EXTRACT_META,
            payload={"trunk_id": trunk_id, "document_id": document_id}
        )
        self.queue.put(task)
        self.stats["total_tasks"] += 1
        return task
    
    def get_queue_size(self) -> int:
        """Get the number of tasks waiting in the queue"""
        return self.queue.qsize()
    
    def get_stats(self) -> dict:
        """Get statistics"""
        return {
            **self.stats,
            "queue_size": self.queue.qsize(),
            "current_task": self.current_task.id if self.current_task else None,
        }
    
    def clear_completed(self):
        """Clear completed task records"""
        self.completed_tasks.clear()
    
    def clear_failed(self):
        """Clear failed task records"""
        self.failed_tasks.clear()


class ChunkingProcessor:
    """
    Chunking processor
    
    Integrates chunker, database, vector_store, and meta_extractor to provide
    a complete chunking pipeline
    """
    
    def __init__(self, database, vector_store, chunker, config: dict):
        self.database = database
        self.vector_store = vector_store
        self.chunker = chunker
        self.config = config
        
        # Create meta extractor
        self.meta_extractor = None
        try:
            from .meta_extractor import create_meta_extractor
            self.meta_extractor = create_meta_extractor(config)
            print("✅ Meta extractor initialized")
        except Exception as e:
            print(f"⚠️ Meta extractor initialization failed: {e}")
        
        # Create chat model (for generating hierarchical tags)
        self.chat_model = None
        try:
            from .embedding import get_chat_model
            # This instance mainly handles entity confirmation / image structured meta, attributed to meta-extract;
            # Tagging goes through generate_tags and is auto-attributed to tagging
            self.chat_model = get_chat_model(config, caller="meta-extract")
            if self.chat_model:
                print("✅ Chat model initialized (for tag generation)")
        except Exception as e:
            print(f"⚠️ Chat model initialization failed: {e}")
        
        # Create task queue
        self.task_queue = ChunkingTaskQueue()
        
        # Set callbacks
        self.task_queue.on_chunk_document = self._handle_chunk_document
        self.task_queue.on_process_trunk = self._handle_process_trunk
        self.task_queue.on_extract_meta = self._handle_extract_meta
    
    def start(self):
        """Start the processor"""
        self.task_queue.start()
    
    def stop(self):
        """Stop the processor"""
        self.task_queue.stop()
    
    def queue_document_for_chunking(self, memory_id: str):
        """Queue a document for chunking"""
        # Update status to chunking
        self.database.update_memory_trunk_status(memory_id, "chunking")
        # Add task
        self.task_queue.add_chunk_task(memory_id)
    
    def rechunk_document(self, memory_id: str):
        """Reprocess a document (supports text and images)"""
        # Get old trunk info (for type detection and image path recovery)
        old_trunks = self.database.get_trunks_by_document(memory_id)
        
        # Detect if it's an image type
        is_image_doc = False
        image_trunk_info = None
        if old_trunks and len(old_trunks) == 1 and old_trunks[0].content_type == "image":
            is_image_doc = True
            old_trunk = old_trunks[0]
            image_trunk_info = {
                "id": old_trunk.id,
                "content": old_trunk.content,  # Image file path
                "image_url": old_trunk.image_url,
                "image_exif": old_trunk.image_exif
            }
        
        # Delete old trunks
        for trunk in old_trunks:
            # Delete from vector store
            if self.vector_store:
                self.vector_store.delete_trunk(trunk.id)
            # Delete associated time events
            self.database.delete_time_events_by_trunk(trunk.id)
            # Delete associated entity links
            self.database.delete_entity_links_by_trunk(trunk.id)
            # Delete from database
            self.database.delete_trunk(trunk.id)
        
        # Delete from Whoosh index
        if self.database.whoosh_search:
            self.database.whoosh_search.delete_trunks_by_document(memory_id)
        
        if is_image_doc and image_trunk_info:
            # Image document: recreate trunk and trigger image analysis
            from .models import Trunk
            
            trunk = Trunk(
                id=image_trunk_info["id"],  # Keep the same trunk ID
                document_id=memory_id,
                order=0,
                content=image_trunk_info["content"],  # Keep original image path
                content_type="image",
                status="pending",
                meta_status="pending",
                image_url=image_trunk_info["image_url"],
                image_exif=image_trunk_info["image_exif"]
            )
            
            self.database.add_trunk(trunk)
            self.database.update_memory_trunk_status(memory_id, "chunking", [trunk.id])
            
            # Add image processing task
            self.task_queue.add_extract_meta_task(trunk.id, memory_id)
            print(f"Image document queued for reprocessing: {memory_id}")
        else:
            # Text document: normal chunking flow
            self.database.update_memory_trunk_status(memory_id, "not_chunked", [])
            self.queue_document_for_chunking(memory_id)
    
    def _handle_chunk_document(self, payload: dict):
        """Handle document chunking task"""
        memory_id = payload["memory_id"]
        
        # Get document
        memory = self.database.get_memory(memory_id)
        if not memory:
            print(f"Document not found: {memory_id}")
            return
        
        if memory.status != "active":
            print(f"Document is not active: {memory_id}")
            return
        
        print(f"Starting document chunking: {memory.title} ({memory_id})")
        
        try:
            # Chunk
            trunks = self.chunker.chunk_document(memory_id, memory.content)
            
            if not trunks:
                # Empty content, mark as done
                self.database.update_memory_trunk_status(memory_id, "ready", [])
                print(f"Document content is empty, skipping chunking: {memory_id}")
                return
            
            # Save trunks to database
            trunk_ids = []
            for trunk in trunks:
                self.database.add_trunk(trunk)
                trunk_ids.append(trunk.id)
                
                # Add trunk processing task (summary + tags + vectorization)
                self.task_queue.add_process_trunk_task(trunk.id, memory_id)
            
            # Update document's trunk list
            self.database.update_memory_trunk_status(memory_id, "chunking", trunk_ids)
            
            print(f"Document chunking complete: {memory.title}, {len(trunks)} trunks")
            
        except Exception as e:
            print(f"Document chunking failed: {memory_id}, error: {e}")
            self.database.update_memory_trunk_status(memory_id, "error")
            raise
    
    def _handle_process_trunk(self, payload: dict):
        """
        Process a single trunk (full pipeline)
        
        Pipeline:
        1. Generate summary
        2. Extract metadata (semantic tags)
        3. Dual-path similarity search (content-based + metadata-based)
        4. Generate hierarchical tags (referencing similar trunk tags)
        5. Vectorize
        6. Full-text index
        """
        trunk_id = payload["trunk_id"]
        document_id = payload["document_id"]
        
        # Get trunk
        trunk = self.database.get_trunk(trunk_id)
        if not trunk:
            print(f"Trunk not found: {trunk_id}")
            return
        
        print(f"Processing trunk: {trunk_id}")
        
        try:
            # Update status to processing
            trunk.status = "processing"
            trunk.meta_status = "processing"
            self.database.update_trunk(trunk)
            
            # ========== Step 1: Generate summary ==========
            print(f"  [1/6] Generating summary...")
            summary = self.chunker.generate_trunk_summary(trunk)
            trunk.summary = summary
            self.database.update_trunk(trunk)
            
            # ========== Step 2: Extract metadata (semantic tags) ==========
            print(f"  [2/6] Extracting metadata...")
            if self.meta_extractor:
                try:
                    meta_result = self.meta_extractor.extract_meta(
                        content=trunk.content,
                        content_type=trunk.content_type
                    )
                    trunk.meta = meta_result.meta_dict
                    trunk.meta_tags = meta_result.meta_tags
                    trunk.meta_status = "ready"
                    self.database.update_trunk(trunk)
                    print(f"    Extracted {len(trunk.meta_tags)} semantic tags")
                    
                    # Save meta tags to index table
                    if trunk.meta_tags:
                        self._save_meta_tags_to_index(trunk_id, trunk.meta_tags)
                    
                    # Extract entities and establish knowledge graph links
                    if trunk.meta:
                        self._extract_and_save_entities(trunk_id, trunk.meta)
                except Exception as e:
                    print(f"    Metadata extraction failed: {e}")
                    trunk.meta_status = "error"
            else:
                trunk.meta_status = "skipped"
            
            # ========== Step 2.5: Time event extraction ==========
            print(f"  [2.5/6] Extracting time events...")
            self._extract_and_save_time_events(trunk)
            
            # ========== Step 3: Dual-path similarity search ==========
            print(f"  [3/6] Dual-path similarity search...")
            similar_tags_from_content = []
            similar_tags_from_meta = []
            
            # 3a. Search for similar trunks based on original content
            similar_tags_from_content = self._get_similar_tags_for_content(trunk.content, limit=5)
            print(f"    Found {len(similar_tags_from_content)} similar tags based on content")
            
            # 3b. Search for similar trunks based on metadata/semantic tags
            if trunk.meta_tags and len(trunk.meta_tags) > 0:
                meta_text = " ".join(trunk.meta_tags[:10])  # Use the first 10 semantic tags
                similar_tags_from_meta = self._get_similar_tags_for_content(meta_text, limit=5)
                print(f"    Found {len(similar_tags_from_meta)} similar tags based on metadata")
            
            # Merge and deduplicate
            all_similar_tags = list(set(similar_tags_from_content + similar_tags_from_meta))
            print(f"    {len(all_similar_tags)} similar tags after merging")
            
            # ========== Step 4: Generate hierarchical tags ==========
            print(f"  [4/6] Generating hierarchical tags...")
            # Get document tags as reference
            memory = self.database.get_memory(document_id)
            existing_tags = memory.tags if memory else []
            
            # Get all existing system tags (to constrain hierarchical tag generation)
            all_tags = self.database.get_all_tags()
            tags = self.chunker.generate_trunk_tags(
                trunk, existing_tags, 
                tag_tree=all_tags, 
                similar_tags=all_similar_tags
            )
            trunk.tags = tags
            self.database.update_trunk(trunk)
            print(f"    Generated {len(tags)} hierarchical tags: {tags}")
            
            # ========== Step 5: Vectorize ==========
            print(f"  [5/6] Vectorizing...")
            if self.vector_store:
                try:
                    self.vector_store.add_trunk(trunk)
                    print(f"    Vectorization complete")
                except Exception as e:
                    print(f"    Vectorization failed: {e}")
            
            # ========== Step 6: Full-text index ==========
            print(f"  [6/6] Full-text indexing...")
            if self.database.whoosh_search:
                self.database.whoosh_search.add_trunk(
                    trunk_id=trunk.id,
                    document_id=document_id,
                    content=trunk.content,
                    summary=trunk.summary or "",
                    tags=trunk.tags + (trunk.meta_tags or []),  # Merge hierarchical and semantic tags
                    order=trunk.order,
                    status="ready"
                )
            
            # Update status to complete
            trunk.status = "ready"
            self.database.update_trunk(trunk)
            
            print(f"Trunk processing complete: {trunk_id}")
            print(f"  - Summary: {summary[:30] if summary else 'N/A'}...")
            print(f"  - Hierarchical tags: {trunk.tags}")
            print(f"  - Semantic tags: {len(trunk.meta_tags or [])}")
            
            # Check if all trunks of the document are processed
            self._check_document_completion(document_id)
            
        except Exception as e:
            print(f"Trunk processing failed: {trunk_id}, error: {e}")
            trunk.status = "error"
            self.database.update_trunk(trunk)
            raise
    
    def _get_similar_tags_for_content(self, content: str, limit: int = 5, min_score: float = 0.4) -> list:
        """
        Find tags from similar content via semantic search
        
        Args:
            content: Content to search for
            limit: Number of similar results to return
            min_score: Minimum similarity threshold
        
        Returns:
            List of all tags used by similar content (may contain duplicates for frequency analysis)
        """
        if not self.vector_store:
            return []
        
        try:
            # Use the first 500 characters as query
            query = content[:500] if len(content) > 500 else content
            
            # Semantic search
            similar_results = self.vector_store.search(query, limit=limit, min_score=min_score)
            
            # Collect tags from all similar articles
            all_tags = []
            for memory_id, score in similar_results:
                memory = self.database.get_memory(memory_id)
                if memory and memory.tags:
                    all_tags.extend(memory.tags)
            
            return all_tags
        except Exception as e:
            print(f"Failed to get similar content tags: {e}")
            return []
    
    def _check_document_completion(self, document_id: str):
        """Check if all trunks of the document are processed"""
        trunks = self.database.get_trunks_by_document(document_id)
        
        if not trunks:
            return
        
        all_ready = all(t.status == "ready" for t in trunks)
        any_error = any(t.status == "error" for t in trunks)
        
        if all_ready:
            self.database.update_memory_trunk_status(document_id, "ready")
            print(f"All trunks processed for document: {document_id}")
        elif any_error:
            # Has errors but the rest are done
            ready_count = sum(1 for t in trunks if t.status == "ready")
            if ready_count == len(trunks) - sum(1 for t in trunks if t.status == "error"):
                self.database.update_memory_trunk_status(document_id, "error")
    
    def process_pending_documents(self):
        """Process all documents pending chunking"""
        memories = self.database.get_memories_needing_chunking(limit=50)
        
        for memory in memories:
            self.queue_document_for_chunking(memory.id)
        
        return len(memories)
    
    def recover_interrupted_documents(self) -> int:
        """
        Startup recovery: re-queue chunking tasks that were interrupted by the last shutdown.

        Background: chunking runs as in-process background tasks; when the process is killed,
        their status stays at 'chunking'. The old recovery only picked up 'not_chunked', so
        these memories never got chunked — search and exploration work at the chunk level,
        making them completely invisible in retrieval.

        Documents that already have partial trunks are re-chunked (delete then rebuild)
        to avoid duplicate chunks.
        """
        recovered = 0
        memories = self.database.get_memories_needing_chunking(limit=500, include_stalled=True)
        
        for memory in memories:
            try:
                if self.database.get_trunks_by_document(memory.id):
                    self.rechunk_document(memory.id)
                else:
                    self.queue_document_for_chunking(memory.id)
                recovered += 1
            except Exception as e:
                print(f"[WARN] Failed to recover chunking for {memory.id}: {e}")
        
        return recovered
    
    def _handle_extract_meta(self, payload: dict):
        """Handle meta extraction task"""
        trunk_id = payload["trunk_id"]
        document_id = payload["document_id"]
        
        trunk = self.database.get_trunk(trunk_id)
        if not trunk:
            print(f"Trunk not found: {trunk_id}")
            return
        
        if not self.meta_extractor:
            print(f"Meta extractor unavailable, skipping: {trunk_id}")
            trunk.meta_status = "error"
            self.database.update_trunk(trunk)
            return
        
        print(f"Extracting meta: {trunk_id} (type: {trunk.content_type})")
        
        try:
            trunk.meta_status = "processing"
            self.database.update_trunk(trunk)
            
            if trunk.content_type == "image":
                # Image type: full analysis (tags + description + OCR)
                self._process_image_meta(trunk)
            else:
                # Text type: standard meta extraction
                meta_result = self.meta_extractor.extract_meta(
                    content=trunk.content,
                    content_type=trunk.content_type
                )
                
                # Update trunk's meta info
                trunk.meta = meta_result.meta_dict
                trunk.meta_tags = meta_result.meta_tags
                trunk.meta_status = "ready"
                self.database.update_trunk(trunk)
                
                # Save meta tags to index table
                if meta_result.meta_tags:
                    self._save_meta_tags_to_index(trunk_id, meta_result.meta_tags)
                
                print(f"Meta extraction complete: {trunk_id}, tag count: {len(meta_result.meta_tags)}")
            
        except Exception as e:
            print(f"Meta extraction failed: {trunk_id}, error: {e}")
            trunk.meta_status = "error"
            self.database.update_trunk(trunk)
            # Don't raise — meta extraction failure shouldn't block the main pipeline
    
    def _process_image_meta(self, trunk):
        """
        Process meta extraction for image-type trunks
        
        Pipeline (similar to text):
        1. AI image description + OCR
        2. Extract visual feature tags (meta_tags, 10+)
        3. Vector search for similar content -> get tag references
        4. Generate hierarchical tags based on reference tags + system tag tree
        5. Extract structured meta information
        """
        import base64
        import os
        
        trunk_id = trunk.id
        image_path = trunk.content  # Image file path
        
        # Read image and convert to base64
        if not os.path.exists(image_path):
            print(f"Image file not found: {image_path}")
            trunk.meta_status = "error"
            self.database.update_trunk(trunk)
            return
        
        with open(image_path, "rb") as f:
            image_data = f.read()
        image_base64 = base64.b64encode(image_data).decode('utf-8')
        
        # Get image analyzer
        from .meta_extractor import ImageMetaExtractor
        image_extractor = self.meta_extractor.get_extractor("image")
        
        if not image_extractor or not isinstance(image_extractor, ImageMetaExtractor):
            print(f"Image analyzer unavailable: {trunk_id}")
            trunk.meta_status = "error"
            self.database.update_trunk(trunk)
            return
        
        print(f"Starting full image analysis: {trunk_id}")
        
        # ========== Phase 1: AI description ==========
        print("  [Phase 1] Generating image description...")
        
        description = ""
        ocr_text = ""
        meta_tags = []
        
        try:
            description = image_extractor.generate_description(image_base64)
            # Update database immediately so the frontend can show progress
            trunk.image_description = description
            trunk.summary = description[:200] if description else None
            self.database.update_trunk(trunk)
            print(f"    Description generated: {len(description)} chars")
        except Exception as e:
            print(f"    Description generation failed: {e}")
        
        # ========== Phase 2: OCR ==========
        print("  [Phase 2] OCR text recognition...")
        try:
            ocr_text = image_extractor.extract_ocr(image_base64)
            # Update database immediately
            trunk.image_ocr = ocr_text
            self.database.update_trunk(trunk)
            print(f"    OCR complete: {len(ocr_text) if ocr_text else 0} chars")
        except Exception as e:
            print(f"    OCR failed: {e}")
        
        # ========== Phase 3: Visual feature tags ==========
        print("  [Phase 3] Extracting visual feature tags...")
        try:
            meta_result = image_extractor.extract(image_base64, is_base64=True)
            meta_tags = meta_result.meta_tags
            # Update database immediately
            trunk.meta_tags = meta_tags
            self.database.update_trunk(trunk)
            print(f"    Extracted {len(meta_tags)} visual feature tags")
        except Exception as e:
            print(f"    Visual tag extraction failed: {e}")
        
        # ========== Phase 4: Vector search for similar content tag references ==========
        print("  [Phase 4] Searching similar content for tag references...")
        similar_tags = []
        tag_tree = []
        
        if self.vector_store and description:
            try:
                # Search for similar content using description
                search_results = self.vector_store.search_trunks(
                    query=description[:500],
                    limit=10,
                    min_score=0.15
                )
                
                # Collect tags from similar content
                for result in search_results:
                    result_trunk = self.database.get_trunk(result.trunk_id)
                    if result_trunk and result_trunk.id != trunk_id:
                        if result_trunk.tags:
                            similar_tags.extend(result_trunk.tags)
                
                print(f"    Collected {len(similar_tags)} tags from similar content")
            except Exception as e:
                print(f"    Similar content search failed: {e}")
        
        # Get system tag tree
        try:
            tag_tree = self.database.get_all_tags()
            print(f"    System has {len(tag_tree)} existing tags")
        except Exception as e:
            print(f"    Failed to get tag tree: {e}")
        
        # ========== Phase 5: Generate hierarchical tags ==========
        print("  [Phase 5] Generating hierarchical tags...")
        
        tags = []
        meta = {}
        
        # Use LLM (text model) to generate hierarchical tags, not VLM
        # Input: description + OCR + visual features, same as text content processing
        if self.chat_model and self.chat_model.is_available():
            try:
                # Build "content" for tag generation
                content_parts = []
                if description:
                    content_parts.append(f"Image description: {description}")
                if ocr_text:
                    content_parts.append(f"Image text: {ocr_text[:500]}")
                if meta_tags:
                    content_parts.append(f"Visual features: {', '.join(meta_tags[:20])}")
                
                content_for_tags = "\n\n".join(content_parts)
                
                # Get memory title
                memory = self.database.get_memory(trunk.document_id)
                title = memory.title if memory else "Image"
                
                # Use LLM to generate tags (same as text processing)
                tags = self.chat_model.generate_tags(
                    title=title,
                    content=content_for_tags,
                    existing_tags=[],
                    tag_tree=tag_tree,
                    similar_tags=similar_tags
                )
                # Update database immediately
                trunk.tags = tags
                self.database.update_trunk(trunk)
                print(f"    Generated {len(tags)} hierarchical tags using LLM")
            except Exception as e:
                print(f"    LLM hierarchical tag generation failed: {e}")
        else:
            print("    Chat model unavailable, skipping hierarchical tag generation")
        
        # ========== Phase 6: Extract structured meta ==========
        print("  [Phase 6] Extracting structured meta...")
        try:
            # Use LLM (text model) instead of VLM for structured meta extraction
            # since only text processing is needed at this point (description, OCR, visual tags)
            meta = self._extract_image_structured_meta_with_llm(
                description=description,
                ocr_text=ocr_text,
                visual_tags=meta_tags
            )
            # Update database immediately
            trunk.meta = meta
            self.database.update_trunk(trunk)
            print(f"    Structured meta extraction complete")
        except Exception as e:
            print(f"    Structured meta extraction failed: {e}")
        
        # ========== Final phase: Finalize meta and mark complete ==========
        print("  [Phase 7] Finalizing meta...")
        
        # If meta is empty, build a base structure
        if not trunk.meta:
            trunk.meta = {
                "entities": {"person": [], "location": [], "organization": [], "object": []},
                "time_expressions": {"mentioned": [], "scene_time": ""},
                "theme": [],
                "scene": "",
                "sentiment": "",
                "domain": [],
                "visual_tags": trunk.meta_tags,
                "visual_tag_count": len(trunk.meta_tags)
            }
        
        # If EXIF data exists, merge into meta
        if trunk.image_exif:
            trunk.meta["exif"] = trunk.image_exif
        
        # Add OCR info to meta
        trunk.meta["has_ocr"] = bool(trunk.image_ocr)
        trunk.meta["ocr_length"] = len(trunk.image_ocr) if trunk.image_ocr else 0
        
        # Ensure tags are not empty (use previously generated values)
        if not trunk.tags:
            trunk.tags = tags
        if not trunk.meta_tags:
            trunk.meta_tags = meta_tags
        
        trunk.meta_status = "ready"
        trunk.status = "ready"
        self.database.update_trunk(trunk)
        
        # Save meta tags to index table
        if trunk.meta_tags:
            self._save_meta_tags_to_index(trunk_id, trunk.meta_tags)
        
        # Extract entities and establish knowledge graph links
        if trunk.meta:
            self._extract_and_save_entities(trunk_id, trunk.meta)
        
        # Extract time events
        print("  [Phase 7.5] Extracting time events...")
        self._extract_and_save_time_events(trunk)
        
        # ========== Phase 8: Vectorization ==========
        print("  [Phase 8] Generating embedding vectors...")
        if self.vector_store:
            # Build text for vectorization (description + OCR + tags)
            text_for_embedding = []
            if trunk.image_description:
                text_for_embedding.append(trunk.image_description)
            if trunk.image_ocr:
                text_for_embedding.append(trunk.image_ocr)
            if trunk.meta_tags:
                text_for_embedding.append(" ".join(trunk.meta_tags))
            if trunk.tags:
                text_for_embedding.append(" ".join(trunk.tags))
            
            if text_for_embedding:
                # Temporarily modify content for vectorization
                original_content = trunk.content
                trunk.content = "\n".join(text_for_embedding)
                try:
                    self.vector_store.add_trunk(trunk)
                    print(f"    Embedding complete: {len(trunk.content)} chars")
                except Exception as e:
                    print(f"    Embedding failed: {e}")
                trunk.content = original_content  # Restore original path
            else:
                print("    No content to vectorize")
        else:
            print("    Vector store not enabled")
        
        # Add to Whoosh index
        if self.database.whoosh_search:
            # Use description and OCR as searchable content
            searchable_content = []
            if trunk.image_description:
                searchable_content.append(trunk.image_description)
            if trunk.image_ocr:
                searchable_content.append(trunk.image_ocr)
            
            self.database.whoosh_search.add_trunk(
                trunk_id=trunk.id,
                document_id=trunk.document_id,
                content=" ".join(searchable_content) if searchable_content else "",
                summary=trunk.summary or "",
                tags=trunk.tags + trunk.meta_tags,  # Merge explicit and implicit tags
                order=trunk.order,
                status="ready"
            )
        
        print(f"Image analysis complete: {trunk_id}")
        print(f"  - Hierarchical tags: {len(trunk.tags)}, Meta tags: {len(trunk.meta_tags)}")
        print(f"  - Description: {len(trunk.image_description or '')} chars, OCR: {len(trunk.image_ocr or '')} chars")
        
        # Update document status
        self.database.update_memory_trunk_status(trunk.document_id, "ready")
    
    def _extract_image_structured_meta_with_llm(
        self, 
        description: str, 
        ocr_text: str, 
        visual_tags: list
    ) -> dict:
        """
        Use LLM (text model) to extract structured meta information from image descriptions
        
        This replaces VLM since structured meta extraction only requires text processing
        """
        # If chat model is unavailable, return base structure
        if not self.chat_model or not self.chat_model.is_available():
            print("    LLM unavailable, using base meta structure")
            return self._build_empty_image_meta(visual_tags)
        
        # Build context
        context_parts = []
        if description:
            context_parts.append(f"Image description: {description}")
        if ocr_text:
            context_parts.append(f"Text in image: {ocr_text[:1000]}")
        if visual_tags:
            context_parts.append(f"Visual features: {', '.join(visual_tags[:30])}")
        
        if not context_parts:
            return self._build_empty_image_meta(visual_tags)
        
        context = "\n".join(context_parts)
        
        prompt = f"""Analyze the following image information and extract structured metadata.

{context}

Output in the following JSON format (output JSON only, no other content):
{{
  "entities": {{
    "person": ["identified person names"],
    "location": ["identified locations"],
    "organization": ["identified organizations/brands"],
    "object": ["main objects"]
  }},
  "time_expressions": {{
    "mentioned": ["times mentioned in the image"],
    "scene_time": "inferred time of day (e.g., daytime/dusk/night)"
  }},
  "theme": ["theme1", "theme2"],
  "scene": "scene type (e.g., indoor/outdoor/mall/park)",
  "sentiment": "overall sentiment (e.g., positive/neutral/negative)",
  "domain": ["domain1", "domain2"]
}}

Notes:
1. Only fill in what can be determined from the image information
2. If an item cannot be determined, use an empty array or empty string
3. Output JSON directly, no explanations"""

        try:
            import json
            
            # Use chat_model for text inference
            response = self.chat_model.generate_raw(prompt, max_tokens=1000, temperature=0.2)
            
            if not response:
                return self._build_empty_image_meta(visual_tags)
            
            # Clean response
            result = response.strip()
            
            # Remove possible markdown code block markers
            if result.startswith("```"):
                lines = result.split('\n')
                result = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
            
            # Remove <think> tags
            import re
            result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL)
            result = result.strip()
            
            # Try to parse JSON
            meta = json.loads(result)
            
            # Add visual tags to meta
            meta["visual_tags"] = visual_tags
            meta["visual_tag_count"] = len(visual_tags) if visual_tags else 0
            
            print(f"    LLM successfully extracted structured meta")
            return meta
            
        except json.JSONDecodeError as e:
            print(f"    Meta JSON parsing failed: {e}")
            return self._build_empty_image_meta(visual_tags)
        except Exception as e:
            print(f"    LLM structured meta extraction failed: {e}")
            return self._build_empty_image_meta(visual_tags)
    
    def _build_empty_image_meta(self, visual_tags: list) -> dict:
        """Build an empty image meta structure"""
        return {
            "entities": {
                "person": [],
                "location": [],
                "organization": [],
                "object": []
            },
            "time_expressions": {
                "mentioned": [],
                "scene_time": ""
            },
            "theme": [],
            "scene": "",
            "sentiment": "",
            "domain": [],
            "visual_tags": visual_tags if visual_tags else [],
            "visual_tag_count": len(visual_tags) if visual_tags else 0
        }
    
    def _save_meta_tags_to_index(self, trunk_id: str, meta_tags: list):
        """Save meta tags to the index table"""
        # Build tag records
        meta_tag_records = []
        for tag in meta_tags:
            # Parse tag type (e.g., "person:John" -> type=person, value=John)
            if ':' in tag:
                parts = tag.split(':', 1)
                tag_type = parts[0]
                tag_value = parts[1]
            else:
                tag_type = "general"
                tag_value = tag
            
            meta_tag_records.append({
                "tag_type": tag_type,
                "tag_value": tag_value,
                "confidence": 1.0
            })
        
        # Delete old meta tags first
        self.database.delete_meta_tags_by_chunk(trunk_id)
        # Add new meta tags
        self.database.add_meta_tags(trunk_id, meta_tag_records)
    
    def _extract_and_save_entities(self, trunk_id: str, meta: dict):
        """
        Extract entities from meta and save to knowledge graph
        
        Processes the following from the meta structure:
        - entities.person: People
        - entities.organization: Organizations
        - entities.location: Locations
        - entities.product: Products
        - entities.concept: Concepts
        - time_expressions: Time information
        """
        if not meta:
            return
        
        # Delete old associations first
        self.database.delete_entity_links_by_trunk(trunk_id)
        
        entities = meta.get("entities", {})
        entity_ids = []  # Collect all entity IDs in this trunk for co-occurrence relationships
        
        # Entity type mapping
        type_mapping = {
            "person": "person",
            "organization": "organization",
            "location": "location",
            "product": "product",
            "concept": "concept",
            "object": "object"  # Objects in images
        }
        
        for entity_type, entity_list in entities.items():
            if not entity_list or not isinstance(entity_list, list):
                continue
            
            db_type = type_mapping.get(entity_type, entity_type)
            
            for entity_name in entity_list:
                # Person entities are {"name": ..., "role": ...} dicts; other types are strings
                role = None
                if isinstance(entity_name, dict):
                    name = (entity_name.get("name") or "").strip()
                    role = (entity_name.get("role") or "").strip() or None
                    if not name:
                        continue
                elif isinstance(entity_name, str) and entity_name:
                    # Handle entities with roles, e.g., "John(client)"
                    name = entity_name
                    if '(' in entity_name and ')' in entity_name:
                        parts = entity_name.rsplit('(', 1)
                        name = parts[0].strip()
                        role = parts[1].rstrip(')').strip()
                else:
                    continue
                
                # Smart matching: check if a similar entity already exists
                entity_id = self._smart_entity_match(name, db_type)
                entity_ids.append(entity_id)
                
                # Link to trunk
                self.database.link_entity_to_trunk(entity_id, trunk_id, role=role)
        
        # Process time information
        time_expressions = meta.get("time_expressions", {})
        if isinstance(time_expressions, dict):
            # Mentioned times
            mentioned = time_expressions.get("mentioned", [])
            if isinstance(mentioned, list):
                for time_val in mentioned:
                    if time_val:
                        self.database.add_trunk_timeline(trunk_id, "mentioned", time_val)
            
            # Inferred absolute times
            inferred = time_expressions.get("inferred_absolute", [])
            if isinstance(inferred, list):
                for time_val in inferred:
                    if time_val:
                        self.database.add_trunk_timeline(
                            trunk_id, "inferred", time_val, 
                            time_normalized=time_val  # Assumed to be in standard format
                        )
            
            # Scene time (images)
            scene_time = time_expressions.get("scene_time")
            if scene_time:
                self.database.add_trunk_timeline(trunk_id, "scene", scene_time)
        
        print(f"    Extracted {len(entity_ids)} entity associations")
    
    def _smart_entity_match(self, name: str, entity_type: str) -> int:
        """
        Smart entity matching:
        1. Exact match: identical names
        2. Fuzzy match: identical after normalization
        3. AI confirmation: let AI judge if they are the same entity
        
        Returns:
            Entity ID
        """
        # Step 1: Normalize name
        normalized = self._normalize_entity_name(name)
        
        # Step 2: Find candidate entities (same type + fuzzy match)
        candidates = self._find_similar_entities(normalized, entity_type)
        
        if not candidates:
            # No candidates, create new
            return self.database.upsert_entity(name, entity_type)
        
        # Step 3: Check for exact match
        for candidate in candidates:
            if self._normalize_entity_name(candidate["name"]) == normalized:
                # Exact match, use directly
                # Update mention count
                return self.database.upsert_entity(candidate["name"], entity_type)
        
        # Step 4: AI confirmation of entity identity
        best_match = self._ai_confirm_entity_match(name, candidates)
        
        if best_match:
            # AI confirmed same entity, use existing one
            return self.database.upsert_entity(best_match["name"], entity_type)
        else:
            # AI confirmed different entity, create new
            return self.database.upsert_entity(name, entity_type)
    
    def _normalize_entity_name(self, name: str) -> str:
        """Normalize entity name"""
        # Remove parenthetical content
        result = name.split('（')[0].split('(')[0]
        # Strip whitespace
        result = result.strip()
        # Convert to lowercase
        result = result.lower()
        # Remove common suffixes
        for suffix in ['公司', '有限公司', '集团', '科技', 'inc', 'ltd', 'corp']:
            if result.endswith(suffix):
                result = result[:-len(suffix)].strip()
        return result
    
    def _find_similar_entities(self, normalized_name: str, entity_type: str) -> list:
        """Find similar existing entities"""
        # Get all entities of the same type
        all_entities = self.database.get_all_entities(entity_type=entity_type, limit=500)
        
        candidates = []
        for entity in all_entities:
            entity_normalized = self._normalize_entity_name(entity["name"])
            
            # Check similarity
            similarity = self._calculate_similarity(normalized_name, entity_normalized)
            
            if similarity > 0.6:  # Similarity threshold
                entity["similarity"] = similarity
                candidates.append(entity)
        
        # Sort by similarity
        candidates.sort(key=lambda x: x["similarity"], reverse=True)
        return candidates[:5]  # Return at most 5 candidates
    
    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """Calculate similarity between two strings"""
        if s1 == s2:
            return 1.0
        
        # Containment relationship
        if s1 in s2 or s2 in s1:
            return 0.9
        
        # Edit distance similarity
        len1, len2 = len(s1), len(s2)
        if len1 == 0 or len2 == 0:
            return 0.0
        
        # Simplified similarity (common character ratio)
        common = sum(1 for c in s1 if c in s2)
        return (2.0 * common) / (len1 + len2)
    
    def _ai_confirm_entity_match(self, new_name: str, candidates: list) -> dict:
        """
        Let AI confirm whether a new entity matches a candidate entity
        
        Returns:
            The matching candidate entity, or None if it's a new entity
        """
        if not candidates or not self.chat_model:
            return None
        
        # Build candidate list
        candidate_list = "\n".join([
            f"{i+1}. {c['name']} (mentioned {c['mention_count']} times)"
            for i, c in enumerate(candidates)
        ])
        
        prompt = f"""Determine whether the newly extracted entity is the same as an existing entity.

New entity: "{new_name}"

Existing candidate entities:
{candidate_list}

Instructions:
- If the new entity is the same as a candidate, reply with that candidate's number (e.g., "1")
- If the new entity is brand new and different from all candidates, reply "0"

Criteria:
- Different names for the same person/company/product should be considered the same (e.g., "Twitter" and "X")
- Abbreviations and full names of the same thing should be considered the same (e.g., "IBM" and "International Business Machines")
- Subordinate relationships that are not the same entity should be considered different (e.g., "Instagram" and "Meta" are different entities)

Reply with a number only, no explanation."""

        try:
            messages = [
                {"role": "system", "content": "You are an entity matching assistant. Reply with numbers only."},
                {"role": "user", "content": prompt}
            ]
            
            response = self.chat_model.chat(messages, max_tokens=10)
            
            # Parse response
            answer = response.strip()
            if answer == "0":
                return None
            
            try:
                index = int(answer) - 1
                if 0 <= index < len(candidates):
                    print(f"      AI confirmed: '{new_name}' = '{candidates[index]['name']}'")
                    return candidates[index]
            except ValueError:
                pass
            
            return None
            
        except Exception as e:
            print(f"      AI entity matching failed: {e}")
            return None
    
    def _extract_and_save_time_events(self, trunk):
        """
        Extract time events from a trunk and save them
        
        Handles both text and image trunk types
        """
        try:
            from .time_extractor import TimeExtractor
            
            # Determine text content to analyze
            text_to_analyze = ""
            source_type = "text"
            
            if trunk.content_type == "image":
                # Image type: extract from description and OCR
                source_type = "image"
                if trunk.image_description:
                    text_to_analyze += trunk.image_description + "\n"
                if trunk.image_ocr:
                    text_to_analyze += trunk.image_ocr
            else:
                # Text type
                text_to_analyze = trunk.content
            
            if not text_to_analyze or len(text_to_analyze.strip()) < 10:
                print(f"    No content to analyze for time events")
                return
            
            # Create time extractor (with LLM support)
            time_extractor = TimeExtractor(chat_model=self.chat_model)
            
            # Use trunk creation time as anchor time
            anchor_time = trunk.created_at or datetime.now()
            
            # Extract time expressions
            time_expressions = time_extractor.extract(text_to_analyze, anchor_time)
            
            if not time_expressions:
                print(f"    No time expressions found")
                return
            
            print(f"    Found {len(time_expressions)} time events")
            
            # Delete old time events
            self.database.delete_time_events_by_trunk(trunk.id)
            
            # Save new time events
            for expr in time_expressions:
                event_data = {
                    "trunk_id": trunk.id,
                    "original_text": expr.original_text,
                    "text_start": expr.start,
                    "text_end": expr.end,
                    "event_summary": expr.event_summary,
                    "absolute_time": expr.absolute_time,
                    "time_precision": expr.precision,
                    "is_range": expr.is_range,
                    "range_end": expr.range_end,
                    "event_type": "todo",  # Default to todo
                    "status": "pending",
                    "source_type": source_type,
                    "anchor_time": anchor_time.isoformat() if anchor_time else None
                }
                
                event_id = self.database.add_time_event(event_data)
                print(f"      Time event #{event_id}: {expr.original_text} → {expr.absolute_time[:10]}")
                if expr.event_summary:
                    print(f"        Event: {expr.event_summary}")
                    
        except Exception as e:
            print(f"    Time event extraction failed: {e}")
    
    def get_stats(self) -> dict:
        """Get processor statistics"""
        return {
            "queue": self.task_queue.get_stats(),
            "pending_documents": len(self.database.get_memories_needing_chunking()),
        }

