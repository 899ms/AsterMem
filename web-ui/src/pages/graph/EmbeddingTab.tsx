/**
 * Background: The Embedding scatter tab renders /api/visualize/embeddings (memory-level) or
 * /api/visualize/trunk-embeddings (trunk-level) 3D dimensionality-reduced coordinates as a
 * rotatable/zoomable 3D point cloud.
 * Design intent: Backend already outputs x/y/z 3D coordinates (PCA / t-SNE / UMAP);
 * rendered with Three.js + OrbitControls; raycaster for hover tooltips;
 * clicking a point opens an in-frame preview drawer (title/tags/content excerpt) instead of
 * navigating away; the drawer links to the full memory.
 * Points are colored by a user-chosen dimension (tag / source / document / type = categorical,
 * priority / recency = sequential ramp) with a legend; clicking a legend entry fades every other
 * group toward the paper color so one group can be inspected inside the cloud.
 * Key constraint: Render loop and resources must be released in effect cleanup (renderer.dispose,
 * cancelAnimationFrame) to avoid WebGL context leaks after switching tabs;
 * data fetching and rendering are two separate effects—container isn't mounted during loading.
 * Recoloring must not rebuild the scene (that would reset the camera), so it writes into the
 * existing color attribute through a ref.
 */
import { useEffect, useMemo, useRef, useState } from "react";
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

const PAPER = "#f1efe8";
const INK = "#151613";
/* Categorical hues picked for the cream canvas: saturated enough to separate, dark enough to read */
const CATEGORY_COLORS = [
  "#2f6f9f", "#c2551f", "#2d7a4f", "#7b4ea8", "#b08a00",
  "#b8365a", "#2a8f8f", "#8a5a2b", "#5b6cd0", "#6f8f1f",
];
/* Buckets without a value (no tag, unknown source) recede instead of taking the strongest color */
const MUTED = "#bdb8a7";
const OVERFLOW = "#8d8778";
/* Sequential ramps run light to dark so the ordering stays readable without a legend lookup */
const RECENCY_STOPS = ["#ded9c6", "#a8bda0", "#5f9a86", "#2b6c6f", "#183f52"];
const PRIORITY_STOPS = ["#e2dbc6", "#dfc07a", "#d98a3c", "#b7452c", "#7a1f1f"];
const MAX_CATEGORIES = 10;
const NA_KEY = "__na__";
const OVERFLOW_KEY = "__overflow__";

const METHODS = [
  { value: "pca", label: "PCA" },
  { value: "tsne", label: "t-SNE" },
  { value: "umap", label: "UMAP" },
];

type ColorMode = "tag" | "source" | "document" | "type" | "priority" | "recency" | "none";

interface LegendItem {
  key: string;
  label: string;
  color: string;
  count: number;
}

interface ColorView {
  colors: Float32Array;
  legend: LegendItem[];
  /** Sequential modes show a ramp bar instead of clickable buckets */
  scale: { stops: string[]; minLabel: string; maxLabel: string } | null;
}

/** Sample a hex stop list at t in [0,1]; stops are treated as evenly spaced. */
const sampleStops = (stops: string[], t: number): THREE.Color => {
  const scaled = Math.min(1, Math.max(0, t)) * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  return new THREE.Color(stops[index]).lerp(new THREE.Color(stops[index + 1]), scaled - index);
};

const fadeToPaper = (color: THREE.Color, amount: number): THREE.Color =>
  color.clone().lerp(new THREE.Color(PAPER), amount);

/**
 * Default point sprites are hard squares, which read as pixel noise at this density.
 * A radial-gradient canvas sprite gives round dots with a soft edge; the texture is white so the
 * per-vertex color multiplies through unchanged.
 */
const createDotTexture = (): THREE.CanvasTexture => {
  const size = 64;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    const half = size / 2;
    const gradient = ctx.createRadialGradient(half, half, 0, half, half, half);
    gradient.addColorStop(0, "rgba(255,255,255,1)");
    gradient.addColorStop(0.65, "rgba(255,255,255,1)");
    gradient.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, size, size);
  }
  return new THREE.CanvasTexture(canvas);
};

const timeOf = (p: EmbeddingPoint): number => {
  const parsed = p.created_at ? Date.parse(String(p.created_at)) : NaN;
  return Number.isNaN(parsed) ? NaN : parsed;
};

