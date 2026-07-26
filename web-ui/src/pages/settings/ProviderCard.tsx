/**
 * Background: Each provider in the settings page renders as a collapsible configuration card.
 * Design intent: Collapsed by default showing only a one-line summary (name + API type + active status);
 * expands on click or when it's the "currently active provider"—full configuration form then shows.
 * Design intent: don't spread all providers open; let users modify details after selection.
 * Key constraint: API Key input must be type="text" (project convention enforced);
 * has_api_key only indicates configured, never echoes the key in plaintext.
 */
import { useMemo, useRef, useState } from "react";
import {
  IconPlugConnected,
  IconChevronDown,
  IconTrash,
  IconRefresh,
  IconSearch,
  IconDeviceFloppy,
} from "@tabler/icons-react";
import { api, reportError } from "../../api";
import { emitToast } from "../../toast";
import { useI18n } from "../../i18n";
import type { ProviderConfig } from "../../types";
import { ProviderBrandIcon } from "./ProviderBrandIcon";

export interface ProviderDraft {
  base_url: string;
  embedding_model: string;
  chat_model: string;
  api_key: string;
}

function ModelPicker({ label, value, onSelect, providerId, draftKey, draftBaseUrl, prioritizeEmbedding = false }: {
  label: string;
  value: string;
  onSelect: (model: string) => void;
  providerId: string;
  draftKey: string;
  draftBaseUrl: string;
  prioritizeEmbedding?: boolean;
}) {
  const { t } = useI18n();
  const [models, setModels] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetched, setFetched] = useState(false);
  const [filter, setFilter] = useState("");
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);

  const fetchModels = async () => {
    setLoading(true);
    try {
      const body: Record<string, string> = {};
      if (draftKey.trim()) body.api_key = draftKey.trim();
      if (draftBaseUrl.trim()) body.base_url = draftBaseUrl.trim();
      const res = await api<{ models?: string[]; error?: string; note?: string }>(
        "POST",
        `/api/providers/${encodeURIComponent(providerId)}/models`,
        body,
      );
      const list = res?.models ?? [];
      setModels(list);
      setFetched(true);
      if (list.length > 0) {
        setFilter("");
        setOpen(true);
        emitToast("success", t("{count} models loaded", { count: String(list.length) }));
      } else {
        emitToast("error", res?.error || res?.note || t("No models returned"));
      }
    } catch (err) {
      reportError(err, t("Unable to fetch models"));
    } finally {
      setLoading(false);
    }
  };

  const filtered = useMemo(() => {
    const lc = filter.toLowerCase();
    const visible = filter
      ? models.filter((model) => model.toLowerCase().includes(lc))
      : [...models];
    if (!prioritizeEmbedding) return visible;
    return visible.sort((left, right) => {
      const leftIsEmbedding = left.toLowerCase().includes("embedding");
      const rightIsEmbedding = right.toLowerCase().includes("embedding");
      if (leftIsEmbedding !== rightIsEmbedding) return leftIsEmbedding ? -1 : 1;
      return left.localeCompare(right, "en");
    });
  }, [models, filter, prioritizeEmbedding]);

  const handleBlur = (e: React.FocusEvent) => {
    if (wrapperRef.current?.contains(e.relatedTarget as Node)) return;
    setTimeout(() => setOpen(false), 150);
  };

  return (
    <div className="field model-picker" ref={wrapperRef} onBlur={handleBlur}>
      <div className="model-picker-label">
        <span>{label}</span>
        {fetched && models.length > 0 && (
          <small>{t("{count} models", { count: String(models.length) })}</small>
        )}
      </div>
      <div className="model-picker-control">
        <input className="input mono" value={value}
          onChange={(e) => onSelect(e.target.value)}
          onFocus={() => { if (fetched && models.length > 0) setOpen(true); }}
          placeholder={fetched && models.length === 0 ? t("No models returned") : t("Type or fetch models")}
          autoComplete="off" spellCheck={false} />
        <div className="model-picker-actions">
          <button type="button" className="model-picker-action"
            disabled={loading} onClick={() => void fetchModels()}
            title={t("Fetch models")} aria-label={t("Fetch models")}>
            <IconRefresh className={loading ? "is-spinning" : ""} aria-hidden="true" />
          </button>
          {fetched && models.length > 0 && (
            <button type="button" className="model-picker-action"
            onClick={() => setOpen((prev) => !prev)} aria-label="Toggle model list">
            <IconChevronDown aria-hidden="true"
                style={{ transform: open ? "rotate(180deg)" : "none" }} />
            </button>
          )}
        </div>
      </div>
      {open && models.length > 0 && (
        <div className="model-picker-dropdown">
          <div className="model-picker-search">
            <IconSearch aria-hidden="true" />
            <input value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={t("Filter models")} autoComplete="off" spellCheck={false} />
          </div>
          <ul className="model-picker-list">
            {filtered.length === 0 ? (
              <li className="muted mono-sm" style={{ padding: "6px 8px" }}>{t("No matches")}</li>
            ) : filtered.map((m) => (
              <li key={m} className={`model-picker-option ${m === value ? "active" : ""} ${
                prioritizeEmbedding && m.toLowerCase().includes("embedding") ? "is-embedding" : ""
              }`}
                onMouseDown={(e) => { e.preventDefault(); onSelect(m); setOpen(false); setFilter(""); }}>
                {m === value && <span style={{ marginRight: 4 }}>&#10003;</span>}
                {m}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export function ProviderCard({
  id,
  config,
  draft,
  activeEmbedding,
  activeChat,
  autoExpand,
  saving,
  onChange,
  onSave,
  onUseEmbedding,
  onUseChat,
  onRemove,
}: {
  id: string;
  config: ProviderConfig;
  draft: ProviderDraft;
  activeEmbedding: boolean;
  activeChat: boolean;
  autoExpand?: boolean;
  saving?: boolean;
  onChange: (patch: Partial<ProviderDraft>) => void;
  onSave: () => void;
  onUseEmbedding: () => void;
  onUseChat: () => void;
  onRemove: () => void;
}) {
  const { t } = useI18n();
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success?: boolean; message?: string; dimension?: number } | null>(null);
  const [expanded, setExpanded] = useState(activeEmbedding || activeChat || autoExpand);

  const runTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const body: Record<string, string> = {
        base_url: draft.base_url.trim(),
        embedding_model: draft.embedding_model.trim(),
        chat_model: draft.chat_model.trim(),
      };
      if (draft.api_key.trim()) body.api_key = draft.api_key.trim();
      const res = await api<{ success?: boolean; message?: string; dimension?: number }>(
        "POST",
        `/api/providers/${encodeURIComponent(id)}/test`,
        body,
      );
      setTestResult(res ?? { success: false, message: t("Empty response") });
    } catch (err) {
      reportError(err, t("Provider test failed"));
      setTestResult({ success: false, message: err instanceof Error ? err.message : t("Provider test failed") });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className={`provider-card panel ${activeEmbedding ? "is-active-embed" : ""} ${activeChat ? "is-active-chat" : ""}`}>
      <div className="panel-head clickable" onClick={() => setExpanded((prev) => !prev)} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") setExpanded((prev) => !prev); }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", flex: 1 }}>
          <ProviderBrandIcon id={id} size={24} />
          <strong style={{ fontSize: 15 }}>{config.name || id}</strong>
          {activeEmbedding && <span className="badge fill">{t("Active embedding")}</span>}
          {activeChat && <span className="badge violet">{t("Active chat")}</span>}
        </div>
        <button type="button" className="icon-btn provider-remove" aria-label={t("Remove provider")}
          title={t("Remove provider")} onClick={(event) => { event.stopPropagation(); onRemove(); }}>
          <IconTrash aria-hidden="true" />
        </button>
        <IconChevronDown
          aria-hidden="true"
          style={{ width: 16, height: 16, transition: "transform 0.2s ease", transform: expanded ? "rotate(180deg)" : "rotate(0)" }}
        />
      </div>

      {expanded && (
        <div className="panel-body provider-config-body">
          {config.api_key_env && (
            <label className="field provider-key-field">
              <span>API Key · {config.has_api_key ? t("Configured") : t("Not set")}</span>
              <input className="input mono" type="text" value={draft.api_key}
                placeholder={config.has_api_key ? t("Leave empty to keep the current key") : "sk-..."}
                onChange={(e) => onChange({ api_key: e.target.value })} autoComplete="off" spellCheck={false} />
            </label>
          )}

          <div className="provider-model-grid">
            {config.embedding_model !== undefined && (
              <div className="provider-model-column">
                <ModelPicker
                  label={t("Embedding model")}
                  value={draft.embedding_model}
                  onSelect={(m) => onChange({ embedding_model: m })}
                  providerId={id}
                  draftKey={draft.api_key}
                  draftBaseUrl={draft.base_url}
                  prioritizeEmbedding
                />
                <button type="button" className={`btn small ${activeEmbedding ? "provider-use-active embedding-active" : ""}`}
                  disabled={saving || activeEmbedding || !draft.embedding_model.trim()} onClick={onUseEmbedding}>
                  {activeEmbedding ? t("Embedding in use") : t("Use for embedding")}
                </button>
              </div>
            )}
            {config.chat_model !== undefined && (
              <div className="provider-model-column">
                <ModelPicker
                  label={t("Chat model")}
                  value={draft.chat_model}
                  onSelect={(m) => onChange({ chat_model: m })}
                  providerId={id}
                  draftKey={draft.api_key}
                  draftBaseUrl={draft.base_url}
                />
                <button type="button" className={`btn small ${activeChat ? "provider-use-active chat-active" : ""}`}
                  disabled={saving || activeChat || !draft.chat_model.trim()} onClick={onUseChat}>
                  {activeChat ? t("Chat in use") : t("Use for chat")}
                </button>
              </div>
            )}
          </div>

          <details className="provider-advanced">
            <summary>{t("Advanced settings")}</summary>
            <div className="provider-advanced-fields">
              <label className="field">
                <span>Base URL</span>
                <input className="input mono" value={draft.base_url} onChange={(e) => onChange({ base_url: e.target.value })} />
              </label>
            </div>
          </details>

          <div className="provider-card-footer">
            <button type="button" className="btn primary small" disabled={saving} onClick={onSave}>
              <IconDeviceFloppy aria-hidden="true" />
              {saving ? t("Saving") : t("Save provider settings")}
            </button>
            <button type="button" className="btn small" disabled={testing} onClick={runTest}>
              <IconPlugConnected aria-hidden="true" />
              {testing ? t("Testing") : t("Test connection")}
            </button>
            {testResult && (
              <span className={`mono-sm ${testResult.success ? "text-ok" : "text-danger"}`}>
                {testResult.success ? t("Connection OK") : t("Connection failed")}
                {typeof testResult.dimension === "number" ? ` · ${t("dimension")} ${testResult.dimension}` : ""}
                {testResult.message ? ` · ${testResult.message}` : ""}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
