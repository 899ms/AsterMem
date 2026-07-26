"""
Markdown file storage

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import re
from datetime import datetime
from typing import Optional, List
import frontmatter

from .models import Memory, generate_memory_id


class MemoryStorage:
    """Markdown file storage manager"""
    
    def __init__(self, memories_dir: str):
        self.memories_dir = memories_dir
        self.api_dir = os.path.join(memories_dir, "api")
        self.user_dir = os.path.join(memories_dir, "user")
        
        # Ensure directories exist
        os.makedirs(self.api_dir, exist_ok=True)
        os.makedirs(self.user_dir, exist_ok=True)
    
    def save_memory(self, memory: Memory) -> str:
        """Save memory to an MD file"""
        # Determine save directory
        if memory.source == "api":
            save_dir = self.api_dir
        else:
            save_dir = self.user_dir
        
        # Generate file path
        filename = f"{memory.id}.md"
        file_path = os.path.join(save_dir, filename)
        
        # Generate file content
        content = memory.to_frontmatter()
        
        # Write file
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        
        # Update memory's file path
        memory.file_path = os.path.relpath(file_path, self.memories_dir)
        
        return file_path
    
    def load_memory(self, file_path: str) -> Optional[Memory]:
        """Load memory from an MD file"""
        full_path = file_path
        if not os.path.isabs(file_path):
            full_path = os.path.join(self.memories_dir, file_path)
        
        if not os.path.exists(full_path):
            return None
        
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)
            
            # Parse metadata
            metadata = post.metadata
            content = post.content
            
            # If no ID exists, generate a stable one
            memory_id = metadata.get("id")
            if not memory_id:
                # Extract from filename or generate a stable ID
                basename = os.path.basename(file_path)
                if basename.startswith("mem_"):
                    memory_id = basename.replace(".md", "")
                else:
                    # For user directory files, generate a stable ID from file path
                    # to avoid duplicate imports on each startup
                    import hashlib
                    path_hash = hashlib.md5(file_path.encode()).hexdigest()[:12]
                    memory_id = f"mem_{path_hash}"
            
            # Parse tags
            tags = metadata.get("tags", [])
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",")]
            
            # Parse timestamps
            created_at = metadata.get("created_at")
            updated_at = metadata.get("updated_at")
            
            if isinstance(created_at, str):
                try:
                    created_at = datetime.fromisoformat(created_at)
                except ValueError:
                    created_at = datetime.now()
            elif created_at is None:
                created_at = datetime.now()
            
            if isinstance(updated_at, str):
                try:
                    updated_at = datetime.fromisoformat(updated_at)
                except ValueError:
                    updated_at = datetime.now()
            elif updated_at is None:
                updated_at = datetime.now()
            
            # Determine source
            source = metadata.get("source", "user")
            if "api" in file_path:
                source = "api"
            elif "user" in file_path:
                source = "user"
            
            memory = Memory(
                id=memory_id,
                title=metadata.get("title", self._extract_title(content)),
                content=content,
                tags=tags,
                priority=int(metadata.get("priority", 5)),
                version=int(metadata.get("version", 1)),
                source=source,
                status=metadata.get("status", "active"),
                file_path=os.path.relpath(full_path, self.memories_dir),
                created_at=created_at,
                updated_at=updated_at,
            )
            
            return memory
            
        except Exception as e:
            print(f"Failed to load memory file: {file_path}, error: {e}")
            return None
    
    def _extract_title(self, content: str) -> str:
        """Extract title from content"""
        lines = content.strip().split("\n")
        for line in lines:
            line = line.strip()
            if line:
                # Remove Markdown heading markers
                title = re.sub(r"^#+\s*", "", line)
                return title[:100]  # Limit length
        return "Untitled"
    
    def delete_memory(self, memory: Memory) -> bool:
        """Delete memory file"""
        if memory.file_path:
            full_path = os.path.join(self.memories_dir, memory.file_path)
            if os.path.exists(full_path):
                os.remove(full_path)
                return True
        return False
    
    def scan_user_memories(self) -> List[Memory]:
        """Scan memory files in the user directory"""
        memories = []
        
        if not os.path.exists(self.user_dir):
            return memories
        
        for filename in os.listdir(self.user_dir):
            if filename.endswith(".md"):
                file_path = os.path.join("user", filename)
                memory = self.load_memory(file_path)
                if memory:
                    memories.append(memory)
        
        return memories
    
    def scan_all_memories(self) -> List[Memory]:
        """Scan all memory files"""
        memories = []
        
        # Scan API directory
        if os.path.exists(self.api_dir):
            for filename in os.listdir(self.api_dir):
                if filename.endswith(".md"):
                    file_path = os.path.join("api", filename)
                    memory = self.load_memory(file_path)
                    if memory:
                        memories.append(memory)
        
        # Scan user directory
        memories.extend(self.scan_user_memories())
        
        return memories
    
    def export_memories(self, memories: List[Memory], export_path: str) -> str:
        """Export memories to the specified directory"""
        import json
        import zipfile
        from io import BytesIO
        
        # Create ZIP file
        zip_buffer = BytesIO()
        
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            # Add each memory's MD file
            for memory in memories:
                content = memory.to_frontmatter()
                filename = f"{memory.id}.md"
                zip_file.writestr(filename, content)
            
            # Add index file
            index = {
                "export_time": datetime.now().isoformat(),
                "count": len(memories),
                "memories": [m.to_dict() for m in memories]
            }
            zip_file.writestr("index.json", json.dumps(index, ensure_ascii=False, indent=2))
        
        # Save ZIP file
        with open(export_path, "wb") as f:
            f.write(zip_buffer.getvalue())
        
        return export_path
    
    def import_memories(self, import_path: str) -> List[Memory]:
        """Import memories from a ZIP file"""
        import zipfile
        
        memories = []
        
        with zipfile.ZipFile(import_path, "r") as zip_file:
            for filename in zip_file.namelist():
                if filename.endswith(".md"):
                    content = zip_file.read(filename).decode("utf-8")
                    
                    # Parse content
                    post = frontmatter.loads(content)
                    metadata = post.metadata
                    
                    # Generate new ID (to avoid conflicts)
                    memory_id = generate_memory_id()
                    
                    tags = metadata.get("tags", [])
                    if isinstance(tags, str):
                        tags = [t.strip() for t in tags.split(",")]
                    
                    memory = Memory(
                        id=memory_id,
                        title=metadata.get("title", "Imported memory"),
                        content=post.content,
                        tags=tags,
                        priority=int(metadata.get("priority", 5)),
                        version=1,  # Reset version
                        source="api",  # Mark imported memories as API
                        status="active",
                    )
                    
                    memories.append(memory)
        
        return memories

