"""
AI Explorer - AI narration + Trunk content display
True SSE streaming response

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import json
import re
import time
import asyncio
import httpx
from typing import List, Dict, Any, AsyncGenerator, Optional

from .search import SearchEngine
from .usage_tracker import estimate_tokens, record_usage


class AIExplorer:
    def __init__(self, search_engine: SearchEngine, config: dict):
        self.search_engine = search_engine
        self.config = config
        
        # Background: AI exploration streams directly via OpenAI-compatible chat/completions requests,
        # bypassing providers.py's non-streaming adapter (user-visible LLM responses must be streamed).
        # Design intent: Get connection triplet from the Provider registry for the current chat provider;
        # Gemini protocol providers use Google's official OpenAI-compatible endpoint (/v1beta/openai),
        # model name strips "models/" prefix to match that endpoint's naming convention.
        from .providers import normalize_config, get_provider_entry, resolve_api_key

        normalize_config(config)
        provider_id = (config.get("active") or {}).get("chat_provider", "lmstudio")
        entry = get_provider_entry(config, provider_id) or {}
        self.mode = provider_id

        base_url = (entry.get("base_url") or "http://localhost:1234/v1").rstrip("/")
        model = entry.get("chat_model") or "qwen2.5-7b-instruct"
        if entry.get("api_type") == "gemini":
            base_url = f"{base_url}/openai" if base_url.endswith("/v1beta") else base_url
            model = model.removeprefix("models/")
        self.base_url = base_url
        self.model = model
        self.api_key = resolve_api_key(entry) or None

        print(f"[AIExplorer] Initialized: provider={provider_id}, model={self.model}")

    def _get_headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _record_usage(self, usage: Optional[dict], prompt_text: str, output_text: str,
                      duration_ms: int, status: str = "success", error: str = None) -> None:
        """
        Exploration uses httpx direct connection bypass, not the providers adapter; usage is reported here.
        Uses real values when upstream returns usage; estimates by character count with estimated flag for streaming.
        """
        usage = usage or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        details = usage.get("prompt_tokens_details") or {}
        cached_tokens = int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)
        estimated = prompt_tokens == 0 and completion_tokens == 0
        if estimated and status == "success":
            prompt_tokens = estimate_tokens(prompt_text)
            completion_tokens = estimate_tokens(output_text)
        record_usage(caller="explore", kind="chat", model=self.model,
                     provider=self.mode, provider_name=self.mode,
                     prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                     cached_tokens=cached_tokens, estimated=estimated,
                     duration_ms=duration_ms, status=status, error=error)

    @staticmethod
    def _event(kind: str, code: str, text: str, **variables) -> str:
        """
        Construct a progress event.

        Background: Exploration shows the retrieval process to users, and these messages were originally hardcoded in Chinese.
        Design intent: Provide stable code + variables so the frontend renders in the current language;
        text is only a fallback when the frontend lacks the code, not used in normal display.
        """
        payload = {"type": kind, "code": code}
        payload["message" if kind in ("status", "error", "info") else "content"] = text
        if variables:
            payload["vars"] = variables
        return json.dumps(payload) + "\n"

    async def explore(self, query: str, context: str = None) -> AsyncGenerator[str, None]:
        """
        True SSE streaming exploration
        Supports automatic follow-up (Agent Loop):
        1. Initial search (combines context if available)
        2. AI analyzes if more information is needed
        3. If needed, generates keywords for supplementary search (up to 2 rounds)
        4. Final narration
        """
        MAX_ROUNDS = 2  # Initial + 2 expansion rounds = up to 3 searches
        # When there are only 1-2 results the model still returns ENOUGH (tested with 1 result saying "sufficient"),
        # causing narration to be just a single isolated item. Below this count, "enough" is rejected and another round is forced.
        MIN_TRUNKS = 3
        
        all_trunks = []
        seen_ids = set()
        tried_queries = set()
        
        # If there's context (follow-up scenario), combine with context for search
        if context:
            # Rewrite query with AI, combining context
            yield self._event("thinking", "with_context", f"Understanding follow-up with conversation context: {context}", context=context)
            combined_query = f"{context} {query}"  # Simple concatenation, can use AI rewriting later
            current_query = combined_query
            original_query = query  # Keep original follow-up for display
        else:
            current_query = query
            original_query = query
        
        yield self._event("status", "searching", "Searching")
        
        # --- Search loop ---
        for round in range(MAX_ROUNDS + 1):
            is_initial = (round == 0)
            
            # 1. Execute search
            if not is_initial:
                yield self._event("status", "searching_more", f"Searching deeper: {current_query}", keyword=current_query)
            
            tried_queries.add(current_query)
            search_res = self.search_engine.search_trunks(current_query, limit=8 if is_initial else 4)
            raw_results = search_res.get("results", [])
            
            new_trunks_found = False
            current_round_trunks = []
            
            for i, r in enumerate(raw_results):
                t = r["trunk"]
                if t["id"] in seen_ids:
                    continue
                    
                seen_ids.add(t["id"])
                new_trunks_found = True
                
                # Get first-degree related trunks for top 2 results (first round only)
                context_str = ""
                if is_initial and i < 2 and self.search_engine.vector_store:
                    try:
                        related = self.search_engine.vector_store.find_related_trunks(
                            trunk_id=t["id"], limit=2, current_document_id=t["document_id"]
                        )
                        if related:
                            rel_texts = [self.search_engine.database.get_trunk(tid).content[:80].replace("\n", " ") 
                                       for tid, _, _ in related if self.search_engine.database.get_trunk(tid)]
                            if rel_texts:
                                context_str = " [Related: " + "; ".join(rel_texts) + "]"
                    except Exception:
                        pass

                trunk_data = {
                    "id": t["id"],
                    "document_id": t["document_id"],
                    "title": r.get("document_title", "Unknown"),
                    "content": t["content"],
                    "content_type": t.get("content_type", "text"),
                    "image_url": t.get("image_url"),
                    "image_description": t.get("image_description"),
                    "score": r.get("score", 0),
                    "tags": t.get("tags", []),
                    "meta": t.get("meta", {}),
                    "context": context_str
                }
                all_trunks.append(trunk_data)
                current_round_trunks.append(trunk_data)

            # Send this round's search results
            if current_round_trunks:
                result_list = [{"title": t["title"], "id": t["id"]} for t in current_round_trunks]
                yield json.dumps({"type": "search_results", "data": result_list}) + "\n"

            # If no results on first round, end immediately
            if is_initial and not all_trunks:
                yield self._event("error", "no_results", "No relevant content found")
                return
            
            # If this is the last round, or no new results found this round, stop expanding
            if round == MAX_ROUNDS or (not is_initial and not new_trunks_found):
                break
                
            # 2. AI analyzes if more information is needed (Agent Step)
            # Only expand when trunk count is not too large, to avoid overly long context
            if len(all_trunks) < 15: 
                yield self._event("status", "assessing", "Assessing information completeness")
                
                # Relay thinking process: begin analysis
                yield self._event("thinking", "reading", "Reading existing materials, assessing if sufficient to answer the question")
                
                needs_more = len(all_trunks) < MIN_TRUNKS
                suggestion = await self._get_search_suggestion(
                    query, all_trunks, require_expansion=needs_more
                )
                if suggestion in tried_queries:
                    suggestion = None
                if not suggestion and needs_more:
                    suggestion = self._fallback_expansion(all_trunks, tried_queries)
                
                if suggestion:
                    current_query = suggestion
                    # Relay thinking process: decided to expand search
                    yield self._event("thinking", "expanding", f"Found information gap, supplementary keyword: {suggestion}", keyword=suggestion)
                    # Continue to next round
                    continue
                else:
                    # AI considers it sufficient
                    yield self._event("thinking", "enough", "Existing materials are sufficient, preparing to organize")
                    break
            else:
                yield self._event("thinking", "capped", "Sufficient materials collected, stopping expansion")
                break
        
        # --- Final narration ---
        yield self._event("status", "narrating", f"Organizing {len(all_trunks)} materials, generating narration", count=len(all_trunks))
        
        prompt = self._build_narration_prompt(original_query, all_trunks)
        
        # Send start early so the frontend can prepare
        yield json.dumps({"type": "start"}) + "\n"
        
        async for item in self._stream_and_parse(prompt, all_trunks):
            yield item
        
        # --- Extract action items/schedules ---
        action_items = self._extract_action_items(all_trunks)
        if action_items:
            yield json.dumps({"type": "actions", "data": action_items}) + "\n"
        
        # --- Generate follow-up suggestions (parallel non-blocking, simplified to sync for now) ---
        suggestions = await self._generate_suggestions(original_query, all_trunks)
        if suggestions:
            yield json.dumps({"type": "suggestions", "data": suggestions}) + "\n"
        
        yield json.dumps({"type": "done"}) + "\n"
    
    def _extract_action_items(self, trunks: List[Dict]) -> List[Dict]:
        """Extract action items/schedules from trunks"""
        actions = []
        
        for t in trunks:
            meta = t.get("meta", {})
            items = meta.get("action_items", [])
            
            if items and isinstance(items, list):
                for item in items:
                    if isinstance(item, str) and item.strip():
                        actions.append({
                            "content": item.strip(),
                            "source": t.get("title", ""),
                            "trunk_id": t.get("id"),
                            "document_id": t.get("document_id")
                        })
        
        return actions

    async def _generate_suggestions(self, query: str, trunks: List[Dict]) -> List[str]:
        """Generate 2-3 follow-up suggestions"""
        context_summary = "\n".join([
            f"- 《{t['title']}》: {t['content'][:100]}..." 
            for t in trunks[:5]
        ])
        
        prompt = f"""Based on the following exploration:

