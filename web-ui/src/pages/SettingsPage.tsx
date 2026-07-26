/**
 * Background: Settings page manages the Provider registry (GET/PUT /api/config), active provider selection,
 * semantic search toggle, and vector index status & rebuild.
 * Design intent: Local draft state collects all edits; "Save config" does a one-shot PUT partial update
 * (providers + active + search + api_keys); switching embedding provider is a high-risk operation—
 * shows a "vector index rebuild needed" confirmation, then PUTs first and POSTs /api/vector-rebuild,
 * polling /api/vector-rebuild/status to draw a progress bar.
 * Key constraint: api_keys only submit non-empty new values; polling stops when rebuild finishes or component unmounts.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { IconBook, IconBrandGithub, IconDeviceFloppy, IconPlus, IconRefresh } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { ConfirmModal, Modal } from "../components/Modal";
import { LoadingLine, EmptyState } from "../components/EmptyState";
import { api, reportError } from "../api";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import { LICENSE_NAME, SOURCE_URL } from "../license";
import { ProviderCard, type ProviderDraft } from "./settings/ProviderCard";
import { ProviderBrandIcon } from "./settings/ProviderBrandIcon";
import type { AppConfig, VectorRebuildStatus } from "../types";

const PROVIDER_CATEGORIES = ["global", "platform", "local", "china", "coding"] as const;
const PROVIDER_CATEGORY_LABELS: Record<string, string> = {
  global: "Global providers",
  platform: "API platforms",
  local: "Local runtimes",
  china: "Providers from China",
  coding: "Coding plans",
};
const PROVIDER_PRIORITY = [
  "openai", "anthropic", "google", "xai",
  "openrouter", "pipellm_claude", "siliconflow", "tokendance", "asterove",
  "lmstudio", "ollama",
  "dashscope", "deepseek", "minimax", "moonshot", "volces", "xiaomi", "zhipu",
  "aliyun_coding", "kimi_coding", "minimax_coding", "volces_coding", "xiaomi_coding", "zhipu_coding",
];

export function SettingsPage() {
  const { t } = useI18n();
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [drafts, setDrafts] = useState<Record<string, ProviderDraft>>({});
  const [embeddingProvider, setEmbeddingProvider] = useState("");
  const [chatProvider, setChatProvider] = useState("");
  const [semanticEnabled, setSemanticEnabled] = useState(true);
  const [minSimilarity, setMinSimilarity] = useState(0.15);
  // Upper limit provided by backend: this value is just the noise floor; setting it too high zeroes out all semantic recall
  const [minSimilarityMax, setMinSimilarityMax] = useState(0.4);
  const [saving, setSaving] = useState(false);
  const [showProviderCatalog, setShowProviderCatalog] = useState(false);
  const [providerAction, setProviderAction] = useState("");
  const [newProviderId, setNewProviderId] = useState("");
  const [confirmRebuild, setConfirmRebuild] = useState(false);
  const [pendingActive, setPendingActive] = useState<{
    embedding_provider: string;
    chat_provider: string;
  } | null>(null);
  const [pendingProviderId, setPendingProviderId] = useState<string | null>(null);
  const [vectorStatus, setVectorStatus] = useState<Record<string, unknown> | null>(null);
  const [rebuild, setRebuild] = useState<VectorRebuildStatus | null>(null);
  const pollRef = useRef<number | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await api<AppConfig>("GET", "/api/config");
      setConfig(res ?? {});
      const nextDrafts: Record<string, ProviderDraft> = {};
      for (const [id, p] of Object.entries(res?.providers ?? {})) {
        nextDrafts[id] = {
          base_url: p.base_url ?? "",
          embedding_model: p.embedding_model ?? "",
          chat_model: p.chat_model ?? "",
          api_key: "",
        };
      }
      setDrafts(nextDrafts);
      setEmbeddingProvider(res?.active?.embedding_provider ?? "");
      setChatProvider(res?.active?.chat_provider ?? "");
      setSemanticEnabled(res?.search?.semantic?.enabled ?? true);
      const floorMax = res?.search?.semantic?.min_similarity_max ?? 0.4;
      setMinSimilarityMax(floorMax);
      setMinSimilarity(Math.min(res?.search?.semantic?.min_similarity ?? 0.15, floorMax));
    } catch (err) {
      reportError(err, t("Unable to load configuration"));
    } finally {
      setLoading(false);
    }
    api<Record<string, unknown>>("GET", "/api/vector-status")
      .then((res) => setVectorStatus(res ?? null))
      .catch((err) => console.error("[AsterMem] vector status failed", err));
  }, [t]);

  useEffect(() => {
    void load();
    return () => {
      if (pollRef.current !== null) window.clearInterval(pollRef.current);
    };
  }, [load]);

  /**
   * Background: rebuilding vector index can take a long time; needs progress feedback.
   * Design intent: poll status every 2 seconds; stop timer and refresh vector state when running=false.
   * Key constraint: repeated calls clear the previous timer first to guarantee single-instance polling.
   */
  const startPolling = () => {
    if (pollRef.current !== null) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      try {
        const res = await api<VectorRebuildStatus>("GET", "/api/vector-rebuild/status");
        setRebuild(res ?? null);
        if (!res?.running) {
          if (pollRef.current !== null) window.clearInterval(pollRef.current);
          pollRef.current = null;
          emitToast("success", t("Vector rebuild finished"));
          api<Record<string, unknown>>("GET", "/api/vector-status")
            .then((s) => setVectorStatus(s ?? null))
            .catch((err) => console.error("[AsterMem] vector status failed", err));
        }
      } catch (err) {
        console.error("[AsterMem] rebuild poll failed", err);
      }
    }, 2000);
  };

  /**
   * Background: semantic search toggle and similarity slider previously only changed local state—
   * no save entry point, lost on refresh.
   * Design intent: these two settings save immediately on change, submitting only these two fields
   * (backend supports partial updates), avoiding accidental submission of provider drafts.
   * Slider saves on pointer-up/key-up.
   */
  const saveSearchSettings = async (payload: { semantic_enabled?: boolean; min_similarity?: number }) => {
    try {
      await api("PUT", "/api/config", payload);
      emitToast("success", t("Configuration saved"));
    } catch (err) {
      reportError(err, t("Unable to save configuration"));
    }
  };

  const buildPayload = (
    activeOverride?: { embedding_provider: string; chat_provider: string },
    semanticOverride?: boolean,
  ) => {
    const providers: Record<string, Partial<ProviderDraft>> = {};
    const api_keys: Record<string, string> = {};
    for (const [id, d] of Object.entries(drafts)) {
      providers[id] = { base_url: d.base_url, embedding_model: d.embedding_model, chat_model: d.chat_model };
      if (d.api_key.trim()) api_keys[id] = d.api_key.trim();
    }
    return {
      providers,
      active: activeOverride ?? {
        embedding_provider: embeddingProvider,
        chat_provider: chatProvider,
      },
      semantic_enabled: semanticOverride ?? semanticEnabled,
      min_similarity: minSimilarity,
      ...(Object.keys(api_keys).length > 0 ? { api_keys } : {}),
    };
  };

  const save = async (
    thenRebuild: boolean,
    activeOverride?: { embedding_provider: string; chat_provider: string },
    semanticOverride?: boolean,
  ) => {
    setSaving(true);
    try {
      await api("PUT", "/api/config", buildPayload(activeOverride, semanticOverride));
      emitToast("success", t("Configuration saved"));
      if (thenRebuild) {
        await api("POST", "/api/vector-rebuild");
        setRebuild({ running: true, current: 0, total: 0, completed: false });
        startPolling();
      }
      // After successful save, clear submitted key drafts and re-read server state.
      void load();
    } catch (err) {
      reportError(err, t("Unable to save configuration"));
    } finally {
      setSaving(false);
      setConfirmRebuild(false);
      setPendingActive(null);
      setPendingProviderId(null);
    }
  };

  const handleSaveClick = () => {
    const active = {
      embedding_provider: embeddingProvider,
      chat_provider: chatProvider,
    };
    const originalEmbedding = config?.active?.embedding_provider ?? "";
    if (embeddingProvider && embeddingProvider !== originalEmbedding) {
      setPendingActive(active);
      setConfirmRebuild(true);
    } else {
      void save(false, active);
    }
  };

  const saveProvider = async (
    id: string,
    thenRebuild = false,
    activeOverride?: { embedding_provider: string; chat_provider: string },
    semanticOverride?: boolean,
  ) => {
    const draft = drafts[id];
    if (!draft) return;
    const payload = {
      providers: {
        [id]: {
          base_url: draft.base_url,
          embedding_model: draft.embedding_model,
          chat_model: draft.chat_model,
        },
      },
      ...(activeOverride ? { active: activeOverride } : {}),
      ...(semanticOverride !== undefined ? { semantic_enabled: semanticOverride } : {}),
      ...(draft.api_key.trim() ? { api_keys: { [id]: draft.api_key.trim() } } : {}),
    };

    setSaving(true);
    try {
      await api("PUT", "/api/config", payload);
      if (activeOverride) {
        setEmbeddingProvider(activeOverride.embedding_provider);
        setChatProvider(activeOverride.chat_provider);
      }
      if (semanticOverride !== undefined) setSemanticEnabled(semanticOverride);
      emitToast("success", activeOverride ? t("Provider saved and activated") : t("Provider settings saved"));
      if (thenRebuild) {
        await api("POST", "/api/vector-rebuild");
        setRebuild({ running: true, current: 0, total: 0, completed: false });
        startPolling();
      }
      void load();
    } catch (err) {
      reportError(err, t("Unable to save configuration"));
    } finally {
      setSaving(false);
      setConfirmRebuild(false);
      setPendingActive(null);
      setPendingProviderId(null);
    }
  };

  const useProviderFor = (id: string, purpose: "embedding" | "chat") => {
    const active = {
      embedding_provider: purpose === "embedding" ? id : embeddingProvider,
      chat_provider: purpose === "chat" ? id : chatProvider,
    };
    if (purpose === "embedding" && id !== (config?.active?.embedding_provider ?? "")) {
      setPendingProviderId(id);
      setPendingActive(active);
      setConfirmRebuild(true);
      return;
    }
    void saveProvider(id, false, active, purpose === "embedding" ? true : undefined);
  };

  const rebuildIndex = async () => {
    setSaving(true);
    try {
      await api("POST", "/api/vector-rebuild");
      setRebuild({ running: true, current: 0, total: 0, completed: false });
      startPolling();
      setConfirmRebuild(false);
    } catch (err) {
      reportError(err, t("Unable to rebuild vector index"));
    } finally {
      setSaving(false);
    }
  };

  const providerIds = Object.keys(config?.providers ?? {});
  const availableProviders = Object.entries(config?.provider_catalog ?? {})
    .filter(([id]) => !config?.providers?.[id])
    .sort(([aId, a], [bId, b]) => {
      const aRank = PROVIDER_PRIORITY.indexOf(aId);
      const bRank = PROVIDER_PRIORITY.indexOf(bId);
      if (aRank >= 0 || bRank >= 0) return (aRank < 0 ? 999 : aRank) - (bRank < 0 ? 999 : bRank);
      return (a.name ?? "").localeCompare(b.name ?? "", "en");
    });

  const addProvider = async (id: string) => {
    setProviderAction(id);
    try {
      await api("PUT", "/api/config", { add_providers: [id] });
      emitToast("success", t("Provider added"));
      setNewProviderId(id);
      setShowProviderCatalog(false);
      await load();
    } catch (err) {
      reportError(err, t("Unable to add provider"));
    } finally {
      setProviderAction("");
    }
  };

  const removeProvider = async (id: string) => {
    const providerName = config?.providers?.[id]?.name || id;
    if (!window.confirm(t(
      "Remove {name} from the list? Its saved API key will be kept.",
      { name: providerName },
    ))) return;

    const remaining = providerIds.filter((providerId) => providerId !== id);
    const isReady = (providerId: string) => {
      const provider = config?.providers?.[providerId];
      return !provider?.api_key_env || Boolean(provider.has_api_key);
    };
    const nextEmbedding = embeddingProvider === id
      ? remaining.find((providerId) => Boolean(drafts[providerId]?.embedding_model) && isReady(providerId)) ?? ""
      : embeddingProvider;
    const nextChat = chatProvider === id
      ? remaining.find((providerId) => Boolean(drafts[providerId]?.chat_model) && isReady(providerId)) ?? ""
      : chatProvider;
    const embeddingChanged = embeddingProvider === id && nextEmbedding !== embeddingProvider;

    setProviderAction(id);
    try {
      await api("PUT", "/api/config", {
        remove_providers: [id],
        active: {
          embedding_provider: nextEmbedding,
          chat_provider: nextChat,
        },
        ...(nextEmbedding ? {} : { semantic_enabled: false }),
      });
      setEmbeddingProvider(nextEmbedding);
      setChatProvider(nextChat);
      if (!nextEmbedding) setSemanticEnabled(false);
      if (embeddingChanged && nextEmbedding) {
        await api("POST", "/api/vector-rebuild");
        setRebuild({ running: true, current: 0, total: 0, completed: false });
        startPolling();
      }
      emitToast("success", t("Provider removed"));
      await load();
    } catch (err) {
      reportError(err, t("Unable to remove provider"));
    } finally {
      setProviderAction("");
    }
  };

  const progress = rebuild?.percent
    ?? (rebuild?.total
      ? Math.min(100, Math.round(((rebuild.current ?? 0) / rebuild.total) * 100))
      : 0);
  const statusNumber = (key: string) => {
    const value = vectorStatus?.[key];
    return typeof value === "number" ? value : 0;
  };
  const statusFlag = (key: string) => vectorStatus?.[key] === true;
  const totalMemories = statusNumber("total_memories");
  const indexedMemories = statusNumber("memory_vectorized_count");
  const totalTrunks = statusNumber("total_trunks");
  const indexedTrunks = statusNumber("vectorized_count");
  const indexReady = statusFlag("vector_store_available")
    && indexedMemories >= totalMemories
    && indexedTrunks >= totalTrunks;

  return (
    <Layout
      title={t("Settings")}
      actions={
        <button type="button" className="btn primary" disabled={saving || loading} onClick={handleSaveClick}>
          <IconDeviceFloppy aria-hidden="true" />
          {saving ? t("Saving") : t("Save all settings")}
        </button>
      }
    >
      {loading ? (
        <LoadingLine label={t("Loading")} />
      ) : !config ? (
        <EmptyState message={t("Unable to load configuration")} />
      ) : (
        <div className="settings-layout">
          <section className="settings-providers">
            <div className="provider-list-head">
              <div>
                <span className="kicker">{t("Added providers")}</span>
                <p className="muted">{t("Add a provider before entering its API key.")}</p>
              </div>
              <button type="button" className="btn" onClick={() => setShowProviderCatalog(true)}>
                <IconPlus aria-hidden="true" />
                {t("Add provider")}
              </button>
            </div>

            <div className="provider-list">
              {providerIds.length === 0
                ? <EmptyState message={t("No providers added yet")} />
                : providerIds.map((id) => (
                  <ProviderCard
                    key={id}
                    id={id}
                    config={config.providers?.[id] ?? {}}
                    draft={drafts[id] ?? { base_url: "", embedding_model: "", chat_model: "", api_key: "" }}
                    activeEmbedding={embeddingProvider === id}
                    activeChat={chatProvider === id}
                    autoExpand={newProviderId === id}
                    saving={saving}
                    onChange={(patch) => {
                      setDrafts((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
                    }}
                    onSave={() => void saveProvider(id)}
                    onUseEmbedding={() => useProviderFor(id, "embedding")}
                    onUseChat={() => useProviderFor(id, "chat")}
                    onRemove={() => void removeProvider(id)}
                  />
                ))}
            </div>
          </section>

          <aside className="settings-side">
            <div className="panel">
              <div className="panel-head"><span className="kicker">{t("Semantic search")}</span></div>
              <div className="panel-body settings-search-options">
                <label className="checkbox-row">
                  <input type="checkbox" checked={semanticEnabled} onChange={(e) => {
                    setSemanticEnabled(e.target.checked);
                    void saveSearchSettings({ semantic_enabled: e.target.checked });
                  }} />
                  {t("Enable semantic search")}
                </label>
                <label className="field">
                  <span>{t("Minimum similarity")} · {minSimilarity.toFixed(2)}</span>
                  <input type="range" min={0} max={minSimilarityMax} step={0.01} value={minSimilarity}
                    onChange={(e) => setMinSimilarity(Number(e.target.value))}
                    onPointerUp={(e) => void saveSearchSettings({ min_similarity: Number(e.currentTarget.value) })}
                    onKeyUp={(e) => void saveSearchSettings({ min_similarity: Number(e.currentTarget.value) })}
                    style={{ accentColor: "var(--ink)" }} />
                  <span className="field-note">
                    {t("Noise floor only; relevance is judged automatically")}
                  </span>
                </label>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head">
                <span className="kicker">{t("Vector index")}</span>
                <button type="button" className="btn small" onClick={() => {
                  setPendingActive(null);
                  setPendingProviderId(null);
                  setConfirmRebuild(true);
                }} disabled={Boolean(rebuild?.running)}>
                  <IconRefresh aria-hidden="true" />
                  {t("Rebuild index")}
                </button>
              </div>
              <div className="panel-body" style={{ display: "grid", gap: 12 }}>
                {vectorStatus && (
                  <div className="vector-status">
                    <div className="vector-status-grid">
                      <div>
                        <span>{t("Semantic search")}</span>
                        <strong className={statusFlag("semantic_enabled") ? "text-ok" : "text-danger"}>
                          {statusFlag("semantic_enabled") ? t("Enabled") : t("Disabled")}
                        </strong>
                      </div>
                      <div>
                        <span>{t("Document vectors")}</span>
                        <strong>{indexedMemories} / {totalMemories}</strong>
                      </div>
                      <div>
                        <span>{t("Segment vectors")}</span>
                        <strong>{totalTrunks ? `${indexedTrunks} / ${totalTrunks}` : t("No segments yet")}</strong>
                      </div>
                    </div>
                    <p className={`vector-status-message ${indexReady ? "text-ok" : "muted"}`}>
                      {indexReady ? t("Vector index is ready") : t("Vector index needs rebuilding")}
                    </p>
                  </div>
                )}
                {rebuild && (
                  <div style={{ display: "grid", gap: 6 }}>
                    <div className="progress-track"><i style={{ width: `${progress}%` }} /></div>
                    <span className="mono-sm muted">
                      {rebuild.running ? t("Rebuilding") : t("Finished")} · {rebuild.current ?? 0}/{rebuild.total ?? 0}
                      {` · ${t("Memory")} ${rebuild.memory_done ?? 0} · ${t("Trunks")} ${rebuild.trunk_done ?? 0}`}
                      {rebuild.error ? ` · ${t("failed")} ${rebuild.error}` : ""}
                    </span>
                  </div>
                )}
              </div>
            </div>

            <div className="panel">
              <div className="panel-head"><span className="kicker">{t("About AsterMem")}</span></div>
              <div className="panel-body" style={{ display: "grid", gap: 12 }}>
                <p className="muted">{t("Why original text is the only truth, how retrieval navigates, and what makes the profile trustworthy — the design decisions behind this framework.")}</p>
                <div>
                  <Link className="btn" to="/methodology">
                    <IconBook aria-hidden="true" />
                    {t("How AsterMem works")}
                  </Link>
                </div>
                <p className="muted settings-license">
                  {t("AsterMem is free software licensed under {license}. You are entitled to the complete corresponding source code.", { license: LICENSE_NAME })}
                </p>
                <div>
                  <a className="btn" href={SOURCE_URL} target="_blank" rel="noreferrer">
                    <IconBrandGithub aria-hidden="true" />
                    {t("Source code")}
                  </a>
                </div>
              </div>
            </div>
          </aside>
        </div>
      )}

      {confirmRebuild && (
        <ConfirmModal
          title={t("Rebuild vector index")}
          message={t("Changing the embedding provider requires rebuilding the vector index for all memories. This may take a while.")}
          confirmLabel={t("Save and rebuild")}
          cancelLabel={t("Cancel")}
          busy={saving}
          onConfirm={() => {
            if (pendingProviderId && pendingActive) {
              void saveProvider(pendingProviderId, true, pendingActive, true);
            } else if (pendingActive) {
              void save(true, pendingActive);
            } else {
              void rebuildIndex();
            }
          }}
          onCancel={() => {
            setConfirmRebuild(false);
            setPendingActive(null);
            setPendingProviderId(null);
          }}
        />
      )}

      {showProviderCatalog && (
        <Modal title={t("Add provider")} wide onClose={() => setShowProviderCatalog(false)}>
          {availableProviders.length === 0 ? (
            <EmptyState message={t("All providers have been added")} />
          ) : (
            <div className="provider-catalog">
              {PROVIDER_CATEGORIES.map((category) => {
                const items = availableProviders.filter(([, provider]) => provider.category === category);
                if (!items.length) return null;
                return (
                  <section key={category} className="provider-catalog-group">
                    <span className="kicker">{t(PROVIDER_CATEGORY_LABELS[category])}</span>
                    <div className="provider-catalog-grid">
                      {items.map(([id, provider]) => (
                        <button key={id} type="button" className="provider-catalog-item"
                          disabled={Boolean(providerAction)} onClick={() => void addProvider(id)}>
                          <ProviderBrandIcon id={id} size={34} />
                          <span className="provider-catalog-copy">
                            <strong>{provider.name || id}</strong>
                            <small>{providerAction === id ? t("Adding") : t("Add provider")}</small>
                          </span>
                        </button>
                      ))}
                    </div>
                  </section>
                );
              })}
            </div>
          )}
        </Modal>
      )}
    </Layout>
  );
}
