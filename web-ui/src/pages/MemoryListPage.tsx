/**
 * Background: The memory library is the main view, serving both browsing (GET /api/memories with filtering/pagination)
 * and searching (POST /api/search keyword/semantic/hybrid) modes.
 * Design intent: When a search term exists, switch to search results (with similarity scores);
 * clearing reverts to list mode; tag/status filters are in the left rail, mutually exclusive with
 * the memories endpoint's query parameter combination.
 * Key constraint: Enter-to-search must check e.nativeEvent.isComposing (CJK IME);
 * backend fields may be missing, all accessed optionally.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { IconSearch, IconPlus, IconChevronRight, IconFilter } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { Select } from "../components/Select";
import { EmptyState, LoadingLine } from "../components/EmptyState";
import { Pagination } from "../components/Pagination";
import { api, reportError } from "../api";
import { flattenResults } from "../normalize";
import { useI18n } from "../i18n";
import type { MemoryListResponse, SearchResultItem, TagStat } from "../types";

const PAGE_SIZE = 20;
const SEARCH_MODES = ["keyword", "semantic", "hybrid"] as const;
// Mode dropdown labels: keys must be English originals to be matched by the t() dictionary.
const MODE_LABELS: Record<(typeof SEARCH_MODES)[number], string> = {
  keyword: "Keyword",
  semantic: "Semantic",
  hybrid: "Hybrid",
};
const STATUS_OPTIONS = ["", "active", "archived"] as const;

interface TagNode {
  /** Current level name, e.g. "habits" */
  name: string;
  /** Full path, e.g. "personal/habits", the value sent to the backend for filtering */
  path: string;
  /** Total count including all descendants */
  total: number;
  children: TagNode[];
}

/**
 * Background: Tags are paths like `A/B/C`; the backend only provides a flat list + counts.
 * Rendering flat would scatter branches (e.g. "life/daily/personal" and "daily/personal" side by side).
 * Design intent: Frontend merges them into a tree by `/` in real-time; parent nodes appear even
 * without their own memories (virtual nodes); counts accumulate upward because backend tag filtering
 * is prefix-matching (selecting "personal" also returns "personal/habits").
 * Key constraint: Only depends on flat data from /api/tags/stats; whenever tags change the tree updates—no extra endpoint needed.
 */
function buildTagTree(stats: TagStat[]): TagNode[] {
  const roots: TagNode[] = [];
  const index = new Map<string, TagNode>();

  const ensure = (path: string, name: string, parent: TagNode | null): TagNode => {
    const existing = index.get(path);
    if (existing) return existing;
    const node: TagNode = { name, path, total: 0, children: [] };
    index.set(path, node);
    (parent ? parent.children : roots).push(node);
    return node;
  };

  for (const stat of stats) {
    const full = (stat.tag || stat.name || "").trim();
    if (!full) continue;
    const count = typeof stat.count === "number" ? stat.count : 0;
    const segments = full.split("/").map((s) => s.trim()).filter(Boolean);
    let parent: TagNode | null = null;
    let path = "";
    for (const segment of segments) {
      path = path ? `${path}/${segment}` : segment;
      const node = ensure(path, segment, parent);
      node.total += count;
      parent = node;
    }
  }

  const sort = (nodes: TagNode[]) => {
    nodes.sort((a, b) => b.total - a.total || a.name.localeCompare(b.name));
    nodes.forEach((node) => sort(node.children));
  };
  sort(roots);
  return roots;
}

function TagTree({ nodes, depth, selected, expanded, onToggle, onSelect }: {
  nodes: TagNode[];
  depth: number;
  selected: string;
  expanded: Set<string>;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
}) {
  return (
    <>
      {nodes.map((node) => {
        const hasChildren = node.children.length > 0;
        const open = expanded.has(node.path);
        return (
          <div key={node.path}>
            <button type="button" className={`tag-node ${selected === node.path ? "active" : ""}`}
              style={{ paddingLeft: 9 + depth * 13 }} onClick={() => onSelect(node.path)}
              title={node.path}>
              {hasChildren ? (
                <span className={`tag-node-caret ${open ? "open" : ""}`} role="presentation"
                  onClick={(e) => { e.stopPropagation(); onToggle(node.path); }}>
                  <IconChevronRight aria-hidden="true" />
                </span>
              ) : (
                <span className="tag-node-caret placeholder" aria-hidden="true" />
              )}
              <span className="tag-node-name">{node.name}</span>
              <span className="tag-node-count">{node.total}</span>
            </button>
            {hasChildren && open && (
              <TagTree nodes={node.children} depth={depth + 1} selected={selected}
                expanded={expanded} onToggle={onToggle} onSelect={onSelect} />
            )}
          </div>
        );
      })}
    </>
  );
}

/**
 * Preview is two lines only without Markdown rendering—strips syntax symbols back to plain text.
 * Note: preview text is already collapsed into a single line; heading/list markers may appear
 * mid-line, so anchoring to line start only is insufficient.
 */