Topic: {query}

Related materials:
{context_summary}

Please generate 2-3 directions to help organize my thinking. Requirements:
1. Should be "actions" not "questions", e.g. "summarize key points of xxx", "compare xxx and xxx", "outline strategy for xxx"
2. Based on material content, specific and targeted
3. Each suggestion no more than 25 words

Output suggestions only, one per line, no numbering or explanation.
"""
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._get_headers(),
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.5,
                        "max_tokens": 100
                    }
                )
                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"].strip()
                    self._record_usage(result.get("usage"), prompt, content,
                                       int((time.time() - start) * 1000))
                    suggestions = [s.strip() for s in content.split("\n") if s.strip()]
                    return suggestions[:3]
        except Exception as e:
            self._record_usage(None, prompt, "", int((time.time() - start) * 1000),
                               status="error", error=str(e))
            print(f"[AIExplorer] Suggestion generation failed: {e}")
        return []

    def _fallback_expansion(self, all_trunks: List[Dict], tried_queries: set) -> Optional[str]:
        """
        Deterministic fallback when recall is insufficient and the model refuses to provide expansion keywords.

        Uses titles from already-hit materials for another search round: titles often contain proper nouns
        (names, project names) missing from the query, enough to bring out other chunks on the same topic
        without relying on model cooperation.
        """
        for t in all_trunks:
            title = (t.get("title") or "").strip()
            if title and title != "Unknown" and title not in tried_queries:
                return title
        return None

    async def _get_search_suggestion(
        self,
        original_query: str,
        current_trunks: List[Dict],
        require_expansion: bool = False
    ) -> Optional[str]:
        """
        Let AI judge whether existing materials are sufficient to answer the question.
        If not, return a new search keyword.
        If sufficient, return None.

        When require_expansion is True, materials have been deemed insufficient; the "enough" option is not offered.
        """
        # Simplify trunk content to save tokens
        context_summary = "\n".join([
            f"- 《{t['title']}》: {t['content'][:150]}..." 
            for t in current_trunks[:10]
        ])
        
        if require_expansion:
            instruction = f"""Currently only {len(current_trunks)} materials found, insufficient for a complete answer.
