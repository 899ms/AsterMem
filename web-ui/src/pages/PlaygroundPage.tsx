/**
 * Background: Design system playground, dev-only route (registered in App.tsx only when import.meta.env.DEV),
 * for debugging styles element by element; demos are added but never removed as requirements change.
 * Design intent: Each UI atom gets its own demo block (buttons/inputs/cards/chips/tables/
 * streaming bubbles/locale switcher/empty states/toasts/modals/badges/progress bars).
 * Streaming bubble simulation uses a timer to append characters, verifying the stream-cursor animation.
 * Key constraint: This page makes no backend requests; block titles go through t() for all languages.
 */
import { useEffect, useState } from "react";
import { IconPlus } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { Modal, ConfirmModal } from "../components/Modal";
import { EmptyState, LoadingLine } from "../components/EmptyState";
import { TagInput } from "../components/TagInput";
import { Select } from "../components/Select";
import { Tabs } from "../components/Tabs";
import { Pagination } from "../components/Pagination";
import { Markdown } from "../components/Markdown";
import { LocaleSwitcher, useI18n } from "../i18n";
import { emitToast } from "../toast";

const STREAM_TEXT = "Streaming tokens arrive one by one, and the acid cursor blinks at the end of the line…";

function PlaygroundSelectDemo() {
  const [value, setValue] = useState("hybrid");
  return (
    <Select value={value} onChange={setValue}
      options={["hybrid", "keyword", "semantic"].map((v) => ({ value: v, label: v }))} />
  );
}

function Demo({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="demo-section">
      <span className="kicker">{title}</span>
      <div className="demo-box">{children}</div>
    </section>
  );
}

