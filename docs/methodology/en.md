# How AsterMem Works

Most "AI memory" products stuff your words into a black box — you never know what it remembered, why, or when it will surface. AsterMem takes a different path: **your memory is your asset first, and AI context second.** This document explains every design decision behind the framework.

## 1. The Original Text Is the Only Truth

Every memory is stored as plain Markdown. Everything AI generates — summaries, tags, your profile — is a **derivative** that can be rebuilt from the source at any time.

This isn't purism. It guards against a fatal degradation path: **paraphrases of paraphrases**. A summary is lossy compression; if the system keeps summarizing its own summaries, each pass drifts further from what you actually wrote — like photocopying a photocopy until the letters blur. So AsterMem enforces a hard constraint: **any AI call that produces or rewrites a conclusion must receive the original text as input.** Intermediate artifacts are reference only.

You can edit the MD files with any editor and the index syncs automatically. Your data is never locked in a database — exporting is just copying a folder.

## 2. Two-Level Retrieval: Documents and Passages

In a long piece of memory material, usually only a paragraph or two is relevant to the question at hand. AsterMem automatically splits each memory into **passages (trunks)**, each with its own summary, tags, and embedding. At query time:

- **Keyword search** (Whoosh full-text index) handles exact hits: names, projects, jargon
- **Semantic search** (vectors) handles fuzzy intent: "what did I say to watch out for?"
- **Hybrid mode** merges both with RRF (Reciprocal Rank Fusion), dynamically weighted by query characteristics

The AI receives passage-precise results, not whole documents. Context windows are scarce — 500 relevant words beat 5,000 off-topic ones.

## 3. Retrieval Is Navigation, Not Q&A

Every search returns more than results — it returns **next-step guidance**: IDs of semantically nearby memories that weren't shown, tags found in the hits, documents worth expanding. The AI doesn't have to guess its next query; it follows the intrinsic links of your memory graph.

This mimics how humans retrace memory material: you don't stop at the first search hit — you follow "that thing this source mentioned" onward.

## 4. Profile: "Who This Person Is" in One Call

Making the AI learn who you are from scratch every session is the fundamental waste of stateless chat. AsterMem's profile layer distills your entire memory store into dense context that an agent retrieves with a single `get_profile` call.

The profile has three source layers:

1. **Basic info** — structured fields like name, occupation, timezone. AI fills them in automatically from your memories; you can change anything, and **once you edit a field, AI never touches it again**. Every change archives the old value into version history.
2. **Your own introduction** — Markdown you wrote yourself, passed to the AI verbatim. No code path in the system can modify it.
3. **What AI knows** — observations distilled from your memories, tiered into long-term traits, recent activity, and a topic overview.

## 5. Every AI-Written Sentence Is Traceable

Every conclusion the AI writes into your profile must cite source memory IDs. **Untraceable claims are dropped at the parsing layer** — not reviewed and deleted, but never admitted in the first place.

Generation and review are two independent AI calls: first distill candidate conclusions, then an auditor verifies each one against the original text — "does the source actually support this claim?" A daily retrospection also rotates through existing conclusions: deleted sources get flagged as "source invalid," long-unverified ones as "possibly outdated," and everything lands in a pending list for your judgment. **The system never silently deletes, and never silently believes.**

## 6. Dreaming: Low-Frequency Deep Consolidation

Daily distillation only sees each day's increment; it can't spot patterns spanning months. AsterMem borrows the "dreaming" idea (offline consolidation) proposed by Anthropic researchers: periodically re-examine the whole memory store — dedupe, merge, resolve contradictions, induce long-term themes.

The key design: **deep consolidation never takes effect directly.** It produces a candidate version; you review the diff (what was added, merged, removed) and activate or discard it manually. Consolidation is event-driven — enough new content accumulated, pending issues piling up, a bulk import finished — not a rigid cron job. People don't deep-clean on a fixed schedule; they clean when things look messy.

## 7. Visible, Editable, Switchable

A profile is the AI's summary of you — possibly wrong, possibly one-sided. So the product must guarantee three things:

- **Always visible** — "what agents see" is displayed verbatim; there are no hidden prompts
- **Always editable** — every conclusion can be kept or deleted, every field rewritten
- **Always switchable** — the profile is off by default; when off, it makes zero AI calls and costs nothing

Trust isn't built on promises. It's built on "you can open it and check anytime, and fix it with one click."

## 8. Built for Agents

AsterMem isn't a traditional document tool — it's a **memory backend for agents**:

- A complete tool API (search, read/write, profile) with Bearer token auth and read/write/destructive permission tiers
- A bundled Skill package: Cursor, Claude Code, and other agents install and go
- `quick_match` returns time context + the most relevant passages + next-step guidance in one call, designed for session openings

You provide the memory material. AI remembers who you are. That's AsterMem.