Please provide **1** keyword most worth searching: it can be a core concept in the question, a related person,
or an alternative phrasing of the same topic as existing materials. No explanation, just the keyword.
"""
        else:
            instruction = """Please judge: are the existing materials sufficient to answer my question?
1. If sufficient, reply "ENOUGH"
2. If key information is missing, provide **1** keyword most needed to supplement the information. No explanation, just the keyword.

Reply only with the keyword or "ENOUGH".
"""
        
        prompt = f"""My question: "{original_query}"
        
Materials found:
{context_summary}

{instruction}"""
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._get_headers(),
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1, # Low temperature for precision
                        "max_tokens": 50
                    }
                )
                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"].strip()
                    self._record_usage(result.get("usage"), prompt, content,
                                       int((time.time() - start) * 1000))
                    if "ENOUGH" in content.upper():
                        return None
                    # Clean up, prevent AI verbosity
                    keyword = content.replace('"', '').replace("'", "").split('\n')[0].strip()
                    return keyword if keyword and keyword != original_query else None
        except Exception as e:
            self._record_usage(None, prompt, "", int((time.time() - start) * 1000),
                               status="error", error=str(e))
            print(f"[AIExplorer] Failed to get suggestion: {e}")
            return None
        return None

    async def drill_down(self, trunk_id: str, question: str = "") -> AsyncGenerator[str, None]:
        """
        Drill-down exploration:
        1. AI infers why the user clicked this card
        2. Generate search keywords
        3. Search for related content
        4. Optional Agent loop expansion
        5. Narration
        """
        trunk = self.search_engine.database.get_trunk(trunk_id)
        if not trunk:
            yield self._event("error", "trunk_missing", "Content not found")
            return
        
        memory = self.search_engine.database.get_memory(trunk.document_id)
        doc_title = memory.title if memory else "Unknown"
        
        yield json.dumps({
            "type": "focus",
            "data": {"id": trunk_id, "title": doc_title, "content": trunk.content}
        }) + "\n"
        
        # 1. AI infers user intent
        yield self._event("thinking", "drill_intent", "Analyzing why you're interested in this content")
        
        drill_keywords = await self._infer_drill_intent(trunk.content, question)
        
        if drill_keywords:
            joined = ", ".join(drill_keywords)
            yield self._event("thinking", "drill_guess", f"You might want to know about: {joined}", keyword=joined)
        else:
            drill_keywords = [trunk.content[:100]]  # Use content itself
            yield self._event("thinking", "drill_fallback", "Will search for related information based on the content itself")
        
        # 2. Search for related content
        yield self._event("status", "searching_related", "Searching for related content")
        
        all_trunks = []
        seen_ids = {trunk_id}  # Exclude itself
        
        for keyword in drill_keywords:
            search_res = self.search_engine.search_trunks(keyword, limit=4)
            raw_results = search_res.get("results", [])
            
            current_round = []
            for r in raw_results:
                t = r["trunk"]
                if t["id"] in seen_ids:
                    continue
                seen_ids.add(t["id"])
                current_round.append({
                    "id": t["id"],
                    "document_id": t.get("document_id", ""),
                    "title": r.get("document_title", "Unknown"),
                    "content": t["content"],
                    "score": r.get("score", 0),
                    "tags": t.get("tags", []),
                    "highlights": []
                })
            all_trunks.extend(current_round)
            
            if current_round:
                result_list = [{"title": t["title"], "id": t["id"]} for t in current_round]
                yield json.dumps({"type": "search_results", "data": result_list}) + "\n"
        
        if not all_trunks:
            yield self._event("info", "no_related", "No additional related content found")
            return
        
        yield self._event("thinking", "found_n", f"Found {len(all_trunks)} related materials", count=len(all_trunks))
        
        # 3. Narration
        yield self._event("status", "organizing", "Organizing")
        
        user_intent = question if question else "Learn more about this content"
        prompt = self._build_drill_prompt(trunk.content, all_trunks, user_intent)
        
        yield json.dumps({"type": "start"}) + "\n"
        
        async for item in self._stream_and_parse(prompt, all_trunks):
            yield item
        
        # 4. Extract action items
        action_items = self._extract_action_items(all_trunks)
        if action_items:
            yield json.dumps({"type": "actions", "data": action_items}) + "\n"
        
        # 5. Generate follow-up suggestions
        suggestions = await self._generate_suggestions(user_intent, all_trunks)
        if suggestions:
            yield json.dumps({"type": "suggestions", "data": suggestions}) + "\n"
        
        yield json.dumps({"type": "done"}) + "\n"

    async def _infer_drill_intent(self, content: str, question: str = "") -> List[str]:
        """
        Let AI infer the user's intent for drilling down, and generate search keywords
        """
        prompt = f"""I am viewing the following content and clicked the "drill down" button:

