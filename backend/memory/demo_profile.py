"""
Demo profile: filled-in fields, a hand-written intro, and the claims an LLM would have distilled

Background: the profile layer is normally built by the distillation run, which the demo disables to
stay free to host. The Profile page therefore rendered blank inputs and empty tabs — the layer that
explains what AsterMem does with a memory library was the one with nothing in it.
Design intent: the sample library is fixed, so its profile can be written by hand and inserted next
to the memories, exactly as the knowledge graph is. Deterministic, no model call, no cost.
Key constraints:
  - Everything traces back to SAMPLE_MEMORIES_EN; a profile asserting things the visitor cannot
    find in the memories would misrepresent how the feature works
  - Claims carry the memory ids they came from, because the UI offers to show the source text
  - Fields are written as 'distilled' rather than 'manual': they are meant to look like AI output,
    and manual values are the ones the distiller is forbidden to touch

Copyright (c) 2026 Asterove
AGPL-3.0 License
"""

import json
from datetime import datetime, timedelta
from typing import Optional

#: L1/L2 field values, matching the "About me" and "People at work" samples.
DEMO_FIELDS = {
    "nickname": "Alex",
    "gender": "Prefer not to say",
    "language": "English, some Spanish",
    "timezone": "America/Los_Angeles",
    "occupation": "Software engineer",
    "location": "San Francisco",
    "organization": "Engineering team under Sarah Chen",
    "focus": "API refactor, auth flow design doc, learning Rust",
    "preferences": "Async updates over meetings. Short answers first, detail on request.",
    "taboos": "No phone calls when a message would do.",
}

#: Free-form notes handed to the AI verbatim.
DEMO_MANUAL = """## How to work with me

- I think in writing. Send me a short summary first, then the detail if I ask.
- I am detail-oriented to a fault; if something looks wrong, say so directly.
- Mornings are for deep work. I run at 6:30 and start work at 8:30.
- I would rather ship something small and iterate than plan for a month.

## Context worth remembering

- I have been weighing a team switch, but I like the people I work with.
- Saving toward $20k by year end, so I am cautious about spending.
- Want a dog. Worried my schedule is not fair to one yet.
"""

#: Claims as (tier, text, source memory titles, confidence). Tiers are core / recent / map.
DEMO_CLAIMS: list[tuple[str, str, list[str], float]] = [
    ("core", "Works as a software engineer and treats shipping early as the default.",
     ["About me"], 0.95),
    ("core", "Introverted, but talkative with people he is close to.", ["About me"], 0.9),
    ("core", "Detail-oriented to the point of perfectionism, and says so himself.",
     ["About me"], 0.9),
    ("core", "Keeps a firm daily structure: runs at 6:30, works 8:30 to 18:00, reads before bed.",
     ["Daily routine"], 0.95),
    ("core", "Prefers written, asynchronous communication over meetings and phone calls.",
     ["People at work", "Thoughts and wishes"], 0.85),
    ("recent", "Is midway through an API refactor and owes a design doc on the auth flow.",
     ["Current to-do list"], 0.9),
    ("recent", "Is learning Rust, currently on chapter 4 of the book.",
     ["Current to-do list"], 0.9),
    ("recent", "Has been considering a team switch while still liking the current team.",
     ["Thoughts and wishes"], 0.8),
    ("recent", "Is saving toward $20k by year end.", ["Thoughts and wishes"], 0.85),
    ("map", "Work: reports to Sarah Chen; collaborates with Dan, Priya and Tom.",
     ["People at work"], 0.95),
    ("map", "Family: calls his mother every Sunday; sister Emma is in med school.",
     ["Family and friends"], 0.9),
    ("map", "Interests: running, reading, cooking Italian food, street photography.",
     ["Hobbies and interests"], 0.95),
    ("map", "Wants someday: visit Japan, learn guitar, launch a side project.",
     ["Thoughts and wishes"], 0.85),
]


def _memory_ids_by_title(database) -> dict[str, str]:
    return {
        memory.title: memory.id
        for memory in database.list_memories(limit=200)
        if getattr(memory, "title", None) and getattr(memory, "id", None)
    }


def seed_demo_profile(profile_service, database) -> dict:
    """
    Fill in the demo profile.

    A no-op once fields exist, so a restart against the surviving tmpfs does not duplicate claims.
    """
    if profile_service.get_fields().get("values"):
        return {"skipped": "profile already present"}

    for key, value in DEMO_FIELDS.items():
        profile_service._set_field(key, value, "distilled")

    profile_service.update_manual(DEMO_MANUAL)

    memory_ids = _memory_ids_by_title(database)
    version_id = profile_service.get_active_version_id()
    now = datetime.now()
    written = 0

    with database.get_connection() as conn:
        for index, (tier, text, titles, confidence) in enumerate(DEMO_CLAIMS):
            sources = [memory_ids[title] for title in titles if title in memory_ids]
            if not sources:
                continue
            # Spread verification timestamps so the audit rotation has a natural oldest-first order
            # instead of every claim looking equally stale.
            verified_at = (now - timedelta(days=index % 7)).isoformat()
            conn.execute(
                "INSERT INTO profile_claims "
                "(version_id, tier, text, sources, source_kind, confidence, status, "
                " created_at, updated_at, verified_at) "
                "VALUES (?, ?, ?, ?, 'memory', ?, 'active', ?, ?, ?)",
                (version_id, tier, text, json.dumps(sources), confidence,
                 now.isoformat(), now.isoformat(), verified_at),
            )
            written += 1

    return {"fields": len(DEMO_FIELDS), "claims": written, "version_id": version_id}


def maybe_seed_demo_profile(profile_service, database) -> Optional[dict]:
    """Seed the profile, reporting failures without letting them take the service down."""
    try:
        return seed_demo_profile(profile_service, database)
    except Exception as exc:  # noqa: BLE001 - the demo still works without a profile
        print(f"[demo] Failed to seed profile: {exc}")
        return None
