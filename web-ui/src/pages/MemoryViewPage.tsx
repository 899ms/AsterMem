/**
 * Background: Memory detail page aggregates four types of info: rendered content, trunk segments,
 * version history, and related memories, plus edit/archive/rechunk/generate-tags/delete actions.
 * Design intent: Content occupies the main column; segments are no longer in a separate panel—
 * instead rendered inline as trunks with divider lines at split points (trunk content is the original
 * paragraphs concatenated in order, losslessly reconstructing the full text).
 * Right narrow column holds history/related panels loaded in parallel, each failing independently (separate catches).
 * Key constraints: Archive via PUT status update; delete requires confirmation;
 * rechunk and generate-tags refresh corresponding data on completion.
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  IconPencil, IconArchive, IconArchiveOff, IconScissors, IconTags, IconTrash, IconArrowLeft,
} from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { Markdown } from "../components/Markdown";
import { ConfirmModal } from "../components/Modal";
import { EmptyState, LoadingLine } from "../components/EmptyState";
import { api, reportError } from "../api";
import { flattenResults, unwrapMemory } from "../normalize";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import type { HistoryEntry, MemoryDetail, MemorySummary, TrunkItem } from "../types";

function asArray<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[];
  const nested = (value as Record<string, unknown> | null)?.[key];
  return Array.isArray(nested) ? (nested as T[]) : [];
}

const trunkText = (trunk: TrunkItem) => trunk.content || trunk.text || "";

/**
 * Flatten all whitespace then compare, used to determine if segments can losslessly restore the current content:
 * the chunker only splits by blank lines and concatenates (doesn't rewrite text), but rechunk is async,
 * so trunks may briefly lag behind after editing—in that case we must fall back to rendering the full content.
 */
const flatten = (text: string) => text.replace(/\s+/g, " ").trim();

