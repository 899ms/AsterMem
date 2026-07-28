/**
 * Background: The Embedding scatter tab renders /api/visualize/embeddings (memory-level) or
 * /api/visualize/trunk-embeddings (trunk-level) 3D dimensionality-reduced coordinates as a
 * rotatable/zoomable 3D point cloud.
 * Design intent: Backend already outputs x/y/z 3D coordinates (PCA / t-SNE / UMAP);
 * rendered with Three.js + OrbitControls; raycaster for hover tooltips;
 * clicking a point opens an in-frame preview drawer (title/tags/content excerpt) instead of
 * navigating away; the drawer links to the full memory. Color buckets by first tag.
 * Key constraint: Render loop and resources must be released in effect cleanup (renderer.dispose,
 * cancelAnimationFrame) to avoid WebGL context leaks after switching tabs;
 * data fetching and rendering are two separate effects—container isn't mounted during loading.
 */
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { IconX } from "@tabler/icons-react";
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { EmptyState, LoadingLine } from "../../components/EmptyState";
import { Select } from "../../components/Select";
import { Markdown } from "../../components/Markdown";
import { api, reportError } from "../../api";
import { unwrapMemory } from "../../normalize";
import { useI18n } from "../../i18n";
import type { EmbeddingPoint } from "../../types";

/** Memory id behind a point; trunk points resolve to their parent document. */
const memoryIdOf = (p: EmbeddingPoint): string => {
  const id = String(p.document_id ?? p.memory_id ?? p.id ?? "");
  return id.startsWith("mem_") ? id : "";
};

const CLUSTER_COLORS = ["#151613", "#8b7cff", "#a2522e", "#2d742d", "#b08a00", "#2b6cb0"];
const PAPER = "#f1efe8";

const METHODS = [
  { value: "pca", label: "PCA" },
  { value: "tsne", label: "t-SNE" },
  { value: "umap", label: "UMAP" },
];

