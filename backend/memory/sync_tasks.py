"""
Sync task state management

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import threading
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ItemStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class TaskItem:
    """A single item in a task"""
    id: str
    title: str
    status: ItemStatus = ItemStatus.PENDING
    message: str = ""
    ai_tags: List[str] = field(default_factory=list)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status.value,
            "message": self.message,
            "ai_tags": self.ai_tags,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


@dataclass
class SyncTask:
    """Sync task"""
    id: str
    type: str  # "full_sync", "user_sync"
    status: TaskStatus = TaskStatus.PENDING
    items: List[TaskItem] = field(default_factory=list)
    total: int = 0
    processed: int = 0
    success: int = 0
    failed: int = 0
    skipped: int = 0
    ai_tagged: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "status": self.status.value,
            "total": self.total,
            "processed": self.processed,
            "success": self.success,
            "failed": self.failed,
            "skipped": self.skipped,
            "ai_tagged": self.ai_tagged,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "error": self.error,
            "items": [item.to_dict() for item in self.items],
        }


class SyncTaskManager:
    """Sync task manager"""
    
    def __init__(self, max_tasks: int = 10):
        self.tasks: Dict[str, SyncTask] = {}
        self.max_tasks = max_tasks
        self.lock = threading.Lock()
        self._task_counter = 0
    
    def create_task(self, task_type: str) -> SyncTask:
        """Create a new task"""
        with self.lock:
            self._task_counter += 1
            task_id = f"sync_{self._task_counter}_{datetime.now().strftime('%H%M%S')}"
            
            task = SyncTask(
                id=task_id,
                type=task_type,
                status=TaskStatus.PENDING,
                started_at=datetime.now()
            )
            
            self.tasks[task_id] = task
            
            # Clean up old tasks (keep the most recent max_tasks)
            if len(self.tasks) > self.max_tasks:
                old_ids = sorted(self.tasks.keys())[:-self.max_tasks]
                for old_id in old_ids:
                    del self.tasks[old_id]
            
            return task
    
    def get_task(self, task_id: str) -> Optional[SyncTask]:
        """Get a task"""
        return self.tasks.get(task_id)
    
    def get_latest_task(self) -> Optional[SyncTask]:
        """Get the latest task"""
        if not self.tasks:
            return None
        latest_id = max(self.tasks.keys())
        return self.tasks.get(latest_id)
    
    def get_running_task(self) -> Optional[SyncTask]:
        """Get the currently running task"""
        for task in self.tasks.values():
            if task.status == TaskStatus.RUNNING:
                return task
        return None
    
    def list_tasks(self, limit: int = 10) -> List[SyncTask]:
        """List tasks"""
        sorted_tasks = sorted(
            self.tasks.values(),
            key=lambda t: t.started_at or datetime.min,
            reverse=True
        )
        return sorted_tasks[:limit]
    
    def start_task(self, task: SyncTask, items: List[Dict[str, str]]):
        """Start a task"""
        with self.lock:
            task.status = TaskStatus.RUNNING
            task.total = len(items)
            task.items = [
                TaskItem(id=item["id"], title=item["title"])
                for item in items
            ]
    
    def update_item(
        self, 
        task: SyncTask, 
        item_id: str, 
        status: ItemStatus,
        message: str = "",
        ai_tags: List[str] = None
    ):
        """Update task item status"""
        with self.lock:
            for item in task.items:
                if item.id == item_id:
                    if item.status == ItemStatus.PENDING:
                        item.started_at = datetime.now()
                    
                    item.status = status
                    item.message = message
                    if ai_tags:
                        item.ai_tags = ai_tags
                    
                    if status in [ItemStatus.SUCCESS, ItemStatus.FAILED, ItemStatus.SKIPPED]:
                        item.finished_at = datetime.now()
                        task.processed += 1
                        
                        if status == ItemStatus.SUCCESS:
                            task.success += 1
                            if ai_tags:
                                task.ai_tagged += 1
                        elif status == ItemStatus.FAILED:
                            task.failed += 1
                        elif status == ItemStatus.SKIPPED:
                            task.skipped += 1
                    
                    break
    
    def finish_task(self, task: SyncTask, error: str = None):
        """Finish a task"""
        with self.lock:
            task.finished_at = datetime.now()
            if error:
                task.status = TaskStatus.FAILED
                task.error = error
            else:
                task.status = TaskStatus.SUCCESS


# Global task manager instance
_sync_task_manager: Optional[SyncTaskManager] = None


def get_sync_task_manager() -> SyncTaskManager:
    """Get the global task manager"""
    global _sync_task_manager
    if _sync_task_manager is None:
        _sync_task_manager = SyncTaskManager()
    return _sync_task_manager

