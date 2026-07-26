"""
Meta extraction module

Automatic meta tag extraction for multimodal content:
- Text: entity recognition, topic classification, sentiment analysis, time expressions, etc.
- Image: visual features, object recognition, scene classification, OCR, etc.
- Audio: (reserved) transcription, speaker identification, emotional tone, etc.
- Video: (reserved) scene segmentation, human actions, etc.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import os
import re
import time
import httpx
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime
from abc import ABC, abstractmethod

from .usage_tracker import estimate_tokens, record_usage


def _report_meta_usage(extractor, data: Optional[dict], duration_ms: int,
                       prompt_text: str = "", output_text: str = "",
                       status: str = "success", error: str = None) -> None:
    """
    Meta extractors use direct httpx connections (bypassing provider adapters), so usage is reported here.
    Uses real values when upstream returns OpenAI-format usage; falls back to text-based estimation
    when missing (image base64 is excluded from estimation).
    """
    usage = (data or {}).get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    details = usage.get("prompt_tokens_details") or {}
    cached_tokens = int(details.get("cached_tokens") or usage.get("prompt_cache_hit_tokens") or 0)
    estimated = prompt_tokens == 0 and completion_tokens == 0
    if estimated and status == "success":
        prompt_tokens = estimate_tokens(prompt_text)
        completion_tokens = estimate_tokens(output_text)
    record_usage(caller="meta-extract", kind="chat", model=extractor.model,
                 provider=getattr(extractor, "provider_id", ""),
                 provider_name=getattr(extractor, "provider_name", ""),
                 prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                 cached_tokens=cached_tokens, estimated=estimated,
                 duration_ms=duration_ms, status=status, error=error)


@dataclass
class MetaResult:
    """Meta extraction result"""
    meta_tags: List[str] = field(default_factory=list)  # Flat list of tags
    meta_dict: Dict[str, Any] = field(default_factory=dict)  # Structured meta information
    content_type: str = "text"
    extracted_at: Optional[datetime] = None
    
    def __post_init__(self):
        if self.extracted_at is None:
            self.extracted_at = datetime.now()


class BaseMetaExtractor(ABC):
    """Base class for meta extractors"""
    
    @abstractmethod
    def extract(self, content: str, **kwargs) -> MetaResult:
        """Extract meta information"""
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if the extractor is available"""
        pass


class TextMetaExtractor(BaseMetaExtractor):
    """
    Text meta extractor
    
    Uses LLM to extract rich structured metadata from text:
    - Entity recognition (people, organizations, locations, products, amounts, values)
    - Time information (mentioned times + inferred absolute times)
    - Semantic structure (document type, intent, action items)
    - Relationship extraction (person roles, organizational relationships)
    - Sentiment and attitude
    - Domain classification and keywords
    """
    
    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "openai/gpt-oss-20b"
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = ""  # Optional API key
        self.provider_id = ""    # Injected by factory for usage attribution
        self.provider_name = ""
        self.client = httpx.Client(timeout=120.0)
    
    def _get_headers(self) -> dict:
        """Get request headers"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    def is_available(self) -> bool:
        """Check if the service is available"""
        try:
            response = self.client.get(
                f"{self.base_url}/models", 
                timeout=5.0,
                headers=self._get_headers()
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def extract(self, content: str, **kwargs) -> MetaResult:
        """Extract rich meta information from text"""
        if not content or len(content.strip()) < 10:
            return MetaResult(content_type="text")
        
        # Truncate content to avoid excessive length
        content_preview = content[:3000] if len(content) > 3000 else content
        
        # Get current date for time inference
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        prompt = f"""Please perform a deep analysis of the following text and extract structured metadata.

[Text Content]
{content_preview}

[Current Date]
{current_date}

[Extraction Requirements - Output in JSON format]
{{
  "entities": {{
    "person": [
      {{"name": "person name", "role": "role/title (if any)"}}
    ],
    "organization": ["organization/company/brand name"],
    "location": ["location"],
    "product": ["product/project/brand name"],
    "amount": ["amounts/values (keep units, e.g. 120K, 70%)"]
  }},
  "time_expressions": {{
    "mentioned": ["time expressions mentioned in text: yesterday, next Wednesday, Q2"],
    "inferred_absolute": ["inferred absolute time: calculated based on current date"]
  }},
  "document_type": "document type (e.g.: meeting notes/proposal revision/customer feedback/to-do/diary/notes/notice)",
  "intent": ["intent/action (e.g.: needs adjustment/pending confirmation/completed/suggestion/feedback/request)"],
  "action_items": ["action items (if any)"],
  "references": ["mentioned documents/projects/links (if any)"],
  "theme": ["theme tags"],
  "domain": ["domain classification (e.g.: business/technology/product/HR/finance/lifestyle)"],
  "sentiment": "overall sentiment (positive/neutral/negative)",
  "attitude": ["attitude tendency (e.g.: supportive/opposed/wait-and-see/skeptical/approving)"],
  "risk_signals": ["risk signals (if negative language or concerns exist)"],
  "keywords": ["keywords (5-10 core terms)"]
}}