Content snippet:
"{content[:400]}..."

{f"My follow-up question: {question}" if question else "No follow-up question was entered, just clicked drill down."}

Please infer what I might want to learn about, and provide 2-3 search keywords to help find related content.
Keywords should be: specific names, project names, concepts, time points, etc.

Output keywords only, comma-separated, no explanation.
"""
        start = time.time()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self._get_headers(),
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "max_tokens": 100
                    }
                )
                if response.status_code == 200:
                    result = response.json()
                    content = result["choices"][0]["message"]["content"].strip()
                    self._record_usage(result.get("usage"), prompt, content,
                                       int((time.time() - start) * 1000))
                    keywords = [k.strip() for k in content.split(",") if k.strip()]
                    return keywords[:3]  # At most 3
        except Exception as e:
            self._record_usage(None, prompt, "", int((time.time() - start) * 1000),
                               status="error", error=str(e))
            print(f"[AIExplorer] Intent inference failed: {e}")
        return []

    async def _stream_and_parse(self, prompt: str, trunks: List[Dict]) -> AsyncGenerator[str, None]:
        """
        Parse XML tags while reading LLM streaming response
        Format: <trunk id="N" hl="keyword1,keyword2"/>
        """
        buffer = ""
        in_tag = False
        tag_buffer = ""
        output_chars = 0
        stream_usage: Optional[dict] = None
        start = time.time()

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._get_headers(),
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "stream": True,
                        "max_tokens": 1500
                    }
                ) as response:
                    if response.status_code != 200:
                        self._record_usage(None, prompt, "", int((time.time() - start) * 1000),
                                           status="error", error=f"HTTP {response.status_code}")
                        yield self._event("error", "llm_error", f"LLM error: {response.status_code}", detail=str(response.status_code))
                        return
                    
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        
                        # Handle SSE format
                        if line.startswith("data: "):
                            line = line[6:]
                        elif line.startswith(":"):
                            continue
                        
                        if line.strip() == "[DONE]":
                            break
                            
                        try:
                            data = json.loads(line)
                            # Some upstreams (DeepSeek / Zhipu etc.) attach usage in the last chunk
                            if isinstance(data.get("usage"), dict):
                                stream_usage = data["usage"]
                            content = ""
                            if "choices" in data and len(data["choices"]) > 0:
                                delta = data["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                            elif "content" in data:
                                content = data["content"]
                                
                            if not content:
                                continue
                            output_chars += len(content)
                            
                            # Process character by character
                            for char in content:
                                if in_tag:
                                    tag_buffer += char
                                    # Detect tag end: /> or >
                                    if tag_buffer.endswith("/>") or (tag_buffer.endswith(">") and not tag_buffer.endswith("/>")):
                                        # Parse XML tag
                                        trunk_data = self._parse_trunk_tag(tag_buffer, trunks)
                                        if trunk_data:
                                            yield json.dumps({"type": "trunk", "data": trunk_data}) + "\n"
                                        tag_buffer = ""
                                        in_tag = False
                                else:
                                    buffer += char
                                    # Detect <trunk start
                                    if buffer.endswith("<trunk"):
                                        text_before = buffer[:-6]
                                        if text_before:
                                            yield json.dumps({"type": "text", "content": text_before}) + "\n"
                                        buffer = ""
                                        in_tag = True
                                        tag_buffer = "<trunk"
                                    elif not buffer.endswith("<") and not buffer.endswith("<t") and not buffer.endswith("<tr") and not buffer.endswith("<tru") and not buffer.endswith("<trun"):
                                        yield json.dumps({"type": "text", "content": buffer}) + "\n"
                                        buffer = ""
                                        await asyncio.sleep(0)
                                        
                        except (json.JSONDecodeError, Exception):
                            continue
                    
                    if buffer.strip():
                        yield json.dumps({"type": "text", "content": buffer}) + "\n"

            self._record_usage(stream_usage, prompt, "x" * output_chars,
                               int((time.time() - start) * 1000))

        except Exception as e:
            self._record_usage(None, prompt, "", int((time.time() - start) * 1000),
                               status="error", error=str(e))
            yield self._event("error", "llm_error", str(e), detail=str(e))

    def _parse_trunk_tag(self, tag: str, trunks: List[Dict]) -> Optional[Dict]:
        """Parse <trunk id="N" hl="..."/> tag"""
        # Extract id
        id_match = re.search(r'id\s*=\s*["\']?(\d+)["\']?', tag)
        if not id_match:
            return None
        
        idx = int(id_match.group(1)) - 1
        if idx < 0 or idx >= len(trunks):
            return None
        
        trunk = trunks[idx].copy()
        
        # Extract hl (highlights)
        hl_match = re.search(r'hl\s*=\s*["\']([^"\']+)["\']', tag)
        if hl_match:
            highlights = [h.strip() for h in hl_match.group(1).split(",") if h.strip()]
            trunk["highlights"] = highlights
        else:
            trunk["highlights"] = []
        
        return trunk

    def _build_narration_prompt(self, query: str, trunks: List[Dict]) -> str:
        trunk_list = []
        for i, t in enumerate(trunks):
            # Extract key metadata
            meta_info = []
            if t.get('meta'):
                m = t['meta']
                if m.get('entities'):
                    ents = []
                    for k, v in m['entities'].items():
                        ents.extend(v if isinstance(v, list) else [v])
                    if ents: meta_info.append(f"Contains: {', '.join(str(e) for e in ents[:5])}")
                if m.get('time_expressions'):
                    times = m['time_expressions']
                    if isinstance(times, dict): times = times.get('mentioned', [])
                    if times: meta_info.append(f"Time: {', '.join(str(tm) for tm in times)}")
            
            meta_str = f" ({'; '.join(meta_info)})" if meta_info else ""
            
            content = t['content'][:400]
            context = t.get('context', '')
            
            trunk_list.append(f"[{i+1}] Source: {t['title']}{meta_str}:\n{content}...{context}")

        trunk_text = "\n".join(trunk_list)
        
        return f"""You are a knowledge narrator. I searched for "{query}" and found the following snippets.

