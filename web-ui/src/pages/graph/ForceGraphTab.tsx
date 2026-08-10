/**
 * Background: The knowledge graph tab uses d3 force-directed layout to render
 * /api/knowledge-graph/graph-data nodes/edges; node click opens an entity detail side drawer.
 * Design intent: d3 handles SVG internal rendering and simulation, React manages data and drawer state;
 * node radius scales by connectivity degree, colors distinguish entity types (ink/purple/acid green cycling).
 * Selecting a node also pushes everything outside its direct neighbourhood into a muted palette,
 * so a single click answers "what is this entity wired to" without losing the graph as context.
 * Key constraint: Simulation must be stopped in effect cleanup to avoid background spinning after tab switch;
 * edges referencing missing nodes are discarded (backend data may be inconsistent);
 * data fetching and d3 rendering must be two separate effects—during loading the SVG isn't mounted,
 * operating svgRef directly in a fetch callback would only get null and silently render nothing.
 */
import { useEffect, useRef, useState } from "react";
import * as d3 from "d3";
import { IconX } from "@tabler/icons-react";
import { EmptyState, LoadingLine } from "../../components/EmptyState";
import { api, reportError } from "../../api";
import { useI18n } from "../../i18n";
import type { GraphEdge, GraphNode } from "../../types";

interface SimNode extends d3.SimulationNodeDatum {
  id: string;
  label: string;
  type: string;
  degree: number;
  raw: GraphNode;
}

interface SimLink extends d3.SimulationLinkDatum<SimNode> {
  relation: string;
}

const TYPE_COLORS = ["#151613", "#8b7cff", "#6d6d66", "#a2522e"];
const LINK_COLOR = "rgba(21,22,19,0.25)";
const LABEL_COLOR = "#151613";
/*
 * Focus/context palette: de-emphasised elements are recoloured towards the beige canvas rather
 * than made translucent. Fading ink-black nodes with opacity turns them into muddy grey smudges
 * and reads as a rendering glitch; a flat warm grey keeps them crisp while dropping them to a
 * background layer. The accent purple already belongs to the type palette, so reusing it for the
 * focused edges states "this is the path" instead of merely "this is darker".
 */
const ACCENT = "#8b7cff";
const MUTED_NODE_COLOR = "#d8d4ca";
const MUTED_LINK_COLOR = "rgba(21,22,19,0.07)";
const MUTED_LABEL_COLOR = "#b5b0a4";
const FOCUS_RING_GAP = 7;
const FADE_MS = 180;

