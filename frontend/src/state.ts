import type {
  Interests,
  Metaconstellation,
  NormalizedPayload,
  Project,
  ProjectDiff,
  SyncStatus,
  ViewId,
} from "./types";
import { projectKey } from "./util";

// ---------------------------------------------------------------------------
// Event bus
// ---------------------------------------------------------------------------

export type EventMap = {
  "data:loaded": { diff: ProjectDiff };
  "filter:changed": Record<string, never>;
  "view:switched": { from: ViewId; to: ViewId };
  "project:selected": { id: string | null };
  "project:hovered": { id: string | null };
  "sync:status": { status: SyncStatus };
};

type Listener<K extends keyof EventMap> = (payload: EventMap[K]) => void;

class EventBus {
  private listeners = new Map<string, Set<Function>>();

  on<K extends keyof EventMap>(
    event: K,
    fn: Listener<K>,
  ): () => void {
    if (!this.listeners.has(event)) {
      this.listeners.set(event, new Set());
    }
    this.listeners.get(event)!.add(fn);
    return () => {
      this.listeners.get(event)?.delete(fn);
    };
  }

  emit<K extends keyof EventMap>(event: K, payload: EventMap[K]): void {
    const fns = this.listeners.get(event);
    if (!fns) return;
    for (const fn of fns) {
      (fn as Listener<K>)(payload);
    }
  }
}

export const bus = new EventBus();

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

export interface AppState {
  allProjects: Project[];
  filteredProjects: Project[];
  interests: Interests | null;
  metaconstellations: Metaconstellation[];
  searchTerm: string;
  categoryFilter: string;
  viewMode: "core" | "all";
  activeView: ViewId;
  selectedProjectId: string | null;
  hoveredProjectId: string | null;
  liveSync: boolean;
  syncStatus: SyncStatus;
  lastSnapshotKey: string;
  initialLoadDone: boolean;
  generatedAt: string | null;
}

export const state: AppState = {
  allProjects: [],
  filteredProjects: [],
  interests: null,
  metaconstellations: [],
  searchTerm: "",
  categoryFilter: "all",
  viewMode: "core",
  activeView: "cosmos",
  selectedProjectId: null,
  hoveredProjectId: null,
  liveSync: true,
  syncStatus: { text: "Sync idle", tone: "ok" },
  lastSnapshotKey: "",
  initialLoadDone: false,
  generatedAt: null,
};

// ---------------------------------------------------------------------------
// Derived computation
// ---------------------------------------------------------------------------

function computeFilteredProjects(): Project[] {
  const search = state.searchTerm.trim().toLowerCase();
  return state.allProjects.filter((project) => {
    if (state.viewMode === "core" && project.attribution === "interests") {
      return false;
    }
    if (
      state.categoryFilter !== "all" &&
      project.category !== state.categoryFilter
    ) {
      return false;
    }
    if (!search) return true;
    const haystack = [
      project.name,
      project.description,
      project.category,
      ...project.concepts,
      ...project.tags,
      ...project.mined_motifs,
      ...project.metaconstellations.map((m) => m.name),
    ]
      .join(" ")
      .toLowerCase();
    return haystack.includes(search);
  });
}

function refilter(): void {
  state.filteredProjects = computeFilteredProjects();
  bus.emit("filter:changed", {});
}

// ---------------------------------------------------------------------------
// Mutations
// ---------------------------------------------------------------------------

export function setSearchTerm(term: string): void {
  state.searchTerm = term;
  refilter();
}

export function setCategoryFilter(cat: string): void {
  state.categoryFilter = cat;
  refilter();
}

export function setViewMode(mode: "core" | "all"): void {
  state.viewMode = mode;
  refilter();
}

export function selectProject(id: string | null): void {
  state.selectedProjectId = id;
  bus.emit("project:selected", { id });
}

export function hoverProject(id: string | null): void {
  state.hoveredProjectId = id;
  bus.emit("project:hovered", { id });
}

export function switchActiveView(to: ViewId): void {
  const from = state.activeView;
  if (from === to) return;
  state.activeView = to;
  bus.emit("view:switched", { from, to });
}

export function setSyncStatus(status: SyncStatus): void {
  state.syncStatus = status;
  bus.emit("sync:status", { status });
}

export function applyPayload(
  payload: NormalizedPayload,
): ProjectDiff {
  const prevMap = new Map(
    state.allProjects.map((p) => [projectKey(p), p]),
  );
  const nextMap = new Map(
    payload.projects.map((p) => [projectKey(p), p]),
  );

  let added = 0;
  let updated = 0;
  let removed = 0;

  nextMap.forEach((next, key) => {
    const prev = prevMap.get(key);
    if (!prev) {
      added++;
    } else if (
      prev._hash !== next._hash ||
      prev._updated_at !== next._updated_at
    ) {
      updated++;
    }
  });
  prevMap.forEach((_, key) => {
    if (!nextMap.has(key)) removed++;
  });

  const diff: ProjectDiff = {
    added,
    updated,
    removed,
    total: added + updated + removed,
  };

  state.allProjects = payload.projects;
  state.interests = payload.interests;
  state.metaconstellations = payload.metaconstellations;
  state.generatedAt = payload.generatedAt ?? null;
  state.initialLoadDone = true;

  refilter();
  bus.emit("data:loaded", { diff });
  return diff;
}

export function getProjectById(
  id: string | null,
): Project | undefined {
  if (!id) return undefined;
  return state.allProjects.find(
    (p) => projectKey(p) === id,
  );
}

export function getProjectByKey(key: string): Project | undefined {
  return state.allProjects.find((p) => projectKey(p) === key);
}
