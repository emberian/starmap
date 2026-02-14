import type { Project } from "../types";
import { bus, getProjectByKey, selectProject, state } from "../state";
import { escapeHtml } from "../util";

let panel: HTMLElement | null = null;

const CONTENT_VIEWS = new Set(["atlas", "codex"]);

export function initDetails(parent: HTMLElement): HTMLElement {
  panel = document.createElement("aside");
  panel.className = "panel details";
  panel.id = "detailsPanel";
  parent.appendChild(panel);

  bus.on("project:selected", ({ id }) => {
    if (id) {
      const project = getProjectByKey(id);
      if (project) {
        renderProject(project);
        panel?.classList.add("details--open");
        return;
      }
    }
    panel?.classList.remove("details--open");
    renderOverview();
  });

  bus.on("data:loaded", () => {
    if (state.selectedProjectId) {
      const project = getProjectByKey(state.selectedProjectId);
      if (project) {
        renderProject(project);
        panel?.classList.add("details--open");
        return;
      }
    }
    renderOverview();
  });

  bus.on("view:switched", () => {
    // In content views, hide panel unless a project is actively selected
    if (CONTENT_VIEWS.has(state.activeView) && !state.selectedProjectId) {
      panel?.classList.remove("details--open");
    }
  });

  renderOverview();
  return panel;
}

function makeCloseButton(): string {
  return `<button class="details-close" aria-label="Close">\u00d7</button>`;
}

function attachCloseHandler(): void {
  const btn = panel?.querySelector(".details-close");
  btn?.addEventListener("click", () => selectProject(null));
}

function makePills(arr: string[], limit: number): string {
  return [...new Set(arr)]
    .slice(0, limit)
    .map((c) => `<span class="pill">${escapeHtml(c)}</span>`)
    .join("");
}

function renderProject(p: Project): void {
  if (!panel) return;

  const conceptPills = makePills(
    [...p.concepts, ...p.tags],
    12,
  );
  const minedPills = makePills(p.mined_motifs, 10);
  const metaconstellationPills = p.metaconstellations
    .slice(0, 8)
    .map((m) => `<span class="pill">${escapeHtml(m.name)}</span>`)
    .join("");
  const langPills = makePills(p.languages, 8);

  const rawCategory = p.raw_category || p.category;
  const hasRawCategory =
    rawCategory && rawCategory !== p.category;
  const rawCategoryLine = hasRawCategory
    ? `<p class="meta-line">Raw category: ${escapeHtml(rawCategory)}</p>`
    : "";

  const neighborItems = (p.motif_neighbors || [])
    .slice(0, 6)
    .map((n) => {
      const sim =
        n.similarity != null
          ? ` \u00b7 sim ${n.similarity.toFixed(3)}`
          : "";
      const path = n.path
        ? ` <span style="color:#93a7b6">${escapeHtml(n.path)}</span>`
        : "";
      return `<li>${escapeHtml(n.name)}${sim}${path}</li>`;
    })
    .join("");

  const embeddingMeta = p.embedding
    ? `${escapeHtml(p.embedding.model || "embedding")} \u00b7 dim ${escapeHtml(String(p.embedding.dim ?? "?"))}`
    : "No embedding metadata";

  const attrMap: Record<string, string> = {
    "my-projects": "Ember Original",
    contributed: "Contributed",
    interests: "Workspace Interest",
  };
  const attrLabel = attrMap[p.attribution] || p.attribution;

  panel.innerHTML = `
    ${makeCloseButton()}
    <h2>${escapeHtml(p.name)}</h2>
    <p>${escapeHtml(p.description)}</p>
    <h3>${escapeHtml(p.category)} \u00b7 <span style="color:var(--accent-gold)">${escapeHtml(attrLabel)}</span></h3>
    ${rawCategoryLine}
    <div class="pill-row">${langPills || '<span class="pill">Language unknown</span>'}</div>
    <h3>Motifs</h3>
    <div class="pill-row">${conceptPills || '<span class="pill">No motifs extracted</span>'}</div>
    <h3>Mined Motifs</h3>
    <div class="pill-row">${minedPills || '<span class="pill">No mined motifs</span>'}</div>
    <h3>Metaconstellations</h3>
    <div class="pill-row">${metaconstellationPills || '<span class="pill">Unassigned</span>'}</div>
    <h3>Semantic Neighbors</h3>
    <ul>${neighborItems || "<li>No semantic neighbors yet</li>"}</ul>
    <p class="meta-line">${embeddingMeta}</p>
    <p class="meta-line">${escapeHtml(p.display_path || "")}</p>
  `;
  attachCloseHandler();
}

function renderOverview(): void {
  if (!panel) return;

  const interests = state.interests;
  if (!interests) {
    panel.innerHTML = `<h2>Atlas Initializing</h2><p>Loading project data...</p>`;
    return;
  }

  const topCats = interests.top_categories
    .slice(0, 4)
    .map((c) => `${escapeHtml(c.name)} (${c.count})`)
    .join(" \u2022 ");
  const concepts = makePills(
    interests.top_concepts.slice(0, 6).map((c) => `${c.name} \u00b7 ${c.count}`),
    6,
  );
  const mined = makePills(
    interests.top_mined_motifs.slice(0, 6).map((c) => `${c.name} \u00b7 ${c.count}`),
    6,
  );
  const metas = makePills(
    interests.top_metaconstellations.slice(0, 6).map((c) => `${c.name} \u00b7 ${c.count}`),
    6,
  );
  const langs = makePills(
    interests.top_languages.slice(0, 5).map((l) => `${l.name} \u00b7 ${l.count}`),
    5,
  );
  const embed = interests.embedding_coverage;

  panel.innerHTML = `
    <h2>Interest Profile</h2>
    <p>The atlas maps <strong>${state.filteredProjects.length}</strong> visible projects. Select a star to inspect its narrative.</p>
    <h3>Dominant Constellations</h3>
    <p>${topCats || "No categories yet"}</p>
    <h3>Recurring Concepts</h3>
    <div class="pill-row">${concepts || '<span class="pill">No concepts detected</span>'}</div>
    <h3>Mined Motifs</h3>
    <div class="pill-row">${mined || '<span class="pill">No mined motifs yet</span>'}</div>
    <h3>Metaconstellations</h3>
    <div class="pill-row">${metas || '<span class="pill">No metaconstellations yet</span>'}</div>
    <h3>Language Weight</h3>
    <div class="pill-row">${langs || '<span class="pill">No language signals</span>'}</div>
    <p class="meta-line">Embeddings: ${embed.ready}/${embed.total} (${embed.pct}%). Metaconstellations: ${state.metaconstellations.length}.</p>
  `;
}