export function MemoryViewPage() {
  const { t } = useI18n();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [memory, setMemory] = useState<MemoryDetail | null>(null);
  const [trunks, setTrunks] = useState<TrunkItem[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [related, setRelated] = useState<MemorySummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busyAction, setBusyAction] = useState("");

  const loadAll = useCallback(() => {
    if (!id) return;
    setLoading(true);
    api<unknown>("GET", `/api/memories/${id}`)
      .then((res) => setMemory(unwrapMemory(res)))
      .catch((err) => reportError(err, t("Unable to load this memory")))
      .finally(() => setLoading(false));
    api<unknown>("GET", `/api/memories/${id}/trunks`)
      .then((res) => setTrunks(
        asArray<TrunkItem>(res, "trunks")
          .slice()
          .sort((a, b) => (a.order ?? a.index ?? 0) - (b.order ?? b.index ?? 0)),
      ))
      .catch((err) => console.error("[AsterMem] trunks load failed", err));
    api<unknown>("GET", `/api/memories/${id}/history`)
      .then((res) => setHistory(asArray<HistoryEntry>(res, "history")))
      .catch((err) => console.error("[AsterMem] history load failed", err));
    api<unknown>("GET", `/api/memories/${id}/related`)
      .then((res) => setRelated(flattenResults(res, ["related", "results", "memories"])))
      .catch((err) => console.error("[AsterMem] related load failed", err));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  /**
   * Background: Archive/activate is a toggle operation on the same button.
   * Design intent: Use PUT for partial status update; on success, update locally to avoid full page refresh.
   * Key constraint: If the backend rejects (e.g. unsupported field), use the unified error toast.
   */
  const toggleArchive = async () => {
    if (!id || !memory) return;
    const nextStatus = memory.status === "archived" ? "active" : "archived";
    setBusyAction("archive");
    try {
      await api("PUT", `/api/memories/${id}`, { status: nextStatus });
      setMemory({ ...memory, status: nextStatus });
      emitToast("success", nextStatus === "archived" ? t("Memory archived") : t("Memory restored"));
    } catch (err) {
      reportError(err, t("Unable to update status"));
    } finally {
      setBusyAction("");
    }
  };

  const rechunk = async () => {
    if (!id) return;
    setBusyAction("rechunk");
    try {
      await api("POST", `/api/memories/${id}/rechunk`);
      emitToast("success", t("Rechunk completed"));
      loadAll();
    } catch (err) {
      reportError(err, t("Rechunk failed"));
    } finally {
      setBusyAction("");
    }
  };

  const generateTags = async () => {
    if (!id) return;
    setBusyAction("tags");
    try {
      await api("POST", "/api/generate-tags", { memory_id: id });
      emitToast("success", t("Tags generated"));
      loadAll();
    } catch (err) {
      reportError(err, t("Unable to generate tags"));
    } finally {
      setBusyAction("");
    }
  };

  const remove = async () => {
    if (!id) return;
    setBusyAction("delete");
    try {
      await api("DELETE", `/api/memories/${id}`);
      emitToast("success", t("Memory deleted"));
      navigate("/memories");
    } catch (err) {
      reportError(err, t("Unable to delete memory"));
      setBusyAction("");
      setConfirmDelete(false);
    }
  };

  const archived = memory?.status === "archived";
  const trunksInSync = memory != null && trunks.length > 0
    && flatten(trunks.map(trunkText).join("\n\n")) === flatten(memory.content ?? "");

  return (
    <Layout
      title={memory?.title || t("Memory")}
      actions={
        /*
         * Six action buttons with text would take up half the screen on mobile; btn-compact hides
         * text on narrow screens showing only icons; text must be wrapped in span for individual hiding,
         * with aria-label preserving the accessible name.
         */
        <>
          <button type="button" className="btn btn-compact" aria-label={t("Back")}
            onClick={() => navigate("/memories")}>
            <IconArrowLeft aria-hidden="true" /><span className="btn-label">{t("Back")}</span>
          </button>
          <button type="button" className="btn btn-compact" aria-label={t("Edit")}
            onClick={() => navigate(`/edit/${id}`)}>
            <IconPencil aria-hidden="true" /><span className="btn-label">{t("Edit")}</span>
          </button>
          <button type="button" className="btn btn-compact" aria-label={archived ? t("Restore") : t("Archive")}
            disabled={busyAction === "archive"} onClick={toggleArchive}>
            {archived ? <IconArchiveOff aria-hidden="true" /> : <IconArchive aria-hidden="true" />}
            <span className="btn-label">{archived ? t("Restore") : t("Archive")}</span>
          </button>
          <button type="button" className="btn btn-compact" aria-label={t("Rechunk")}
            disabled={busyAction === "rechunk"} onClick={rechunk}>
            <IconScissors aria-hidden="true" /><span className="btn-label">{t("Rechunk")}</span>
          </button>
          <button type="button" className="btn btn-compact" aria-label={t("Generate tags")}
            disabled={busyAction === "tags"} onClick={generateTags}>
            <IconTags aria-hidden="true" /><span className="btn-label">{t("Generate tags")}</span>
          </button>
          <button type="button" className="btn danger btn-compact" aria-label={t("Delete")}
            onClick={() => setConfirmDelete(true)}>
            <IconTrash aria-hidden="true" /><span className="btn-label">{t("Delete")}</span>
          </button>
        </>
      }
    >
      {loading ? (
        <LoadingLine label={t("Loading")} />
      ) : !memory ? (
        <EmptyState message={t("This memory does not exist")} />
      ) : (
        <div className="view-layout">
          <section>
            <div className="mono-sm muted" style={{ display: "flex", gap: 16, flexWrap: "wrap", marginBottom: 14 }}>
              {memory.status && <span>{memory.status === "archived" ? t("Archived") : t("Active")}</span>}
              {typeof memory.version === "number" && <span>v{memory.version}</span>}
              {typeof memory.priority === "number" && <span>{t("Priority")} {memory.priority}</span>}
              {memory.source && <span>{memory.source}</span>}
              {memory.updated_at && <span>{memory.updated_at.slice(0, 16).replace("T", " ")}</span>}
              {trunks.length > 1 && <span>{t("Trunks")} · {trunks.length}</span>}
            </div>
            <div className="chip-row" style={{ marginBottom: 18 }}>
              {/* Document tags are in the tag system; clicking navigates to the list page filtered by tag */}
              {(memory.tags ?? []).map((tag) => (
                <span key={tag} className="chip clickable" role="link" tabIndex={0}
                  onClick={() => navigate(`/memories?tag=${encodeURIComponent(tag)}`)}
                  onKeyDown={(e) => { if (e.key === "Enter") navigate(`/memories?tag=${encodeURIComponent(tag)}`); }}>
                  {tag}
                </span>
              ))}
            </div>
            <div className="panel"><div className="panel-body">
              {/*
               * Content rendered directly by trunk segments, with dashed lines and sequence numbers
               * at split points, making vector search chunk boundaries directly visible in the text;
               * when trunks are not loaded or out of sync with content, fall back to rendering full text.
               */}
              {trunksInSync ? (
                trunks.map((trunk, i) => (
                  <div key={trunk.id ?? i} className="trunk-block">
                    {trunks.length > 1 && (
                      <span className="trunk-block-label mono-sm muted">#{i + 1}</span>
                    )}
                    <Markdown source={trunkText(trunk)} />
                    {(trunk.meta_tags?.length ?? 0) > 0 && (
                      <div className="chip-row trunk-meta">
                        {/*
                         * Semantic tags (format "type:value") are not in the tag system and cannot be filtered by tag;
                         * clicking takes the value portion as a keyword and navigates to the list page for search.
                         */}
                        {trunk.meta_tags!.slice(0, 12).map((tag) => {
                          const keyword = tag.includes(":") ? tag.slice(tag.indexOf(":") + 1) : tag;
                          const go = () => navigate(`/memories?q=${encodeURIComponent(keyword)}`);
                          return (
                            <span key={tag} className="chip clickable" role="link" tabIndex={0}
                              onClick={go} onKeyDown={(e) => { if (e.key === "Enter") go(); }}>
                              {tag}
                            </span>
                          );
                        })}
                        {trunk.meta_tags!.length > 12 && (
                          <span className="mono-sm muted">+{trunk.meta_tags!.length - 12}</span>
                        )}
                      </div>
                    )}
                  </div>
                ))
              ) : (
                <Markdown source={memory.content ?? ""} />
              )}
            </div></div>
          </section>

          <aside className="view-side panel">
            <section className="view-side-section">
              <div className="panel-head"><span className="kicker">{t("Version history")}</span></div>
              <div className="view-side-scroll history-scroll">
                {history.length === 0
                  ? <p className="mono-sm muted" style={{ padding: "14px 18px" }}>{t("No history")}</p>
                  : history.map((entry, i) => (
                      <div key={i} className="history-item">
                        <span className="mono-sm">v{entry.version ?? "?"} {entry.action || entry.change || entry.title || ""}</span>
                        <span className="mono-sm muted">{(entry.changed_at || entry.updated_at || entry.created_at || "").slice(0, 16).replace("T", " ")}</span>
                      </div>
                    ))}
              </div>
            </section>

            <section className="view-side-section related-section">
              <div className="panel-head"><span className="kicker">{t("Related memories")}</span></div>
              <div className="view-side-scroll related-scroll">
                {related.length === 0
                  ? <p className="mono-sm muted" style={{ padding: "14px 18px" }}>{t("No related memories")}</p>
                  : related.map((item, i) => (
                      <Link key={item.id ?? i} to={`/view/${item.id}`} className="history-item" style={{ display: "grid" }}>
                        <span style={{ fontSize: 13, fontWeight: 600 }}>{item.title || t("Untitled")}</span>
                        {typeof item.score === "number" && <span className="mono-sm muted">{item.score.toFixed(3)}</span>}
                      </Link>
                    ))}
              </div>
            </section>
          </aside>
        </div>
      )}

      {confirmDelete && (
        <ConfirmModal
          title={t("Delete memory")}
          message={t("This will permanently delete the memory and its chunks. This cannot be undone.")}
          confirmLabel={t("Delete")}
          cancelLabel={t("Cancel")}
          danger
          busy={busyAction === "delete"}
          onConfirm={remove}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </Layout>
  );
}