export function EmbeddingTab() {
  const { t } = useI18n();
  const [source, setSource] = useState<"memories" | "trunks">("memories");
  const [method, setMethod] = useState("pca");
  const [colorMode, setColorMode] = useState<ColorMode>("tag");
  /* Legend focus: keep one bucket in color and fade the rest, so a group can be read inside the cloud */
  const [focusKey, setFocusKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [points, setPoints] = useState<EmbeddingPoint[]>([]);
  const [methodInfo, setMethodInfo] = useState("");
  const frameRef = useRef<HTMLDivElement>(null);
  const tooltipRef = useRef<HTMLDivElement>(null);
  const geometryRef = useRef<THREE.BufferGeometry | null>(null);
  const colorsRef = useRef<Float32Array>(new Float32Array(0));
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

  const colorModes = [
    { value: "tag", label: t("Color by tag") },
    ...(source === "trunks" ? [{ value: "document", label: t("Color by memory") }] : []),
    { value: "source", label: t("Color by source") },
    { value: "type", label: t("Color by type") },
    { value: "priority", label: t("Color by priority") },
    { value: "recency", label: t("Color by recency") },
    { value: "none", label: t("Single color") },
  ];

  /* "By memory" only exists for trunk points; switching the data source must not leave it selected */
  useEffect(() => {
    if (source === "memories" && colorMode === "document") setColorMode("tag");
  }, [source, colorMode]);

  /*
   * One pass over the points produces both the per-vertex color buffer and the legend, so the two can
   * never disagree. Categorical modes bucket by a key, keep the ten largest buckets and merge the rest.
   */
  const view = useMemo<ColorView>(() => {
    const colors = new Float32Array(points.length * 3);
    const write = (index: number, color: THREE.Color) => {
      colors[index * 3] = color.r;
      colors[index * 3 + 1] = color.g;
      colors[index * 3 + 2] = color.b;
    };

    if (colorMode === "none") {
      const ink = new THREE.Color(INK);
      points.forEach((_, i) => write(i, ink));
      return { colors, legend: [], scale: null };
    }

    if (colorMode === "priority" || colorMode === "recency") {
      const stops = colorMode === "priority" ? PRIORITY_STOPS : RECENCY_STOPS;
      const values = points.map((p) =>
        colorMode === "priority" ? Number(p.priority ?? NaN) : timeOf(p),
      );
      /* Reduce instead of Math.min(...values): the spread would blow the argument limit on big clouds */
      let min = Infinity;
      let max = -Infinity;
      let validCount = 0;
      values.forEach((value) => {
        if (Number.isNaN(value)) return;
        validCount += 1;
        min = Math.min(min, value);
        max = Math.max(max, value);
      });
      /* Older backends send no timestamps at all; a fully muted cloud would look broken, so fall back to ink */
      if (!validCount) {
        const ink = new THREE.Color(INK);
        points.forEach((_, i) => write(i, ink));
        return { colors, legend: [], scale: null };
      }
      const span = max - min;
      values.forEach((value, i) => {
        if (Number.isNaN(value)) {
          write(i, new THREE.Color(MUTED));
          return;
        }
        write(i, sampleStops(stops, span > 0 ? (value - min) / span : 1));
      });
      const format = (value: number) =>
        colorMode === "priority" ? String(value) : new Date(value).toLocaleDateString();
      return {
        colors,
        legend: [],
        scale: { stops, minLabel: format(min), maxLabel: format(max) },
      };
    }

    const keyOf = (p: EmbeddingPoint): { key: string; label: string } => {
      if (colorMode === "type") {
        const isImage = p.is_image === true || p.content_type === "image";
        return isImage ? { key: "image", label: t("Image") } : { key: "text", label: t("Text") };
      }
      if (colorMode === "document") {
        const id = String(p.document_id ?? "");
        if (!id) return { key: NA_KEY, label: t("Unknown") };
        return { key: id, label: p.document_title || id };
      }
      if (colorMode === "source") {
        const src = String(p.source ?? "").trim();
        return src ? { key: src, label: src } : { key: NA_KEY, label: t("Unknown") };
      }
      // Tags are hierarchical ("tech/llm"); bucketing on the top level keeps the legend short
      const tag = Array.isArray(p.tags) && p.tags.length ? String(p.tags[0]).split("/")[0].trim() : "";
      return tag ? { key: tag, label: tag } : { key: NA_KEY, label: t("Untagged") };
    };

    const buckets = new Map<string, { label: string; count: number }>();
    const pointKeys = points.map((p) => {
      const { key, label } = keyOf(p);
      const bucket = buckets.get(key);
      if (bucket) bucket.count += 1;
      else buckets.set(key, { label, count: 1 });
      return key;
    });

    const named = [...buckets.entries()]
      .filter(([key]) => key !== NA_KEY)
      .sort((a, b) => b[1].count - a[1].count);
    const legend: LegendItem[] = [];
    const keyToLegend = new Map<string, { legendKey: string; color: string }>();

    named.slice(0, MAX_CATEGORIES).forEach(([key, bucket], i) => {
      const color = CATEGORY_COLORS[i % CATEGORY_COLORS.length];
      keyToLegend.set(key, { legendKey: key, color });
      legend.push({ key, label: bucket.label, color, count: bucket.count });
    });

    const overflow = named.slice(MAX_CATEGORIES);
    if (overflow.length) {
      let count = 0;
      overflow.forEach(([key, bucket]) => {
        keyToLegend.set(key, { legendKey: OVERFLOW_KEY, color: OVERFLOW });
        count += bucket.count;
      });
      legend.push({ key: OVERFLOW_KEY, label: t("Other"), color: OVERFLOW, count });
    }

    const na = buckets.get(NA_KEY);
    if (na) {
      keyToLegend.set(NA_KEY, { legendKey: NA_KEY, color: MUTED });
      legend.push({ key: NA_KEY, label: na.label, color: MUTED, count: na.count });
    }

    /* A focused bucket can vanish when the data reloads; ignoring it beats fading every point */
    const activeFocus = legend.some((item) => item.key === focusKey) ? focusKey : null;
    pointKeys.forEach((key, i) => {
      const entry = keyToLegend.get(key);
      const color = new THREE.Color(entry?.color ?? MUTED);
      const dimmed = activeFocus !== null && entry?.legendKey !== activeFocus;
      write(i, dimmed ? fadeToPaper(color, 0.84) : color);
    });

    return { colors, legend, scale: null };
  }, [points, colorMode, focusKey, t]);

  colorsRef.current = view.colors;

  /* Recolor in place: rebuilding the scene here would reset the camera the user just positioned */
  useEffect(() => {
    const geometry = geometryRef.current;
    const attribute = geometry?.getAttribute("color") as THREE.BufferAttribute | undefined;
    if (!attribute || attribute.array.length !== view.colors.length) return;
    (attribute.array as Float32Array).set(view.colors);
    attribute.needsUpdate = true;
  }, [view]);

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

    const dotTexture = createDotTexture();

    // Point cloud: BufferGeometry + vertexColors, one draw call handles thousands of points
    const positions = new Float32Array(points.length * 3);
    points.forEach((p, i) => {
      positions[i * 3] = Number(p.x);
      positions[i * 3 + 1] = Number(p.y);
      positions[i * 3 + 2] = Number(p.z);
    });
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    // Colors come from the color-mode pass; the ref keeps the current buffer without re-running this effect
    geometry.setAttribute("color", new THREE.BufferAttribute(Float32Array.from(colorsRef.current), 3));
    geometryRef.current = geometry;
    const material = new THREE.PointsMaterial({
      size: 0.06,
      map: dotTexture,
      vertexColors: true,
      sizeAttenuation: true,
      transparent: true,
      // Soft-edged sprites must not write depth, otherwise their fade rings punch holes in points behind
      depthWrite: false,
      opacity: 0.95,
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
      geometryRef.current = null;
      geometry.dispose();
      material.dispose();
      dotTexture.dispose();
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
        <Select
          small
          value={colorMode}
          options={colorModes}
          onChange={(next) => {
            setColorMode(next as ColorMode);
            setFocusKey(null);
          }}
          ariaLabel={t("Color by")}
        />
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
          {view.legend.length > 0 && (
            <div className="viz-legend">
              {view.legend.map((item) => (
                <button
                  type="button"
                  key={item.key}
                  className={`viz-legend-item${focusKey === item.key ? " active" : ""}`}
                  title={focusKey === item.key ? t("Show all") : item.label}
                  onClick={() => setFocusKey(focusKey === item.key ? null : item.key)}
                >
                  <i style={{ background: item.color }} />
                  <span className="viz-legend-label">{item.label}</span>
                  <span className="viz-legend-count">{item.count}</span>
                </button>
              ))}
            </div>
          )}
          {view.scale && (
            <div className="viz-legend viz-legend-scale">
              <span className="viz-legend-count">{view.scale.minLabel}</span>
              <i style={{ background: `linear-gradient(90deg, ${view.scale.stops.join(", ")})` }} />
              <span className="viz-legend-count">{view.scale.maxLabel}</span>
            </div>
          )}
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
