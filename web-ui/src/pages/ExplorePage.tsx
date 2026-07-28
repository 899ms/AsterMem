/**
 * Background: The Explore page connects to three streaming endpoints under /api/explore.
 * The backend isn't simple Q&A but "roaming": it performs trunk-level hybrid retrieval,
 * and when material is insufficient the AI adds keywords for up to two more search rounds,
 * then streams a narrative interspersed with <trunk/> tags embedding matched original text.
 * Design intent: UI must reproduce every stage of this pipeline—search process visible (collapsible),
 * narrative text and source cards interleaved in stream arrival order, cards can be drilled into,
 * ends with follow-up directions and action items, and the entire roam can be organized into a new memory.
 * Key constraint: Only one active stream at a time—new query/drill/unmount must abort the old stream;
 * follow-ups chain previous questions as context to the backend; organizing into memory only feeds
 * the trunks collected in the current round.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  IconSparkles,
  IconArrowDownRight,
  IconDeviceFloppy,
  IconChevronDown,
  IconChevronRight,
  IconExternalLink,
  IconPlus,
  IconArrowRight,
  IconChecklist,
  IconRefresh,
  IconNotes,
  IconTrash,
} from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { Markdown } from "../components/Markdown";
import { api, sseStream, reportError } from "../api";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import { useAuthSnapshot } from "../authState";

const SESSIONS_KEY = "astermem_roam_sessions";
const SESSIONS_MAX = 5;

interface RoamTrunk {
  id: string;
  document_id?: string;
  title?: string;
  content?: string;
  score?: number;
  tags?: string[];
  highlights?: string[];
}

interface ActionItem {
  content: string;
  source?: string;
  trunk_id?: string;
  document_id?: string;
}

/** Narrative body split into segments by stream arrival order: text paragraphs and source cards interleaved */
type Segment = { kind: "text"; text: string } | { kind: "trunk"; trunk: RoamTrunk };

interface RoamBlock {
  id: number;
  kind: "search" | "drill";
  query: string;
  status: string;
  thinking: string[];
  found: { id: string; title: string }[];
  focus?: { id: string; title: string; content: string };
  segments: Segment[];
  /** All matched source text from this round, used as the sole material source when organizing into memory */
  pool: RoamTrunk[];
  suggestions: string[];
  actions: ActionItem[];
  streaming: boolean;
  error?: string;
  notice?: string;
  draft?: string;
  drafting?: boolean;
  saving?: boolean;
}

let blockSeq = 0;

/**
 * Session persistence: Each roam result (including AI narrative and source cards) is stored in
 * localStorage keyed by session id, with the URL carrying ?s=<id>—refreshing restores the
 * exact state without re-calling the AI (avoiding extra cost).
 * Key constraint: Trunk source text can be large, so only the most recent SESSIONS_MAX sessions
 * are kept; when storage quota is exceeded, degrade to keeping only the current session,
 * and if that also fails, give up persistence (doesn't affect usage).
 */
interface StoredSession {
  id: string;
  title: string;
  updated: number;
  blocks: RoamBlock[];
}