export function ForceGraphTab() {
  const { t } = useI18n();
  const frameRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<SimNode | null>(null);
  const [data, setData] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null);
  const [mentions, setMentions] = useState<Array<{ document_id: string; document_title: string; summary: string }>>([]);
  // Bridge between React selection state and the d3 layer: the d3 effect only reruns on data change,
  // so it publishes a highlight applier instead of being re-created whenever selection changes
  const highlightRef = useRef<((id: string | null) => void) | null>(null);
  const selectedIdRef = useRef<string | null>(null);

  // After selecting an entity, fetch memories it appears in; drawer provides a navigable source list
  useEffect(() => {
    setMentions([]);
    if (!selected) return;
    const numericId = Number(String(selected.id).replace("entity_", ""));
    if (!Number.isFinite(numericId)) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api<{ trunks?: Array<{ document_id?: string; document_title?: string; summary?: string; content?: string }> }>(
          "GET", `/api/knowledge-graph/entities/${numericId}`);
        if (cancelled) return;
        const seen = new Set<string>();
        const list: Array<{ document_id: string; document_title: string; summary: string }> = [];
        for (const trunk of res?.trunks ?? []) {
          const docId = String(trunk.document_id ?? "");
          if (!docId || seen.has(docId)) continue;
          seen.add(docId);
          list.push({
            document_id: docId,
            document_title: String(trunk.document_title || docId),
            summary: String(trunk.summary || trunk.content || "").slice(0, 80),
          });
        }
        setMentions(list.slice(0, 6));
      } catch {
        // Failure to fetch source list doesn't affect the main drawer display
      }
    })();
    return () => { cancelled = true; };
  }, [selected]);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await api<{ nodes?: GraphNode[]; edges?: GraphEdge[]; links?: GraphEdge[] }>(
          "GET",
          "/api/knowledge-graph/graph-data",
        );
        if (!cancelled) {
          setData({ nodes: res?.nodes ?? [], edges: res?.edges ?? res?.links ?? [] });
        }
      } catch (err) {
        reportError(err, t("Unable to load the knowledge graph"));
        if (!cancelled) setData({ nodes: [], edges: [] });
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const nodesRaw = data?.nodes ?? [];
    const edgesRaw = data?.edges ?? [];
    if (nodesRaw.length === 0 || !svgRef.current) return;
    let simulation: d3.Simulation<SimNode, SimLink> | null = null;

    {
      const svg = d3.select(svgRef.current);
      svg.selectAll("*").remove();
      const width = frameRef.current?.clientWidth ?? 900;
      const height = frameRef.current?.clientHeight ?? 620;
      svg.attr("viewBox", `0 0 ${width} ${height}`);

      const typeIndex = new Map<string, number>();
      const nodes: SimNode[] = nodesRaw.map((n, i) => {
        const type = String(n.type ?? "entity");
        if (!typeIndex.has(type)) typeIndex.set(type, typeIndex.size);
        return { id: String(n.id ?? n.name ?? i), label: String(n.name ?? n.label ?? n.id ?? ""), type, degree: 0, raw: n };
      });
      const byId = new Map(nodes.map((n) => [n.id, n]));
      const links: SimLink[] = [];
      for (const e of edgesRaw) {
        const s = byId.get(String(e.source ?? ""));
        const tgt = byId.get(String(e.target ?? ""));
        if (!s || !tgt) continue;
        s.degree++;
        tgt.degree++;
        links.push({ source: s, target: tgt, relation: String(e.relation ?? e.label ?? "") });
      }

      /*
       * On touch, d3.drag and d3.zoom's single-finger pan compete for the same touch events:
       * if a finger lands on a node, panning the canvas turns into dragging the node.
       * Touch therefore keeps only zoom/pan and drops drag—dragging nodes is a desktop toy;
       * panning is the essential need for viewing graphs. Also enlarge nodes and labels—
       * desktop's 5px nodes are impossible to tap accurately with a fingertip.
       */
      const coarsePointer = window.matchMedia("(pointer: coarse)").matches;
      const radiusOf = (d: SimNode) => (coarsePointer ? 8 : 5) + Math.min(11, d.degree * 1.4);

      const container = svg.append("g");
      // Panning ends with a mouseup that the browser also reports as a click on the backdrop;
      // without this flag every pan would clear the current selection
      let panned = false;
      svg.call(
        d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.25, 4])
          .on("start", () => { panned = false; })
          .on("zoom", (event) => {
            container.attr("transform", event.transform);
            if (event.sourceEvent) panned = true;
          }),
      );
      svg.on("click", () => {
        if (panned) {
          panned = false;
          return;
        }
        setSelected(null);
      });

      let nodeDragged = false;

      const link = container.append("g").selectAll<SVGLineElement, SimLink>("line").data(links).join("line")
        .attr("stroke", LINK_COLOR).attr("stroke-width", 1);

      // Ring lives behind the nodes so it reads as a halo rather than a border drawn over the dot
      const focusRing = container.append("circle")
        .attr("fill", "none").attr("stroke", ACCENT).attr("stroke-width", 2)
        .attr("r", 0).attr("opacity", 0).style("pointer-events", "none");

      const fillOf = (d: SimNode) => TYPE_COLORS[(typeIndex.get(d.type) ?? 0) % TYPE_COLORS.length];

      const node = container.append("g").selectAll<SVGCircleElement, SimNode>("circle").data(nodes).join("circle")
        .attr("r", radiusOf)
        .attr("fill", fillOf)
        .attr("stroke", "#f1efe8").attr("stroke-width", 1.4)
        .style("cursor", "pointer")
        .on("click", (event, d) => {
          event.stopPropagation();
          // A drag also ends in a click on the node, which would otherwise toggle the highlight off
          if (nodeDragged) {
            nodeDragged = false;
            return;
          }
          setSelected((prev) => (prev?.id === d.id ? null : d));
        });

      node.append("title").text((d) => d.label);

      // Show all names for small graphs (when relation extraction hasn't produced edges yet, all degrees are 0—without names it's just a bunch of matte dots)
      const labeled = nodes.length <= 60 ? nodes : nodes.filter((d) => d.degree >= 1);
      const label = container.append("g").selectAll("text").data(labeled).join("text")
        .text((d) => d.label)
        .attr("font-family", "DM Mono, monospace").attr("font-size", coarsePointer ? 11 : 9.5)
        // Offset must clear the dot and the focus ring drawn around it: radius grows with degree,
        // so a fixed dx buries the names of exactly the well-connected entities that matter most
        .attr("fill", LABEL_COLOR).attr("dx", (d) => radiusOf(d) + FOCUS_RING_GAP + 3).attr("dy", 3);

      const neighbors = new Map<string, Set<string>>();
      for (const l of links) {
        const a = (l.source as SimNode).id;
        const b = (l.target as SimNode).id;
        if (!neighbors.has(a)) neighbors.set(a, new Set());
        if (!neighbors.has(b)) neighbors.set(b, new Set());
        neighbors.get(a)!.add(b);
        neighbors.get(b)!.add(a);
      }

      // The focused node's direct neighbourhood keeps its real colours; everything else recedes
      // towards the canvas so the graph's shape survives as context instead of collapsing
      let focusNode: SimNode | null = null;
      const touchesFocus = (l: SimLink, id: string) =>
        (l.source as SimNode).id === id || (l.target as SimNode).id === id;

      const applyHighlight = (id: string | null) => {
        focusNode = id ? nodes.find((n) => n.id === id) ?? null : null;
        if (!focusNode) {
          node.transition().duration(FADE_MS).attr("fill", fillOf);
          link.transition().duration(FADE_MS).attr("stroke", LINK_COLOR).attr("stroke-width", 1);
          label.transition().duration(FADE_MS).attr("fill", LABEL_COLOR);
          focusRing.transition().duration(FADE_MS).attr("opacity", 0).attr("r", 0);
          return;
        }
        const focusId = focusNode.id;
        const related = new Set(neighbors.get(focusId) ?? []);
        related.add(focusId);
        node.transition().duration(FADE_MS)
          .attr("fill", (d) => (related.has(d.id) ? fillOf(d) : MUTED_NODE_COLOR));
        link.transition().duration(FADE_MS)
          .attr("stroke", (d) => (touchesFocus(d, focusId) ? ACCENT : MUTED_LINK_COLOR))
          .attr("stroke-width", (d) => (touchesFocus(d, focusId) ? 1.6 : 1));
        label.transition().duration(FADE_MS)
          .attr("fill", (d) => (related.has(d.id) ? LABEL_COLOR : MUTED_LABEL_COLOR));
        focusRing
          .attr("cx", focusNode.x ?? 0)
          .attr("cy", focusNode.y ?? 0)
          .transition().duration(FADE_MS)
          .attr("opacity", 1)
          .attr("r", radiusOf(focusNode) + FOCUS_RING_GAP);
      };
      highlightRef.current = applyHighlight;

      if (!coarsePointer) {
        node.call(
          d3.drag<SVGCircleElement, SimNode>()
            .on("start", (event, d) => {
              if (!event.active) simulation?.alphaTarget(0.25).restart();
              nodeDragged = false;
              d.fx = d.x;
              d.fy = d.y;
            })
            .on("drag", (event, d) => {
              nodeDragged = true;
              d.fx = event.x;
              d.fy = event.y;
            })
            .on("end", (event, d) => {
              if (!event.active) simulation?.alphaTarget(0);
              d.fx = null;
              d.fy = null;
            }),
        );
      }

      simulation = d3.forceSimulation<SimNode>(nodes)
        .force("link", d3.forceLink<SimNode, SimLink>(links).id((d) => d.id).distance(80))
        .force("charge", d3.forceManyBody().strength(-160))
        .force("center", d3.forceCenter(width / 2, height / 2))
        .force("collide", d3.forceCollide(18))
        .on("tick", () => {
          link
            .attr("x1", (d) => (d.source as SimNode).x ?? 0)
            .attr("y1", (d) => (d.source as SimNode).y ?? 0)
            .attr("x2", (d) => (d.target as SimNode).x ?? 0)
            .attr("y2", (d) => (d.target as SimNode).y ?? 0);
          node.attr("cx", (d) => d.x ?? 0).attr("cy", (d) => d.y ?? 0);
          label.attr("x", (d) => d.x ?? 0).attr("y", (d) => d.y ?? 0);
          if (focusNode) focusRing.attr("cx", focusNode.x ?? 0).attr("cy", focusNode.y ?? 0);
        });

      applyHighlight(selectedIdRef.current);
    }

    return () => {
      simulation?.stop();
      highlightRef.current = null;
    };
  }, [data]);

  useEffect(() => {
    selectedIdRef.current = selected?.id ?? null;
    highlightRef.current?.(selectedIdRef.current);
  }, [selected, data]);

  if (loading) return <LoadingLine label={t("Loading")} />;
  if ((data?.nodes.length ?? 0) === 0) return <EmptyState message={t("No entities extracted yet")} />;

  return (
    <div className="viz-frame" ref={frameRef}>
      <svg ref={svgRef} />
      {selected && (
        <div className="entity-drawer">
          <div className="panel-head">
            <span className="kicker">{t("Entity")}</span>
            <button type="button" onClick={() => setSelected(null)} aria-label={t("Close")}>
              <IconX size={15} stroke={1.5} />
            </button>
          </div>
          <div className="panel-body" style={{ display: "grid", gap: 8 }}>
            <strong style={{ fontSize: 16 }}>{selected.label}</strong>
            <span className="mono-sm muted">{t("Type")}: {selected.type}</span>
            <span className="mono-sm muted">{t("Connections")}: {selected.degree}</span>
            {Object.entries(selected.raw)
              .filter(([k, v]) => !["id", "name", "label", "type", "x", "y"].includes(k) && (typeof v === "string" || typeof v === "number"))
              .slice(0, 8)
              .map(([k, v]) => (
                <span key={k} className="mono-sm muted">{k}: {String(v)}</span>
              ))}
            {mentions.length > 0 && (
              <>
                <span className="kicker" style={{ marginTop: 6 }}>{t("Appears in")}</span>
                {mentions.map((m) => (
                  <a key={m.document_id} className="entity-mention" href={`/view/${m.document_id}`}
                     target="_blank" rel="noreferrer">
                    <strong>{m.document_title}</strong>
                    {m.summary && <span className="mono-sm muted">{m.summary}</span>}
                  </a>
                ))}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
