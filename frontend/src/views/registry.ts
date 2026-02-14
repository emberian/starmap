import type { ViewId, ViewLifecycle } from "../types";
import { bus, state } from "../state";

const factories = new Map<ViewId, () => ViewLifecycle>();
let activeView: ViewLifecycle | null = null;
let container: HTMLElement | null = null;

export function registerView(
  id: ViewId,
  factory: () => ViewLifecycle,
): void {
  factories.set(id, factory);
}

export function setViewContainer(el: HTMLElement): void {
  container = el;
}

export function mountInitialView(): void {
  switchView(state.activeView);
}

export function switchView(to: ViewId): void {
  if (!container) return;

  if (activeView) {
    activeView.destroy();
    container.innerHTML = "";
  }

  const factory = factories.get(to);
  if (!factory) {
    console.error(`No view registered for "${to}"`);
    return;
  }

  activeView = factory();
  activeView.mount(container);
  activeView.update();
}

// Listen for view switch events
bus.on("view:switched", ({ to }) => {
  switchView(to);
});

// Re-render active view on filter/data changes
bus.on("filter:changed", () => {
  activeView?.update();
});

bus.on("data:loaded", () => {
  activeView?.update();
});
