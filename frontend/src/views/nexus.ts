import * as d3 from "d3";
import type {
  GraphEdge,
  GraphNode,
  Project,
  ViewLifecycle,
} from "../types";
import { getCategoryColor } from "../palette";
import { hoverProject, selectProject, state } from "../state";
import { projectKey } from "../util";
import {
  hideTooltip,
  moveTooltip,
  showTooltip,
} from "../components/tooltip";

function computeEdges(projects: Project[]): GraphEdge[] {
  // Inverted index: concept → project keys
  const conceptIndex = new Map<string, Set<string>>();

  for (const p of projects) {
    const key = projectKey(p);
    const terms = [
      ...p.concepts,
      ...p.tags,
      ...p.mined_motifs,
    ];
    for (const term of terms) {
      const k = term.toLowerCase();
      if (!conceptIndex.has(k)) conceptIndex.set(k, new Set());
      conceptIndex.get(k)!.add(key);
    }
  }

  // Accumulate weighted edges
  const edgeMap = new Map<
    string,
    { weight: number; items: Set<string> }
  >();

  const edgeKey = (a: string, b: string) =>
    a < b ? `${a}|||${b}` : `${b}|||${a}`;

  for (const [concept, keys] of conceptIndex) {
    if (keys.size < 2 || keys.size > 30) continue;
    const arr = [...keys];
    for (let i = 0; i < arr.length; i++) {
      for (let j = i + 1; j < arr.length; j++) {
        const ek = edgeKey(arr[i], arr[j]);
        if (!edgeMap.has(ek)) {
          edgeMap.set(ek, { weight: 0, items: new Set() });
        }
        const entry = edgeMap.get(ek)!;
        entry.weight += 1;
        entry.items.add(concept);
      }
    }
  }

  // Motif neighbors (bonus weight)
  const keyByName = new Map<string, string>();
  for (const p of projects) {
    keyByName.set(p.name, projectKey(p));
  }
  for (const p of projects) {
    const pk = projectKey(p);
    for (const neighbor of p.motif_neighbors) {
      const nk = keyByName.get(neighbor.name);
      if (nk && nk !== pk) {
        const ek = edgeKey(pk, nk);
        if (!edgeMap.has(ek)) {
          edgeMap.set(ek, { weight: 0, items: new Set() });
        }
        edgeMap.get(ek)!.weight += 2;
      }
    }
  }

  // Filter and convert
  const edges: GraphEdge[] = [];
  for (const [ek, { weight, items }] of edgeMap) {
    if (weight < 2) continue;
    const [source, target] = ek.split("|||");
    edges.push({
      source,
      target,
      weight,
      sharedItems: [...items].slice(0, 8),
    });
  }

  edges.sort((a, b) => b.weight - a.weight);
  return edges;
}

