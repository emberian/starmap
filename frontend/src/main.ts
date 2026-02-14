import "./styles/base.css";
import "./styles/shell.css";
import "./styles/components.css";
import "./styles/cosmos.css";
import "./styles/atlas.css";
import "./styles/codex.css";
import "./styles/nexus.css";

import { initShell } from "./components/shell";
import { showToast } from "./components/toast";
import { syncData, startSyncLoop, setToastFn } from "./api";
import { selectProject, switchActiveView, state } from "./state";
import {
  registerView,
  setViewContainer,
  mountInitialView,
} from "./views/registry";
import { createCosmosView } from "./views/cosmos";
import { createAtlasView } from "./views/atlas";
import { createCodexView } from "./views/codex";
import { createNexusView } from "./views/nexus";

async function init(): Promise<void> {
  const root = document.getElementById("app");
  if (!root) return;

  // Wire toast function for API module
  setToastFn(showToast);

  // Build shell
  const { viewContainer } = initShell(root);

  // Register views
  registerView("cosmos", createCosmosView);
  registerView("atlas", createAtlasView);
  registerView("nexus", createNexusView);
  registerView("codex", createCodexView);
  setViewContainer(viewContainer);

  // Keyboard shortcuts
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (state.selectedProjectId) {
        selectProject(null);
      }
    }
  });

  // URL hash routing
  function applyHash(): void {
    const hash = window.location.hash.slice(1);
    if (
      hash === "cosmos" ||
      hash === "atlas" ||
      hash === "nexus" ||
      hash === "codex"
    ) {
      switchActiveView(hash);
    }
  }
  window.addEventListener("hashchange", applyHash);
  applyHash();

  // Initial data load
  await syncData({ manual: true, announce: false });

  // Mount initial view
  mountInitialView();

  // Start live sync
  startSyncLoop();

  // Loaded state
  root.classList.add("loaded");
}

init();
