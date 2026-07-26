"""
Time expression extraction and anchoring

Identifies time expressions in text and converts them to absolute times based on an anchor time.

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass


@dataclass
class TimeExpression:
    """Time expression"""
    original_text: str       # Original text
    start: int              # Start position in the original text
    end: int                # End position in the original text
    absolute_time: str      # Anchored absolute time (ISO format)
    precision: str          # Precision: year/month/day/hour/minute
    is_range: bool = False  # Whether this is a time range
    range_end: str = None   # End time of the range
    event_summary: str = None  # Event summary (extracted from context)


class TimeExtractor:
    """Time expression extractor"""
    
    # Relative time patterns
    RELATIVE_PATTERNS = [
        # Days
        (r'今天', 0, 'day'),
        (r'明天', 1, 'day'),
        (r'后天', 2, 'day'),
        (r'大后天', 3, 'day'),
        (r'昨天', -1, 'day'),
        (r'前天', -2, 'day'),
        (r'大前天', -3, 'day'),
        
        # N days ago/later
        (r'(\d+)\s*天后', 'days_after', 'day'),
        (r'(\d+)\s*天前', 'days_before', 'day'),
        
        # Weeks
        (r'这周|本周', 'this_week', 'day'),
        (r'下周', 'next_week', 'day'),
        (r'上周', 'last_week', 'day'),
        (r'下下周', 'next_next_week', 'day'),
        
        # Day of the week
        (r'(?:这|本)?周([一二三四五六日天])', 'this_weekday', 'day'),
        (r'下周([一二三四五六日天])', 'next_weekday', 'day'),
        (r'上周([一二三四五六日天])', 'last_weekday', 'day'),
        
        # Months
        (r'这个月|本月', 'this_month', 'day'),
        (r'下个月|下月', 'next_month', 'day'),
        (r'上个月|上月', 'last_month', 'day'),
        
        # Position within month
        (r'月初', 'month_start', 'day'),
        (r'月中|中旬', 'month_mid', 'day'),
        (r'月末|月底', 'month_end', 'day'),
        
        # Years
        (r'今年', 'this_year', 'year'),
        (r'明年', 'next_year', 'year'),
        (r'去年', 'last_year', 'year'),
        (r'前年', 'year_before_last', 'year'),
        
        # Quarters/Seasons
        (r'这个季度', 'this_quarter', 'month'),
        (r'下个季度', 'next_quarter', 'month'),
        (r'上个季度', 'last_quarter', 'month'),
        (r'春天|春季', 'spring', 'month'),
        (r'夏天|夏季', 'summer', 'month'),
        (r'秋天|秋季', 'autumn', 'month'),
        (r'冬天|冬季', 'winter', 'month'),
    ]
    
    # Absolute time patterns
    ABSOLUTE_PATTERNS = [
        # Full date
        (r'(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})[日号]?', 'full_date'),
        # Month and day
        (r'(\d{1,2})[-月/](\d{1,2})[日号]?', 'month_day'),
        # Month only
        (r'(\d{1,2})月份?(?![日号\d])', 'month_only'),
        # Day only
        (r'(\d{1,2})[日号]', 'day_only'),
    ]
    
    # Time patterns
    TIME_PATTERNS = [
        (r'(\d{1,2})[点时:](\d{1,2})?分?', 'time'),
        (r'早上|上午|早晨', 'morning', 9),
        (r'中午', 'noon', 12),
        (r'下午', 'afternoon', 14),
        (r'晚上|晚间', 'evening', 19),
        (r'深夜|凌晨', 'midnight', 2),
    ]
    
    # Weekday mapping
    WEEKDAY_MAP = {
        '一': 0, '二': 1, '三': 2, '四': 3, 
        '五': 4, '六': 5, '日': 6, '天': 6
    }
    
    def __init__(self, chat_model=None):
        """
        Initialize the time extractor
        
        Args:
            chat_model: Optional LLM model for extracting event context
        """
        self.chat_model = chat_model
    
    def extract(self, text: str, anchor_time: datetime = None) -> List[TimeExpression]:
        """
        Extract time expressions from text and anchor them
        
        Args:
            text: Source text
            anchor_time: Anchor time, defaults to current time
            
        Returns:
            List of time expressions
        """
        if anchor_time is None:
            anchor_time = datetime.now()
        
        expressions = []
        
        # 1. Extract relative times
        expressions.extend(self._extract_relative_times(text, anchor_time))
        
        # 2. Extract absolute times
        expressions.extend(self._extract_absolute_times(text, anchor_time))
        
        # 3. Deduplicate (by position)
        expressions = self._deduplicate(expressions)
        
        # 4. Extract event context
        if self.chat_model:
            expressions = self._extract_event_context(text, expressions)
        else:
            # Simple context extraction
            expressions = self._extract_simple_context(text, expressions)
        
        return expressions
    
    def _extract_relative_times(self, text: str, anchor: datetime) -> List[TimeExpression]:
        """Extract relative time expressions"""
        results = []
        
        for pattern_info in self.RELATIVE_PATTERNS:
            pattern, offset_info, precision = pattern_info
            
            for match in re.finditer(pattern, text):
                try:
                    absolute_time = self._resolve_relative(match, offset_info, anchor)
                    if absolute_time:
                        results.append(TimeExpression(
                            original_text=match.group(),
                            start=match.start(),
                            end=match.end(),
                            absolute_time=absolute_time.isoformat(),
                            precision=precision
                        ))
                except Exception:
                    continue
        
        return results
    
    def _resolve_relative(self, match, offset_info, anchor: datetime) -> Optional[datetime]:
        """Resolve relative time to absolute time"""
        
        # Simple day offset
        if isinstance(offset_info, int):
            return anchor + timedelta(days=offset_info)
        
        # N days ago/later
        if offset_info == 'days_after':
            days = int(match.group(1))
            return anchor + timedelta(days=days)
        if offset_info == 'days_before':
            days = int(match.group(1))
            return anchor - timedelta(days=days)
        
        # Week-related
        if offset_info == 'this_week':
            return anchor - timedelta(days=anchor.weekday())  # This Monday
        if offset_info == 'next_week':
            return anchor + timedelta(days=(7 - anchor.weekday()))  # Next Monday
        if offset_info == 'last_week':
            return anchor - timedelta(days=(anchor.weekday() + 7))  # Last Monday
        if offset_info == 'next_next_week':
            return anchor + timedelta(days=(14 - anchor.weekday()))  # Monday two weeks from now
        
        # Day of the week
        if offset_info in ['this_weekday', 'next_weekday', 'last_weekday']:
            weekday_char = match.group(1)
            target_weekday = self.WEEKDAY_MAP.get(weekday_char, 0)
            current_weekday = anchor.weekday()
            
            if offset_info == 'this_weekday':
                days_diff = target_weekday - current_weekday
                # If the target day has passed, default to next week (future-oriented)
                if days_diff <= 0:
                    days_diff += 7
                return anchor + timedelta(days=days_diff)
            elif offset_info == 'next_weekday':
                days_diff = (7 - current_weekday) + target_weekday
                return anchor + timedelta(days=days_diff)
            else:  # last_weekday
                days_diff = current_weekday - target_weekday
                if days_diff <= 0:
                    days_diff += 7
                return anchor - timedelta(days=days_diff)
        
        # Month-related
        if offset_info == 'this_month':
            return anchor.replace(day=1)
        if offset_info == 'next_month':
            if anchor.month == 12:
                return anchor.replace(year=anchor.year + 1, month=1, day=1)
            return anchor.replace(month=anchor.month + 1, day=1)
        if offset_info == 'last_month':
            if anchor.month == 1:
                return anchor.replace(year=anchor.year - 1, month=12, day=1)
            return anchor.replace(month=anchor.month - 1, day=1)
        
        # Position within month
        if offset_info == 'month_start':
            return anchor.replace(day=1)
        if offset_info == 'month_mid':
            return anchor.replace(day=15)
        if offset_info == 'month_end':
            return anchor.replace(day=25)
        
        # Year-related
        if offset_info == 'this_year':
            return anchor.replace(month=1, day=1)
        if offset_info == 'next_year':
            return anchor.replace(year=anchor.year + 1, month=1, day=1)
        if offset_info == 'last_year':
            return anchor.replace(year=anchor.year - 1, month=1, day=1)
        if offset_info == 'year_before_last':
            return anchor.replace(year=anchor.year - 2, month=1, day=1)
        
        # Quarters
        if offset_info == 'this_quarter':
            quarter_month = ((anchor.month - 1) // 3) * 3 + 1
            return anchor.replace(month=quarter_month, day=1)
        if offset_info == 'next_quarter':
            quarter_month = ((anchor.month - 1) // 3) * 3 + 4
            if quarter_month > 12:
                return anchor.replace(year=anchor.year + 1, month=quarter_month - 12, day=1)
            return anchor.replace(month=quarter_month, day=1)
        if offset_info == 'last_quarter':
            quarter_month = ((anchor.month - 1) // 3) * 3 - 2
            if quarter_month < 1:
                return anchor.replace(year=anchor.year - 1, month=quarter_month + 12, day=1)
            return anchor.replace(month=quarter_month, day=1)
        
        # Seasons (aligned to the first month of the quarter)
        if offset_info == 'spring':
            return anchor.replace(month=3, day=1)
        if offset_info == 'summer':
            return anchor.replace(month=6, day=1)
        if offset_info == 'autumn':
            return anchor.replace(month=9, day=1)
        if offset_info == 'winter':
            return anchor.replace(month=12, day=1)
        
        return None
    
    def _extract_absolute_times(self, text: str, anchor: datetime) -> List[TimeExpression]:
        """Extract absolute time expressions"""
        results = []
        
        for pattern, pattern_type in self.ABSOLUTE_PATTERNS:
            for match in re.finditer(pattern, text):
                try:
                    absolute_time, precision = self._resolve_absolute(
                        match, pattern_type, anchor
                    )
                    if absolute_time:
                        results.append(TimeExpression(
                            original_text=match.group(),
                            start=match.start(),
                            end=match.end(),
                            absolute_time=absolute_time.isoformat(),
                            precision=precision
                        ))
                except Exception:
                    continue
        
        return results
    
    def _resolve_absolute(self, match, pattern_type: str, anchor: datetime) -> Tuple[Optional[datetime], str]:
        """Resolve absolute time"""
        
        if pattern_type == 'full_date':
            year = int(match.group(1))
            month = int(match.group(2))
            day = int(match.group(3))
            return datetime(year, month, day), 'day'
        
        if pattern_type == 'month_day':
            month = int(match.group(1))
            day = int(match.group(2))
            # Smart year inference: default to future
            year = anchor.year
            candidate = datetime(year, month, day)
            if candidate < anchor:
                year += 1
            return datetime(year, month, day), 'day'
        
        if pattern_type == 'month_only':
            month = int(match.group(1))
            year = anchor.year
            # Default to future
            if month < anchor.month:
                year += 1
            return datetime(year, month, 1), 'month'
        
        if pattern_type == 'day_only':
            day = int(match.group(1))
            year = anchor.year
            month = anchor.month
            # Default to future
            if day < anchor.day:
                if month == 12:
                    month = 1
                    year += 1
                else:
                    month += 1
            return datetime(year, month, day), 'day'
        
        return None, 'day'
    
    def _deduplicate(self, expressions: List[TimeExpression]) -> List[TimeExpression]:
        """Deduplicate: remove overlapping time expressions, keeping longer/more precise ones"""
        if not expressions:
            return []
        
        # Sort by start position and length
        sorted_exprs = sorted(expressions, key=lambda x: (x.start, -(x.end - x.start)))
        
        result = []
        last_end = -1
        
        for expr in sorted_exprs:
            if expr.start >= last_end:
                result.append(expr)
                last_end = expr.end
        
        return result
    
    def _extract_simple_context(self, text: str, expressions: List[TimeExpression]) -> List[TimeExpression]:
        """Simple context extraction (without LLM)"""
        for expr in expressions:
            # Get text surrounding the time expression
            context_start = max(0, expr.start - 20)
            context_end = min(len(text), expr.end + 50)
            context = text[context_start:context_end]
            
            # Remove the time expression itself, use the remainder as event description
            before = text[context_start:expr.start].strip()
            after = text[expr.end:context_end].strip()
            
            # Simple heuristic: if followed by a verb or noun phrase, use it as the event
            summary = after[:30] if after else before[-30:] if before else None
            if summary:
                # Clean up punctuation
                summary = re.sub(r'^[，。！？、：；""''（）\s]+', '', summary)
                summary = re.sub(r'[，。！？、：；""''（）\s]+$', '', summary)
                expr.event_summary = summary[:50] if summary else None
        
        return expressions
    
    def _extract_event_context(self, text: str, expressions: List[TimeExpression]) -> List[TimeExpression]:
        """Extract event context using LLM"""
        if not expressions or not self.chat_model:
            return expressions
        
        # Build prompt
        time_markers = []
        for i, expr in enumerate(expressions):
            time_markers.append(f"{i+1}. \"{expr.original_text}\" (position {expr.start}-{expr.end})")
        
        prompt = f"""Extract the corresponding event description for each time expression from the following text.