Task:
1. Write short transition text to guide reading
2. Use XML tags to display original text: <trunk id="N" hl="keyword1,keyword2"/>
   - id is the snippet number
   - hl is 2-4 keywords/phrases you think should be highlighted in the snippet
3. Each transition should be no more than 2 sentences
4. Do not rewrite the original text

Snippets:
{trunk_text}

Format example:
Regarding this topic, I found some records:

<trunk id="1" hl="project progress,Q2 goals"/>

There's also a related record:

<trunk id="2" hl="budget adjustment,financial approval"/>

Begin:"""

    def _build_drill_prompt(self, source_content: str, trunks: List[Dict], question: str) -> str:
        trunk_list = "\n".join([
            f"[{i+1}] Source: {t['title']}:\n{t['content'][:400]}..."
            for i, t in enumerate(trunks)
        ])
        
        context = f"Follow-up: {question}" if question else "Learn more"
        
        return f"""I'm viewing content: "{source_content[:300]}..."

{context}

Related snippets:
{trunk_list}

Write short transition narration, use XML tags <trunk id="N" hl="keyword1,keyword2"/> to display original text (hl is keywords to highlight). Begin:"""

    async def generate_memory(
        self, 
        trunks: List[Dict], 
        query: str,
        extra_requirement: str = ""
    ) -> AsyncGenerator[str, None]:
        """
        Generate new memory based on exploration results
        Strictly reference original text, no creative writing
        """
        # Build source reference
        source_texts = "\n\n".join([
            f"[Source {i+1}: {t.get('title', 'Unknown')}]\n{t.get('content', '')}"
            for i, t in enumerate(trunks)
        ])
        
        extra_hint = f"\n\nUser additional requirement: {extra_requirement}" if extra_requirement else ""
        
        prompt = f"""You are a rigorous information organizer. Please organize the following source materials into a well-structured note.

