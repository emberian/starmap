import type { ViewId } from "../types";
import { GenerativeComposer } from "../effects/audio";
import { initEffects, setStarfieldActive, startLoop } from "../effects/loop";
import { bus, selectProject, state, switchActiveView } from "../state";
import { initDetails } from "./details";
import { initFilters } from "./filters";
import { initLegend } from "./legend";
import { initSearch } from "./search";
import { initToastStack, showToast } from "./toast";
import { initTooltip } from "./tooltip";

const composer = new GenerativeComposer();
let nebulaCanvas: HTMLCanvasElement;
let starfieldCanvas: HTMLCanvasElement;

// View-specific effect behavior
const ATMOSPHERIC_VIEWS: ViewId[] = ["cosmos", "nexus"];

export function initShell(root: HTMLElement): {
  viewContainer: HTMLElement;
  showOverlay: () => void;
} {
  root.innerHTML = "";

  // Overlay
  const overlay = document.createElement("div");
  overlay.id = "overlay";
  overlay.innerHTML = `
    <button class="start-btn" id="startBtn">Initialize Atlas</button>
    <div class="start-hint">Commence synchronization and audio alignment</div>
  `;

  // App container
  const app = document.createElement("div");
  app.id = "app";

  // Effect canvases
  nebulaCanvas = document.createElement("canvas");
  nebulaCanvas.id = "nebula";
  starfieldCanvas = document.createElement("canvas");
  starfieldCanvas.id = "starfield";
  app.appendChild(nebulaCanvas);
  app.appendChild(starfieldCanvas);

  // View container
  const viewContainer = document.createElement("div");
  viewContainer.id = "view-container";
  app.appendChild(viewContainer);

  // HUD
  const hud = document.createElement("div");
  hud.className = "hud";

  // Title panel
  const titlePanel = document.createElement("section");
  titlePanel.className = "panel title-panel";
  titlePanel.innerHTML = `
    <h1 class="title">Constellation Atlas</h1>
    <p class="subtitle" id="summaryText">Sampling project intent from recursive summaries...</p>
    <div class="meta" id="metaText">Awaiting telemetry.</div>
  `;
  hud.appendChild(titlePanel);

  // Nav bar
  const nav = document.createElement("nav");
  nav.className = "panel nav-panel";

  const navToggle = document.createElement("div");
  navToggle.className = "view-toggle-group";

  const views: { id: ViewId; label: string }[] = [
    { id: "cosmos", label: "Cosmos" },
    { id: "atlas", label: "Atlas" },
    { id: "nexus", label: "Nexus" },
    { id: "codex", label: "Codex" },
  ];

  for (const { id, label } of views) {
    const btn = document.createElement("button");
    btn.className = `view-btn nav-view-btn${id === state.activeView ? " active" : ""}`;
    btn.dataset.view = id;
    btn.textContent = label;
    btn.addEventListener("click", () => switchActiveView(id));
    navToggle.appendChild(btn);
  }
  nav.appendChild(navToggle);
  hud.appendChild(nav);

  // Control panel
  const controlPanel = document.createElement("section");
  controlPanel.className = "panel control-panel";
  initSearch(controlPanel);
  initFilters(controlPanel);
  hud.appendChild(controlPanel);

  app.appendChild(hud);

  // Details panel
  initDetails(app);

  // Legend
  initLegend(app);

  // Toast stack
  initToastStack(app);

  // Tooltip
  initTooltip(app);

  // Audio control
  const audioControl = document.createElement("div");
  audioControl.id = "audioControl";
  audioControl.textContent = "Mute Audio";
  audioControl.addEventListener("click", () => {
    const playing = composer.toggle();
    audioControl.textContent = playing ? "Mute Audio" : "Unmute Audio";
  });
  app.appendChild(audioControl);

  root.appendChild(overlay);
  root.appendChild(app);

  // Start effects
  initEffects(nebulaCanvas, starfieldCanvas);
  startLoop();

  // Overlay start button
  const startBtn = overlay.querySelector("#startBtn")!;
  startBtn.addEventListener("click", () => {
    composer.init();
    overlay.classList.add("hidden");
    showToast("Synchronization sequence active", "ok");
  });

  // View switch: update nav buttons + effect layers
  // Set initial data-view attribute
  app.dataset.view = state.activeView;

  bus.on("view:switched", ({ to }) => {
    // Update data-view for CSS selectors
    app.dataset.view = to;

    // Update nav buttons
    navToggle.querySelectorAll<HTMLElement>(".nav-view-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.dataset.view === to);
    });

    // Adjust effects
    const atmospheric = ATMOSPHERIC_VIEWS.includes(to);
    setStarfieldActive(atmospheric);
    starfieldCanvas.style.opacity = atmospheric ? "0.65" : "0";
    nebulaCanvas.style.opacity = atmospheric ? "0.8" : "0.25";

    if (atmospheric) {
      composer.resume();
    } else {
      composer.pause();
    }
  });

  // Update header on data load
  bus.on("data:loaded", () => {
    updateHeader();
    root.classList.add("loaded");
  });

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.selectedProjectId) {
      selectProject(null);
    }
  });

  return {
    viewContainer,
    showOverlay: () => overlay.classList.remove("hidden"),
  };
}

function updateHeader(): void {
  const summaryText = document.getElementById("summaryText");
  const metaText = document.getElementById("metaText");
  if (!summaryText || !metaText) return;

  const interests = state.interests;
  if (!interests || !state.allProjects.length) {
    summaryText.textContent = "No summaries found yet.";
    metaText.textContent = "Expected source: ~/.summarization";
    return;
  }

  const topCats = interests.top_categories
    .slice(0, 3)
    .map((c) => c.name);
  summaryText.textContent = topCats.length
    ? `Strongest constellations: ${topCats.join(", ")}.`
    : "Constellation categories are converging.";

  const embed = interests.embedding_coverage;
  const dateText = state.generatedAt
    ? new Date(state.generatedAt).toLocaleString()
    : "unknown";
  metaText.textContent = `${state.allProjects.length} projects \u2022 Embeddings ${embed.ready}/${embed.total} (${embed.pct}%) \u2022 Metaconstellations ${state.metaconstellations.length} \u2022 Updated ${dateText}`;
}