Text:
{text}

Time expressions:
{chr(10).join(time_markers)}

Please extract a brief event description (10-30 characters) for each time expression, in the following format:
1. Event description
2. Event description
...

Only output event descriptions, no other explanations. If a time has no clear event, output "no clear event"."""

        try:
            response = self.chat_model.generate(prompt, max_tokens=500)
            
            # Parse response
            lines = response.strip().split('\n')
            for i, line in enumerate(lines):
                if i < len(expressions):
                    # Remove numbering
                    summary = re.sub(r'^\d+[.、\s]+', '', line).strip()
                    if summary and summary != "no clear event":
                        expressions[i].event_summary = summary[:50]
        except Exception:
            # Fall back to simple extraction on failure
            return self._extract_simple_context(text, expressions)
        
        return expressions
    
    def extract_with_llm(self, text: str, anchor_time: datetime = None) -> List[TimeExpression]:
        """
        Extract time expressions with LLM assistance (more accurate but slower)
        
        Args:
            text: Source text
            anchor_time: Anchor time
            
        Returns:
            List of time expressions
        """
        if anchor_time is None:
            anchor_time = datetime.now()
        
        if not self.chat_model:
            return self.extract(text, anchor_time)
        
        prompt = f"""Analyze the following text, extract all time expressions, and convert them to absolute times.

