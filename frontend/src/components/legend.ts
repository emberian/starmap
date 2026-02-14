import { getAllCategoryColors } from "../palette";
import { bus, setCategoryFilter, state } from "../state";
import { escapeHtml } from "../util";

let legendEl: HTMLDivElement | null = null;
let hoveredCategory: string | null = null;

type HoverCallback = (category: string | null) => void;
let onHoverCategory: HoverCallback | null = null;

export function setLegendHoverCallback(fn: HoverCallback): void {
  onHoverCategory = fn;
}

export function initLegend(parent: HTMLElement): HTMLDivElement {
  legendEl = document.createElement("div");
  legendEl.className = "legend panel";
  legendEl.id = "categoryLegend";
  parent.appendChild(legendEl);

  bus.on("data:loaded", () => renderLegend());
  bus.on("filter:changed", () => renderLegend());
  renderLegend();

  return legendEl;
}

function renderLegend(): void {
  if (!legendEl) return;

  // Count projects per category in current filtered set
  const counts = new Map<string, number>();
  for (const p of state.filteredProjects) {
    counts.set(p.category, (counts.get(p.category) || 0) + 1);
  }

  const allColors = getAllCategoryColors();
  const items = allColors
    .filter(([cat]) => counts.has(cat))
    .map(
      ([cat, color]) =>
        `<div class="legend-item" data-category="${escapeHtml(cat)}">
        <span class="legend-dot" style="background:${color}"></span>
        <span class="legend-label">${escapeHtml(cat)}</span>
        <span class="legend-count">${counts.get(cat) || 0}</span>
      </div>`,
    );

  legendEl.innerHTML = items.join("");

  legendEl.querySelectorAll<HTMLElement>(".legend-item").forEach(
    (el) => {
      const cat = el.dataset.category ?? "";

      el.addEventListener("click", () => {
        if (state.categoryFilter === cat) {
          setCategoryFilter("all");
        } else {
          setCategoryFilter(cat);
        }
      });

      el.addEventListener("mouseenter", () => {
        hoveredCategory = cat;
        onHoverCategory?.(cat);
      });

      el.addEventListener("mouseleave", () => {
        hoveredCategory = null;
        onHoverCategory?.(null);
      });
    },
  );
}

export function getHoveredCategory(): string | null {
  return hoveredCategory;
}