[Notes]
1. Only extract information explicitly mentioned or reasonably inferable from the text
2. Use empty arrays [] or empty strings "" for fields with no relevant content
3. Try to identify person roles (client/colleague/manager/decision-maker, etc.)
4. Time inference should be calculated based on the [Current Date]
5. Output JSON directly, no other explanations or Markdown formatting

[Output JSON]"""

        start = time.time()
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": "You are a professional text metadata extraction assistant. Only output results in JSON format, without any explanations or reasoning."},
                        {"role": "user", "content": prompt}
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2000
                }
            )
            response.raise_for_status()
            data = response.json()
            result_text = data["choices"][0]["message"]["content"]
            _report_meta_usage(self, data, int((time.time() - start) * 1000),
                               prompt_text=prompt, output_text=result_text)

            return self._parse_json_response(result_text)
            
        except Exception as e:
            _report_meta_usage(self, None, int((time.time() - start) * 1000),
                               status="error", error=str(e))
            print(f"Text meta extraction failed: {e}")
            return MetaResult(content_type="text")
    
    def _parse_json_response(self, response: str) -> MetaResult:
        """Parse JSON-formatted LLM response"""
        import json
        
        # Initialize default structure
        meta_dict = {
            "entities": {
                "person": [],
                "organization": [],
                "location": [],
                "product": [],
                "amount": []
            },
            "time_expressions": {
                "mentioned": [],
                "inferred_absolute": []
            },
            "document_type": "",
            "intent": [],
            "action_items": [],
            "references": [],
            "theme": [],
            "domain": [],
            "sentiment": "",
            "attitude": [],
            "risk_signals": [],
            "keywords": []
        }
        meta_tags = []
        
        try:
            # Clean response (remove possible Markdown code block markers)
            cleaned = response.strip()
            if cleaned.startswith("```"):
                lines = cleaned.split('\n')
                # Remove the first and last lines containing ```
                cleaned = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
            
            # Parse JSON
            parsed = json.loads(cleaned)
            
            # Extract entities
            if "entities" in parsed:
                entities = parsed["entities"]
                
                # People (may be an array of objects or strings)
                if "person" in entities:
                    for p in entities["person"]:
                        if isinstance(p, dict):
                            name = p.get("name", "")
                            role = p.get("role", "")
                            if name:
                                meta_dict["entities"]["person"].append({"name": name, "role": role})
                                if role:
                                    meta_tags.append(f"person:{name}({role})")
                                else:
                                    meta_tags.append(f"person:{name}")
                        elif isinstance(p, str) and p:
                            meta_dict["entities"]["person"].append({"name": p, "role": ""})
                            meta_tags.append(f"person:{p}")
                
                # Organizations
                if "organization" in entities:
                    meta_dict["entities"]["organization"] = [o for o in entities["organization"] if o]
                    meta_tags.extend([f"org:{o}" for o in meta_dict["entities"]["organization"]])
                
                # Locations
                if "location" in entities:
                    meta_dict["entities"]["location"] = [l for l in entities["location"] if l]
                    meta_tags.extend([f"location:{l}" for l in meta_dict["entities"]["location"]])
                
                # Products/Projects
                if "product" in entities:
                    meta_dict["entities"]["product"] = [p for p in entities["product"] if p]
                    meta_tags.extend([f"product:{p}" for p in meta_dict["entities"]["product"]])
                
                # Amounts/Values
                if "amount" in entities:
                    meta_dict["entities"]["amount"] = [a for a in entities["amount"] if a]
                    meta_tags.extend([f"amount:{a}" for a in meta_dict["entities"]["amount"]])
            
            # Time information
            if "time_expressions" in parsed:
                time_expr = parsed["time_expressions"]
                if isinstance(time_expr, dict):
                    meta_dict["time_expressions"] = {
                        "mentioned": time_expr.get("mentioned", []),
                        "inferred_absolute": time_expr.get("inferred_absolute", [])
                    }
                    for t in meta_dict["time_expressions"]["mentioned"]:
                        if t:
                            meta_tags.append(f"time:{t}")
            
            # Document type
            if "document_type" in parsed and parsed["document_type"]:
                meta_dict["document_type"] = parsed["document_type"]
                meta_tags.append(f"type:{parsed['document_type']}")
            
            # Intent
            if "intent" in parsed:
                meta_dict["intent"] = [i for i in parsed["intent"] if i]
                meta_tags.extend([f"intent:{i}" for i in meta_dict["intent"]])
            
            # Action items
            if "action_items" in parsed:
                meta_dict["action_items"] = [a for a in parsed["action_items"] if a]
            
            # References
            if "references" in parsed:
                meta_dict["references"] = [r for r in parsed["references"] if r]
            
            # Themes
            if "theme" in parsed:
                meta_dict["theme"] = [t for t in parsed["theme"] if t]
                meta_tags.extend(meta_dict["theme"])
            
            # Domains
            if "domain" in parsed:
                meta_dict["domain"] = [d for d in parsed["domain"] if d]
                meta_tags.extend(meta_dict["domain"])
            
            # Sentiment
            if "sentiment" in parsed and parsed["sentiment"]:
                meta_dict["sentiment"] = parsed["sentiment"]
                meta_tags.append(f"sentiment:{parsed['sentiment']}")
            
            # Attitude
            if "attitude" in parsed:
                meta_dict["attitude"] = [a for a in parsed["attitude"] if a]
            
            # Risk signals
            if "risk_signals" in parsed:
                meta_dict["risk_signals"] = [r for r in parsed["risk_signals"] if r]
                if meta_dict["risk_signals"]:
                    meta_tags.append("⚠️risk_warning")
            
            # Keywords
            if "keywords" in parsed:
                meta_dict["keywords"] = [k for k in parsed["keywords"] if k]
                meta_tags.extend(meta_dict["keywords"])
            
        except json.JSONDecodeError as e:
            print(f"  JSON parsing failed, falling back to line parsing: {e}")
            # Fall back to line-by-line parsing
            return self._parse_line_response(response)
        
        return MetaResult(
            meta_tags=meta_tags,
            meta_dict=meta_dict,
            content_type="text"
        )
    
    def _parse_line_response(self, response: str) -> MetaResult:
        """Fallback: parse line-formatted LLM response"""
        meta_dict = {
            "entities": {
                "person": [],
                "organization": [],
                "location": [],
                "product": [],
                "amount": []
            },
            "time_expressions": {
                "mentioned": [],
                "inferred_absolute": []
            },
            "theme": [],
            "domain": [],
            "sentiment": "",
            "keywords": []
        }
        meta_tags = []
        
        lines = response.strip().split('\n')
        for line in lines:
            line = line.strip()
            if not line or ':' not in line:
                continue
            
            # Parse "category: value1, value2"
            parts = line.split(':', 1)
            if len(parts) != 2:
                continue
            
            category = parts[0].strip().lower()
            values = [v.strip() for v in parts[1].split(',') if v.strip()]
            
            if not values:
                continue
            
            # Assign by category
            if '人物' in category or 'person' in category:
                meta_dict["entities"]["person"] = [{"name": v, "role": ""} for v in values]
                meta_tags.extend([f"person:{v}" for v in values])
            elif '地点' in category or 'location' in category:
                meta_dict["entities"]["location"] = values
                meta_tags.extend([f"location:{v}" for v in values])
            elif '组织' in category or 'organization' in category:
                meta_dict["entities"]["organization"] = values
                meta_tags.extend([f"org:{v}" for v in values])
            elif '产品' in category or 'product' in category:
                meta_dict["entities"]["product"] = values
                meta_tags.extend([f"product:{v}" for v in values])
            elif '金额' in category or '数值' in category or 'amount' in category:
                meta_dict["entities"]["amount"] = values
                meta_tags.extend([f"amount:{v}" for v in values])
            elif '时间' in category or 'time' in category:
                meta_dict["time_expressions"]["mentioned"] = values
                meta_tags.extend([f"time:{v}" for v in values])
            elif '主题' in category or 'theme' in category:
                meta_dict["theme"] = values
                meta_tags.extend(values)
            elif '领域' in category or 'domain' in category:
                meta_dict["domain"] = values
                meta_tags.extend(values)
            elif '情感' in category or 'sentiment' in category:
                meta_dict["sentiment"] = values[0] if values else ""
                if values:
                    meta_tags.append(f"sentiment:{values[0]}")
            elif '关键' in category or 'keyword' in category:
                meta_dict["keywords"] = values
                meta_tags.extend(values)
        
        return MetaResult(
            meta_tags=meta_tags,
            meta_dict=meta_dict,
            content_type="text"
        )


class ImageMetaExtractor(BaseMetaExtractor):
    """
    Image meta extractor
    
    Uses VLM (Vision Language Model) to extract from images:
    - Visual features (color, composition, lighting)
    - Object recognition
    - Scene classification
    - OCR text
    - Emotional atmosphere
    """
    
    # Image analysis prompt
    IMAGE_PROMPT = """# Role
