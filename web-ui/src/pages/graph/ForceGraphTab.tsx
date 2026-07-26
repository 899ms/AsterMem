/**
 * Background: The knowledge graph tab uses d3 force-directed layout to render
 * /api/knowledge-graph/graph-data nodes/edges; node click opens an entity detail side drawer.
 * Design intent: d3 handles SVG internal rendering and simulation, React manages data and drawer state;
 * node radius scales by connectivity degree, colors distinguish entity types (ink/purple/acid green cycling).
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

export function ForceGraphTab() {
  const { t } = useI18n();
  const frameRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<SimNode | null>(null);
  const [data, setData] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null);
  const [mentions, setMentions] = useState<Array<{ document_id: string; document_title: string; summary: string }>>([]);

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

      const container = svg.append("g");
      svg.call(
        d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.25, 4]).on("zoom", (event) => {
          container.attr("transform", event.transform);
        }),
      );

      const link = container.append("g").selectAll("line").data(links).join("line")
        .attr("stroke", "rgba(21,22,19,0.25)").attr("stroke-width", 1);

      const node = container.append("g").selectAll<SVGCircleElement, SimNode>("circle").data(nodes).join("circle")
        .attr("r", (d) => (coarsePointer ? 8 : 5) + Math.min(11, d.degree * 1.4))
        .attr("fill", (d) => TYPE_COLORS[(typeIndex.get(d.type) ?? 0) % TYPE_COLORS.length])
        .attr("stroke", "#f1efe8").attr("stroke-width", 1.4)
        .style("cursor", "pointer")
        .on("click", (_, d) => setSelected(d));

      node.append("title").text((d) => d.label);

      // Show all names for small graphs (when relation extraction hasn't produced edges yet, all degrees are 0—without names it's just a bunch of matte dots)
      const labeled = nodes.length <= 60 ? nodes : nodes.filter((d) => d.degree >= 1);
      const label = container.append("g").selectAll("text").data(labeled).join("text")
        .text((d) => d.label)
        .attr("font-family", "DM Mono, monospace").attr("font-size", coarsePointer ? 11 : 9.5)
        .attr("fill", "#151613").attr("dx", 10).attr("dy", 3);

      if (!coarsePointer) {
        node.call(
          d3.drag<SVGCircleElement, SimNode>()
            .on("start", (event, d) => {
              if (!event.active) simulation?.alphaTarget(0.25).restart();
              d.fx = d.x;
              d.fy = d.y;
            })
            .on("drag", (event, d) => {
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
        });
    }

    return () => {
      simulation?.stop();
    };
  }, [data]);

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
