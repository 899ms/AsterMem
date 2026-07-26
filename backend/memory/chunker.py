"""
Document chunking module (Chunker)

Responsible for splitting documents into semantically coherent segments (Trunks).
Supports AI-assisted chunking and intelligent merging/splitting.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import re
from typing import List, Tuple, Optional
from dataclasses import dataclass

from .models import Trunk, generate_trunk_id
# After refactoring, Chat models are unified as protocol adapters in providers.py,
# providing .chat() / .generate_raw() / .generate_tags() via duck typing; used here only for type hints.
from .embedding import OpenAICompatibleChat


# Configuration constants
MIN_TRUNK_LENGTH = 100      # Minimum trunk length (characters)
MAX_TRUNK_LENGTH = 2000     # Maximum trunk length (characters)
PARAGRAPH_SEPARATORS = ['\n\n', '\n']  # Paragraph separator priority


@dataclass
class Paragraph:
    """Raw paragraph"""
    index: int      # Index (starting from 1)
    content: str    # Paragraph content
    length: int     # Character length


class Chunker:
    """Document chunker"""
    
    def __init__(self, chat_model: Optional[OpenAICompatibleChat] = None):
        self.chat_model = chat_model
    
    def chunk(self, content: str) -> List[str]:
        """
        Perform chunking and return a list of content strings (without creating Trunk objects).
        Used for preview and processing before import.
        """
        # Reuse the core logic of chunk_document, but with an empty document_id
        trunks = self.chunk_document("temp", content)
        return [t.content for t in trunks]

    def chunk_document(self, document_id: str, content: str) -> List[Trunk]:
        """
        Split a document into multiple Trunks.
        
        Args:
            document_id: Document ID
            content: Document content
        
        Returns:
            List of Trunks
        """
        # 1. Split by natural paragraphs
        paragraphs = self._split_into_paragraphs(content)
        
        if not paragraphs:
            # Empty content, return empty list
            return []
        
        if len(paragraphs) == 1:
            # Only one paragraph, use it as a single trunk
            return self._create_trunks_from_groups(
                document_id, 
                paragraphs, 
                [[0]]
            )
        
        # 2. Attempt AI chunking
        groups = None
        if self.chat_model:
            try:
                groups = self._ai_chunk(paragraphs)
            except Exception as e:
                print(f"AI chunking failed, falling back to heuristic chunking: {e}")
        
        # 3. If AI chunking failed, use heuristic chunking
        if groups is None:
            groups = self._heuristic_chunk(paragraphs)
        
        # 4. Merge short groups
        groups = self._merge_short_groups(paragraphs, groups)
        
        # 5. Split long groups
        groups = self._split_long_groups(paragraphs, groups)
        
        # 6. Create Trunk objects
        trunks = self._create_trunks_from_groups(document_id, paragraphs, groups)
        
        return trunks
    
    def _split_into_paragraphs(self, content: str) -> List[Paragraph]:
        """
        Split content by natural paragraphs.
        
        Prefers splitting by double newlines; falls back to single newlines if too few results.
        """
        content = content.strip()
        if not content:
            return []
        
        # First try splitting by double newlines
        parts = re.split(r'\n\s*\n', content)
        parts = [p.strip() for p in parts if p.strip()]
        
        # If only one large paragraph, try splitting by single newlines
        if len(parts) == 1 and len(parts[0]) > MAX_TRUNK_LENGTH:
            parts = content.split('\n')
            parts = [p.strip() for p in parts if p.strip()]
        
        paragraphs = []
        for i, part in enumerate(parts):
            paragraphs.append(Paragraph(
                index=i + 1,  # Index starts from 1
                content=part,
                length=len(part)
            ))
        
        return paragraphs
    
    def _ai_chunk(self, paragraphs: List[Paragraph]) -> List[List[int]]:
        """
        Use AI for semantic chunking.
        
        Returns:
            List of groups, where each group is a list of paragraph indices (0-based)
        """
        if not self.chat_model:
            return None
        
        # Build paragraph list text (limit length to avoid exceeding context)
        para_texts = []
        total_len = 0
        max_preview_len = 200  # Maximum characters shown per paragraph
        
        for p in paragraphs:
            preview = p.content[:max_preview_len]
            if len(p.content) > max_preview_len:
                preview += "..."
            para_texts.append(f"[{p.index}] {preview}")
            total_len += len(preview)
            
            # If total length exceeds 8000 characters, truncate
            if total_len > 8000:
                para_texts.append(f"... ({len(paragraphs)} paragraphs total)")
                break
        
        prompt = f"""You are a document chunking assistant. Below is a list of numbered paragraphs from a document.