Original question: {query}

Source materials:
{source_texts}
{extra_hint}

[Important Rules]
1. **Strictly reference original text**: Only organize and summarize information explicitly mentioned in the source, do not add creative content
2. **Mark inferred content**: If certain information is not directly stated in the source but you think it's necessary to add, prefix it with ❓
3. **Preserve key details**: Names, numbers, dates, specific requirements and other key information must be preserved
4. **Action item format**: If there are todos/tasks/schedules, use `- [ ]` format
5. **Structured presentation**: Use Markdown format with clear hierarchy

[Output Format]
Output in XML format, with note content inside <content> tags:
<content>
# Title

## Section One
- Content...

## Action Items
- [ ] Task 1
- [ ] Task 2
</content>

Please begin organizing:"""

        yield json.dumps({"type": "start"}) + "\n"

        output_chars = 0
        stream_usage: Optional[dict] = None
        start = time.time()

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/chat/completions",
                    headers=self._get_headers(),
                    json={
                        "model": self.model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.3,
                        "stream": True,
                        "max_tokens": 2000
                    }
                ) as response:
                    if response.status_code != 200:
                        self._record_usage(None, prompt, "", int((time.time() - start) * 1000),
                                           status="error", error=f"HTTP {response.status_code}")
                        yield self._event("error", "llm_error", f"LLM error: {response.status_code}", detail=str(response.status_code))
                        return
                    
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        
                        if line.startswith("data: "):
                            line = line[6:]
                        elif line.startswith(":"):
                            continue
                        
                        if line.strip() == "[DONE]":
                            break
                            
                        try:
                            data = json.loads(line)
                            if isinstance(data.get("usage"), dict):
                                stream_usage = data["usage"]
                            content = ""
                            if "choices" in data and len(data["choices"]) > 0:
                                delta = data["choices"][0].get("delta", {})
                                content = delta.get("content", "")
                            
                            if content:
                                output_chars += len(content)
                                yield json.dumps({"type": "text", "content": content}) + "\n"
                                await asyncio.sleep(0)
                                
                        except (json.JSONDecodeError, Exception):
                            continue

            self._record_usage(stream_usage, prompt, "x" * output_chars,
                               int((time.time() - start) * 1000))

        except Exception as e:
            self._record_usage(None, prompt, "", int((time.time() - start) * 1000),
                               status="error", error=str(e))
            yield self._event("error", "llm_error", str(e), detail=str(e))
        
        yield json.dumps({"type": "done"}) + "\n"