export function createNexusView(): ViewLifecycle {
  let svg: d3.Selection<SVGSVGElement, unknown, null, undefined> | null = null;
  let simulation: d3.Simulation<GraphNode, GraphEdge> | null = null;
  let container: HTMLElement | null = null;
  let statusBar: HTMLElement | null = null;
  let hubCount = 60;
  let width = window.innerWidth;
  let height = window.innerHeight;
  const unsubs: (() => void)[] = [];

  function render(): void {
    if (!svg || !container) return;
    svg.selectAll("*").remove();

    const projects = state.filteredProjects;
    const totalCount = projects.length;

    // Build all nodes
    const allNodes: GraphNode[] = projects.map((p) => ({
      id: projectKey(p),
      label: p.name,
      project: p,
      category: p.category,
      attribution: p.attribution,
      r: 5,
      x: undefined,
      y: undefined,
    }));

    const nodeMap = new Map(allNodes.map((n) => [n.id, n]));
    const allEdges = computeEdges(projects).filter(
      (e) =>
        nodeMap.has(e.source as string) &&
        nodeMap.has(e.target as string),
    );

    // Compute degree for each node
    const degree = new Map<string, number>();
    for (const e of allEdges) {
      const s = e.source as string;
      const t = e.target as string;
      degree.set(s, (degree.get(s) || 0) + e.weight);
      degree.set(t, (degree.get(t) || 0) + e.weight);
    }

    // Sort by degree, take top N
    const sortedByDegree = [...degree.entries()].sort(
      (a, b) => b[1] - a[1],
    );
    const keepSet = new Set(
      sortedByDegree.slice(0, hubCount).map((d) => d[0]),
    );

    // Filter nodes and edges
    const nodes = allNodes.filter((n) => keepSet.has(n.id));
    const edges = allEdges.filter(
      (e) =>
        keepSet.has(e.source as string) &&
        keepSet.has(e.target as string),
    );

    // Compute category centroids from UMAP coords of all projects
    const catCentroids = new Map<
      string,
      { sx: number; sy: number; count: number }
    >();
    for (const p of projects) {
      if (p.umap_x == null || p.umap_y == null) continue;
      const c = catCentroids.get(p.category) || {
        sx: 0,
        sy: 0,
        count: 0,
      };
      c.sx += p.umap_x;
      c.sy += p.umap_y;
      c.count += 1;
      catCentroids.set(p.category, c);
    }
    const catCenter = new Map<string, { x: number; y: number }>();
    for (const [cat, { sx, sy, count }] of catCentroids) {
      catCenter.set(cat, {
        x: (sx / count) * width,
        y: (sy / count) * height,
      });
    }

    // Size nodes by degree
    const maxDegree = sortedByDegree.length
      ? sortedByDegree[0][1]
      : 1;
    for (const n of nodes) {
      const d = degree.get(n.id) || 0;
      n.r = 6 + (d / maxDegree) * 14;
      // Seed position near category centroid
      const cc = catCenter.get(n.category);
      if (cc) {
        n.x = cc.x + (Math.random() - 0.5) * 80;
        n.y = cc.y + (Math.random() - 0.5) * 80;
      } else {
        n.x = width / 2 + (Math.random() - 0.5) * 200;
        n.y = height / 2 + (Math.random() - 0.5) * 200;
      }
    }

    // Force simulation
    if (simulation) simulation.stop();
    simulation = d3
      .forceSimulation<GraphNode, GraphEdge>(nodes)
      .force(
        "charge",
        d3.forceManyBody<GraphNode>().strength(-120),
      )
      .force(
        "link",
        d3
          .forceLink<GraphNode, GraphEdge>(edges)
          .id((d) => d.id)
          .distance((d) => 80 / (d as GraphEdge).weight)
          .strength((d) =>
            Math.min(1, (d as GraphEdge).weight * 0.15),
          ),
      )
      .force(
        "center",
        d3.forceCenter(width / 2, height / 2),
      )
      .force(
        "collision",
        d3.forceCollide<GraphNode>().radius((d) => d.r + 8),
      )
      .force(
        "x",
        d3
          .forceX<GraphNode>()
          .x((d) => catCenter.get(d.category)?.x ?? width / 2)
          .strength(0.05),
      )
      .force(
        "y",
        d3
          .forceY<GraphNode>()
          .y((d) => catCenter.get(d.category)?.y ?? height / 2)
          .strength(0.05),
      )
      .alphaDecay(0.02);

    const g = svg.append("g");

    // Zoom
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 5])
      .on("zoom", (event) => {
        g.attr("transform", event.transform);
      });
    svg.call(zoom);

    // Edges
    const linkSel = g
      .append("g")
      .attr("class", "nexus-links")
      .selectAll<SVGLineElement, GraphEdge>("line")
      .data(edges)
      .join("line")
      .attr("stroke", (d) => {
        const a = Math.min(0.5, 0.08 + d.weight * 0.04);
        return `rgba(135, 193, 217, ${a})`;
      })
      .attr("stroke-width", (d) => Math.min(3, 0.5 + d.weight * 0.25));

    // Nodes
    const nodeSel = g
      .append("g")
      .attr("class", "nexus-nodes")
      .selectAll<SVGCircleElement, GraphNode>("circle")
      .data(nodes)
      .join("circle")
      .attr("r", (d) => d.r)
      .attr("fill", (d) => getCategoryColor(d.category))
      .attr("stroke", "rgba(250, 231, 186, 0.3)")
      .attr("stroke-width", 0.8)
      .attr("cursor", "pointer")
      .call(
        d3
          .drag<SVGCircleElement, GraphNode>()
          .on("start", (event, d) => {
            if (!event.active) simulation?.alphaTarget(0.3).restart();
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

    // Labels for all hub nodes
    const labelSel = g
      .append("g")
      .attr("class", "nexus-labels")
      .selectAll<SVGTextElement, GraphNode>("text")
      .data(nodes)
      .join("text")
      .attr("class", "label")
      .attr("font-size", (d) => `${Math.max(8, Math.min(12, d.r * 0.8))}px`)
      .attr("fill", "#d8e6ef")
      .attr("text-anchor", "start")
      .attr("dominant-baseline", "middle")
      .text((d) => d.label);

    // Build adjacency for hover highlighting
    const adjacency = new Map<string, Set<string>>();
    for (const e of edges) {
      const s = (e.source as GraphNode).id ?? (e.source as string);
      const t = (e.target as GraphNode).id ?? (e.target as string);
      if (!adjacency.has(s)) adjacency.set(s, new Set());
      if (!adjacency.has(t)) adjacency.set(t, new Set());
      adjacency.get(s)!.add(t);
      adjacency.get(t)!.add(s);
    }

    // Hover highlighting
    nodeSel
      .on("mouseover", (event: MouseEvent, d: GraphNode) => {
        hoverProject(d.id);
        const neighbors = adjacency.get(d.id) || new Set();

        nodeSel.classed("dimmed", (n) => n.id !== d.id && !neighbors.has(n.id));
        linkSel.classed("dimmed", (e) => {
          const s = (e.source as GraphNode).id;
          const t = (e.target as GraphNode).id;
          return s !== d.id && t !== d.id;
        });
        linkSel.classed("highlighted", (e) => {
          const s = (e.source as GraphNode).id;
          const t = (e.target as GraphNode).id;
          return s === d.id || t === d.id;
        });
        labelSel.classed("dimmed", (n) => n.id !== d.id && !neighbors.has(n.id));

        const deg = degree.get(d.id) || 0;
        showTooltip(
          event,
          d.project.name,
          `${d.project.category}\nConnections: ${deg}\n${d.project.description.slice(0, 100)}`,
        );
      })
      .on("mousemove", (event: MouseEvent) => moveTooltip(event))
      .on("mouseout", () => {
        hoverProject(null);
        nodeSel.classed("dimmed", false);
        linkSel.classed("dimmed", false).classed("highlighted", false);
        labelSel.classed("dimmed", false);
        hideTooltip();
      })
      .on("click", (_event: MouseEvent, d: GraphNode) => {
        selectProject(d.id);
      });

    // Tick
    simulation.on("tick", () => {
      linkSel
        .attr("x1", (d) => (d.source as GraphNode).x!)
        .attr("y1", (d) => (d.source as GraphNode).y!)
        .attr("x2", (d) => (d.target as GraphNode).x!)
        .attr("y2", (d) => (d.target as GraphNode).y!);

      nodeSel.attr("cx", (d) => d.x!).attr("cy", (d) => d.y!);

      labelSel
        .attr("x", (d) => d.x! + d.r + 3)
        .attr("y", (d) => d.y!);
    });

    // Update status bar text
    updateStatusText(nodes.length, totalCount, edges.length);
  }

  function updateStatusText(
    shown: number,
    total: number,
    edgeCount: number,
  ): void {
    const label = statusBar?.querySelector(".nexus-status-label");
    if (label) {
      label.textContent = `Showing ${shown} most-connected projects of ${total} \u00b7 ${edgeCount} relationships`;
    }
  }

  function createStatusBar(): void {
    if (!container) return;
    statusBar = document.createElement("div");
    statusBar.className = "nexus-status";
    statusBar.innerHTML = `
      <span class="nexus-status-label">Loading...</span>
      <input type="range" class="nexus-slider" min="20" max="120" value="${hubCount}" />
    `;
    container.appendChild(statusBar);

    const slider = statusBar.querySelector<HTMLInputElement>(".nexus-slider");
    slider?.addEventListener("input", () => {
      hubCount = parseInt(slider.value, 10);
      render();
    });
  }

  return {
    id: "nexus",

    mount(el: HTMLElement) {
      container = el;
      width = window.innerWidth;
      height = window.innerHeight;

      svg = d3
        .select(container)
        .append("svg")
        .attr("viewBox", `0 0 ${width} ${height}`)
        .attr("class", "nexus-svg");

      createStatusBar();

      const onResize = () => {
        width = window.innerWidth;
        height = window.innerHeight;
        svg?.attr("viewBox", `0 0 ${width} ${height}`);
      };
      window.addEventListener("resize", onResize);
      unsubs.push(() =>
        window.removeEventListener("resize", onResize),
      );
    },

    update() {
      render();
    },

    destroy() {
      if (simulation) {
        simulation.stop();
        simulation = null;
      }
      if (statusBar) {
        statusBar.remove();
        statusBar = null;
      }
      for (const unsub of unsubs) unsub();
      unsubs.length = 0;
      svg = null;
      container = null;
    },
  };
}
