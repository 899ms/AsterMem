/**
 * Background: /new and /edit/:id share a dual-column Markdown editor—
 * left side mono editing area, right side marked+dompurify live preview.
 * Design intent: Edit mode GETs detail to prefill; after save, navigates to the detail page
 * so the user immediately sees the rechunk result. Title/tags/priority in a row above the editor.
 * Key constraint: Tag input Enter must handle isComposing (already implemented in TagInput);
 * priority backend semantics have unknown range, uses 0-10 numeric input without strict validation.
 */
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { IconDeviceFloppy, IconArrowLeft, IconCopy, IconDownload, IconPencil, IconUpload } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { Select } from "../components/Select";
import { Markdown } from "../components/Markdown";
import { TagInput } from "../components/TagInput";
import { LoadingLine } from "../components/EmptyState";
import { api, reportError } from "../api";
import { copyText } from "../clipboard";
import { SOURCE_URL } from "../license";
import { unwrapMemory } from "../normalize";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import { useAuthSnapshot } from "../authState";
import type { TokenItem } from "../types";

/** Auto-created token name for new users; read/write/config scopes are just enough for AI onboarding. */
const DEFAULT_TOKEN_NAME = "AI";

export function MemoryEditorPage() {
  const { t } = useI18n();
  const { demoMode } = useAuthSnapshot();
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const isEdit = Boolean(id);

  const [loading, setLoading] = useState(isEdit);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [tags, setTags] = useState<string[]>([]);
  const [priority, setPriority] = useState(0);
  const [saving, setSaving] = useState(false);
  const [manualOpen, setManualOpen] = useState(isEdit);
  const [tokens, setTokens] = useState<TokenItem[]>([]);
  const [tokenValue, setTokenValue] = useState("");
  const [selectedTokenId, setSelectedTokenId] = useState("");
  const [tokenLoading, setTokenLoading] = useState(false);
  const textFilesRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    api<unknown>("GET", `/api/memories/${id}`)
      .then((res) => {
        if (cancelled) return;
        const detail = unwrapMemory(res);
        setTitle(detail?.title ?? "");
        setContent(detail?.content ?? "");
        setTags(detail?.tags ?? []);
        setPriority(detail?.priority ?? 0);
      })
      .catch((err) => reportError(err, t("Unable to load this memory")))
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  /**
   * Background: New users finish setup and want to hand instructions to AI, but get stuck on
   * "go to Admin and create a token first".
   * Design intent: Prepare credentials when the page loads—if no usable tokens exist, auto-create
   * a default token; if tokens exist, select the first one and reveal its full value so copied
   * instructions always include credentials.
   * Key constraint: Only auto-create when the list is empty and notify the user, preventing
   * repeated recreation after manual revocation.
   */
  useEffect(() => {
    if (isEdit) return;
    // The demo seals the token endpoints, so the whole bootstrap can only fail here. Visitors are
    // told to run their own instance instead of being pointed at an Admin page that will refuse.
    if (demoMode) return;
    let cancelled = false;
    const listActive = async () => {
      const res = await api<unknown>("GET", "/api/tokens");
      const list = Array.isArray(res) ? res : (res as { tokens?: unknown })?.tokens;
      return Array.isArray(list) ? (list as TokenItem[]).filter((tk) => !tk.revoked) : [];
    };
    void (async () => {
      setTokenLoading(true);
      try {
        let active = await listActive();
        let value = "";
        if (active.length === 0) {
          const created = await api<{ token?: string }>("POST", "/api/tokens", {
            name: DEFAULT_TOKEN_NAME,
            scopes: ["read", "write", "config"],
          });
          value = created?.token ?? "";
          active = await listActive();
          if (value) emitToast("success", t("Created a default API token for you"));
        }
        if (cancelled) return;
        setTokens(active);
        const first = active[0];
        if (!first?.id) return;
        setSelectedTokenId(String(first.id));
        if (!value) {
          const revealed = await api<{ token?: string }>("GET", `/api/tokens/${first.id}/reveal`);
          value = revealed?.token ?? "";
        }
        if (!cancelled) setTokenValue(value);
      } catch (err) {
        console.error("[AsterMem] token setup failed", err);
      } finally {
        if (!cancelled) setTokenLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isEdit]);

  /**
   * Background: The list endpoint only returns token prefixes, but AI instructions need the full credential.
   * Design intent: After selection, reveal the full value by id from the backend—users don't need to
   * look it up elsewhere or copy it manually.
   */
  const selectToken = async (rawId: string) => {
    setSelectedTokenId(rawId);
    setTokenValue("");
    if (!rawId) return;
    setTokenLoading(true);
    try {
      const res = await api<{ token?: string }>("GET", `/api/tokens/${rawId}/reveal`);
      if (res?.token) setTokenValue(res.token);
      else emitToast("error", t("The backend did not return a token value"));
    } catch (err) {
      reportError(err, t("Unable to load this token"));
      setSelectedTokenId("");
    } finally {
      setTokenLoading(false);
    }
  };

  const save = async () => {
    if (!title.trim() && !content.trim()) {
      emitToast("error", t("Title or content is required"));
      return;
    }
    setSaving(true);
    const payload = { title: title.trim(), content, tags, priority };
    try {
      if (isEdit && id) {
        await api("PUT", `/api/memories/${id}`, payload);
        emitToast("success", t("Memory updated"));
        navigate(`/view/${id}`);
      } else {
        const created = unwrapMemory(await api<unknown>("POST", "/api/memories", payload));
        emitToast("success", t("Memory created"));
        navigate(created?.id ? `/view/${created.id}` : "/memories");
      }
    } catch (err) {
      reportError(err, t("Unable to save memory"));
      setSaving(false);
    }
  };

  /**
   * Background: The old copy said "install the Skill I've attached", but users often just paste text,
   * and the AI can't find attachments and asks where the file is.
   * Design intent: Changed to self-bootstrapping instructions—the Skill download endpoint also accepts
   * Bearer Token (read scope), so provide the URL, token, and install steps all at once;
   * the AI can download, install, and connect on its own.
   */
  const copyAIPrompt = async () => {
    const token = tokenValue.trim();
    // Instructions without credentials are useless to AI—better to block than give copyable but non-functional text
    if (!token) {
      emitToast("error", t("Select an API token first"));
      return;
    }
    const baseUrl = window.location.origin;
    const base = t(
      "Set up AsterMem for me, then use it as my memory. Do not ask me to attach any file.\n\nAsterMem runs at {baseUrl} and my API token is at the bottom.\n\n1. Read the Skill: `curl -fsSL -H \"Authorization: Bearer <TOKEN>\" {baseUrl}/api/skill/raw`. That single page is the whole manual, including every REST endpoint.\n2. Save the credentials to `~/.astermem/credentials`:\nASTERMEM_BASE_URL={baseUrl}\nASTERMEM_TOKEN=<TOKEN>\n3. Optional, for the CLI wrapper: download `{baseUrl}/api/skill/download` with the same header, unzip the `astermem/` folder into your skills folder (`~/.claude/skills` for Claude Code, `~/.cursor/skills` for Cursor), then verify with `scripts/astermem.sh config`.\n\nAfter that, use AsterMem to add, search, update and organize memories, and to configure the model providers AsterMem itself uses. Ask me only for missing credentials or decisions. Do not send me back to fill the New memory or Settings forms.",
      { baseUrl },
    );
    const prompt = `${base}\n\n${t("Replace <TOKEN> with: {token}", { token })}`;
    if (await copyText(prompt)) emitToast("success", t("Instructions copied"));
    else emitToast("error", t("Copy failed, select the text manually"));
  };

  /**
   * Background: The deployment guide lives in the repo (skill/deploy-astermem/SKILL.md), and the AI
   * doing the deployment runs on the user's machine, which already has the project checked out.
   * Design intent: One click copies a self-contained brief—where the guide is (local folder first,
   * repository as fallback), what to ask the user for, and what "done" looks like—so the user can
   * paste it into any agent app without hunting for files. No token needed: deployment happens over
   * SSH, not through this instance's API.
   */
  const copyDeployBrief = async () => {
    const brief = t(
      "Deploy AsterMem for me, following the deployment guide that ships with the project.\n\n1. Read the guide first: skill/deploy-astermem/SKILL.md inside my local AsterMem folder (the project currently running at {baseUrl}). If you cannot find the folder, fetch the same file from {repo}.\n2. Start by asking me how I want to deploy: a cloud server, or a machine I already own (this computer, a NAS, a Raspberry Pi) exposed through Cloudflare Tunnel with no public IP. Then follow the guide end to end and do the work yourself.\n3. Once it is live, remind me to change the default admin password, and migrate my existing memories by copying my local data/ directory over if needed.\n\nAsk me only for credentials and decisions. When finished, report the final URL and the completion checklist.",
      { baseUrl: window.location.origin, repo: SOURCE_URL },
    );
    if (await copyText(brief)) emitToast("success", t("Instructions copied"));
    else emitToast("error", t("Copy failed, select the text manually"));
  };

  /**
   * The second DIY path: batch-upload plain text files.
   * Reuses the import page's /api/import-text, reading each file as text in the browser then submitting,
   * using filename (without extension) as title—no new backend endpoint needed.
   */
  const importTextFiles = async (list: FileList) => {
    setUploading(true);
    let ok = 0;
    let firstErr: unknown = null;
    for (const file of Array.from(list)) {
      try {
        const text = await file.text();
        if (!text.trim()) continue;
        await api("POST", "/api/import-text", {
          title: file.name.replace(/\.[^.]+$/, ""),
          content: text,
        });
        ok += 1;
      } catch (err) {
        if (!firstErr) firstErr = err;
      }
    }
    setUploading(false);
    if (textFilesRef.current) textFilesRef.current.value = "";
    if (ok) emitToast("success", t("Imported {count} memories", { count: String(ok) }));
    if (firstErr) reportError(firstErr, t("Import failed"));
    else if (ok) navigate("/memories");
  };

  return (
    <Layout
      title={isEdit ? t("Edit memory") : manualOpen ? t("New memory") : t("Add with AI")}
      // In edit mode, let the editor fill one screen so columns are truly equal height; AI onboarding page keeps normal scrolling
      fill={!loading && (isEdit || manualOpen)}
      actions={
        <>
          <button type="button" className="btn" onClick={() => navigate(-1)}>
            <IconArrowLeft aria-hidden="true" />
            {t("Back")}
          </button>
          {(isEdit || manualOpen) && (
            <button type="button" className="btn primary" disabled={saving} onClick={save}>
              <IconDeviceFloppy aria-hidden="true" />
              {saving ? t("Saving") : t("Save")}
            </button>
          )}
        </>
      }
    >
      {loading ? (
        <LoadingLine label={t("Loading")} />
      ) : !isEdit && !manualOpen ? (
        <>
        <section className="ai-handoff">
          <span className="kicker">ASTERMEM SKILL / AI FIRST</span>
          <h2>{t("Operating it? Hand it to AI.")}</h2>
          <p>{t("Hand it to your AI: ask it to write down everything it already knows about you, dig through your files such as your Documents folder, or walk you through picking materials. Whatever it reads gets saved here as memories.")}</p>
          <p>{t("The more you use it, the better it knows you. It is also the shared memory hub for Claude Code, Codex, Cursor, and every other agent you use: connect each one to AsterMem and they all share the same brain.")}</p>
          <ol>
            <li><span>01</span>{t("Your API token is ready below, already selected.")}</li>
            <li><span>02</span>{t("Copy the setup instructions and send them to your AI.")}</li>
            <li><span>03</span>{t("The AI downloads and installs the Skill itself, then connects.")}</li>
          </ol>
          <div className="field ai-handoff-token">
            <span>
              {t("API Token")}
              {tokens.length > 0 && (
                <small className="muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                  {t("{count} token(s) available", { count: String(tokens.length) })}
                </small>
              )}
            </span>
            {tokens.length === 0 ? (
              <p className="mono-sm muted">
                {demoMode
                  ? t("The demo issues no tokens. Run your own instance to connect an AI to it.")
                  : tokenLoading
                    ? t("Preparing an API token")
                    : t("Could not prepare a token automatically. Create one in Admin, then come back.")}
              </p>
            ) : (
              <>
                <Select mono value={selectedTokenId}
                  onChange={(v) => void selectToken(v)} ariaLabel={t("API Token")}
                  options={[
                    { value: "", label: t("Select a token") },
                    ...tokens.map((tk) => ({
                      value: String(tk.id),
                      label: `${tk.name || tk.prefix} · ${tk.prefix}`,
                    })),
                  ]} />
                <p className="mono-sm muted">
                  {tokenLoading
                    ? t("Loading")
                    : tokenValue
                      ? t("This token will be included in the copied instructions.")
                      : t("Pick a token so the AI can connect without asking you for it.")}
                </p>
              </>
            )}
          </div>
          <div className="ai-handoff-actions">
            <button type="button" className="btn primary" disabled={!tokenValue}
              title={tokenValue ? undefined : t("Select an API token first")}
              onClick={copyAIPrompt}>
              <IconCopy aria-hidden="true" />
              {t("Copy instructions for AI")}
            </button>
            <a className="btn" href="/api/skill/download" download>
              <IconDownload aria-hidden="true" />
              {t("Download AsterMem Skill")}
            </a>
            <Link className="btn" to="/admin">{t("Create API token")}</Link>
          </div>
          <div className="ai-handoff-selfserve">
            <span className="kicker">{t("Prefer to do it yourself?")}</span>
            <div className="ai-handoff-selfserve-actions">
              <button type="button" className="btn" onClick={() => setManualOpen(true)}>
                <IconPencil aria-hidden="true" />
                {t("Write manually this time")}
              </button>
              <input ref={textFilesRef} type="file" multiple hidden
                accept=".txt,.md,.markdown,.text,.log,.csv,.json"
                onChange={(e) => { if (e.target.files?.length) void importTextFiles(e.target.files); }} />
              <button type="button" className="btn" disabled={uploading}
                onClick={() => textFilesRef.current?.click()}>
                <IconUpload aria-hidden="true" />
                {uploading ? t("Importing") : t("Upload text files")}
              </button>
            </div>
            <p className="mono-sm muted">{t("Multiple selection supported: plain-text files like txt or md, each file becomes one memory.")}</p>
          </div>
        </section>

        {/* Deployment is a sibling "hand it to AI" story: same slab, inverted, no token needed
            because the AI works over SSH rather than through this instance's API. */}
        <section className="ai-handoff deploy">
          <span className="kicker">ASTERMEM DEPLOY / AI FIRST</span>
          <h2>{t("Deploying it? Hand it to AI.")}</h2>
          <p>{t("Take AsterMem live on a cloud server, or on a machine you already own — even without a public IP, through Cloudflare Tunnel. A deployment guide written for AI ships with the project: your AI asks how you want to deploy, then does the rest itself, from Docker and HTTPS to go-live hardening.")}</p>
          <div className="ai-handoff-actions">
            <button type="button" className="btn primary" onClick={copyDeployBrief}>
              <IconCopy aria-hidden="true" />
              {t("Copy instructions for AI")}
            </button>
          </div>
        </section>
        </>
      ) : (
        <>
          <div className="editor-meta-stack">
            <label className="field">
              <span>{t("Title")}</span>
              <input className="input" value={title} onChange={(e) => setTitle(e.target.value)}
                placeholder={t("Memory title")} />
            </label>
            <div className="editor-meta-row">
              <div className="field">
                <span>{t("Tags")}</span>
                <TagInput tags={tags} onChange={setTags} placeholder={t("Type a tag and press Enter")} />
              </div>
              <label className="field">
                <span>{t("Priority")}</span>
                <input className="input mono" type="number" min={0} max={10} value={priority}
                  onChange={(e) => setPriority(Number(e.target.value) || 0)} />
              </label>
            </div>
          </div>
          <div className="editor-grid">
            <div className="field">
              <span>{t("Markdown content")}</span>
              <textarea className="textarea" value={content} onChange={(e) => setContent(e.target.value)}
                placeholder={t("Write in Markdown")} />
            </div>
            <div className="field">
              <span>{t("Preview")}</span>
              <div className="editor-preview">
                {content ? <Markdown source={content} /> : <span className="muted mono-sm">{t("Preview appears here")}</span>}
              </div>
            </div>
          </div>
        </>
      )}
    </Layout>
  );
}
