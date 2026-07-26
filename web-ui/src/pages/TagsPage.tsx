/**
 * Background: Tags support a/b/c hierarchical paths; needs tree-view browsing and merge/rename/delete operations.
 * Design intent: GET /api/tags/tree renders a hierarchically-indented tree; action buttons appear on row hover;
 * merge uses a "check multiple source tags → enter target tag" batch model, matching the merge API signature.
 * Key constraint: Delete/merge are destructive operations requiring confirmation modals;
 * when the tree endpoint fails or is empty, falls back to /api/tags flat list so the page isn't blank.
 */
import { useCallback, useEffect, useState } from "react";
import { IconPencil, IconTrash, IconGitMerge } from "@tabler/icons-react";
import { Layout } from "../components/Layout";
import { Modal, ConfirmModal } from "../components/Modal";
import { EmptyState, LoadingLine } from "../components/EmptyState";
import { api, reportError } from "../api";
import { emitToast } from "../toast";
import { useI18n } from "../i18n";
import type { TagTreeNode } from "../types";

interface FlatTag {
  path: string;
  depth: number;
  count?: number;
}

/**
 * Background: the tree API may return nested children or just a flat path list.
 * Design intent: uniformly flatten into {path, depth, count} row sequences so the render layer handles only one shape.
 * Key constraint: node name field is compatible with name/tag/full_tag/path—four possible field names.
 */
function flattenTree(nodes: TagTreeNode[], depth = 0, parentPath = ""): FlatTag[] {
  const rows: FlatTag[] = [];
  for (const node of nodes) {
    const own = node.name || node.tag || "";
    const path = node.full_tag || node.path || (parentPath ? `${parentPath}/${own}` : own);
    if (path) rows.push({ path, depth, count: node.count });
    if (Array.isArray(node.children) && node.children.length > 0) {
      rows.push(...flattenTree(node.children, depth + 1, path));
    }
  }
  return rows;
}

