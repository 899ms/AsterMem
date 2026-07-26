/**
 * Background: Data import/export page aggregating four import methods (zip/json upload, paste text,
 * image, smart import) and one-click export download.
 * Design intent: Two tabs for "Import & export" and "Smart import";
 * file upload uses native input[type=file] + FormData, export uses apiDownload to trigger browser download.
 * Key constraint: After upload, display the backend-returned import count (structure-tolerant);
 * image import relies on backend OCR/recognition, takes longer and needs busy state.
 */
import { useRef, useState } from "react";
import { IconUpload, IconDownload, IconPhoto, IconClipboardText } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { Tabs } from "../components/Tabs";
import { api, apiUpload, apiDownload, reportError } from "../api";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import { SmartImportTab } from "./SmartImportTab";

function summarizeImport(res: unknown): string | null {
  const o = res as Record<string, unknown> | null;
  const count = o?.imported ?? o?.count ?? o?.total;
  return typeof count === "number" ? String(count) : null;
}

export function ImportPage() {
  const { t } = useI18n();
  const [tab, setTab] = useState("io");
  const fileRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState("");
  const [pasteTitle, setPasteTitle] = useState("");
  const [pasteContent, setPasteContent] = useState("");

  const doExport = async () => {
    setBusy("export");
    try {
      await apiDownload("/api/export", `astermem-export-${new Date().toISOString().slice(0, 10)}.zip`);
      emitToast("success", t("Export started"));
    } catch (err) {
      reportError(err, t("Export failed"));
    } finally {
      setBusy("");
    }
  };

  const doImportFile = async (file: File) => {
    setBusy("import");
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await apiUpload("/api/import", form);
      const count = summarizeImport(res);
      emitToast("success", count ? t("Imported {count} memories", { count }) : t("Import completed"));
    } catch (err) {
      reportError(err, t("Import failed"));
    } finally {
      setBusy("");
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const doImportImage = async (file: File) => {
    setBusy("image");
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await apiUpload("/api/import-image", form);
      const count = summarizeImport(res);
      emitToast("success", count ? t("Imported {count} memories", { count }) : t("Image imported"));
    } catch (err) {
      reportError(err, t("Image import failed"));
    } finally {
      setBusy("");
      if (imageRef.current) imageRef.current.value = "";
    }
  };

  const doImportText = async () => {
    if (!pasteContent.trim()) return;
    setBusy("text");
    try {
      await api("POST", "/api/import-text", {
        title: pasteTitle.trim() || pasteContent.trim().slice(0, 40),
        content: pasteContent,
      });
      emitToast("success", t("Text imported as a memory"));
      setPasteTitle("");
      setPasteContent("");
    } catch (err) {
      reportError(err, t("Import failed"));
    } finally {
      setBusy("");
    }
  };

  return (
    <Layout title={t("Import / Export")}>
      <Tabs
        items={[
          { key: "io", label: t("Import & export") },
          { key: "smart", label: t("Smart import") },
        ]}
        active={tab}
        onChange={setTab}
      />
      <div style={{ paddingTop: 22 }}>
        {tab === "smart" ? (
          <SmartImportTab />
        ) : (
          <div className="import-layout">
            <div className="panel">
              <div className="panel-head"><span className="kicker">{t("Archive file")}</span></div>
              <div className="panel-body" style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
                <input ref={fileRef} type="file" accept=".zip,.json" hidden
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) void doImportFile(f); }} />
                <button type="button" className="btn" disabled={busy === "import"} onClick={() => fileRef.current?.click()}>
                  <IconUpload aria-hidden="true" />
                  {busy === "import" ? t("Importing") : t("Upload zip or json")}
                </button>
                <button type="button" className="btn" disabled={busy === "export"} onClick={doExport}>
                  <IconDownload aria-hidden="true" />
                  {busy === "export" ? t("Exporting") : t("Export everything")}
                </button>
              </div>
            </div>

            <div className="panel">
              <div className="panel-head"><span className="kicker">{t("Image import")}</span></div>
              <div className="panel-body" style={{ display: "grid", gap: 10 }}>
                <p className="muted" style={{ fontSize: 13 }}>{t("Upload a screenshot or photo, the backend extracts text into a memory.")}</p>
                <input ref={imageRef} type="file" accept="image/*" hidden
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) void doImportImage(f); }} />
                <button type="button" className="btn" style={{ justifySelf: "start" }} disabled={busy === "image"} onClick={() => imageRef.current?.click()}>
                  <IconPhoto aria-hidden="true" />
                  {busy === "image" ? t("Importing") : t("Upload image")}
                </button>
              </div>
            </div>

            <div className="panel import-paste">
              <div className="panel-head"><span className="kicker">{t("Paste text")}</span></div>
              <div className="panel-body" style={{ display: "grid", gap: 12 }}>
                <label className="field">
                  <span>{t("Title")}</span>
                  <input className="input" value={pasteTitle} onChange={(e) => setPasteTitle(e.target.value)}
                    placeholder={t("Optional, first 40 characters are used when empty")} />
                </label>
                <label className="field">
                  <span>{t("Content")}</span>
                  <textarea className="textarea" value={pasteContent} onChange={(e) => setPasteContent(e.target.value)}
                    placeholder={t("Paste any text to store as one memory")} />
                </label>
                <button type="button" className="btn primary" style={{ justifySelf: "start" }}
                  disabled={busy === "text" || !pasteContent.trim()} onClick={doImportText}>
                  <IconClipboardText aria-hidden="true" />
                  {busy === "text" ? t("Importing") : t("Import text")}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  );
}
