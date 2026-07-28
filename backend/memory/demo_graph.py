"""
Demo knowledge graph: entities, relations and dated events for the sample library

Background: entities, relations and time events are normally produced by the LLM metadata
extractor. The public demo has no chat model on purpose, so the Graph page would render three
empty tabs — the feature that is hardest to explain in words is the one a visitor cannot see.
Design intent: the sample library is fixed, so its graph can be written by hand and inserted
alongside the seeded memories. The result is deterministic, costs nothing, and needs no LLM,
which keeps the demo's zero-spend guarantee intact.
Key constraints:
  - Everything here must be grounded in SAMPLE_MEMORIES_EN; inventing facts the visitor cannot
    find in the memories would make the graph look wrong rather than illustrative
  - Trunks are produced asynchronously after seeding, so linking waits for them to appear
  - Dated events are stored relative to the current year, otherwise the timeline would drift
    into the past as the demo keeps running

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

from datetime import datetime, timedelta
from typing import Optional

#: Entities per source memory title. Tuples are (name, entity_type, description, role).
#: role is the label shown when the entity is listed under a memory.
DEMO_ENTITIES: dict[str, list[tuple[str, str, str, Optional[str]]]] = {
    "About me": [
        ("Alex", "person", "Software engineer, 28, based in San Francisco", "self"),
        ("San Francisco", "location", "Where Alex lives", None),
        ("Spanish", "concept", "Language Alex is learning", None),
    ],
    "Hobbies and interests": [
        ("Alex", "person", "Software engineer, 28, based in San Francisco", "self"),
        ("Running", "concept", "5 km every morning", None),
        ("Reading", "concept", "Sci-fi, design and non-fiction", None),
        ("Cooking", "concept", "Mostly Italian food", None),
        ("Photography", "concept", "Street and landscape", None),
        ("Coffee", "product", "Flat white, no sugar", None),
    ],
    "People at work": [
        ("Alex", "person", "Software engineer, 28, based in San Francisco", "self"),
        ("Sarah Chen", "person", "Engineering manager; prefers async updates", "manager"),
        ("Dan", "person", "Backend engineer; great at debugging", "teammate"),
        ("Priya", "person", "Product designer; reachable on Slack", "teammate"),
        ("Tom", "person", "Frontend engineer; fast coder", "teammate"),
        ("Slack", "product", "Where Priya prefers to be reached", None),
        ("Board games", "concept", "What Dan does outside work", None),
    ],
    "Family and friends": [
        ("Alex", "person", "Software engineer, 28, based in San Francisco", "self"),
        ("Mom", "person", "Retired teacher; weekly Sunday call", "family"),
        ("Dad", "person", "Engineer who loves hiking", "family"),
        ("Emma", "person", "Alex's sister, in med school", "family"),
        ("Jake", "person", "College roommate, lives in NYC", "friend"),
        ("Mia", "person", "Met at a hackathon; works at a startup", "friend"),
        ("New York City", "location", "Where Jake lives", None),
        ("Hiking", "concept", "What Dad enjoys", None),
    ],
    "Daily routine": [
        ("Alex", "person", "Software engineer, 28, based in San Francisco", "self"),
        ("Running", "concept", "5 km every morning", None),
        ("Team standup", "concept", "Mondays at 10 AM", None),
        ("Gym", "location", "Wednesdays after work", None),
    ],
    "Current to-do list": [
        ("Alex", "person", "Software engineer, 28, based in San Francisco", "self"),
        ("Dan", "person", "Backend engineer; great at debugging", "teammate"),
        ("Rust", "concept", "Working through the Rust book", None),
        ("API refactor", "concept", "Work in progress", None),
    ],
    "Thoughts and wishes": [
        ("Alex", "person", "Software engineer, 28, based in San Francisco", "self"),
        ("Japan", "location", "Somewhere Alex wants to visit", None),
        ("Guitar", "concept", "Something Alex wants to learn", None),
        ("Vinyl records", "product", "Alex collects them", None),
    ],
}

#: Relations as (subject, subject_type, relation_type, object, object_type).
#: The source memory anchors each edge so the graph can point back at where it came from.
DEMO_RELATIONS: list[tuple[str, str, str, str, str, str]] = [
    ("Alex", "person", "lives in", "San Francisco", "location", "About me"),
    ("Alex", "person", "reports to", "Sarah Chen", "person", "People at work"),
    ("Alex", "person", "works with", "Dan", "person", "People at work"),
    ("Alex", "person", "works with", "Priya", "person", "People at work"),
    ("Alex", "person", "works with", "Tom", "person", "People at work"),
    ("Priya", "person", "prefers", "Slack", "product", "People at work"),
    ("Dan", "person", "enjoys", "Board games", "concept", "People at work"),
    ("Alex", "person", "child of", "Mom", "person", "Family and friends"),
    ("Alex", "person", "child of", "Dad", "person", "Family and friends"),
    ("Alex", "person", "sibling of", "Emma", "person", "Family and friends"),
    ("Alex", "person", "friend of", "Jake", "person", "Family and friends"),
    ("Alex", "person", "friend of", "Mia", "person", "Family and friends"),
    ("Jake", "person", "lives in", "New York City", "location", "Family and friends"),
    ("Dad", "person", "enjoys", "Hiking", "concept", "Family and friends"),
    ("Alex", "person", "enjoys", "Running", "concept", "Hobbies and interests"),
    ("Alex", "person", "enjoys", "Reading", "concept", "Hobbies and interests"),
    ("Alex", "person", "enjoys", "Cooking", "concept", "Hobbies and interests"),
    ("Alex", "person", "enjoys", "Photography", "concept", "Hobbies and interests"),
    ("Alex", "person", "drinks", "Coffee", "product", "Hobbies and interests"),
    ("Alex", "person", "learning", "Rust", "concept", "Current to-do list"),
    ("Alex", "person", "learning", "Spanish", "concept", "About me"),
    ("Alex", "person", "wants to visit", "Japan", "location", "Thoughts and wishes"),
    ("Alex", "person", "wants to learn", "Guitar", "concept", "Thoughts and wishes"),
    ("Alex", "person", "collects", "Vinyl records", "product", "Thoughts and wishes"),
]

#: Dated events as (source memory, month, day, summary, original text, event type).
#: Year is filled in at seed time so the timeline always covers the current year.
DEMO_EVENTS: list[tuple[str, int, int, str, str, str]] = [
    ("About me", 3, 15, "Alex's birthday", "Birthday: March 15", "anniversary"),
    ("Family and friends", 6, 10, "Dad's birthday", "Dad's birthday: June 10", "anniversary"),
    ("Family and friends", 9, 2, "Mom's birthday", "Mom's birthday: September 2", "anniversary"),
    ("Family and friends", 12, 5, "Emma's birthday", "Emma's birthday: December 5", "anniversary"),
]

#: Month-day pairs are ambiguous for recurring tasks, so near-term to-dos are placed relative
#: to the seeding date instead. Tuples are (source memory, days from today, summary, text).
DEMO_RELATIVE_EVENTS: list[tuple[str, int, str, str]] = [
    ("Current to-do list", 2, "Review Dan's pull request", "Review Dan's pull request"),
    ("Current to-do list", 5, "Pay rent", "Pay rent (due the 5th)"),
    ("Current to-do list", 9, "Book dentist appointment", "Book dentist appointment"),
    ("Current to-do list", 14, "Finish Chapter 4 of the Rust book",
     "Finish Chapter 4 of the Rust book"),
    ("Daily routine", 1, "Team standup", "Monday: team standup at 10 AM"),
]


def _first_trunk_by_title(database) -> dict[str, str]:
    """Map each seeded memory title to its first trunk id, skipping titles with no trunk yet."""
    mapping: dict[str, str] = {}
    for memory in database.list_memories(limit=200):
        title = getattr(memory, "title", None)
        memory_id = getattr(memory, "id", None)
        if not title or not memory_id:
            continue
        trunks = database.get_trunks_by_document(memory_id)
        if trunks:
            mapping[title] = trunks[0].id
    return mapping


def seed_demo_graph(database) -> dict:
    """
    Insert the hand-written graph for the sample library.

    Returns counts of what was written. Safe to call only on a freshly seeded store: entity
    upserts are keyed by name, but relations and events are not deduplicated.
    """
    trunk_by_title = _first_trunk_by_title(database)
    if not trunk_by_title:
        return {"entities": 0, "relations": 0, "events": 0}

    entity_ids: dict[tuple[str, str], int] = {}
    linked = 0

    for title, entities in DEMO_ENTITIES.items():
        trunk_id = trunk_by_title.get(title)
        if not trunk_id:
            continue
        for name, entity_type, description, role in entities:
            key = (name, entity_type)
            if key not in entity_ids:
                entity_ids[key] = database.upsert_entity(name, entity_type, description)
            database.link_entity_to_trunk(entity_ids[key], trunk_id, role=role)
            linked += 1

    relations = 0
    for subject, subject_type, relation, obj, object_type, source_title in DEMO_RELATIONS:
        subject_id = entity_ids.get((subject, subject_type))
        object_id = entity_ids.get((obj, object_type))
        if not subject_id or not object_id:
            continue
        database.add_entity_relation(
            subject_id, relation, object_id,
            source_trunk_id=trunk_by_title.get(source_title),
        )
        relations += 1

    events = 0
    now = datetime.now()
    for source_title, month, day, summary, text, event_type in DEMO_EVENTS:
        trunk_id = trunk_by_title.get(source_title)
        if not trunk_id:
            continue
        database.add_time_event({
            "trunk_id": trunk_id,
            "original_text": text,
            "event_summary": summary,
            "absolute_time": datetime(now.year, month, day, 9, 0).isoformat(),
            "time_precision": "day",
            "event_type": event_type,
            "status": "pending",
        })
        events += 1

    for source_title, offset_days, summary, text in DEMO_RELATIVE_EVENTS:
        trunk_id = trunk_by_title.get(source_title)
        if not trunk_id:
            continue
        when = now.replace(hour=10, minute=0, second=0, microsecond=0)
        database.add_time_event({
            "trunk_id": trunk_id,
            "original_text": text,
            "event_summary": summary,
            "absolute_time": (when + timedelta(days=offset_days)).isoformat(),
            "time_precision": "day",
            "event_type": "todo",
            "status": "pending",
        })
        events += 1

    return {"entities": len(entity_ids), "links": linked, "relations": relations, "events": events}