export function TagsPage() {
  const { t } = useI18n();
  const [rows, setRows] = useState<FlatTag[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string[]>([]);
  const [renameTarget, setRenameTarget] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [mergeInto, setMergeInto] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const tree = await api<unknown>("GET", "/api/tags/tree");
      const nodes = Array.isArray(tree) ? tree : (tree as { tree?: unknown; tags?: unknown })?.tree ?? (tree as { tags?: unknown })?.tags;
      let flat = Array.isArray(nodes) ? flattenTree(nodes as TagTreeNode[]) : [];
      if (flat.length === 0) {
        // When tree is empty, fall back to flat tag list to ensure existing tags remain manageable.
        const plain = await api<unknown>("GET", "/api/tags");
        const list = Array.isArray(plain) ? plain : (plain as { tags?: unknown })?.tags;
        if (Array.isArray(list)) {
          flat = list.map((x) => {
            if (typeof x === "string") return { path: x, depth: 0 };
            const o = x as TagTreeNode;
            return { path: o.tag || o.name || "", depth: 0, count: o.count };
          }).filter((r) => r.path);
        }
      }
      setRows(flat);
      setSelected([]);
    } catch (err) {
      reportError(err, t("Unable to load tags"));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleSelect = (path: string) => {
    setSelected((prev) => (prev.includes(path) ? prev.filter((x) => x !== path) : [...prev, path]));
  };

  const doRename = async () => {
    if (!renameTarget || !renameValue.trim()) return;
    setBusy(true);
    try {
      await api("POST", "/api/tags/rename", { old_tag: renameTarget, new_tag: renameValue.trim() });
      emitToast("success", t("Tag renamed"));
      setRenameTarget(null);
      void load();
    } catch (err) {
      reportError(err, t("Unable to rename tag"));
    } finally {
      setBusy(false);
    }
  };

  const doDelete = async () => {
    if (!deleteTarget) return;
    setBusy(true);
    try {
      await api("POST", "/api/tags/delete", { tag: deleteTarget });
      emitToast("success", t("Tag deleted"));
      setDeleteTarget(null);
      void load();
    } catch (err) {
      reportError(err, t("Unable to delete tag"));
    } finally {
      setBusy(false);
    }
  };

  const doMerge = async () => {
    if (selected.length === 0 || !mergeInto.trim()) return;
    setBusy(true);
    try {
      await api("POST", "/api/tags/merge", { source_tags: selected, target_tag: mergeInto.trim() });
      emitToast("success", t("Tags merged"));
      setMergeOpen(false);
      setMergeInto("");
      void load();
    } catch (err) {
      reportError(err, t("Unable to merge tags"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Layout
      title={t("Tags")}
      actions={
        <button type="button" className="btn" disabled={selected.length === 0} onClick={() => setMergeOpen(true)}>
          <IconGitMerge aria-hidden="true" />
          {t("Merge selected")} ({selected.length})
        </button>
      }
    >
      {loading ? (
        <LoadingLine label={t("Loading")} />
      ) : rows.length === 0 ? (
        <EmptyState message={t("No tags yet")} />
      ) : (
        <div className="panel tag-tree">
          {rows.map((row) => (
            <div
              key={row.path}
              className="tag-tree-row"
              /* Per-level indent uses CSS variable; stylesheet narrows it on small screens so deep tags aren't pushed off screen */
              style={{ paddingLeft: `calc(14px + ${row.depth} * var(--tag-indent, 22px))` }}
            >
              <label className="checkbox-row" style={{ gap: 10 }}>
                <input type="checkbox" checked={selected.includes(row.path)} onChange={() => toggleSelect(row.path)} />
                <span className="mono-sm">{row.path}</span>
              </label>
              {typeof row.count === "number" && <span className="mono-sm muted">{row.count}</span>}
              <div className="actions">
                <button type="button" className="btn small" onClick={() => { setRenameTarget(row.path); setRenameValue(row.path); }}>
                  <IconPencil aria-hidden="true" />{t("Rename")}
                </button>
                <button type="button" className="btn small danger" onClick={() => setDeleteTarget(row.path)}>
                  <IconTrash aria-hidden="true" />{t("Delete")}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {renameTarget && (
        <Modal
          title={t("Rename tag")}
          onClose={() => setRenameTarget(null)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setRenameTarget(null)}>{t("Cancel")}</button>
              <button type="button" className="btn primary" disabled={busy || !renameValue.trim()} onClick={doRename}>
                {t("Rename")}
              </button>
            </>
          }
        >
          <label className="field">
            <span>{t("New tag name")}</span>
            <input className="input mono" value={renameValue} autoFocus
              onChange={(e) => setRenameValue(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); void doRename(); } }} />
          </label>
        </Modal>
      )}

      {deleteTarget && (
        <ConfirmModal
          title={t("Delete tag")}
          message={t("Delete tag {tag} from all memories?", { tag: deleteTarget })}
          confirmLabel={t("Delete")}
          cancelLabel={t("Cancel")}
          danger
          busy={busy}
          onConfirm={doDelete}
          onCancel={() => setDeleteTarget(null)}
        />
      )}

      {mergeOpen && (
        <Modal
          title={t("Merge tags")}
          onClose={() => setMergeOpen(false)}
          footer={
            <>
              <button type="button" className="btn" onClick={() => setMergeOpen(false)}>{t("Cancel")}</button>
              <button type="button" className="btn primary" disabled={busy || !mergeInto.trim()} onClick={doMerge}>
                {t("Merge")}
              </button>
            </>
          }
        >
          <p style={{ fontSize: 13 }}>{t("Selected source tags will be replaced by the target tag on every memory.")}</p>
          <div className="chip-row">
            {selected.map((tag) => <span key={tag} className="chip">{tag}</span>)}
          </div>
          <label className="field">
            <span>{t("Target tag")}</span>
            <input className="input mono" value={mergeInto} autoFocus
              onChange={(e) => setMergeInto(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.nativeEvent.isComposing) { e.preventDefault(); void doMerge(); } }} />
          </label>
        </Modal>
      )}
    </Layout>
  );
}
