import { TAXONOMY_CATEGORIES } from "../palette";
import {
  bus,
  setCategoryFilter,
  setViewMode,
  state,
} from "../state";
import { syncData } from "../api";
import { escapeHtml } from "../util";

let categorySelect: HTMLSelectElement | null = null;

export function initFilters(parent: HTMLElement): HTMLElement {
  const wrap = document.createElement("div");
  wrap.className = "filters-group";

  // Category dropdown
  categorySelect = document.createElement("select");
  categorySelect.id = "categorySelect";
  rebuildCategoryOptions();

  categorySelect.addEventListener("change", () => {
    setCategoryFilter(categorySelect!.value);
  });
  wrap.appendChild(categorySelect);

  // View mode toggle
  const viewToggle = document.createElement("div");
  viewToggle.className = "view-toggle-group";

  const coreBtn = document.createElement("button");
  coreBtn.className = "view-btn active";
  coreBtn.textContent = "Ember's Galaxy";
  coreBtn.title = "Show only original works and contributions";

  const allBtn = document.createElement("button");
  allBtn.className = "view-btn";
  allBtn.textContent = "Known Universe";
  allBtn.title = "Show all projects including interests";

  coreBtn.addEventListener("click", () => {
    setViewMode("core");
    coreBtn.classList.add("active");
    allBtn.classList.remove("active");
  });
  allBtn.addEventListener("click", () => {
    setViewMode("all");
    allBtn.classList.add("active");
    coreBtn.classList.remove("active");
  });

  viewToggle.appendChild(coreBtn);
  viewToggle.appendChild(allBtn);
  wrap.appendChild(viewToggle);

  // Controls row: live sync + buttons
  const controls = document.createElement("div");
  controls.className = "controls-row";

  const liveLabel = document.createElement("label");
  liveLabel.className = "toggle";
  const liveCheck = document.createElement("input");
  liveCheck.type = "checkbox";
  liveCheck.checked = state.liveSync;
  liveCheck.addEventListener("change", () => {
    state.liveSync = liveCheck.checked;
    if (state.liveSync) {
      syncData({ manual: false, announce: true });
    }
  });
  liveLabel.appendChild(liveCheck);
  liveLabel.append(" Live Sync");
  controls.appendChild(liveLabel);

  const syncBtn = document.createElement("button");
  syncBtn.textContent = "Sync Now";
  syncBtn.addEventListener("click", () => {
    syncData({ manual: true, announce: true });
  });
  controls.appendChild(syncBtn);

  wrap.appendChild(controls);

  // Sync status
  const syncStatus = document.createElement("span");
  syncStatus.className = "sync-status";
  syncStatus.id = "syncStatus";
  syncStatus.textContent = state.syncStatus.text;
  wrap.appendChild(syncStatus);

  bus.on("sync:status", ({ status }) => {
    syncStatus.textContent = status.text;
    syncStatus.className = `sync-status ${status.tone}`;
  });

  bus.on("data:loaded", () => {
    rebuildCategoryOptions();
  });

  parent.appendChild(wrap);
  return wrap;
}

function rebuildCategoryOptions(): void {
  if (!categorySelect) return;
  const current = state.categoryFilter;

  // Use taxonomy categories that actually have projects
  const projectCats = new Set(
    state.allProjects.map((p) => p.category),
  );
  const cats = TAXONOMY_CATEGORIES.filter(
    (c) => projectCats.has(c),
  );

  categorySelect.innerHTML =
    `<option value="all">All categories</option>` +
    cats
      .map(
        (c) =>
          `<option value="${escapeHtml(c)}">${escapeHtml(c)}</option>`,
      )
      .join("");

  if (current !== "all" && !projectCats.has(current)) {
    categorySelect.value = "all";
    setCategoryFilter("all");
  } else {
    categorySelect.value = current;
  }
}
