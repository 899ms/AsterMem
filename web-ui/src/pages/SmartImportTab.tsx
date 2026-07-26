/**
 * Background: Smart import sends a long text to the backend for chunking (/api/smart-import/chunk),
 * then the user previews segments, generates tags per segment (/api/smart-import/generate-tags),
 * and finally batch-imports to the database.
 * Design intent: Three-step linear flow in a single tab: paste → preview editable segments → batch create;
 * each segment can be individually removed; batch import POSTs /api/memories one by one,
 * failed segments remain for retry.
 * Key constraint: generate-tags return structure is tolerant (tags array or {tags});
 * after all segments are saved successfully, state resets back to step one.
 */
import { useState } from "react";
import { IconScissors, IconSparkles, IconDatabaseImport, IconX } from "@tabler/icons-react";
import { TagInput } from "../components/TagInput";
import { LoadingLine } from "../components/EmptyState";
import { api, reportError } from "../api";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import type { SmartChunk } from "../types";

interface DraftChunk {
  title: string;
  content: string;
  tags: string[];
  tagBusy: boolean;
  saved: boolean;
}

export function SmartImportTab() {
  const { t } = useI18n();
  const [text, setText] = useState("");
  const [chunks, setChunks] = useState<DraftChunk[]>([]);
  const [chunking, setChunking] = useState(false);
  const [saving, setSaving] = useState(false);

  const runChunk = async () => {
    if (!text.trim()) return;
    setChunking(true);
    try {
      const res = await api<unknown>("POST", "/api/smart-import/chunk", { text });
      const list = Array.isArray(res) ? res : (res as { chunks?: unknown })?.chunks;
      const parsed = (Array.isArray(list) ? (list as SmartChunk[]) : []).map((c) => ({
        title: c.title ?? "",
        content: c.content ?? c.text ?? "",
        tags: Array.isArray(c.tags) ? c.tags : [],
        tagBusy: false,
        saved: false,
      })).filter((c) => c.content);
      if (parsed.length === 0) {
        emitToast("error", t("The backend returned no chunks"));
      }
      setChunks(parsed);
    } catch (err) {
      reportError(err, t("Chunking failed"));
    } finally {
      setChunking(false);
    }
  };

  const updateChunk = (index: number, patch: Partial<DraftChunk>) => {
    setChunks((prev) => prev.map((c, i) => (i === index ? { ...c, ...patch } : c)));
  };

  const genTags = async (index: number) => {
    const chunk = chunks[index];
    if (!chunk) return;
    updateChunk(index, { tagBusy: true });
    try {
      const res = await api<unknown>("POST", "/api/smart-import/generate-tags", {
        title: chunk.title,
        content: chunk.content,
        text: chunk.content,
      });
      const tags = Array.isArray(res) ? res : (res as { tags?: unknown })?.tags;
      if (Array.isArray(tags)) {
        updateChunk(index, { tags: tags.map(String) });
      } else {
        emitToast("error", t("No tags were generated"));
      }
    } catch (err) {
      reportError(err, t("Unable to generate tags"));
    } finally {
      updateChunk(index, { tagBusy: false });
    }
  };

  /**
   * Background: batch import may partially fail (network jitter, individual chunk validation failure).
   * Design intent: submit chunks sequentially; mark successful ones as saved and skip re-submission;
   * failed chunks stay in the list for user retry—never silently discard content.
   * Key constraint: input area is cleared only when all succeed.
   */
  const saveAll = async () => {
    setSaving(true);
    let failures = 0;
    const next = [...chunks];
    for (let i = 0; i < next.length; i++) {
      if (next[i].saved) continue;
      try {
        await api("POST", "/api/memories", {
          title: next[i].title || next[i].content.slice(0, 40),
          content: next[i].content,
          tags: next[i].tags,
          priority: 0,
        });
        next[i] = { ...next[i], saved: true };
        setChunks([...next]);
      } catch (err) {
        failures++;
        reportError(err, t("Unable to save chunk {index}", { index: i + 1 }));
      }
    }
    setSaving(false);
    if (failures === 0) {
      emitToast("success", t("All chunks imported"));
      setChunks([]);
      setText("");
    } else {
      emitToast("error", t("{count} chunks failed, they remain in the list", { count: failures }));
    }
  };

  if (chunks.length > 0) {
    const pending = chunks.filter((c) => !c.saved).length;
    return (
      <div style={{ display: "grid", gap: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span className="kicker">{t("Preview chunks")} · {chunks.length}</span>
          <div style={{ display: "flex", gap: 10 }}>
            <button type="button" className="btn" onClick={() => setChunks([])}>{t("Start over")}</button>
            <button type="button" className="btn primary" disabled={saving || pending === 0} onClick={saveAll}>
              <IconDatabaseImport aria-hidden="true" />
              {saving ? t("Importing") : t("Import {count} chunks", { count: pending })}
            </button>
          </div>
        </div>
        {chunks.map((chunk, i) => (
          <div key={i} className="panel" style={chunk.saved ? { opacity: 0.5 } : undefined}>
            <div className="panel-head">
              <input className="input" style={{ border: 0, padding: 0, fontWeight: 600 }}
                value={chunk.title}
                placeholder={t("Chunk title")}
                onChange={(e) => updateChunk(i, { title: e.target.value })} />
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                {chunk.saved && <span className="badge fill">{t("Saved")}</span>}
                <button type="button" className="btn small" disabled={chunk.tagBusy || chunk.saved} onClick={() => genTags(i)}>
                  <IconSparkles aria-hidden="true" />
                  {chunk.tagBusy ? t("Generating") : t("Generate tags")}
                </button>
                <button type="button" className="btn small" onClick={() => setChunks((prev) => prev.filter((_, x) => x !== i))} aria-label={t("Remove chunk")}>
                  <IconX aria-hidden="true" />
                </button>
              </div>
            </div>
            <div className="panel-body" style={{ display: "grid", gap: 12 }}>
              <p style={{ fontSize: 13, whiteSpace: "pre-wrap", maxHeight: 160, overflowY: "auto" }}>{chunk.content}</p>
              <TagInput tags={chunk.tags} onChange={(tags) => updateChunk(i, { tags })} placeholder={t("Type a tag and press Enter")} />
            </div>
          </div>
        ))}
      </div>
    );
  }

  return (
    <div style={{ display: "grid", gap: 14 }}>
      <label className="field">
        <span>{t("Paste a long document")}</span>
        <textarea className="textarea mono" style={{ minHeight: 320 }} value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder={t("Paste any long text, the AI will split it into memory chunks")} />
      </label>
      {chunking ? (
        <LoadingLine label={t("Chunking")} />
      ) : (
        <button type="button" className="btn primary" style={{ justifySelf: "start" }} disabled={!text.trim()} onClick={runChunk}>
          <IconScissors aria-hidden="true" />
          {t("Split into chunks")}
        </button>
      )}
    </div>
  );
}