Text:
{text}

Current time (anchor time): {anchor_time.strftime('%Y-%m-%d %H:%M')}

Please output in the following JSON format:
[
  {{
    "original": "time expression from the original text",
    "absolute": "YYYY-MM-DD HH:MM or YYYY-MM-DD",
    "precision": "year/month/day/hour/minute",
    "event": "related event description (if any)"
  }}
]

Notes:
1. Relative times like "tomorrow" or "next Wednesday" should be converted based on the anchor time
2. "3 o'clock" defaults to 15:00 (afternoon)
3. Ambiguous dates like "January 2nd" default to the nearest future date
4. Only output the JSON array, no other content"""

        try:
            import json
            response = self.chat_model.generate(prompt, max_tokens=1000)
            
            # Clean up response
            response = response.strip()
            if response.startswith('```'):
                response = re.sub(r'^```\w*\n?', '', response)
                response = re.sub(r'\n?```$', '', response)
            
            data = json.loads(response)
            
            results = []
            for item in data:
                # Find position in original text
                original = item.get('original', '')
                match = re.search(re.escape(original), text)
                if match:
                    results.append(TimeExpression(
                        original_text=original,
                        start=match.start(),
                        end=match.end(),
                        absolute_time=item.get('absolute', ''),
                        precision=item.get('precision', 'day'),
                        event_summary=item.get('event')
                    ))
            
            return results
            
        except Exception as e:
            print(f"LLM time extraction failed: {e}, falling back to regex extraction")
            return self.extract(text, anchor_time)