Please group these paragraphs by semantics. Each group should be a complete topic or logical unit.

Paragraph list:
{chr(10).join(para_texts)}

Output the grouping result directly in this format: 1-2, 3, 4-6, 7-9
- Single paragraphs as numbers (e.g. 3)
- Consecutive paragraphs with hyphens (e.g. 1-2 means paragraphs 1 and 2 form one group)
- Separate groups with commas
- Do not output any other explanation

Grouping result:"""

        try:
            response = self.chat_model.chat([
                {"role": "user", "content": prompt}
            ], temperature=0.1)
            
            # Parse response
            groups = self._parse_ai_response(response, len(paragraphs))
            return groups
            
        except Exception as e:
            print(f"AI chunking call failed: {e}")
            return None
    
    def _parse_ai_response(self, response: str, total_paragraphs: int) -> List[List[int]]:
        """
        Parse the AI-returned grouping result.
        
        Handles hallucinations: if paragraphs are skipped, they become independent groups.
        """
        response = response.strip()
        # Remove possible prefix
        if response.startswith("分组结果：") or response.startswith("分组结果:"):
            response = response[5:]
        response = response.strip()
        
        # Parse groups
        groups = []
        covered = set()
        
        # Split groups
        parts = re.split(r'[,，、\s]+', response)
        
        for part in parts:
            part = part.strip()
            if not part:
                continue
            
            # Parse range (e.g. "1-3") or single number (e.g. "5")
            if '-' in part:
                match = re.match(r'(\d+)\s*[-–—]\s*(\d+)', part)
                if match:
                    start = int(match.group(1))
                    end = int(match.group(2))
                    # Convert to 0-based indices
                    indices = list(range(start - 1, end))
                    # Filter valid indices
                    indices = [i for i in indices if 0 <= i < total_paragraphs]
                    if indices:
                        groups.append(indices)
                        covered.update(indices)
            else:
                match = re.match(r'(\d+)', part)
                if match:
                    idx = int(match.group(1)) - 1  # Convert to 0-based
                    if 0 <= idx < total_paragraphs:
                        groups.append([idx])
                        covered.add(idx)
        
        # Fallback: handle skipped paragraphs
        all_indices = set(range(total_paragraphs))
        missed = all_indices - covered
        
        if missed:
            # Merge consecutive missed paragraphs into one group
            missed_list = sorted(missed)
            current_group = [missed_list[0]]
            
            for i in range(1, len(missed_list)):
                if missed_list[i] == missed_list[i-1] + 1:
                    # Consecutive
                    current_group.append(missed_list[i])
                else:
                    # Not consecutive, save current group and start a new one
                    groups.append(current_group)
                    current_group = [missed_list[i]]
            
            # Save the last group
            if current_group:
                groups.append(current_group)
        
        # Sort by first index
        groups.sort(key=lambda g: g[0] if g else 0)
        
        return groups
    
    def _heuristic_chunk(self, paragraphs: List[Paragraph]) -> List[List[int]]:
        """
        Heuristic chunking (when AI is unavailable).
        
        Strategy: aim for each group to be approximately 500-1000 characters.
        """
        groups = []
        current_group = []
        current_length = 0
        target_length = 800  # Target length
        
        for i, p in enumerate(paragraphs):
            current_group.append(i)
            current_length += p.length
            
            # If current group length exceeds target, or a clear separator is encountered (e.g. heading)
            if current_length >= target_length:
                groups.append(current_group)
                current_group = []
                current_length = 0
        
        # Handle remaining paragraphs
        if current_group:
            groups.append(current_group)
        
        return groups
    
    def _merge_short_groups(
        self, 
        paragraphs: List[Paragraph], 
        groups: List[List[int]]
    ) -> List[List[int]]:
        """
        Merge groups that are too short.
        
        If a group's length < MIN_TRUNK_LENGTH, attempt to merge with adjacent groups.
        """
        if len(groups) <= 1:
            return groups
        
        def get_group_length(group: List[int]) -> int:
            return sum(paragraphs[i].length for i in group)
        
        merged = []
        i = 0
        
        while i < len(groups):
            current = groups[i]
            current_len = get_group_length(current)
            
            # If current group is too short, attempt to merge
            if current_len < MIN_TRUNK_LENGTH:
                # Prefer merging with the next group
                if i + 1 < len(groups):
                    next_group = groups[i + 1]
                    merged.append(current + next_group)
                    i += 2
                    continue
                # If there's no next group, merge with the previous one
                elif merged:
                    merged[-1].extend(current)
                    i += 1
                    continue
            
            merged.append(current)
            i += 1
        
        return merged
    
    def _split_long_groups(
        self, 
        paragraphs: List[Paragraph], 
        groups: List[List[int]]
    ) -> List[List[int]]:
        """
        Split groups that are too long.
        
        If a group's length > MAX_TRUNK_LENGTH, force split it.
        """
        def get_group_length(group: List[int]) -> int:
            return sum(paragraphs[i].length for i in group)
        
        result = []
        
        for group in groups:
            group_len = get_group_length(group)
            
            if group_len <= MAX_TRUNK_LENGTH:
                result.append(group)
            else:
                # Needs splitting
                current_split = []
                current_len = 0
                
                for idx in group:
                    para_len = paragraphs[idx].length
                    
                    # If a single paragraph exceeds max length
                    if para_len > MAX_TRUNK_LENGTH:
                        # Save previous
                        if current_split:
                            result.append(current_split)
                            current_split = []
                            current_len = 0
                        # Make it a standalone group (can't split further even if too long)
                        result.append([idx])
                    elif current_len + para_len > MAX_TRUNK_LENGTH:
                        # Exceeds limit, save current group and start a new one
                        if current_split:
                            result.append(current_split)
                        current_split = [idx]
                        current_len = para_len
                    else:
                        current_split.append(idx)
                        current_len += para_len
                
                if current_split:
                    result.append(current_split)
        
        return result
    
    def _create_trunks_from_groups(
        self, 
        document_id: str, 
        paragraphs: List[Paragraph], 
        groups: List[List[int]]
    ) -> List[Trunk]:
        """
        Create Trunk objects from groups.
        """
        trunks = []
        
        for order, group in enumerate(groups):
            # Merge content of paragraphs within the group
            contents = [paragraphs[i].content for i in group]
            combined_content = '\n\n'.join(contents)
            
            trunk = Trunk(
                id=generate_trunk_id(),
                document_id=document_id,
                order=order,
                content=combined_content,
                summary=None,  # Generated later
                tags=[],       # Generated later
                status="pending"
            )
            trunks.append(trunk)
        
        return trunks
    
    def generate_trunk_summary(self, trunk: Trunk) -> str:
        """
        Generate a summary for a Trunk.
        """
        if not self.chat_model:
            # No AI available, return the first 50 characters of content
            return trunk.content[:50] + "..." if len(trunk.content) > 50 else trunk.content
        
        # Truncate content for preview
        content_preview = trunk.content[:1500] if len(trunk.content) > 1500 else trunk.content
        
        prompt = f"""Summarize the core point of the following content in one sentence (50 words or fewer, output the summary directly with no other explanation):

