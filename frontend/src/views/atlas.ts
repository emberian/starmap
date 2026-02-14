import type { Project, ViewLifecycle } from "../types";
import { TAXONOMY_CATEGORIES } from "../palette";
import { getCategoryColor } from "../palette";
import { selectProject, state } from "../state";
import { escapeHtml, projectKey, truncate } from "../util";

export function createAtlasView(): ViewLifecycle {
  let root: HTMLElement | null = null;
  const collapsedCategories = new Set<string>();

  function render(): void {
    if (!root) return;

    const projects = state.filteredProjects;

    // Group by category
    const groups = new Map<string, Project[]>();
    for (const p of projects) {
      const cat = p.category || "Exploratory";
      if (!groups.has(cat)) groups.set(cat, []);
      groups.get(cat)!.push(p);
    }

    // Order by taxonomy
    const orderedCats = TAXONOMY_CATEGORIES.filter((c) =>
      groups.has(c),
    );

    let html = '<div class="atlas-grid">';

    for (const cat of orderedCats) {
      const catProjects = groups.get(cat)!;
      const color = getCategoryColor(cat);
      const collapsed = collapsedCategories.has(cat);

      html += `
        <div class="atlas-category" data-category="${escapeHtml(cat)}">
          <div class="atlas-category-header" data-category="${escapeHtml(cat)}">
            <span class="atlas-category-dot" style="background:${color}"></span>
            <span class="atlas-category-name">${escapeHtml(cat)}</span>
            <span class="atlas-category-count">${catProjects.length}</span>
            <span class="atlas-category-toggle">${collapsed ? "\u25b6" : "\u25bc"}</span>
          </div>
          ${
            collapsed
              ? ""
              : `<div class="atlas-cards">${catProjects
                  .map((p) => renderCard(p, color))
                  .join("")}</div>`
          }
        </div>
      `;
    }

    html += "</div>";
    root.innerHTML = html;

    // Event handlers
    root.querySelectorAll<HTMLElement>(".atlas-category-header").forEach(
      (el) => {
        el.addEventListener("click", () => {
          const cat = el.dataset.category!;
          if (collapsedCategories.has(cat)) {
            collapsedCategories.delete(cat);
          } else {
            collapsedCategories.add(cat);
          }
          render();
        });
      },
    );

    root.querySelectorAll<HTMLElement>(".atlas-card").forEach(
      (el) => {
        el.addEventListener("click", () => {
          const key = el.dataset.key!;
          selectProject(key);
        });
      },
    );
  }

  function renderCard(p: Project, _color: string): string {
    const key = projectKey(p);
    const tags = p.tags
      .slice(0, 5)
      .map((t) => `<span class="pill">${escapeHtml(t)}</span>`)
      .join("");
    const langs = p.languages
      .slice(0, 3)
      .map((l) => `<span class="atlas-lang">${escapeHtml(l)}</span>`)
      .join("");

    const attrMap: Record<string, string> = {
      "my-projects": "own",
      contributed: "contrib",
      interests: "interest",
    };
    const attrLabel = attrMap[p.attribution] || "";

    return `
      <div class="atlas-card" data-key="${escapeHtml(key)}">
        <div class="atlas-card-header">
          <span class="atlas-card-name">${escapeHtml(p.name)}</span>
          ${attrLabel ? `<span class="atlas-card-attr">${attrLabel}</span>` : ""}
        </div>
        <p class="atlas-card-desc">${escapeHtml(truncate(p.description, 120))}</p>
        <div class="atlas-card-langs">${langs}</div>
        <div class="atlas-card-tags">${tags}</div>
      </div>
    `;
  }

  return {
    id: "atlas",

    mount(container: HTMLElement) {
      root = document.createElement("div");
      root.className = "atlas-view";
      container.appendChild(root);
    },

    update() {
      render();
    },

    destroy() {
      root = null;
    },
  };
}
