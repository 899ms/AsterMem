"""
Data model definitions

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List
import uuid
import json


def generate_memory_id() -> str:
    """Generate a unique memory ID"""
    return f"mem_{uuid.uuid4().hex[:8]}"


def generate_trunk_id() -> str:
    """Generate a unique Trunk ID"""
    return f"trunk_{uuid.uuid4().hex[:8]}"


@dataclass
class Memory:
    """Memory data model"""
    id: str
    title: str
    content: str
    tags: List[str] = field(default_factory=list)
    priority: int = 5
    version: int = 1
    source: str = "api"  # api / user
    status: str = "active"  # active / archived
    file_path: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # Trunk-related fields
    trunk_ids: List[str] = field(default_factory=list)  # Trunk ID list, in order
    trunk_status: str = "not_chunked"  # not_chunked / chunking / ready / error

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "priority": self.priority,
            "version": self.version,
            "source": self.source,
            "status": self.status,
            "file_path": self.file_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "trunk_ids": self.trunk_ids,
            "trunk_status": self.trunk_status,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Memory":
        """Create from dictionary"""
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
            
        tags = data.get("tags", [])
        if isinstance(tags, str):
            tags = json.loads(tags) if tags else []
        
        trunk_ids = data.get("trunk_ids", [])
        if isinstance(trunk_ids, str):
            trunk_ids = json.loads(trunk_ids) if trunk_ids else []
            
        return cls(
            id=data["id"],
            title=data["title"],
            content=data["content"],
            tags=tags,
            priority=data.get("priority", 5),
            version=data.get("version", 1),
            source=data.get("source", "api"),
            status=data.get("status", "active"),
            file_path=data.get("file_path"),
            created_at=created_at,
            updated_at=updated_at,
            trunk_ids=trunk_ids,
            trunk_status=data.get("trunk_status", "not_chunked"),
        )

    def to_frontmatter(self) -> str:
        """Generate YAML Front Matter"""
        lines = [
            "---",
            f"id: {self.id}",
            f"title: {self.title}",
        ]
        
        if self.tags:
            lines.append("tags:")
            for tag in self.tags:
                lines.append(f"  - {tag}")
        
        lines.extend([
            f"priority: {self.priority}",
            f"version: {self.version}",
            f"created_at: {self.created_at.isoformat() if self.created_at else ''}",
            f"updated_at: {self.updated_at.isoformat() if self.updated_at else ''}",
            f"source: {self.source}",
            f"status: {self.status}",
            "---",
            "",
            self.content,
        ])
        
        return "\n".join(lines)


@dataclass
class MemoryHistory:
    """Memory history version"""
    id: Optional[int] = None
    memory_id: str = ""
    version: int = 1
    title: str = ""
    content: str = ""
    tags: List[str] = field(default_factory=list)
    priority: int = 5
    changed_at: Optional[datetime] = None

    def __post_init__(self):
        if self.changed_at is None:
            self.changed_at = datetime.now()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "memory_id": self.memory_id,
            "version": self.version,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "priority": self.priority,
            "changed_at": self.changed_at.isoformat() if self.changed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryHistory":
        changed_at = data.get("changed_at")
        if isinstance(changed_at, str):
            changed_at = datetime.fromisoformat(changed_at)
            
        tags = data.get("tags", [])
        if isinstance(tags, str):
            tags = json.loads(tags) if tags else []
            
        return cls(
            id=data.get("id"),
            memory_id=data["memory_id"],
            version=data["version"],
            title=data["title"],
            content=data["content"],
            tags=tags,
            priority=data.get("priority", 5),
            changed_at=changed_at,
        )


@dataclass
class SearchResult:
    """Search result"""
    memory: Memory
    score: float = 0.0
    match_type: str = "keyword"  # keyword / semantic / hybrid
    keyword_matches: int = 0
    
    def to_dict(self) -> dict:
        return {
            "memory": self.memory.to_dict(),
            "score": self.score,
            "match_type": self.match_type,
            "keyword_matches": self.keyword_matches,
        }


@dataclass
class Trunk:
    """
    Document chunk (Trunk) data model
    
    Full multimodal support: text, image, audio, video, etc.
    Contains explicit tags (user-visible) and implicit Meta tags (AI-extracted, for search enhancement)
    """
    id: str
    document_id: str  # Parent document ID (mem_xxx)
    order: int  # Order within the document (0, 1, 2...)
    content: str  # Original text or file path (for images/audio/video)
    content_type: str = "text"  # Content type: text / image / audio / video
    summary: Optional[str] = None  # AI-generated summary/description
    tags: List[str] = field(default_factory=list)  # Trunk-level user-visible tags
    # Meta metadata (AI-extracted implicit tags)
    meta: Optional[dict] = None  # JSON-format metadata
    meta_tags: List[str] = field(default_factory=list)  # Flat list of meta tags
    meta_status: str = "pending"  # Meta extraction status: pending / processing / ready / error
    status: str = "pending"  # pending / processing / ready / error
    # Image-specific fields
    image_url: Optional[str] = None  # Image access URL
    image_description: Optional[str] = None  # AI-generated detailed image description
    image_ocr: Optional[str] = None  # OCR-recognized text
    image_exif: Optional[dict] = None  # EXIF metadata
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()
        if self.updated_at is None:
            self.updated_at = datetime.now()

    def to_dict(self) -> dict:
        """Convert to dictionary"""
        return {
            "id": self.id,
            "document_id": self.document_id,
            "order": self.order,
            "content": self.content,
            "content_type": self.content_type,
            "summary": self.summary,
            "tags": self.tags,
            "meta": self.meta,
            "meta_tags": self.meta_tags,
            "meta_status": self.meta_status,
            "status": self.status,
            "image_url": self.image_url,
            "image_description": self.image_description,
            "image_ocr": self.image_ocr,
            "image_exif": self.image_exif,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Trunk":
        """Create from dictionary"""
        created_at = data.get("created_at")
        updated_at = data.get("updated_at")
        
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)
            
        tags = data.get("tags", [])
        if isinstance(tags, str):
            tags = json.loads(tags) if tags else []
        
        meta = data.get("meta")
        if isinstance(meta, str):
            meta = json.loads(meta) if meta else None
        
        meta_tags = data.get("meta_tags", [])
        if isinstance(meta_tags, str):
            meta_tags = json.loads(meta_tags) if meta_tags else []
        
        image_exif = data.get("image_exif")
        if isinstance(image_exif, str):
            image_exif = json.loads(image_exif) if image_exif else None
            
        return cls(
            id=data["id"],
            document_id=data["document_id"],
            order=data["order"],
            content=data["content"],
            content_type=data.get("content_type", "text"),
            summary=data.get("summary"),
            tags=tags,
            meta=meta,
            meta_tags=meta_tags,
            meta_status=data.get("meta_status", "pending"),
            status=data.get("status", "pending"),
            image_url=data.get("image_url"),
            image_description=data.get("image_description"),
            image_ocr=data.get("image_ocr"),
            image_exif=image_exif,
            created_at=created_at,
            updated_at=updated_at,
        )
    
    def get_searchable_meta_text(self) -> str:
        """Get searchable meta text (concatenation of all meta tags)"""
        if not self.meta_tags:
            return ""
        return " ".join(self.meta_tags)


@dataclass
class TrunkSearchResult:
    """Trunk search result"""
    trunk: Trunk
    document_title: str  # Parent document title
    score: float = 0.0
    match_type: str = "semantic"
    is_same_document: bool = False  # Whether belonging to the same document as current
    keyword_matches: int = 0  # Number of keyword matches
    
    def to_dict(self) -> dict:
        return {
            "trunk": self.trunk.to_dict(),
            "document_title": self.document_title,
            "score": self.score,
            "match_type": self.match_type,
            "is_same_document": self.is_same_document,
            "keyword_matches": self.keyword_matches,
        }