export function EmbeddingTab() {
  const { t } = useI18n();
  const [source, setSource] = useState<"memories" | "trunks">("memories");
  const [method, setMethod] = useState("pca");
  const [loading, setLoading] = useState(true);
  const [points, setPoints] = useState<EmbeddingPoint[]>([]);
  const [methodInfo, setMethodInfo] = useState("");
  const frameRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  /* Pointer type never changes during lifetime—query once at mount; touch and mouse have different picking interactions */
  const [coarsePointer] = useState(() => window.matchMedia("(pointer: coarse)").matches);
  /* Clicking a point opens an in-chart preview drawer instead of navigating away; body is fetched on demand */
  const [selected, setSelected] = useState<EmbeddingPoint | null>(null);
  const [previewContent, setPreviewContent] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);

  useEffect(() => {
    setPreviewContent("");
    if (!selected) return;
    const docId = memoryIdOf(selected);
    if (!docId) return;
    let cancelled = false;
    setPreviewLoading(true);
    api<unknown>("GET", `/api/memories/${docId}`)
      .then((res) => {
        if (!cancelled) setPreviewContent(unwrapMemory(res)?.content ?? "");
      })
      .catch((err) => console.error("[AsterMem] preview load failed", err))
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selected]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setPoints([]);
    setSelected(null);

    (async () => {
      let nextPoints: EmbeddingPoint[] = [];
      let info = "";
      try {
        const base = source === "memories" ? "/api/visualize/embeddings" : "/api/visualize/trunk-embeddings";
        const res = await api<{ points?: EmbeddingPoint[]; method_info?: string }>(
          "GET", `${base}?method=${method}`);
        nextPoints = (res?.points ?? []).filter(
          (p) => typeof p.x === "number" && typeof p.y === "number" && typeof p.z === "number",
        );
        info = res?.method_info ?? "";
      } catch (err) {
        reportError(err, t("Unable to load embedding points"));
      }
      if (cancelled) return;
      setLoading(false);
      setPoints(nextPoints);
      setMethodInfo(info);
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source, method]);

  // Trunk labels are rebuilt here through i18n rather than reusing the backend's title string
  const labelOf = (p: EmbeddingPoint): string => {
    if (p.document_title) {
      const n = String(Number(p.order ?? 0) + 1);
      return `${p.document_title} · ${t("Paragraph {n}", { n })}`;
    }
    return p.title || String(p.id ?? "");
  };

  useEffect(() => {
    const frame = frameRef.current;
    if (points.length === 0 || !frame) return;

    const width = frame.clientWidth || 900;
    const height = frame.clientHeight || 620;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(PAPER);

    const camera = new THREE.PerspectiveCamera(50, width / height, 0.01, 100);
    camera.position.set(2.4, 1.8, 2.4);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(width, height);
    frame.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    // Lightweight reference frame: thin grid + three axes to help perceive rotation direction
    const grid = new THREE.GridHelper(2.4, 12, 0xd8d5cb, 0xe4e1d8);
    (grid.material as THREE.Material).transparent = true;
    (grid.material as THREE.Material).opacity = 0.55;
    grid.position.y = -1.1;
    scene.add(grid);
    const axes = new THREE.AxesHelper(1.25);
    (axes.material as THREE.Material).transparent = true;
    (axes.material as THREE.Material).opacity = 0.4;
    scene.add(axes);

    // Point cloud: BufferGeometry + vertexColors, one draw call handles thousands of points
    const clusterKeys = new Map<string, number>();
    const colorOf = (point: EmbeddingPoint) => {
      const tag = Array.isArray(point.tags) && point.tags.length ? String(point.tags[0]).split("/")[0] : "";
      const key = tag || String(point.cluster ?? "0");
      if (!clusterKeys.has(key)) clusterKeys.set(key, clusterKeys.size);
      return new THREE.Color(CLUSTER_COLORS[(clusterKeys.get(key) ?? 0) % CLUSTER_COLORS.length]);
    };

    const positions = new Float32Array(points.length * 3);
    const colors = new Float32Array(points.length * 3);
    points.forEach((p, i) => {
      positions[i * 3] = Number(p.x);
      positions[i * 3 + 1] = Number(p.y);
      positions[i * 3 + 2] = Number(p.z);
      const c = colorOf(p);
      colors[i * 3] = c.r;
      colors[i * 3 + 1] = c.g;
      colors[i * 3 + 2] = c.b;
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
    const material = new THREE.PointsMaterial({
      size: 0.055,
      vertexColors: true,
      sizeAttenuation: true,
      transparent: true,
      opacity: 0.92,
    });
    const cloud = new THREE.Points(geometry, material);
    scene.add(cloud);

    // Hover highlight ring
    const hoverGeo = new THREE.SphereGeometry(0.045, 16, 16);
    const hoverMat = new THREE.MeshBasicMaterial({ color: 0xbfff00, wireframe: true });
    const hoverMarker = new THREE.Mesh(hoverGeo, hoverMat);
    hoverMarker.visible = false;
    scene.add(hoverMarker);

    const raycaster = new THREE.Raycaster();
    raycaster.params.Points = { threshold: 0.05 };
    const pointer = new THREE.Vector2();
    let hoveredIndex = -1;
    /* Two-stage touch picking: remember which point was highlighted last; tapping the same one again confirms opening */
    let pinnedIndex = -1;
    const tooltip = tooltipRef.current;

    const pick = (event: PointerEvent): number => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObject(cloud);
      return hits.length ? (hits[0].index ?? -1) : -1;
    };

    const showLabel = (index: number, clientX: number, clientY: number) => {
      const frameRect = frame.getBoundingClientRect();
      const p = points[index];
      if (tooltip) {
        tooltip.style.display = "block";
        tooltip.style.left = `${clientX - frameRect.left + 14}px`;
        tooltip.style.top = `${clientY - frameRect.top + 10}px`;
        tooltip.textContent = labelOf(p);
      }
      hoverMarker.position.set(Number(p.x), Number(p.y), Number(p.z));
      hoverMarker.visible = true;
    };

    const hideLabel = () => {
      if (tooltip) tooltip.style.display = "none";
      hoverMarker.visible = false;
    };

    const onPointerMove = (event: PointerEvent) => {
      hoveredIndex = pick(event);
      if (hoveredIndex >= 0) {
        showLabel(hoveredIndex, event.clientX, event.clientY);
        renderer.domElement.style.cursor = "pointer";
      } else {
        hideLabel();
        renderer.domElement.style.cursor = "grab";
      }
    };

    // Click a point to open the in-frame preview drawer (no navigation)
    const openPoint = (index: number) => {
      const p = points[index];
      if (memoryIdOf(p)) setSelected(p);
    };

    /*
     * Touch has no hover; the desktop "hover to see label → click to preview" flow degrades to a
     * blind single tap. Touch uses a two-stage approach: first tap highlights and shows the label,
     * confirm it's the desired point then tap again to preview; tap empty space to dismiss.
     */
    const onClick = (event: PointerEvent) => {
      const index = pick(event);
      if (index < 0) {
        pinnedIndex = -1;
        if (coarsePointer) hideLabel();
        return;
      }
      if (coarsePointer && pinnedIndex !== index) {
        pinnedIndex = index;
        showLabel(index, event.clientX, event.clientY);
        return;
      }
      openPoint(index);
    };

    if (!coarsePointer) renderer.domElement.addEventListener("pointermove", onPointerMove);
    renderer.domElement.addEventListener("click", onClick);

    let rafId = 0;
    const animate = () => {
      rafId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const onResize = () => {
      const w = frame.clientWidth || width;
      const h = frame.clientHeight || height;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("resize", onResize);
      renderer.domElement.removeEventListener("pointermove", onPointerMove);
      renderer.domElement.removeEventListener("click", onClick);
      controls.dispose();
      geometry.dispose();
      material.dispose();
      hoverGeo.dispose();
      hoverMat.dispose();
      renderer.dispose();
      frame.removeChild(renderer.domElement);
    };
  }, [points, coarsePointer]);

  return (
    <div>
      <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap", alignItems: "center" }}>
        <button type="button" className={`btn small ${source === "memories" ? "acid" : ""}`} onClick={() => setSource("memories")}>
          {t("Memory embeddings")}
        </button>
        <button type="button" className={`btn small ${source === "trunks" ? "acid" : ""}`} onClick={() => setSource("trunks")}>
          {t("Trunk embeddings")}
        </button>
        <Select small value={method} options={METHODS} onChange={setMethod} ariaLabel={t("Projection method")} />
        {methodInfo && <span className="mono-sm muted">{methodInfo}</span>}
        <span className="mono-sm muted">
          {coarsePointer
            ? t("Drag to rotate, pinch to zoom, tap a point twice to preview it")
            : t("Drag to rotate, scroll to zoom, click a point to preview it")}
        </span>
      </div>
      {loading ? (
        <LoadingLine label={t("Loading")} />
      ) : points.length === 0 ? (
        <EmptyState message={source === "memories"
          ? t("No memory embeddings yet. Rebuild the vector index first.")
          : t("No segment embeddings yet. Create segments first.")}
        />
      ) : (
        <div className="viz-frame" ref={frameRef}>
          <div ref={tooltipRef} className="viz-tooltip" style={{ display: "none" }} />
          {selected && (
            <div className="entity-drawer">
              <div className="panel-head">
                <span className="kicker">{t("Memory")}</span>
                <button type="button" onClick={() => setSelected(null)} aria-label={t("Close")}>
                  <IconX size={15} stroke={1.5} />
                </button>
              </div>
              <div className="panel-body" style={{ display: "grid", gap: 10 }}>
                <strong style={{ fontSize: 15 }}>{labelOf(selected)}</strong>
                {(selected.tags ?? []).length > 0 && (
                  <div className="chip-row">
                    {(selected.tags ?? []).slice(0, 6).map((tag) => (
                      <span key={tag} className="chip">{tag}</span>
                    ))}
                  </div>
                )}
                {previewLoading ? (
                  <p className="mono-sm muted">{t("Loading")}</p>
                ) : previewContent ? (
                  <div className="embedding-preview-body">
                    <Markdown source={previewContent} />
                  </div>
                ) : null}
                <Link className="btn small" to={`/view/${memoryIdOf(selected)}`}>
                  {t("Open full memory")}
                </Link>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