function readSessions(): Record<string, StoredSession> {
  try {
    const parsed = JSON.parse(localStorage.getItem(SESSIONS_KEY) || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveSession(session: StoredSession) {
  const map = readSessions();
  map[session.id] = session;
  const kept = Object.values(map)
    .sort((a, b) => b.updated - a.updated)
    .slice(0, SESSIONS_MAX);
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(Object.fromEntries(kept.map((s) => [s.id, s]))));
  } catch {
    try {
      localStorage.setItem(SESSIONS_KEY, JSON.stringify({ [session.id]: session }));
    } catch {
      // Private mode or quota exceeded, give up persistence
    }
  }
}

function deleteStoredSession(id: string) {
  const map = readSessions();
  delete map[id];
  try {
    localStorage.setItem(SESSIONS_KEY, JSON.stringify(map));
  } catch {
    // Ignore write failure
  }
}

function listSessions(): StoredSession[] {
  return Object.values(readSessions())
    .filter((s) => s && Array.isArray(s.blocks) && s.blocks.length > 0)
    .sort((a, b) => b.updated - a.updated);
}

/**
 * Background: Source cards in the narrative carry highlight keywords that need to be marked in the text.
 * Design intent: Split by capture groups and render as React nodes segment by segment—no HTML concatenation,
 * naturally immune to injection.
 * Key constraint: Filter out single-character keywords, otherwise the entire paragraph gets highlighted.
 */
function highlightText(text: string, terms: string[] = []): ReactNode {
  const cleaned = terms.map((x) => x.trim()).filter((x) => x.length >= 2);
  if (cleaned.length === 0) return text;
  const escaped = cleaned.map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const parts = text.split(new RegExp(`(${escaped.join("|")})`, "gi"));
  const lowered = new Set(cleaned.map((x) => x.toLowerCase()));
  return parts.map((part, i) =>
    lowered.has(part.toLowerCase()) ? (
      <mark key={i} className="roam-hl">
        {part}
      </mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

/**
 * Backend progress event semantic codes → dictionary keys.
 * Background: The search process is displayed to users, but the backend shouldn't care about UI language.
 * Design intent: Backend only sends codes and variables; text is assembled here in the current language;
 * unknown codes fall back to the backend's built-in fallback text, never leaving blanks.
 */
const PROGRESS_TEXT: Record<string, string> = {
  with_context: "Reading the follow-up together with: {context}",
  searching: "Searching",
  searching_more: "Searching deeper: {keyword}",
  assessing: "Checking whether the material is enough",
  reading: "Reading what was found, judging if it answers the question",
  expanding: "Something is missing, searching again for: {keyword}",
  enough: "There is enough material, starting to organize",
  capped: "Plenty of material already, stopping the expansion",
  narrating: "Working through {count} passages",
  drill_intent: "Working out why this passage caught your eye",
  drill_guess: "You are probably after: {keyword}",
  drill_fallback: "Searching by the passage itself",
  searching_related: "Searching for related passages",
  found_n: "Found {count} related passages",
  organizing: "Organizing",
  no_results: "Nothing related found",
  no_related: "No further connections found",
  trunk_missing: "That passage no longer exists",
  llm_error: "The model call failed: {detail}",
};

/** Organized result must be wrapped in <content>; during streaming the closing tag hasn't arrived yet, so open-ended match is allowed. */
function extractDraft(raw: string): string {
  const match = /<content>([\s\S]*?)(?:<\/content>|$)/i.exec(raw);
  const body = match ? match[1] : raw;
  return body.replace(/<\/?content>/gi, "").trim();
}

function draftTitle(draft: string, fallback: string): string {
  const heading = /^#{1,3}\s+(.+)$/m.exec(draft);
  return (heading ? heading[1] : fallback).trim().slice(0, 80);
}

export function ExplorePage() {
  const { t } = useI18n();
  const { demoMode } = useAuthSnapshot();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const sessionIdRef = useRef<string | null>(searchParams.get("s"));
  const [query, setQuery] = useState("");
  const [followUp, setFollowUp] = useState("");
  const [blocks, setBlocks] = useState<RoamBlock[]>(() => {
    // On mount, if URL carries a session id and a local archive exists, restore the previous roam result
    const id = sessionIdRef.current;
    const saved = id ? readSessions()[id] : undefined;
    if (!saved?.blocks?.length) return [];
    blockSeq = Math.max(blockSeq, ...saved.blocks.map((b) => b.id));
    return saved.blocks;
  });
  const [sessions, setSessions] = useState<StoredSession[]>(() => listSessions());
  const [openLogs, setOpenLogs] = useState<Record<number, boolean>>({});
  /** Selection popover: after selecting text in the narrative area, a "Go deeper" button appears */
  const [selPop, setSelPop] = useState<{ x: number; y: number; text: string; trunkId: string | null } | null>(null);
  const selPopRef = useRef<HTMLDivElement | null>(null);
  const abortRef = useRef<(() => void) | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => () => abortRef.current?.(), []);

  /**
   * Text selection listener: when 2~200 characters are selected within the narrative area
   * (body text / source cards / starting summary), an action button appears below the selection;
   * if the selection is inside a card, record the trunk id for drilling.
   * Uses fixed positioning with viewport coordinates; collapses on scroll to avoid follow-along calculation.
   */
  useEffect(() => {
    const onMouseUp = (e: MouseEvent) => {
      if (selPopRef.current?.contains(e.target as Node)) return;
      // Selection may not be finalized at mouseup (double-click/drag-select), defer one tick before reading
      setTimeout(() => {
        const selection = window.getSelection();
        const text = selection?.toString().trim() ?? "";
        if (!selection || selection.rangeCount === 0 || text.length < 2 || text.length > 200) {
          setSelPop(null);
          return;
        }
        const anchor = selection.anchorNode;
        const el = anchor instanceof Element ? anchor : anchor?.parentElement;
        const area = el?.closest(".roam-narration, .roam-focus");
        if (!area) {
          setSelPop(null);
          return;
        }
        const card = el?.closest<HTMLElement>("[data-trunk-id]");
        const rect = selection.getRangeAt(0).getBoundingClientRect();
        setSelPop({
          x: Math.max(8, rect.left + rect.width / 2),
          y: rect.bottom + 6,
          text,
          trunkId: card?.dataset.trunkId || null,
        });
      }, 0);
    };
    const onMouseDown = (e: MouseEvent) => {
      if (!selPopRef.current?.contains(e.target as Node)) setSelPop(null);
    };
    document.addEventListener("mouseup", onMouseUp);
    document.addEventListener("mousedown", onMouseDown);
    return () => {
      document.removeEventListener("mouseup", onMouseUp);
      document.removeEventListener("mousedown", onMouseDown);
    };
  }, []);

  const blockCount = blocks.length;
  useEffect(() => {
    if (blockCount === 0) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [blockCount]);

  /** Write the entire session to localStorage after each streaming round ends (don't write during streaming to avoid partial saves) */
  useEffect(() => {
    const id = sessionIdRef.current;
    if (!id || blocks.length === 0) return;
    if (blocks.some((b) => b.streaming || b.drafting || b.saving)) return;
    saveSession({
      id,
      title: blocks[0].query,
      updated: Date.now(),
      blocks: blocks.map((b) => ({ ...b, streaming: false, drafting: false, saving: false, status: "" })),
    });
    setSessions(listSessions());
  }, [blocks]);

  /**
   * URL's ?s= is the sole entry point for sessions: clicking history sessions or browser back/forward
   * only changes the URL—this listener uniformly restores from the archive, never re-calls the AI.
   */
  useEffect(() => {
    const s = searchParams.get("s");
    if (s === sessionIdRef.current) return;
    abortRef.current?.();
    abortRef.current = null;
    sessionIdRef.current = s;
    setOpenLogs({});
    const saved = s ? readSessions()[s] : undefined;
    if (saved?.blocks?.length) {
      blockSeq = Math.max(blockSeq, ...saved.blocks.map((b) => b.id));
      setBlocks(saved.blocks);
    } else {
      setBlocks([]);
    }
  }, [searchParams]);

  const patch = useCallback(
    (id: number, next: Partial<RoamBlock> | ((b: RoamBlock) => Partial<RoamBlock>)) => {
      setBlocks((prev) =>
        prev.map((b) => (b.id === id ? { ...b, ...(typeof next === "function" ? next(b) : next) } : b)),
      );
    },
    [],
  );


  /**
   * Background: Backend events are a discriminated union; each type maps to a different area of the block.
   * Design intent: Text events merge into the last text segment, trunk events insert a new card segment,
   * so narrative and source text interleaving matches the AI output order exactly.
   */
  const handleEvent = useCallback(
    (id: number, payload: unknown) => {
      if (!payload || typeof payload !== "object") return;
      const evt = payload as Record<string, unknown>;
      const type = typeof evt.type === "string" ? evt.type : "";
      const code = typeof evt.code === "string" ? evt.code : "";
      const vars = (evt.vars && typeof evt.vars === "object" ? evt.vars : {}) as Record<string, string | number>;
      const dictKey = PROGRESS_TEXT[code];
      const localized = dictKey ? t(dictKey, vars) : "";
      const message = localized || (typeof evt.message === "string" ? evt.message : "");
      const content = localized || (typeof evt.content === "string" ? evt.content : "");

      switch (type) {
        case "status":
          patch(id, { status: message });
          break;
        case "thinking":
          if (content) patch(id, (b) => ({ thinking: [...b.thinking, content] }));
          break;
        case "search_results":
          if (Array.isArray(evt.data)) {
            const found = evt.data as { id: string; title: string }[];
            patch(id, (b) => ({ found: [...b.found, ...found] }));
          }
          break;
        case "focus":
          patch(id, { focus: evt.data as RoamBlock["focus"] });
          break;
        case "start":
          patch(id, { status: "" });
          setOpenLogs((prev) => ({ ...prev, [id]: false }));
          break;
        case "text": {
          if (!content) break;
          patch(id, (b) => {
            const segments = [...b.segments];
            const last = segments[segments.length - 1];
            if (last && last.kind === "text") segments[segments.length - 1] = { kind: "text", text: last.text + content };
            else segments.push({ kind: "text", text: content });
            return { segments };
          });
          break;
        }
        case "trunk": {
          const trunk = evt.data as RoamTrunk | undefined;
          if (!trunk?.id) break;
          patch(id, (b) => ({
            segments: [...b.segments, { kind: "trunk", trunk }],
            pool: b.pool.some((x) => x.id === trunk.id) ? b.pool : [...b.pool, trunk],
          }));
          break;
        }
        case "actions":
          if (Array.isArray(evt.data)) patch(id, { actions: evt.data as ActionItem[] });
          break;
        case "suggestions":
          if (Array.isArray(evt.data)) patch(id, { suggestions: evt.data as string[] });
          break;
        case "info":
          patch(id, { notice: message });
          break;
        case "error":
          patch(id, { error: message || t("Roaming failed") });
          break;
        case "done":
          patch(id, { streaming: false, status: "" });
          break;
        default:
          break;
      }
    },
    [patch, t],
  );

  const startBlock = useCallback(
    (init: { kind: "search" | "drill"; query: string }, path: string, body: unknown) => {
      abortRef.current?.();
      // Assign session id on first question and write to URL, so refresh/back navigation can match the archive
      if (!sessionIdRef.current) {
        sessionIdRef.current = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
        setSearchParams({ s: sessionIdRef.current });
      }
      const id = ++blockSeq;
      setBlocks((prev) => [
        ...prev,
        {
          id,
          kind: init.kind,
          query: init.query,
          status: t("Searching"),
          thinking: [],
          found: [],
          segments: [],
          pool: [],
          suggestions: [],
          actions: [],
          streaming: true,
        },
      ]);
      setOpenLogs((prev) => ({ ...prev, [id]: true }));
      abortRef.current = sseStream(path, body, {
        onData: (payload) => handleEvent(id, payload),
        onDone: () => patch(id, { streaming: false, status: "" }),
        onError: (err) => {
          console.error("[AsterMem] roam stream failed", err);
          patch(id, { streaming: false, status: "", error: err.message || t("Roaming failed") });
        },
      });
    },
    [handleEvent, patch, setSearchParams, t],
  );

  /** Follow-up questions carry previous questions so the backend can rewrite search terms */
  const buildContext = useCallback(
    () =>
      blocks
        .filter((b) => b.kind === "search")
        .map((b) => b.query)
        .join(" → "),
    [blocks],
  );

  const roam = (raw: string, withContext: boolean) => {
    const q = raw.trim();
    if (!q) return;
    const context = withContext ? buildContext() : "";
    startBlock({ kind: "search", query: q }, "/api/explore/search", context ? { query: q, context } : { query: q });
  };

  const drill = (trunk: RoamTrunk, question = "") => {
    startBlock(
      { kind: "drill", query: question || trunk.title || t("Go deeper") },
      "/api/explore/drill",
      { trunk_id: trunk.id, question },
    );
  };

  /** Selection popover action: selection inside a card drills into that card; other selections continue roaming as follow-ups */
  const drillSelection = () => {
    if (!selPop) return;
    const { text, trunkId } = selPop;
    setSelPop(null);
    window.getSelection()?.removeAllRanges();
    if (trunkId) drill({ id: trunkId }, text);
    else roam(text, true);
  };

  const resetConversation = () => {
    abortRef.current?.();
    abortRef.current = null;
    sessionIdRef.current = null;
    setSearchParams({});
    setBlocks([]);
    setOpenLogs({});
    setFollowUp("");
    setQuery("");
  };

  /**
   * Background: Source texts unearthed during roaming are scattered; the user wants to consolidate
   * this round's conclusions.
   * Design intent: Send this round's trunk source texts back to the backend, which strictly organizes
   * them into a Markdown draft—shown on page for human review before writing to the database.
   * Key constraint: Organization is streamed, using sseStream instead of the regular api().
   */
  const organize = (block: RoamBlock, extraRequirement = "") => {
    if (block.pool.length === 0) return;
    abortRef.current?.();
    patch(block.id, { drafting: true, draft: "", error: undefined });
    let raw = "";
    abortRef.current = sseStream(
      "/api/explore/generate-memory",
      { trunks: block.pool, query: block.query, extra_requirement: extraRequirement },
      {
        onData: (payload) => {
          const evt = payload as Record<string, unknown> | null;
          if (!evt || typeof evt !== "object") return;
          if (evt.type === "text" && typeof evt.content === "string") {
            raw += evt.content;
            patch(block.id, { draft: extractDraft(raw) });
          } else if (evt.type === "error" && typeof evt.message === "string") {
            patch(block.id, { drafting: false, error: evt.message });
          }
        },
        onDone: () => patch(block.id, { drafting: false, draft: extractDraft(raw) }),
        onError: (err) => {
          console.error("[AsterMem] organize failed", err);
          patch(block.id, { drafting: false, error: err.message || t("Roaming failed") });
        },
      },
    );
  };

  const saveDraft = async (block: RoamBlock) => {
    const draft = block.draft?.trim();
    if (!draft) return;
    patch(block.id, { saving: true });
    try {
      const created = await api<{ id?: string }>("POST", "/api/memories", {
        title: draftTitle(draft, block.query),
        content: draft,
        tags: ["explore"],
        priority: 0,
      });
      emitToast("success", t("Saved as memory"));
      if (created?.id) navigate(`/view/${created.id}`);
    } catch (err) {
      reportError(err, t("Unable to save as memory"));
    } finally {
      patch(block.id, { saving: false });
    }
  };

  const busy = useMemo(() => blocks.some((b) => b.streaming || b.drafting), [blocks]);

  const renderTrunkCard = (trunk: RoamTrunk, key: string | number) => (
    <article key={key} className="roam-card" data-trunk-id={trunk.id}>
      <header className="roam-card-head">
        <span className="roam-card-title">{trunk.title || t("Untitled")}</span>
        {typeof trunk.score === "number" && <span className="roam-card-score">{trunk.score.toFixed(2)}</span>}
      </header>
      <p className="roam-card-body">{highlightText(trunk.content || "", trunk.highlights)}</p>
      <footer className="roam-card-foot">
        <button type="button" className="btn small" disabled={busy} onClick={() => drill(trunk)}>
          <IconArrowDownRight aria-hidden="true" />
          {t("Go deeper")}
        </button>
        {trunk.document_id && (
          <button type="button" className="btn small" onClick={() => navigate(`/view/${trunk.document_id}`)}>
            <IconExternalLink aria-hidden="true" />
            {t("Open full memory")}
          </button>
        )}
        {(trunk.tags || []).slice(0, 4).map((tag) => (
          <span key={tag} className="chip">
            {tag}
          </span>
        ))}
      </footer>
    </article>
  );

  const renderBlock = (block: RoamBlock) => {
    const logOpen = openLogs[block.id] ?? false;
    const hasLog = block.thinking.length > 0 || block.found.length > 0 || Boolean(block.status);
    return (
      <section key={block.id} className={`roam-block ${block.streaming ? "streaming" : ""}`}>
        <header className="roam-question">
          <span className="roam-question-kind">{block.kind === "drill" ? t("Go deeper") : t("Roam")}</span>
          <h2>{block.query}</h2>
        </header>

        {block.focus && (
          <div className="roam-focus">
            <span className="kicker">{t("Starting from")}</span>
            <strong>{block.focus.title}</strong>
            <p>{block.focus.content}</p>
          </div>
        )}

        {hasLog && (
          <div className="roam-log">
            <button type="button" className="roam-log-toggle" onClick={() => setOpenLogs((p) => ({ ...p, [block.id]: !logOpen }))}>
              {logOpen ? <IconChevronDown aria-hidden="true" /> : <IconChevronRight aria-hidden="true" />}
              {t("Search process")}
              {block.status && <em>{block.status}</em>}
            </button>
            {logOpen && (
              <div className="roam-log-body">
                {block.thinking.map((line, i) => (
                  <p key={i} className="roam-log-line">
                    {line}
                  </p>
                ))}
                {block.found.length > 0 && (
                  <p className="roam-log-found">
                    {t("Matched")}: {block.found.map((f) => f.title).join(" · ")}
                  </p>
                )}
              </div>
            )}
          </div>
        )}

        {block.error && <p className="text-danger mono-sm">{block.error}</p>}
        {block.notice && <p className="muted mono-sm">{block.notice}</p>}

        <div className="roam-narration">
          {block.segments.map((seg, i) =>
            seg.kind === "text" ? (
              <div key={i} className="roam-text">
                <Markdown source={seg.text} />
              </div>
            ) : (
              renderTrunkCard(seg.trunk, i)
            ),
          )}
          {block.streaming && <span className="stream-cursor" aria-hidden="true" />}
        </div>

        {block.actions.length > 0 && (
          <div className="roam-actions">
            <span className="kicker">
              <IconChecklist aria-hidden="true" />
              {t("Action items found")}
            </span>
            <ul>
              {block.actions.map((a, i) => (
                <li key={i}>
                  <span>{a.content}</span>
                  {a.source && <em>{a.source}</em>}
                </li>
              ))}
            </ul>
          </div>
        )}

        {block.suggestions.length > 0 && (
          <div className="roam-suggestions">
            <span className="kicker">{t("Where to go next")}</span>
            <div className="chip-row">
              {block.suggestions.map((s) => (
                <button key={s} type="button" className="chip clickable" disabled={busy} onClick={() => roam(s, true)}>
                  <IconArrowRight aria-hidden="true" />
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {!block.streaming && block.pool.length > 0 && (
          <div className="roam-organize">
            <button type="button" className="btn acid" disabled={busy} onClick={() => organize(block)}>
              <IconNotes aria-hidden="true" />
              {block.drafting ? t("Organizing") : t("Turn this into a memory")}
            </button>
            <span className="mono-sm muted">{t("{n} passages collected", { n: block.pool.length })}</span>
          </div>
        )}

        {(block.draft || block.drafting) && (
          <div className="roam-draft">
            <span className="kicker">{t("Draft memory")}</span>
            <div className="roam-draft-body">
              {block.draft ? <Markdown source={block.draft} /> : <p className="muted mono-sm">{t("Organizing")}</p>}
            </div>
            {!block.drafting && block.draft && (
              <div className="roam-draft-foot">
                <button type="button" className="btn primary" disabled={block.saving} onClick={() => saveDraft(block)}>
                  <IconDeviceFloppy aria-hidden="true" />
                  {block.saving ? t("Saving") : t("Save as memory")}
                </button>
                <button type="button" className="btn small" disabled={busy} onClick={() => organize(block)}>
                  <IconRefresh aria-hidden="true" />
                  {t("Regenerate")}
                </button>
              </div>
            )}
          </div>
        )}
      </section>
    );
  };

  return (
    <Layout title={t("Explore")} fill>
      <div className="roam-shell">
        <div className="roam-scroll" ref={scrollRef} onScroll={() => setSelPop(null)}>
          {blocks.length === 0 ? (
            <div className="roam-welcome">
              <span className="kicker">{t("Memory roaming")}</span>
              <h1>{t("Ask in your own words")}</h1>
              <p>{demoMode
                ? t("This page needs a language model, and the demo runs without one so it stays free to host. Run your own instance to use it.")
                : t("AsterMem searches your memory base, widens the search on its own when the material is thin, then walks you through the original passages.")}</p>
              <div className="roam-welcome-input">
                <input
                  className="input"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                      e.preventDefault();
                      roam(query, false);
                      setQuery("");
                    }
                  }}
                  placeholder={t("What do you want to dig up?")}
                />
                <button
                  type="button"
                  className="btn primary"
                  disabled={!query.trim() || busy}
                  onClick={() => {
                    roam(query, false);
                    setQuery("");
                  }}
                >
                  <IconSparkles aria-hidden="true" />
                  {t("Roam")}
                </button>
              </div>
              {sessions.length > 0 && (
                <div className="roam-history">
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <span className="kicker">{t("Recently roamed")}</span>
                    <button
                      type="button"
                      className="roam-history-clear"
                      onClick={() => {
                        setSessions([]);
                        try { localStorage.removeItem(SESSIONS_KEY); } catch { /* May throw in private mode, ignore */ }
                      }}
                      title={t("Clear all")}
                    >
                      <IconTrash size={13} aria-hidden="true" />
                      {t("Clear all")}
                    </button>
                  </div>
                  <table className="roam-session-table" style={{ marginTop: 6 }}>
                    <tbody>
                      {sessions.map((s) => (
                        <tr key={s.id} onClick={() => setSearchParams({ s: s.id })}>
                          <td className="roam-session-title">{s.title}</td>
                          <td className="roam-session-meta">{new Date(s.updated).toLocaleString()}</td>
                          <td className="roam-session-meta">
                            {t("{n} passages collected", { n: s.blocks.reduce((sum, b) => sum + b.pool.length, 0) })}
                          </td>
                          <td style={{ width: 30, textAlign: "right" }}>
                            <button
                              type="button"
                              className="roam-session-delete"
                              title={t("Delete")}
                              onClick={(e) => {
                                e.stopPropagation();
                                deleteStoredSession(s.id);
                                setSessions(listSessions());
                              }}
                            >
                              <IconTrash size={13} aria-hidden="true" />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ) : (
            <div className="roam-thread">{blocks.map(renderBlock)}</div>
          )}
        </div>

        {selPop && (
          <div
            ref={selPopRef}
            style={{
              position: "fixed",
              left: selPop.x,
              top: selPop.y,
              transform: "translateX(-50%)",
              zIndex: 60,
              background: "var(--paper)",
              border: "1.5px solid var(--ink)",
              boxShadow: "3px 3px 0 var(--ink)",
              padding: 4,
            }}
          >
            <button type="button" className="btn small" disabled={busy} onClick={drillSelection}>
              <IconArrowDownRight aria-hidden="true" />
              {selPop.trunkId ? t("Go deeper") : t("Roam")}
            </button>
          </div>
        )}

        {blocks.length > 0 && (
          <div className="roam-bottom">
            <button type="button" className="btn small" onClick={resetConversation}>
              <IconPlus aria-hidden="true" />
              {t("New roam")}
            </button>
            <input
              className="input"
              value={followUp}
              onChange={(e) => setFollowUp(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  e.preventDefault();
                  roam(followUp, true);
                  setFollowUp("");
                }
              }}
              placeholder={t("Keep asking, the earlier questions stay in context")}
            />
            <button
              type="button"
              className="btn primary"
              disabled={!followUp.trim() || busy}
              onClick={() => {
                roam(followUp, true);
                setFollowUp("");
              }}
            >
              <IconArrowRight aria-hidden="true" />
              {t("Send")}
            </button>
          </div>
        )}
      </div>
    </Layout>
  );
}