{content_preview}

Summary:"""

        try:
            response = self.chat_model.chat([
                {"role": "user", "content": prompt}
            ], temperature=0.3)
            
            # Clean up response
            summary = response.strip()
            if summary.startswith("摘要：") or summary.startswith("摘要:"):
                summary = summary[3:]
            summary = summary.strip().strip('"\'')
            
            # Limit length
            if len(summary) > 80:
                summary = summary[:77] + "..."
            
            return summary
            
        except Exception as e:
            print(f"Failed to generate summary: {e}")
            return trunk.content[:50] + "..." if len(trunk.content) > 50 else trunk.content
    
    def generate_trunk_tags(
        self, 
        trunk: Trunk, 
        existing_tags: List[str] = None, 
        tag_tree: List[str] = None,
        similar_tags: List[str] = None
    ) -> List[str]:
        """
        Generate hierarchical tags for a Trunk.
        
        Args:
            trunk: The Trunk to tag
            existing_tags: Existing tags (to avoid duplicates)
            tag_tree: All tags in the system (used to constrain generation)
            similar_tags: Tags used by similar content (obtained via semantic search, given priority)
        """
        if not self.chat_model:
            return []
        
        try:
            tags = self.chat_model.generate_tags(
                title="",  # trunks have no title
                content=trunk.content,
                existing_tags=existing_tags,
                tag_tree=tag_tree,
                similar_tags=similar_tags
            )
            return tags
        except Exception as e:
            print(f"Failed to generate tags: {e}")
            return []


def create_chunker(config: dict) -> Chunker:
    """
    Create a Chunker from configuration.
    """
    from .embedding import get_chat_model

    # Chunking/summary usage is attributed to "chunking"; tagging goes through generate_tags which auto-attributes to "tagging"
    chat_model = get_chat_model(config, caller="chunking")
    return Chunker(chat_model=chat_model)

