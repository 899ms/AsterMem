"""
Vector database operations (Chroma)

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
from typing import Callable, List, Optional, Tuple
import chromadb
from chromadb.config import Settings

from .models import Memory, Trunk
from .embedding import EmbeddingModel


class VectorStore:
    """Vector store management (backed by Chroma)"""
    
    def __init__(
        self, 
        chroma_dir: str,
        embedding_model: Optional[EmbeddingModel] = None,
        title_resolver: Optional[Callable[[str], str]] = None
    ):
        self.chroma_dir = chroma_dir
        self.embedding_model = embedding_model
        # Injected by the wiring layer: chunks only store document_id, so the owning
        # memory title is resolved from it when embedding
        self.title_resolver = title_resolver
        
        # Make sure the directory exists
        os.makedirs(chroma_dir, exist_ok=True)
        
        # Initialize the Chroma client
        self.client = chromadb.PersistentClient(
            path=chroma_dir,
            settings=Settings(anonymized_telemetry=False)
        )
        
        # Get or create collection - document level
        self.collection = self.client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"}  # Use cosine similarity
        )
        
        # Get or create collection - trunk level
        self.trunk_collection = self.client.get_or_create_collection(
            name="trunks",
            metadata={"hnsw:space": "cosine"}
        )
    
    def set_embedding_model(self, model: EmbeddingModel):
        """Set the embedding model"""
        self.embedding_model = model
    
    def ensure_collection(self):
        """Ensure the vector collections exist (they may be dropped after clearing the database)"""
        try:
            # Re-get or create the memories collection
            self.collection = self.client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"}
            )
            # Re-get or create the trunks collection
            self.trunk_collection = self.client.get_or_create_collection(
                name="trunks",
                metadata={"hnsw:space": "cosine"}
            )
            print("✅ Vector collections ensured")
        except Exception as e:
            print(f"Failed to ensure vector collections exist: {e}")
    
    def _ensure_memory_collection(self):
        """Ensure the memories collection exists ("clear all data" drops it, so recreate when missing)"""
        try:
            self.collection.count()
        except Exception as e:
            print(f"Memories collection missing, rebuilding: {e}")
            self.collection = self.client.get_or_create_collection(
                name="memories",
                metadata={"hnsw:space": "cosine"}
            )

    def add_memory(self, memory: Memory) -> bool:
        """Add a memory to the vector store"""
        if not self.embedding_model:
            return False
        
        try:
            self._ensure_memory_collection()
            # Generate the embedding vector
            text = f"{memory.title}\n\n{memory.content}"
            embedding = self.embedding_model.embed(text)
            
            # Add to Chroma
            self.collection.add(
                ids=[memory.id],
                embeddings=[embedding],
                metadatas=[{
                    "title": memory.title,
                    "tags": ",".join(memory.tags),
                    "priority": memory.priority,
                    "source": memory.source,
                }],
                documents=[text]
            )
            
            return True
        except Exception as e:
            print(f"Failed to add vector: {e}")
            return False
    
    def update_memory(self, memory: Memory) -> bool:
        """Update a memory's vector"""
        if not self.embedding_model:
            return False
        
        try:
            # Delete the old one first
            self.delete_memory(memory.id)
            # Then add the new one
            return self.add_memory(memory)
        except Exception as e:
            print(f"Failed to update vector: {e}")
            return False
    
    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory from the vector store"""
        try:
            self.collection.delete(ids=[memory_id])
            return True
        except Exception as e:
            print(f"Failed to delete vector: {e}")
            return False
    
    def search(
        self, 
        query: str, 
        limit: int = 10,
        min_score: float = 0.3
    ) -> List[Tuple[str, float]]:
        """
        Semantic search
        
        Returns:
            List of (memory_id, score) tuples
        """
        if not self.embedding_model:
            return []
        
        try:
            # Generate the query vector
            query_embedding = self.embedding_model.embed(query)
            
            # Search
            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                include=["distances"]
            )
            
            # Process results
            search_results = []
            if results["ids"] and results["ids"][0]:
                ids = results["ids"][0]
                distances = results["distances"][0] if results["distances"] else []
                
                for i, memory_id in enumerate(ids):
                    # Chroma returns distances, which need to be converted to similarity
                    # For cosine distance, similarity = 1 - distance
                    distance = distances[i] if i < len(distances) else 0
                    score = 1 - distance
                    
                    if score >= min_score:
                        search_results.append((memory_id, score))
            
            return search_results
            
        except Exception as e:
            print(f"Semantic search failed: {e}")
            return []
    
    def find_related(
        self, 
        memory_id: str, 
        limit: int = 5
    ) -> List[Tuple[str, float]]:
        """Find related memories"""
        if not self.embedding_model:
            return []
        
        try:
            # Get the memory's vector
            result = self.collection.get(
                ids=[memory_id],
                include=["embeddings"]
            )
            
            # Check whether embeddings is empty (numpy-array safe)
            embeddings = result.get("embeddings")
            if embeddings is None or len(embeddings) == 0:
                return []
            
            embedding = embeddings[0]
            
            # Search for similar ones (fetch one extra so we can exclude itself)
            results = self.collection.query(
                query_embeddings=[embedding],
                n_results=limit + 1,
                include=["distances"]
            )
            
            # Process results, excluding itself
            search_results = []
            if results["ids"] and results["ids"][0]:
                ids = results["ids"][0]
                distances = results["distances"][0] if results["distances"] else []
                
                for i, mid in enumerate(ids):
                    if mid == memory_id:
                        continue
                    
                    distance = distances[i] if i < len(distances) else 0
                    score = 1 - distance
                    search_results.append((mid, score))
                    
                    if len(search_results) >= limit:
                        break
            
            return search_results
            
        except Exception as e:
            print(f"Failed to find related memories: {e}")
            return []
    
    def get_count(self) -> int:
        """Get the number of vectors"""
        return self.collection.count()
    
    def get_all_ids(self) -> List[str]:
        """Get all vectorized memory IDs"""
        try:
            result = self.collection.get(include=[])
            return result["ids"] if result["ids"] else []
        except Exception as e:
            print(f"Failed to get the vector ID list: {e}")
            return []
    
    def has_vector(self, memory_id: str) -> bool:
        """Check whether a memory has been vectorized"""
        try:
            result = self.collection.get(ids=[memory_id], include=[])
            return bool(result["ids"])
        except:
            return False
    
    def get_all_embeddings(self) -> dict:
        """
        Get the embedding vectors of all memories
        
        Returns:
            dict: {
                "ids": List[str],
                "embeddings": List[List[float]],
                "metadatas": List[dict]
            }
        """
        try:
            result = self.collection.get(
                include=["embeddings", "metadatas", "documents"]
            )
            return {
                "ids": result["ids"] if result["ids"] else [],
                "embeddings": result["embeddings"] if result["embeddings"] is not None else [],
                "metadatas": result["metadatas"] if result["metadatas"] else [],
                "documents": result["documents"] if result["documents"] else []
            }
        except Exception as e:
            print(f"Failed to get all embedding vectors: {e}")
            return {"ids": [], "embeddings": [], "metadatas": [], "documents": []}
    
    def rebuild_index(self, memories: List[Memory]) -> int:
        """Rebuild the vector index"""
        if not self.embedding_model:
            return 0
        
        # Clear the collection
        self.client.delete_collection("memories")
        self.collection = self.client.get_or_create_collection(
            name="memories",
            metadata={"hnsw:space": "cosine"}
        )
        
        # Re-add all memories
        count = 0
        for memory in memories:
            if memory.status == "active":
                if self.add_memory(memory):
                    count += 1
        
        return count
    
    # ==================== Trunk operations ====================
    
    def _ensure_trunk_collection(self):
        """Ensure the trunk collection exists, recreating it if missing"""
        try:
            # Try to access the collection
            self.trunk_collection.count()
        except Exception as e:
            print(f"Trunk collection missing, rebuilding: {e}")
            # Recreate the collection
            self.trunk_collection = self.client.get_or_create_collection(
                name="trunks",
                metadata={"hnsw:space": "cosine"}
            )
            print("✅ Trunk vector collection rebuilt")
    
    def _trunk_embedding_text(self, trunk: Trunk) -> str:
        """
        Build the text used to embed a chunk.

        Chunks frequently lose their subject once they are split apart - a fragment such as
        "## Decision style - relies heavily on intuition" contains no personal name, so
        queries like "who am I" or "what kind of person is X" naturally score very low
        against it. Prefixing the owning memory's title gives each chunk back its document
        context.
        """
        if not self.title_resolver or not trunk.document_id:
            return trunk.content
        try:
            title = self.title_resolver(trunk.document_id) or ""
        except Exception as e:
            print(f"Failed to resolve document title ({trunk.document_id}): {e}")
            return trunk.content
        return f"{title}\n{trunk.content}" if title else trunk.content

    def add_trunk(self, trunk: Trunk) -> bool:
        """Add a trunk to the vector store"""
        if not self.embedding_model:
            return False
        
        try:
            # Make sure the collection exists
            self._ensure_trunk_collection()
            
            # Generate the embedding vector (trunk content + owning memory title)
            embedding = self.embedding_model.embed(self._trunk_embedding_text(trunk))
            
            # Add to Chroma
            self.trunk_collection.add(
                ids=[trunk.id],
                embeddings=[embedding],
                metadatas=[{
                    "document_id": trunk.document_id,
                    "order": trunk.order,
                    "tags": ",".join(trunk.tags) if trunk.tags else "",
                    "summary": trunk.summary or "",
                }],
                documents=[trunk.content]
            )
            
            return True
        except Exception as e:
            # If the error is a missing collection, try to recover
            if "does not exist" in str(e):
                try:
                    print(f"Detected a missing collection, attempting recovery...")
                    self.trunk_collection = self.client.get_or_create_collection(
                        name="trunks",
                        metadata={"hnsw:space": "cosine"}
                    )
                    # Retry the add
                    embedding = self.embedding_model.embed(self._trunk_embedding_text(trunk))
                    self.trunk_collection.add(
                        ids=[trunk.id],
                        embeddings=[embedding],
                        metadatas=[{
                            "document_id": trunk.document_id,
                            "order": trunk.order,
                            "tags": ",".join(trunk.tags) if trunk.tags else "",
                            "summary": trunk.summary or "",
                        }],
                        documents=[trunk.content]
                    )
                    print(f"✅ Recovery succeeded, trunk added to the vector store")
                    return True
                except Exception as retry_e:
                    print(f"Failed to add trunk vector (after recovery): {retry_e}")
                    return False
            print(f"Failed to add trunk vector: {e}")
            return False
    
    def update_trunk(self, trunk: Trunk) -> bool:
        """Update a trunk's vector"""
        if not self.embedding_model:
            return False
        
        try:
            # Delete the old one first
            self.delete_trunk(trunk.id)
            # Then add the new one
            return self.add_trunk(trunk)
        except Exception as e:
            print(f"Failed to update trunk vector: {e}")
            return False
    
    def delete_trunk(self, trunk_id: str) -> bool:
        """Delete a trunk from the vector store"""
        try:
            self.trunk_collection.delete(ids=[trunk_id])
            return True
        except Exception as e:
            print(f"Failed to delete trunk vector: {e}")
            return False
    
    def delete_trunks_by_document(self, document_id: str) -> int:
        """Delete all trunk vectors of a document"""
        try:
            # Query all trunks of this document
            results = self.trunk_collection.get(
                where={"document_id": document_id},
                include=[]
            )
            
            if results["ids"]:
                self.trunk_collection.delete(ids=results["ids"])
                return len(results["ids"])
            return 0
        except Exception as e:
            print(f"Failed to delete document trunk vectors: {e}")
            return 0
    
    def search_trunks(
        self, 
        query: str, 
        limit: int = 10,
        min_score: float = 0.3
    ) -> List[Tuple[str, float]]:
        """
        Semantic search over trunks
        
        Returns:
            List of (trunk_id, score) tuples
        """
        if not self.embedding_model:
            return []
        
        try:
            # Generate the query vector
            query_embedding = self.embedding_model.embed(query)
            
            # Search
            results = self.trunk_collection.query(
                query_embeddings=[query_embedding],
                n_results=limit,
                include=["distances", "metadatas"]
            )
            
            # Process results
            search_results = []
            if results["ids"] and results["ids"][0]:
                ids = results["ids"][0]
                distances = results["distances"][0] if results["distances"] else []
                
                for i, trunk_id in enumerate(ids):
                    distance = distances[i] if i < len(distances) else 0
                    score = 1 - distance
                    
                    if score >= min_score:
                        search_results.append((trunk_id, score))
            
            return search_results
            
        except Exception as e:
            print(f"Trunk semantic search failed: {e}")
            return []
    
    def find_related_trunks(
        self, 
        trunk_id: str, 
        limit: int = 5,
        current_document_id: str = None,
        same_document_boost: float = 0.1
    ) -> List[Tuple[str, float, bool]]:
        """
        Find related trunks
        
        Args:
            trunk_id: Current trunk ID
            limit: Number of results to return
            current_document_id: Current document ID (used to tell whether it is the same document)
            same_document_boost: Score boost for chunks from the same document
        
        Returns:
            List of (trunk_id, score, is_same_document) tuples
        """
        if not self.embedding_model:
            return []
        
        try:
            # Get the trunk's vector
            result = self.trunk_collection.get(
                ids=[trunk_id],
                include=["embeddings", "metadatas"]
            )
            
            embeddings = result.get("embeddings")
            if embeddings is None or len(embeddings) == 0:
                return []
            
            embedding = embeddings[0]
            
            # Get the current trunk's document_id
            metadatas = result.get("metadatas")
            if current_document_id is None and metadatas and len(metadatas) > 0:
                current_document_id = metadatas[0].get("document_id")
            
            # Search for similar ones (fetch extras because we exclude itself)
            results = self.trunk_collection.query(
                query_embeddings=[embedding],
                n_results=limit + 10,
                include=["distances", "metadatas"]
            )
            
            # Process results
            search_results = []
            if results["ids"] and results["ids"][0]:
                ids = results["ids"][0]
                distances = results["distances"][0] if results["distances"] else []
                metadatas = results["metadatas"][0] if results["metadatas"] else []
                
                for i, tid in enumerate(ids):
                    if tid == trunk_id:
                        continue
                    
                    distance = distances[i] if i < len(distances) else 0
                    score = 1 - distance
                    
                    # Determine whether it is from the same document
                    meta = metadatas[i] if i < len(metadatas) else {}
                    doc_id = meta.get("document_id", "")
                    is_same_doc = (doc_id == current_document_id) if current_document_id else False
                    
                    # Same-document boost
                    if is_same_doc:
                        score += same_document_boost
                    
                    search_results.append((tid, score, is_same_doc))
                    
                    if len(search_results) >= limit:
                        break
            
            # Sort by score
            search_results.sort(key=lambda x: x[1], reverse=True)
            return search_results[:limit]
            
        except Exception as e:
            print(f"Failed to find related trunks: {e}")
            return []
    
    def get_trunk_count(self) -> int:
        """Get the number of trunk vectors"""
        return self.trunk_collection.count()
    
    def get_all_trunk_ids(self) -> List[str]:
        """Get all vectorized trunk IDs"""
        try:
            # Make sure the collection exists
            self._ensure_trunk_collection()
            result = self.trunk_collection.get(include=[])
            return result["ids"] if result["ids"] else []
        except Exception as e:
            print(f"Failed to get the trunk ID list: {e}")
            return []
    
    def get_all_trunk_embeddings(self) -> dict:
        """
        Get the embedding vectors of all trunks
        
        Returns:
            dict: {
                "ids": List[str],
                "embeddings": List[List[float]],
                "metadatas": List[dict],
                "documents": List[str]
            }
        """
        try:
            result = self.trunk_collection.get(
                include=["embeddings", "metadatas", "documents"]
            )
            return {
                "ids": result["ids"] if result["ids"] else [],
                "embeddings": result["embeddings"] if result["embeddings"] is not None else [],
                "metadatas": result["metadatas"] if result["metadatas"] else [],
                "documents": result["documents"] if result["documents"] else []
            }
        except Exception as e:
            print(f"Failed to get all trunk embedding vectors: {e}")
            return {"ids": [], "embeddings": [], "metadatas": [], "documents": []}
    
    def rebuild_trunk_index(self, trunks: List[Trunk]) -> int:
        """Rebuild the trunk vector index"""
        if not self.embedding_model:
            return 0
        
        # Clear the collection
        try:
            self.client.delete_collection("trunks")
        except:
            pass
        self.trunk_collection = self.client.get_or_create_collection(
            name="trunks",
            metadata={"hnsw:space": "cosine"}
        )
        
        # Re-add all trunks
        count = 0
        for trunk in trunks:
            if trunk.status == "ready":
                if self.add_trunk(trunk):
                    count += 1
        
        return count