export function PlaygroundPage() {
  const { t } = useI18n();
  const [tags, setTags] = useState<string[]>(["memory/dev", "design"]);
  const [tab, setTab] = useState("one");
  const [offset, setOffset] = useState(0);
  const [modalOpen, setModalOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [streamed, setStreamed] = useState("");
  const [streaming, setStreaming] = useState(false);

  /**
   * Background: Need to verify streaming bubble visuals without a backend.
   * Design intent: Local timer emits characters one by one from a fixed text to simulate SSE.
   * Key constraint: Clean up interval on unmount.
   */
  useEffect(() => {
    if (!streaming) return;
    setStreamed("");
    let i = 0;
    const timer = window.setInterval(() => {
      i += 2;
      setStreamed(STREAM_TEXT.slice(0, i));
      if (i >= STREAM_TEXT.length) {
        window.clearInterval(timer);
        setStreaming(false);
      }
    }, 40);
    return () => window.clearInterval(timer);
  }, [streaming]);

  return (
    <Layout title={t("Playground")}>
      <Demo title={t("Buttons")}>
        <button type="button" className="btn">Default</button>
        <button type="button" className="btn primary">Primary</button>
        <button type="button" className="btn acid">Acid</button>
        <button type="button" className="btn danger">Danger</button>
        <button type="button" className="btn" disabled>Disabled</button>
        <button type="button" className="btn small"><IconPlus aria-hidden="true" />Small + icon</button>
      </Demo>

      <Demo title={t("Inputs")}>
        <label className="field" style={{ width: 220 }}>
          <span>Text input</span>
          <input className="input" placeholder="Type here" />
        </label>
        <label className="field" style={{ width: 220 }}>
          <span>Mono input (API key, type=text)</span>
          <input className="input mono" type="text" placeholder="sk-..." />
        </label>
        <label className="field" style={{ width: 180 }}>
          <span>Select</span>
          <PlaygroundSelectDemo />
        </label>
        <label className="field" style={{ width: 280 }}>
          <span>Textarea</span>
          <textarea className="textarea" style={{ minHeight: 70 }} placeholder="Markdown…" />
        </label>
      </Demo>

      <Demo title={t("Tag chips")}>
        <span className="chip">plain</span>
        <span className="chip active">active</span>
        <span className="chip clickable">hover me</span>
        <div style={{ width: 320 }}>
          <TagInput tags={tags} onChange={setTags} placeholder={t("Type a tag and press Enter")} />
        </div>
      </Demo>

      <Demo title={t("Cards")}>
        <div className="card" style={{ width: 240 }}>
          <h3 style={{ fontSize: 16, fontWeight: 600 }}>Static card</h3>
          <p className="muted" style={{ fontSize: 13 }}>Paper surface, hairline border.</p>
        </div>
        <div className="card clickable" style={{ width: 240 }}>
          <h3 style={{ fontSize: 16, fontWeight: 600 }}>Clickable card</h3>
          <p className="muted" style={{ fontSize: 13 }}>Hover darkens paper.</p>
        </div>
        <div className="stat-cell" style={{ width: 170 }}>
          <span className="kicker">Total memories</span>
          <strong>1,024</strong>
        </div>
      </Demo>

      <Demo title={t("Badges and progress")}>
        <span className="badge">openai_compatible</span>
        <span className="badge fill">Active embedding</span>
        <span className="badge violet">Active chat</span>
        <span className="score-tag">0.873</span>
        <div style={{ width: 260 }}>
          <div className="progress-track"><i style={{ width: "62%" }} /></div>
        </div>
      </Demo>

      <Demo title={t("Table")}>
        <table className="table" style={{ maxWidth: 560 }}>
          <thead>
            <tr><th>Time</th><th>Method</th><th>Path</th><th>Status</th></tr>
          </thead>
          <tbody>
            <tr className="clickable"><td className="mono-sm">2026-07-26 12:00</td><td className="mono-sm">GET</td><td className="mono-sm">/api/memories</td><td className="mono-sm text-ok">200</td></tr>
            <tr className="clickable"><td className="mono-sm">2026-07-26 12:01</td><td className="mono-sm">POST</td><td className="mono-sm">/api/search</td><td className="mono-sm text-danger">500</td></tr>
          </tbody>
        </table>
      </Demo>

      <Demo title={t("Streaming bubble")}>
        <div style={{ display: "grid", gap: 10, width: "100%", maxWidth: 560 }}>
          <button type="button" className="btn small" style={{ justifySelf: "start" }} onClick={() => setStreaming(true)} disabled={streaming}>
            Replay stream
          </button>
          <div className={`stream-block ${streaming ? "streaming" : ""}`}>
            <span className="kicker" style={{ display: "block", marginBottom: 8 }}>Question · demo</span>
            {streamed}
            {streaming && <span className="stream-cursor" aria-hidden="true" />}
          </div>
        </div>
      </Demo>

      <Demo title={t("Markdown rendering")}>
        <div style={{ maxWidth: 560 }}>
          <Markdown source={"## Heading\n\nBody with **bold**, `code`, and a [link](https://example.com).\n\n- list item\n- another\n\n```ts\nconst x: number = 1\n```"} />
        </div>
      </Demo>

      <Demo title={t("Tabs and pagination")}>
        <div style={{ display: "grid", gap: 14, width: "100%", maxWidth: 460 }}>
          <Tabs items={[{ key: "one", label: "Tab one" }, { key: "two", label: "Tab two" }]} active={tab} onChange={setTab} />
          <Pagination offset={offset} limit={20} total={97} pageCount={20} onPage={setOffset} />
        </div>
      </Demo>

      <Demo title={t("Language switcher")}>
        <LocaleSwitcher />
      </Demo>

      <Demo title={t("Empty and loading states")}>
        <div style={{ width: 300 }}><EmptyState message={t("No memories yet")} /></div>
        <div style={{ width: 220 }}><LoadingLine label={t("Loading")} /></div>
      </Demo>

      <Demo title={t("Toast")}>
        <button type="button" className="btn small" onClick={() => emitToast("info", "Info toast")}>Info</button>
        <button type="button" className="btn small" onClick={() => emitToast("success", "Success toast")}>Success</button>
        <button type="button" className="btn small" onClick={() => emitToast("error", "Error toast")}>Error</button>
      </Demo>

      <Demo title={t("Modals")}>
        <button type="button" className="btn small" onClick={() => setModalOpen(true)}>Open modal</button>
        <button type="button" className="btn small danger" onClick={() => setConfirmOpen(true)}>Open confirm</button>
      </Demo>

      {modalOpen && (
        <Modal title="DEMO MODAL" onClose={() => setModalOpen(false)}
          footer={<button type="button" className="btn primary" onClick={() => setModalOpen(false)}>OK</button>}>
          <p style={{ fontSize: 14 }}>Hard-shadow editorial modal, zero radius.</p>
        </Modal>
      )}
      {confirmOpen && (
        <ConfirmModal title="DEMO CONFIRM" message="Destructive action confirmation demo."
          confirmLabel={t("Delete")} cancelLabel={t("Cancel")} danger
          onConfirm={() => setConfirmOpen(false)} onCancel={() => setConfirmOpen(false)} />
      )}
    </Layout>
  );
}