function stripMarkdown(text: string): string {
  return text
    .replace(/```[a-z]*/gi, "")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__)([^*_]+)\1/g, "$2")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/(^|\s)#{1,6}\s+/g, "$1")
    .replace(/(^|\s)[-*+]\s+\[[ xX]\]\s*/g, "$1")
    .replace(/(^|\s)>\s+/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

/**
 * Background: Both the list page and search results render the same memory card.
 * Design intent: Title + two-line preview + tag chips + mono metadata row;
 * search mode includes similarity score badge.
 * Key constraint: All fields may be missing; clicking the entire card navigates to detail.
 */
function MemoryCard({ memory, onTagClick }: { memory: SearchResultItem; onTagClick?: (tag: string) => void }) {
  const { t } = useI18n();
  const navigate = useNavigate();
  const timestamp = memory.updated_at || memory.created_at || "";

  return (
    <div className="card clickable memory-card" role="link" tabIndex={0}
      onClick={() => memory.id && navigate(`/view/${memory.id}`)}
      onKeyDown={(e) => { if (e.key === "Enter" && memory.id) navigate(`/view/${memory.id}`); }}>
      <h3>{memory.title || t("Untitled")}</h3>
      <p className="preview">{stripMarkdown(memory.content_preview || memory.snippet || memory.content || "")}</p>
      <div className="meta-row">
        <div className="chip-row">
          {(memory.tags ?? []).map((tag) => (
            <span key={tag} className={`chip ${onTagClick ? "clickable" : ""}`}
              onClick={(e) => { if (onTagClick) { e.stopPropagation(); onTagClick(tag); } }}>
              {tag}
            </span>
          ))}
        </div>
        <span className="mono-sm muted" style={{ display: "flex", gap: 12, alignItems: "center" }}>
          {typeof memory.score === "number" && <span className="score-tag">{memory.score.toFixed(3)}</span>}
          {memory.source && <span>{memory.source}</span>}
          {memory.status === "archived" && <span>{t("Archived")}</span>}
          {timestamp && <span>{timestamp.slice(0, 16).replace("T", " ")}</span>}
        </span>
      </div>
    </div>
  );
}

export function MemoryListPage() {
  const { t } = useI18n();
  const navigate = useNavigate();
  // Detail page tag clicks navigate here with params: ?tag=xxx for tag filter, ?q=xxx for direct search
  const [searchParams] = useSearchParams();
  const initialTag = searchParams.get("tag") ?? "";
  const initialQuery = searchParams.get("q") ?? "";
  const [query, setQuery] = useState(initialQuery);
  const [mode, setMode] = useState<(typeof SEARCH_MODES)[number]>("hybrid");
  const [statusFilter, setStatusFilter] = useState("");
  const [tagFilter, setTagFilter] = useState(initialTag);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState<SearchResultItem[]>([]);
  const [total, setTotal] = useState<number | undefined>(undefined);
  const [searchActive, setSearchActive] = useState(false);
  const [tags, setTags] = useState<TagStat[]>([]);
  const [expandedTags, setExpandedTags] = useState<Set<string>>(new Set());
  /* Only effective on narrow screens: desktop filter rail is always visible, this toggle and its trigger button are hidden by CSS */
  const [filtersOpen, setFiltersOpen] = useState(false);

  const tagTree = useMemo(() => buildTagTree(tags), [tags]);

  // Expand first level by default; deeper branches left for users to expand on demand; URL-carried tags expand the full path
  useEffect(() => {
    const next = new Set(tagTree.filter((node) => node.children.length > 0).map((node) => node.path));
    if (initialTag) {
      const segments = initialTag.split("/");
      for (let i = 1; i <= segments.length; i += 1) next.add(segments.slice(0, i).join("/"));
    }
    setExpandedTags(next);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tagTree]);

  /**
   * Background: The left tag filter rail needs tags and their counts.
   * Design intent: Prefer /api/tags/stats; structure-tolerant (array or {tags:[...]}).
   * Key constraint: Failure only toasts, does not block the main list.
   */
  useEffect(() => {
    api<unknown>("GET", "/api/tags/stats")
      .then((res) => {
        const list = Array.isArray(res) ? res : (res as { tags?: unknown })?.tags;
        if (Array.isArray(list)) {
          setTags(list.map((x) => (typeof x === "string" ? { tag: x } : (x as TagStat))));
        }
      })
      .catch((err) => reportError(err, t("Unable to load tags")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadList = useCallback(async (nextOffset: number) => {
    setLoading(true);
    setSearchActive(false);
    try {
      const params = new URLSearchParams();
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(nextOffset));
      if (statusFilter) params.set("status", statusFilter);
      // Backend parameter name is "tags" (comma-separated); passing "tag" would be ignored
      if (tagFilter) params.set("tags", tagFilter);
      const res = await api<MemoryListResponse>("GET", `/api/memories?${params.toString()}`);
      setItems(res?.memories ?? []);
      setTotal(typeof res?.total === "number" ? res.total : undefined);
      setOffset(nextOffset);
    } catch (err) {
      reportError(err, t("Unable to load memories"));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, tagFilter, t]);

  const runSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) {
      void loadList(0);
      return;
    }
    setLoading(true);
    setSearchActive(true);
    try {
      const res = await api<unknown>("POST", "/api/search", { query: q, mode, limit: 50, min_score: 0 });
      setItems(flattenResults(res));
      setTotal(undefined);
      setOffset(0);
    } catch (err) {
      reportError(err, t("Search failed"));
      setItems([]);
    } finally {
      setLoading(false);
    }
  }, [query, mode, loadList, t]);

  /*
   * When entering with ?q= in the URL, execute search directly on first render;
   * skip loadList's mount-time load, otherwise two async requests race
   * and the later one overwrites the search results.
   */
  const skipFirstLoad = useRef(Boolean(initialQuery.trim()));
  useEffect(() => {
    if (skipFirstLoad.current) {
      skipFirstLoad.current = false;
      return;
    }
    void loadList(0);
  }, [loadList]);

  useEffect(() => {
    if (initialQuery.trim()) void runSearch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleSearchKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.nativeEvent.isComposing) {
      e.preventDefault();
      void runSearch();
    }
  };

  const selectTag = (tag: string) => {
    setQuery("");
    setTagFilter((prev) => (prev === tag ? "" : tag));
    // When clicking a tag from a card, expand its full path in the tree
    setExpandedTags((prev) => {
      const next = new Set(prev);
      const segments = tag.split("/");
      for (let i = 1; i <= segments.length; i += 1) next.add(segments.slice(0, i).join("/"));
      return next;
    });
    // On narrow screens, collapse the panel after filtering; otherwise results are hidden behind the tag tree
    setFiltersOpen(false);
  };

  const toggleTagNode = (path: string) => {
    setExpandedTags((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  return (
    <Layout
      title={t("Memories")}
      actions={
        <button type="button" className="btn primary" onClick={() => navigate("/new")}>
          <IconPlus aria-hidden="true" />
          {t("New memory")}
        </button>
      }
      fill
      toolbar={
        <div className="search-bar">
          <input
            className="input mono"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleSearchKey}
            placeholder={t("Search memories, press Enter")}
          />
          <Select style={{ width: 150 }} value={mode}
            onChange={(v) => setMode(v as typeof mode)} ariaLabel={t("Search mode")}
            options={SEARCH_MODES.map((m) => ({ value: m, label: t(MODE_LABELS[m]) }))} />
          <button type="button" className="btn" onClick={runSearch}>
            <IconSearch aria-hidden="true" />
            {t("Search")}
          </button>
          <button
            type="button"
            className="btn filters-toggle"
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen((open) => !open)}
          >
            <IconFilter aria-hidden="true" />
            {t("Filters")}
          </button>
        </div>
      }
    >
      <div className="memories-layout">
        <aside className={filtersOpen ? "filter-rail open" : "filter-rail"}>
          <div className="filter-group">
            <span className="kicker">{t("Status")}</span>
            <div className="options">
              {STATUS_OPTIONS.map((s) => (
                <button key={s || "all"} type="button" className={statusFilter === s ? "active" : ""}
                  onClick={() => setStatusFilter(s)}>
                  <span>{s === "" ? t("All") : s === "active" ? t("Active") : t("Archived")}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="filter-group filter-group-grow">
            <span className="kicker">{t("Tags")}</span>
            <div className="options tag-tree">
              {tagTree.length === 0 ? (
                <span className="mono-sm muted">{t("No tags yet")}</span>
              ) : (
                <TagTree nodes={tagTree} depth={0} selected={tagFilter} expanded={expandedTags}
                  onToggle={toggleTagNode} onSelect={selectTag} />
              )}
            </div>
          </div>
        </aside>

        <section className="memories-results">
          {searchActive && (
            <p className="kicker" style={{ marginBottom: 12 }}>
              {t("Search results")} · {items.length}
              <button type="button" className="btn small" style={{ marginLeft: 12 }}
                onClick={() => { setQuery(""); void loadList(0); }}>
                {t("Clear")}
              </button>
            </p>
          )}
          {loading ? (
            <LoadingLine label={t("Loading")} />
          ) : items.length === 0 ? (
            <EmptyState
              message={searchActive ? t("No results for this query") : t("No memories yet")}
              action={
                !searchActive ? (
                  <Link to="/new" className="btn small">{t("Create the first memory")}</Link>
                ) : undefined
              }
            />
          ) : (
            <div className="memory-card-list">
              {items.map((memory, i) => (
                <MemoryCard key={memory.id ?? i} memory={memory} onTagClick={selectTag} />
              ))}
            </div>
          )}
          {!searchActive && !loading && (
            <Pagination offset={offset} limit={PAGE_SIZE} total={total} pageCount={items.length} onPage={(o) => void loadList(o)} />
          )}
        </section>
      </div>
    </Layout>
  );
}
