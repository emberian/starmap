import * as d3 from "d3";
import type {
  CategoryCentroid,
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
import { setLegendHoverCallback } from "../components/legend";

export function createCosmosView(): ViewLifecycle {
  let svg: d3.Selection<SVGSVGElement, unknown, null, undefined> | null = null;
  let g: d3.Selection<SVGGElement, unknown, null, undefined> | null = null;
  let zoom: d3.ZoomBehavior<SVGSVGElement, unknown> | null = null;
  let width = window.innerWidth;
  let height = window.innerHeight;
  const unsubs: (() => void)[] = [];

  function buildNodes(projects: Project[]): GraphNode[] {
    return projects.map((project) => {
      const id = projectKey(project);
      const r =
        5 +
        Math.min(
          7,
          (project.concepts.length +
            project.tags.length +
            project.mined_motifs.length) *
            0.3,
        );
      return {
        id,
        label: project.name,
        project,
        category: project.category,
        attribution: project.attribution,
        r,
      };
    });
  }

  function computeCentroids(
    nodes: GraphNode[],
    xScale: d3.ScaleLinear<number, number>,
    yScale: d3.ScaleLinear<number, number>,
  ): CategoryCentroid[] {
    const groups = new Map<
      string,
      { xs: number[]; ys: number[] }
    >();

    for (const node of nodes) {
      const p = node.project;
      if (p.umap_x == null || p.umap_y == null) continue;
      const cat = p.category || "Exploratory";
      if (!groups.has(cat)) groups.set(cat, { xs: [], ys: [] });
      const grp = groups.get(cat)!;
      grp.xs.push(xScale(p.umap_x));
      grp.ys.push(yScale(p.umap_y));
    }

    const centroids: CategoryCentroid[] = [];
    for (const [cat, { xs, ys }] of groups) {
      const n = xs.length;
      if (n < 3) continue; // Hide labels for tiny clusters
      const cx = xs.reduce((a, b) => a + b) / n;
      const cy = ys.reduce((a, b) => a + b) / n;
      const sx = Math.sqrt(
        xs.reduce((s, x) => s + (x - cx) ** 2, 0) / n,
      );
      const sy = Math.sqrt(
        ys.reduce((s, y) => s + (y - cy) ** 2, 0) / n,
      );
      centroids.push({
        category: cat,
        color: getCategoryColor(cat),
        x: cx,
        y: cy,
        count: n,
        spread: Math.sqrt(sx * sx + sy * sy),
      });
    }

    // Simple collision avoidance
    for (let iter = 0; iter < 10; iter++) {
      for (let a = 0; a < centroids.length; a++) {
        for (let b = a + 1; b < centroids.length; b++) {
          const dx = centroids[b].x - centroids[a].x;
          const dy = centroids[b].y - centroids[a].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const minDist = 70;
          if (dist < minDist && dist > 0) {
            const nudge = (minDist - dist) / 2;
            const nx = (dx / dist) * nudge;
            const ny = (dy / dist) * nudge;
            centroids[a].x -= nx;
            centroids[a].y -= ny;
            centroids[b].x += nx;
            centroids[b].y += ny;
          }
        }
      }
    }

    return centroids;
  }

  function buildNeighborIndex(
    nodes: GraphNode[],
  ): Map<string, Set<string>> {
    const neighbors = new Map<string, Set<string>>();
    for (const n of nodes) {
      neighbors.set(n.id, new Set([n.id]));
    }

    // Concept index
    const conceptIndex = new Map<string, string[]>();
    for (const n of nodes) {
      const allConcepts = [
        ...n.project.concepts,
        ...n.project.tags,
        ...n.project.mined_motifs,
      ];
      for (const c of allConcepts) {
        const key = c.toLowerCase();
        if (!conceptIndex.has(key)) conceptIndex.set(key, []);
        conceptIndex.get(key)!.push(n.id);
      }
    }

    // Motif neighbors (O(n) via name→id map)
    const nameToId = new Map<string, string>();
    for (const n of nodes) {
      nameToId.set(n.project.name, n.id);
    }
    for (const n of nodes) {
      for (const neighbor of n.project.motif_neighbors) {
        const otherId = nameToId.get(neighbor.name);
        if (otherId && otherId !== n.id) {
          neighbors.get(n.id)!.add(otherId);
          neighbors.get(otherId)!.add(n.id);
        }
      }
    }

    // Connect via shared concepts (cap at 20)
    for (const [, ids] of conceptIndex) {
      if (ids.length < 2 || ids.length > 20) continue;
      for (const a of ids) {
        for (const b of ids) {
          if (a !== b) {
            neighbors.get(a)?.add(b);
            neighbors.get(b)?.add(a);
          }
        }
      }
    }

    return neighbors;
  }

  function updateLabelVisibility(k: number): void {
    if (!g) return;
    g.select(".label-layer")
      .selectAll<SVGTextElement, GraphNode>("text")
      .attr("visibility", (d) => {
        if (d.landmark) return "visible";
        if (k >= 2.0) return "visible";
        if (k >= 1.2 && d.r >= 7) return "visible";
        return "hidden";
      });
  }

  function render(): void {
    if (!svg || !g) return;

    const projects = state.filteredProjects;
    const nodes = buildNodes(projects);
    // nodes used locally for this render pass

    const padding = 80;
    const xScale = d3
      .scaleLinear()
      .domain([0, 1])
      .range([padding, width - padding]);
    const yScale = d3
      .scaleLinear()
      .domain([0, 1])
      .range([padding, height - padding]);

    for (const node of nodes) {
      const p = node.project;
      if (p.umap_x != null && p.umap_y != null) {
        node.x = xScale(p.umap_x);
        node.y = yScale(p.umap_y);
      } else {
        node.x =
          width / 2 + (Math.random() - 0.5) * 200;
        node.y =
          height / 2 + (Math.random() - 0.5) * 200;
      }
    }

    // Mark landmarks
    const sorted = [...nodes].sort((a, b) => b.r - a.r);
    sorted.slice(0, 12).forEach((n) => {
      n.landmark = true;
    });

    const neighborIndex = buildNeighborIndex(nodes);
    const centroids = computeCentroids(nodes, xScale, yScale);

    // Node layer
    const nodeLayer = g.select<SVGGElement>(".node-layer");
    nodeLayer
      .selectAll<SVGCircleElement, GraphNode>("circle")
      .data(nodes, (d) => d.id)
      .join(
        (enter) =>
          enter
            .append("circle")
            .attr("cx", (d) => d.x!)
            .attr("cy", (d) => d.y!)
            .attr("r", 0)
            .attr("opacity", 0)
            .call((e) =>
              e
                .transition()
                .duration(500)
                .attr("r", (d) => d.r)
                .attr("opacity", 1),
            ),
        (update) =>
          update.call((u) =>
            u
              .transition()
              .duration(500)
              .attr("cx", (d) => d.x!)
              .attr("cy", (d) => d.y!)
              .attr("r", (d) => d.r),
          ),
        (exit) =>
          exit.call((e) =>
            e
              .transition()
              .duration(300)
              .attr("r", 0)
              .attr("opacity", 0)
              .remove(),
          ),
      )
      .attr(
        "class",
        (d) => `node project ${d.attribution || ""}`.trim(),
      )
      .attr("fill", (d) => getCategoryColor(d.category))
      .attr("stroke", (d) =>
        d.attribution === "my-projects"
          ? "var(--accent-gold)"
          : "rgba(250, 231, 186, 0.35)",
      )
      .attr("stroke-width", (d) =>
        d.attribution === "my-projects" ? 1.5 : 0.8,
      )
      .attr("filter", "url(#star-glow)");

    // Label layer
    const labelLayer = g.select<SVGGElement>(".label-layer");
    labelLayer
      .selectAll<SVGTextElement, GraphNode>("text")
      .data(nodes, (d) => d.id)
      .join(
        (enter) =>
          enter
            .append("text")
            .attr("x", (d) => d.x! + d.r + 4)
            .attr("y", (d) => d.y! + 3)
            .attr("opacity", 0)
            .call((e) =>
              e.transition().duration(500).attr("opacity", 0.9),
            ),
        (update) =>
          update.call((u) =>
            u
              .transition()
              .duration(500)
              .attr("x", (d) => d.x! + d.r + 4)
              .attr("y", (d) => d.y! + 3),
          ),
        (exit) =>
          exit.call((e) =>
            e
              .transition()
              .duration(300)
              .attr("opacity", 0)
              .remove(),
          ),
      )
      .attr("class", (d) =>
        d.landmark ? "label landmark" : "label",
      )
      .text((d) => d.label);

    // Centroid labels
    const centroidLayer = g.select<SVGGElement>(".centroid-layer");
    centroidLayer
      .selectAll<SVGTextElement, CategoryCentroid>("text")
      .data(centroids, (d) => d.category)
      .join(
        (enter) =>
          enter
            .append("text")
            .attr("x", (d) => d.x)
            .attr("y", (d) => d.y)
            .attr("opacity", 0)
            .call((e) =>
              e.transition().duration(800).attr("opacity", 0.6),
            ),
        (update) =>
          update.call((u) =>
            u
              .transition()
              .duration(500)
              .attr("x", (d) => d.x)
              .attr("y", (d) => d.y),
          ),
        (exit) =>
          exit.call((e) =>
            e
              .transition()
              .duration(300)
              .attr("opacity", 0)
              .remove(),
          ),
      )
      .attr("class", "centroid-label")
      .attr("text-anchor", "middle")
      .attr("fill", (d) => d.color)
      .attr(
        "font-size",
        (d) => `${Math.max(11, Math.min(16, 10 + d.count * 0.08))}px`,
      )
      .text((d) => d.category)
      .attr("pointer-events", "none");

    // Zoom level
    const currentTransform = d3.zoomTransform(svg.node()!);
    updateLabelVisibility(currentTransform.k);

    // Event handlers
    const onHover = (event: MouseEvent, d: GraphNode) => {
      hoverProject(d.id);
      applyHighlight(d.id, neighborIndex);
      showTooltip(
        event,
        d.project.name,
        `${d.project.category}\n${d.project.display_path}`,
      );
    };
    const onMove = (event: MouseEvent) => {
      moveTooltip(event);
    };
    const onOut = () => {
      hoverProject(null);
      hideTooltip();
      applyHighlight(state.selectedProjectId, neighborIndex);
    };
    const onClick = (event: MouseEvent, d: GraphNode) => {
      event.stopPropagation();
      if (state.selectedProjectId === d.id) {
        selectProject(null);
        applyHighlight(null, neighborIndex);
      } else {
        selectProject(d.id);
        applyHighlight(d.id, neighborIndex);
      }
    };

    nodeLayer
      .selectAll<SVGCircleElement, GraphNode>("circle")
      .on("mouseover", onHover as never)
      .on("mousemove", onMove as never)
      .on("mouseout", onOut)
      .on("click", onClick as never);

    labelLayer
      .selectAll<SVGTextElement, GraphNode>("text")
      .on("mouseover", onHover as never)
      .on("mousemove", onMove as never)
      .on("mouseout", onOut)
      .on("click", onClick as never);

    // Click on background to deselect
    svg.on("click", (event: MouseEvent) => {
      const target = event.target as Element;
      if (target.tagName === "svg" || target.tagName === "g") {
        selectProject(null);
        applyHighlight(null, neighborIndex);
      }
    });

    applyHighlight(state.selectedProjectId, neighborIndex);
  }

  function applyHighlight(
    centerId: string | null,
    neighborIndex: Map<string, Set<string>>,
  ): void {
    if (!g) return;
    const nodeCircles = g
      .select(".node-layer")
      .selectAll<SVGCircleElement, GraphNode>("circle");
    const labels = g
      .select(".label-layer")
      .selectAll<SVGTextElement, GraphNode>("text");

    if (!centerId || !neighborIndex.has(centerId)) {
      nodeCircles.attr("opacity", 1).classed("selected", false);
      labels.attr("opacity", 0.9);
      if (svg) {
        const k = d3.zoomTransform(svg.node()!).k;
        updateLabelVisibility(k);
      }
      return;
    }

    const active = neighborIndex.get(centerId)!;
    nodeCircles
      .classed("selected", (d) => d.id === centerId)
      .attr("opacity", (d) => (active.has(d.id) ? 1 : 0.12));
    labels
      .attr("opacity", (d) => (active.has(d.id) ? 1 : 0.08))
      .attr("visibility", (d) =>
        active.has(d.id) ? "visible" : d.landmark ? "visible" : "hidden",
      );
  }

  function highlightCategory(category: string | null): void {
    if (!g) return;
    const nodeCircles = g
      .select(".node-layer")
      .selectAll<SVGCircleElement, GraphNode>("circle");
    const labels = g
      .select(".label-layer")
      .selectAll<SVGTextElement, GraphNode>("text");
    const centroids = g
      .select(".centroid-layer")
      .selectAll<SVGTextElement, CategoryCentroid>("text");

    if (!category) {
      nodeCircles.attr("opacity", 1);
      labels.attr("opacity", 0.9);
      centroids.attr("opacity", 0.6);
      return;
    }

    nodeCircles.attr("opacity", (d) =>
      d.category === category ? 1 : 0.08,
    );
    labels.attr("opacity", (d) =>
      d.category === category ? 1 : 0.06,
    );
    centroids.attr("opacity", (d) =>
      d.category === category ? 1 : 0.15,
    );
  }

  function recenter(): void {
    if (!svg || !zoom) return;
    const transform = d3.zoomIdentity
      .translate(width * 0.5, height * 0.5)
      .scale(0.86)
      .translate(-width * 0.5, -height * 0.5);
    svg.transition().duration(650).call(zoom.transform, transform);
  }

  return {
    id: "cosmos",

    mount(container: HTMLElement) {
      width = window.innerWidth;
      height = window.innerHeight;

      svg = d3
        .select(container)
        .append("svg")
        .attr("viewBox", `0 0 ${width} ${height}`)
        .attr("preserveAspectRatio", "xMidYMid slice")
        .attr("class", "cosmos-svg");

      // Defs
      const defs = svg.append("defs");
      const glow = defs
        .append("filter")
        .attr("id", "star-glow")
        .attr("x", "-50%")
        .attr("y", "-50%")
        .attr("width", "200%")
        .attr("height", "200%");
      glow
        .append("feGaussianBlur")
        .attr("stdDeviation", "2.8")
        .attr("result", "blur");
      glow
        .append("feMerge")
        .selectAll("feMergeNode")
        .data(["blur", "SourceGraphic"])
        .join("feMergeNode")
        .attr("in", (d) => d);

      g = svg.append("g");
      g.append("g").attr("class", "centroid-layer");
      g.append("g").attr("class", "node-layer");
      g.append("g").attr("class", "label-layer");

      zoom = d3
        .zoom<SVGSVGElement, unknown>()
        .scaleExtent([0.35, 6])
        .on("zoom", (event) => {
          g!.attr("transform", event.transform);
          updateLabelVisibility(event.transform.k);
        });

      svg.call(zoom);
      recenter();

      // Legend hover → highlight category
      setLegendHoverCallback(highlightCategory);

      // Resize handler
      const onResize = () => {
        width = window.innerWidth;
        height = window.innerHeight;
        svg?.attr("viewBox", `0 0 ${width} ${height}`);
        render();
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
      for (const unsub of unsubs) unsub();
      unsubs.length = 0;
      setLegendHoverCallback(() => {});
      svg = null;
      g = null;
      zoom = null;
    },
  };
}