You are a professional image recognition and tag generation system. Deeply analyze images, extract features with retrieval value, only extract **directly visible visual features** from the image, and output a flat, **non-duplicated** tag list.

# Constraints
1. Output format: Only use commas to separate tags, no other text, symbols, or Markdown formatting.
2. Tag requirements: Aim for 10+ tags covering multi-dimensional features. No duplicate tags. Do not meaninglessly decompose a single object into body parts (e.g., don't split a car into wheels, doors). Tags must be **strongly related** to the image's visual content. Absolutely no abstract concepts without visual correlation.
3. Fine-grained rules: Describe objects specifically (e.g., red rose instead of flower, frost-covered trees instead of trees). State and attributes should be combined with scene context (e.g., flying red bird, rainy forest).

# Extraction Dimensions (must cover all)
1. Media & Technical:
   - Extract: Image type (e.g., aerial, screenshot, portrait, poster), file properties, perceived resolution, estimated shooting device, post-processing traces.
2. Visual & Artistic:
   - Extract: Dominant colors (e.g., red primary, green accent), color scheme (e.g., red-blue contrast), lighting features (e.g., backlight, side light), composition (e.g., rule of thirds, diagonal), depth of field, art style (e.g., cyberpunk, minimalism).
3. Subject & Details (core):
   - Extract: Specific names, states, and attributes of all visible objects (e.g., long-haired woman, red dress, rusty iron gate, contemplative expression).
   - Fine-grained: Don't just write "flower", write "rose, red rose, blooming"; don't just write "interface", write "WeChat interface, chat window, green bubble".
4. UI & Interaction (for screenshots):
   - Extract: Control names (buttons, sliders), layout style, icon types, OS version features (iOS/Android), battery status, network status.
5. Text Information (OCR):
   - Extract: All visible text content, numbers, times, titles, watermarks in the image.
6. Emotion & Scene:
   - Extract: Atmosphere keywords (e.g., serene, anxious, tech-feel), specific scene (e.g., office, subway station), emotional tendency (e.g., happy, sarcastic).
# Check
Check whether tags contain many repetitive or similar entries; if so, regenerate the tags.
# Output Example
(Do not output a title, start directly with tags)
HD, photography, wide-angle lens, natural light, outdoor, blue sky, overcast, grassland, green, running, golden retriever, pet, leash, frisbee, motion blur, happy, companionship, leisure, weekend, 4K quality, depth of field, bokeh background"""
    
    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        model: str = "zai-org/glm-4.6v-flash"
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = ""  # Optional API key
        self.provider_id = ""
        self.provider_name = ""
        self.client = httpx.Client(timeout=180.0)  # Image analysis may require longer timeout
    
    def _get_headers(self) -> dict:
        """Get request headers"""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    def is_available(self) -> bool:
        """Check if the VLM model is available"""
        try:
            response = self.client.get(
                f"{self.base_url}/models", 
                timeout=5.0,
                headers=self._get_headers()
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def extract(self, content: str, **kwargs) -> MetaResult:
        """
        Extract meta information from an image
        
        Args:
            content: Base64-encoded image or URL
            **kwargs: Additional parameters
                - image_url: Image URL (if content is not a URL)
                - is_base64: Whether content is base64-encoded
        """
        is_base64 = kwargs.get("is_base64", False)
        image_url = kwargs.get("image_url", content)
        
        # Build message
        if is_base64:
            image_content = {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{content}"
                }
            }
        else:
            image_content = {
                "type": "image_url",
                "image_url": {
                    "url": image_url
                }
            }
        
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": self.IMAGE_PROMPT},
                    image_content
                ]
            }
        ]
        
        start = time.time()
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers=self._get_headers(),
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 5000
                }
            )
            response.raise_for_status()
            data = response.json()
            result_text = data["choices"][0]["message"]["content"]
            _report_meta_usage(self, data, int((time.time() - start) * 1000),
                               prompt_text=self.IMAGE_PROMPT, output_text=result_text)

            return self._parse_response(result_text)
            
        except Exception as e:
            _report_meta_usage(self, None, int((time.time() - start) * 1000),
                               status="error", error=str(e))
            print(f"Image meta extraction failed: {e}")
            return MetaResult(content_type="image")
    
    def _parse_response(self, response: str) -> MetaResult:
        """Parse VLM response"""
        # Clean response
        response = response.strip()
        
        # Filter out <think></think> reasoning tags
        response = self._remove_think_tags(response)
        
        # If result is invalid, return empty
        if not response or '<' in response:
            print(f"  Visual tag extraction result invalid")
            return MetaResult(meta_tags=[], meta_dict={"visual_tags": [], "tag_count": 0}, content_type="image")
        
        # Remove possible prefixes
        for prefix in ["标签:", "标签：", "Tags:", "tags:"]:
            if response.startswith(prefix):
                response = response[len(prefix):].strip()
        
        # Split tags and filter out invalid ones
        import re
        tags = []
        for t in response.split(','):
            t = t.strip()
            # Filter out tags with special characters and ensure reasonable length
            if t and len(t) < 30 and not re.search(r'[<>|"\[\]{}()]', t):
                tags.append(t)
        
        # Deduplicate
        seen = set()
        unique_tags = []
        for tag in tags:
            tag_lower = tag.lower()
            if tag_lower not in seen:
                seen.add(tag_lower)
                unique_tags.append(tag)
        
        # Build structured meta
        meta_dict = {
            "visual_tags": unique_tags,
            "tag_count": len(unique_tags)
        }
        
        return MetaResult(
            meta_tags=unique_tags,
            meta_dict=meta_dict,
            content_type="image"
        )
    
    def _remove_think_tags(self, text: str) -> str:
        """
        Remove special tags and their content from model output
        
        Args:
            text: Raw text
        
        Returns:
            Cleaned text
        """
        import re
        
        if not text:
            return ""
        
        # Match <think>...</think> tags and their content (including multiline)
        cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)
        
        # If there's no closing </think>, remove everything after <think>
        if '<think>' in cleaned.lower():
            # Remove all content after <think>
            cleaned = re.sub(r'<think>.*', '', cleaned, flags=re.DOTALL | re.IGNORECASE)
        
        # Remove <|begin_of_box|> and <|end_of_box|> tags
        cleaned = cleaned.replace('<|begin_of_box|>', '').replace('<|end_of_box|>', '')
        
        # Remove other possible special tags
        cleaned = re.sub(r'<\|[^|]+\|>', '', cleaned)
        
        # Clean up excess blank lines
        cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned)
        
        return cleaned.strip()
    
    def generate_description(self, image_base64: str) -> str:
        """
        Generate a detailed description of an image
        
        Args:
            image_base64: Base64-encoded image
        
        Returns:
            Detailed description text of the image
        """
        prompt = """Please describe this image in detail, including:
1. Main subject: The most important people, objects, or scenes in the image
2. Environment: Location, time of day, weather, and other environmental details
3. Details: Colors, textures, expressions, actions, and other specifics
4. Atmosphere: The overall mood or atmosphere conveyed

Please describe in fluent paragraph form, not list format. The description should be as detailed yet concise as possible, around 200-400 words."""

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                    }
                ]
            }
        ]
        
        start = time.time()
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.5,
                    "max_tokens": 8000
                }
            )
            response.raise_for_status()
            data = response.json()
            result = data["choices"][0]["message"]["content"].strip()
            _report_meta_usage(self, data, int((time.time() - start) * 1000),
                               prompt_text=prompt, output_text=result)
            # Filter <think> tags
            return self._remove_think_tags(result)
        except Exception as e:
            _report_meta_usage(self, None, int((time.time() - start) * 1000),
                               status="error", error=str(e))
            print(f"Image description generation failed: {e}")
            return ""
    
    def extract_ocr(self, image_base64: str) -> str:
        """
        Extract text from an image (OCR)
        
        Args:
            image_base64: Base64-encoded image
        
        Returns:
            Recognized text content
        """
        prompt = """Please carefully identify and extract all visible text content from this image.

Requirements:
1. Output text in positional order within the image (top to bottom, left to right)
2. Preserve original line breaks and paragraph structure
3. If it's a table, try to maintain the table structure
4. If text has special formatting (e.g., headings, lists), preserve it as much as possible
5. If there is no text in the image, simply reply "[no text content]"

Output the recognized text directly, without any explanations or prefixes."""

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}
                    }
                ]
            }
        ]
        
        start = time.time()
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": 2000
                }
            )
            response.raise_for_status()
            data = response.json()
            result = data["choices"][0]["message"]["content"].strip()
            _report_meta_usage(self, data, int((time.time() - start) * 1000),
                               prompt_text=prompt, output_text=result)

            # Filter <think> tags
            result = self._remove_think_tags(result)
            
            # If marked as no text content, return empty string
            if result == "[无文字内容]" or "无文字" in result or result == "[no text content]" or "no text content" in result.lower():
                return ""
            
            return result
        except Exception as e:
            _report_meta_usage(self, None, int((time.time() - start) * 1000),
                               status="error", error=str(e))
            print(f"OCR extraction failed: {e}")
            return ""
    
    def analyze_image_full(
        self, 
        image_base64: str,
        similar_tags: List[str] = None,
        tag_tree: List[str] = None
    ) -> Dict[str, Any]:
        """
        Full image analysis (in order):
        1. AI describes image content
        2. OCR extracts text
        3. Extract visual feature tags (meta_tags)
        4. Generate hierarchical tags (tags) - based on similar content tags + system tag hierarchy
        5. Extract structured Meta information (meta)
        
        Args:
            image_base64: Base64-encoded image
            similar_tags: Tags from similar content (primary reference)
            tag_tree: System's existing tag hierarchy
        
        Returns:
            Dictionary containing description, ocr, meta_tags, tags, meta
        """
        result = {
            "description": "",
            "ocr": "",
            "meta_tags": [],  # AI-extracted visual feature tags (should be 10+)
            "tags": [],       # Generated hierarchical tags (for archiving)
            "meta": {},       # Structured meta information
        }
        
        # Step 1: AI describes image content
        print("  [1/5] Generating image description...")
        try:
            result["description"] = self.generate_description(image_base64)
        except Exception as e:
            print(f"  Description generation failed: {e}")
        
        # Step 2: OCR text extraction
        print("  [2/5] OCR text recognition...")
        try:
            result["ocr"] = self.extract_ocr(image_base64)
        except Exception as e:
            print(f"  OCR failed: {e}")
        
        # Step 3: Extract visual feature tags (should generate 10+)
        print("  [3/5] Extracting visual feature tags...")
        try:
            meta_result = self.extract(image_base64, is_base64=True)
            result["meta_tags"] = meta_result.meta_tags
            print(f"    Extracted {len(result['meta_tags'])} visual tags")
        except Exception as e:
            print(f"  Visual tag extraction failed: {e}")
        
        # Step 4: Generate hierarchical tags - using similar tags as reference
        print("  [4/5] Generating hierarchical tags...")
        if similar_tags:
            print(f"    Reference similar tags: {len(similar_tags)} tags")
        try:
            result["tags"] = self.generate_hierarchical_tags(
                description=result["description"],
                ocr_text=result["ocr"],
                meta_tags=result["meta_tags"],
                similar_tags=similar_tags,
                tag_tree=tag_tree
            )
        except Exception as e:
            print(f"  Hierarchical tag generation failed: {e}")
        
        # Step 5: Extract structured Meta information
        print("  [5/5] Extracting structured Meta information...")
        try:
            result["meta"] = self.extract_structured_meta(
                description=result["description"],
                ocr_text=result["ocr"],
                visual_tags=result["meta_tags"]
            )
        except Exception as e:
            print(f"  Meta information extraction failed: {e}")
            result["meta"] = self._build_empty_meta(result["meta_tags"])
        
        return result
    
    def generate_hierarchical_tags(
        self, 
        description: str, 
        ocr_text: str, 
        meta_tags: List[str],
        similar_tags: List[str] = None,
        tag_tree: List[str] = None
    ) -> List[str]:
        """
        Generate hierarchical tags based on image content (similar to text document tag systems)
        
        Args:
            description: Image description
            ocr_text: OCR-recognized text
            meta_tags: Visual feature tags
            similar_tags: Tags from similar content (primary reference)
            tag_tree: System's existing tag hierarchy
        
        Returns:
            List of hierarchical tags
        """
        # Build context
        context_parts = []
        if description:
            context_parts.append(f"[Image Description]\n{description}")
        if ocr_text:
            context_parts.append(f"[Text in Image]\n{ocr_text[:500]}")
        if meta_tags:
            context_parts.append(f"[Visual Features]\n{', '.join(meta_tags[:30])}")
        
        if not context_parts:
            return []
        
        context = "\n\n".join(context_parts)
        
        # Build similar article tag hints (most important reference)
        similar_hint = ""
        if similar_tags and len(similar_tags) > 0:
            # Count tag occurrences, sort by frequency
            tag_counts = {}
            for tag in similar_tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
            sorted_tags = sorted(tag_counts.items(), key=lambda x: x[1], reverse=True)
            top_tags = [f"{tag}({count}x)" for tag, count in sorted_tags[:15]]
            similar_hint = f"""
[⭐ Tags from Similar Content (Primary Reference)]
{', '.join(top_tags)}

Please prioritize selecting appropriate tags from the similar content tags above!"""
        
        # Build existing tag hierarchy hints
        tag_tree_hint = ""
        if tag_tree and len(tag_tree) > 0:
            # Only use first 50 tags as reference
            sample_tags = tag_tree[:50]
            tag_tree_hint = f"""
[System's Existing Tag Hierarchy]
{', '.join(sample_tags)}

If similar content tags are not applicable, please select from the tag hierarchy above."""
        
        prompt = f"""Based on the following image information, generate hierarchical tags suitable for archiving and retrieval.

{context}
{similar_hint}
{tag_tree_hint}

[Requirements]
1. Generate 2-5 tags in the format "level1/level2" or "level1/level2/level3"
2. Tags should be abstract, classifiable concepts (e.g.: lifestyle/shopping, travel/scenery, work/meeting, etc.)
3. Prioritize using tags from similar content or the system's existing tags
4. Separate with commas
5. [Important] Output tags directly, do not output any reasoning or explanations

[Output Example]
lifestyle/shopping, daily/grocery, spending-record"""

        # Use system prompt to force no reasoning output
        messages = [
            {"role": "system", "content": "You are a tag generation assistant. Only output tags separated by commas, without any reasoning or explanations."},
            {"role": "user", "content": prompt}
        ]
        
        start = time.time()
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.3,
                    "max_tokens": 10000
                }
            )
            response.raise_for_status()
            data = response.json()
            result = data["choices"][0]["message"]["content"].strip()
            _report_meta_usage(self, data, int((time.time() - start) * 1000),
                               prompt_text=prompt, output_text=result)

            # Clean special tags
            result = self._remove_think_tags(result)
            
            # If result is empty or still contains special tags, return empty
            if not result or '<' in result:
                print(f"  Hierarchical tag generation result invalid: {result[:100] if result else 'empty'}")
                return []
            
            # Parse tags (accept CJK, Latin, digits, and spaces)
            import re
            tags = []
            for t in result.split(','):
                t = t.strip()
                # Filter out tags with special characters
                if t and len(t) < 20 and not re.search(r'[<>|"\'\[\]{}()]', t):
                    tags.append(t)
            
            # Deduplicate
            seen = set()
            unique_tags = []
            for tag in tags:
                tag_lower = tag.lower()
                if tag_lower not in seen and tag not in meta_tags:
                    seen.add(tag_lower)
                    unique_tags.append(tag)
            
            return unique_tags[:8]
        except Exception as e:
            _report_meta_usage(self, None, int((time.time() - start) * 1000),
                               status="error", error=str(e))
            print(f"Hierarchical tag generation failed: {e}")
            return []
    
    def extract_structured_meta(
        self, 
        description: str, 
        ocr_text: str, 
        visual_tags: List[str]
    ) -> Dict[str, Any]:
        """
        Extract structured Meta information from image content
        
        Args:
            description: Image description
            ocr_text: OCR-recognized text
            visual_tags: Visual feature tags
        
        Returns:
            Structured meta dictionary
        """
        # Build context
        context_parts = []
        if description:
            context_parts.append(f"Image description: {description}")
        if ocr_text:
            context_parts.append(f"Text in image: {ocr_text[:1000]}")
        if visual_tags:
            context_parts.append(f"Visual features: {', '.join(visual_tags[:30])}")
        
        if not context_parts:
            return self._build_empty_meta(visual_tags)
        
        context = "\n".join(context_parts)
        
        prompt = f"""Analyze the following image information and extract structured metadata.

{context}

Please output in the following JSON format (output JSON directly, no other content):
{{
  "entities": {{
    "person": ["identified person names"],
    "location": ["identified locations"],
    "organization": ["identified organizations/brands"],
    "object": ["main objects"]
  }},
  "time_expressions": {{
    "mentioned": ["times mentioned in the image"],
    "scene_time": "inferred time of day (e.g.: daytime/evening/night)"
  }},
  "theme": ["theme1", "theme2"],
  "scene": "scene type (e.g.: indoor/outdoor/mall/park)",
  "sentiment": "overall sentiment (e.g.: positive/neutral/negative)",
  "domain": ["domain1", "domain2"]
}}

Notes:
1. Only fill in information that can be determined from the image
2. Use empty arrays or empty strings for undetermined fields
3. Output JSON directly, no explanations"""

        messages = [
            {"role": "system", "content": "You are a metadata extraction assistant. Only output results in JSON format, without any explanations or reasoning."},
            {"role": "user", "content": prompt}
        ]
        
        start = time.time()
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 1000
                }
            )
            response.raise_for_status()
            data = response.json()
            result = data["choices"][0]["message"]["content"].strip()
            _report_meta_usage(self, data, int((time.time() - start) * 1000),
                               prompt_text=prompt, output_text=result)

            # Clean special tags
            result = self._remove_think_tags(result)
            
            # Try to parse JSON
            import json
            
            # Remove possible markdown code block markers
            if result.startswith("```"):
                lines = result.split('\n')
                result = '\n'.join(lines[1:-1] if lines[-1].strip() == '```' else lines[1:])
            
            meta = json.loads(result)
            
            # Add visual tags to meta
            meta["visual_tags"] = visual_tags
            meta["visual_tag_count"] = len(visual_tags)
            
            return meta
            
        except json.JSONDecodeError as e:
            print(f"  Meta JSON parsing failed: {e}")
            return self._build_empty_meta(visual_tags)
        except Exception as e:
            _report_meta_usage(self, None, int((time.time() - start) * 1000),
                               status="error", error=str(e))
            print(f"Structured meta extraction failed: {e}")
            return self._build_empty_meta(visual_tags)
    
    def _build_empty_meta(self, visual_tags: List[str]) -> Dict[str, Any]:
        """Build an empty meta structure"""
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
            "visual_tags": visual_tags,
            "visual_tag_count": len(visual_tags)
        }


class MetaExtractorFactory:
    """Meta extractor factory"""
    
    def __init__(self, config: dict):
        self.config = config
        self._extractors = {}
    
    def get_extractor(self, content_type: str) -> Optional[BaseMetaExtractor]:
        """Get the extractor for the corresponding content type"""
        if content_type in self._extractors:
            return self._extractors[content_type]
        
        # Background: Meta extractors (text/image understanding) build OpenAI-compatible requests directly.
        # Design intent: now retrieves connection info from the Provider registry for the current chat provider;
        # Gemini protocol uses Google's OpenAI-compatible endpoint with "models/" prefix removed;
        # vlm_model can be configured separately per Provider entry, defaults to chat_model.
        from .providers import normalize_config, get_provider_entry, resolve_api_key

        normalize_config(self.config)
        provider_id = (self.config.get("active") or {}).get("chat_provider", "lmstudio")
        entry = get_provider_entry(self.config, provider_id) or {}

        base_url = (entry.get("base_url") or "http://localhost:1234/v1").rstrip("/")
        chat_model = entry.get("chat_model") or "openai/gpt-oss-20b"
        vlm_model = entry.get("vlm_model") or chat_model
        if entry.get("api_type") == "gemini":
            base_url = f"{base_url}/openai" if base_url.endswith("/v1beta") else base_url
            chat_model = chat_model.removeprefix("models/")
            vlm_model = vlm_model.removeprefix("models/")
        api_key = resolve_api_key(entry)
        
        if content_type == "text":
            extractor = TextMetaExtractor(base_url=base_url, model=chat_model)
            # Set API key if available
            if api_key:
                extractor.api_key = api_key
        elif content_type == "image":
            extractor = ImageMetaExtractor(base_url=base_url, model=vlm_model)
            if api_key:
                extractor.api_key = api_key
        else:
            # Other types not yet supported
            return None

        # Usage attribution: record the Provider behind this extractor for usage page and pricing resolution
        extractor.provider_id = provider_id
        extractor.provider_name = entry.get("name", provider_id)
        
        self._extractors[content_type] = extractor
        return extractor
    
    def extract_meta(self, content: str, content_type: str = "text", **kwargs) -> MetaResult:
        """Extract meta information"""
        extractor = self.get_extractor(content_type)
        
        if not extractor:
            print(f"Unsupported content type: {content_type}")
            return MetaResult(content_type=content_type)
        
        if not extractor.is_available():
            print(f"{content_type} extractor not available")
            return MetaResult(content_type=content_type)
        
        return extractor.extract(content, **kwargs)


def create_meta_extractor(config: dict) -> MetaExtractorFactory:
    """Create a Meta extractor factory from configuration"""
    return MetaExtractorFactory(config)

