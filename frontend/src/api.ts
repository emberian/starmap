import type {
  Interests,
  InterestItem,
  Metaconstellation,
  NormalizedPayload,
  Project,
  ProjectMetaconstellation,
} from "./types";
import {
  applyPayload,
  setSyncStatus,
  state,
} from "./state";
import { projectSignature } from "./util";

// ---------------------------------------------------------------------------
// Normalization (mirrors serve.py's normalize_project)
// ---------------------------------------------------------------------------

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((v) => String(v ?? "").trim())
    .filter(Boolean);
}

function normalizeMetaconstellation(
  item: unknown,
): ProjectMetaconstellation | null {
  if (typeof item === "string") {
    const name = item.trim();
    return name ? { id: "", name, score: null, matches: [] } : null;
  }
  if (!item || typeof item !== "object") return null;
  const obj = item as Record<string, unknown>;
  const name = String(obj.name ?? "").trim();
  if (!name) return null;
  const score =
    typeof obj.score === "number" && isFinite(obj.score)
      ? obj.score
      : null;
  return {
    id: String(obj.id ?? "").trim(),
    name,
    score,
    matches: normalizeStringList(obj.matches).slice(0, 10),
  };
}

function normalizeProject(raw: Record<string, unknown>): Project {
  const metaconstellations = Array.isArray(raw.metaconstellations)
    ? (raw.metaconstellations
        .map(normalizeMetaconstellation)
        .filter(Boolean) as ProjectMetaconstellation[])
    : [];

  return {
    name: String(raw.name ?? "Unnamed"),
    description: String(raw.description ?? "No summary available yet."),
    category: String(raw.category ?? "Exploratory"),
    concepts: normalizeStringList(raw.concepts),
    tags: normalizeStringList(raw.tags),
    mined_motifs: normalizeStringList(raw.mined_motifs),
    motif_neighbors: Array.isArray(raw.motif_neighbors)
      ? raw.motif_neighbors.slice(0, 8)
      : [],
    metaconstellations,
    embedding: raw.embedding as Project["embedding"],
    embedding_ready: Boolean(raw.embedding_ready || raw.embedding),
    attribution: String(raw.attribution ?? "interests"),
    languages: normalizeStringList(raw.languages),
    display_path: String(raw.display_path ?? raw._path ?? ""),
    umap_x:
      typeof raw.umap_x === "number" ? raw.umap_x : null,
    umap_y:
      typeof raw.umap_y === "number" ? raw.umap_y : null,
    alignment_stale: Boolean(raw.alignment_stale),
    raw_category: String(raw.raw_category ?? raw.category ?? ""),
    raw_concepts: normalizeStringList(raw.raw_concepts),
    raw_tags: normalizeStringList(raw.raw_tags),
    raw_mined_motifs: normalizeStringList(raw.raw_mined_motifs),
    _path: String(raw._path ?? ""),
    _hash: String(raw._hash ?? ""),
    _updated_at: String(raw._updated_at ?? ""),
  };
}

function deriveInterests(projects: Project[]): Interests {
  const count = (arr: string[]): InterestItem[] => {
    const map = new Map<string, { name: string; count: number }>();
    for (const item of arr) {
      const key = item.toLowerCase();
      const existing = map.get(key);
      if (existing) {
        existing.count++;
      } else {
        map.set(key, { name: item, count: 1 });
      }
    }
    return [...map.values()].sort((a, b) => b.count - a.count);
  };

  const embeddingReady = projects.filter((p) => p.embedding_ready).length;

  return {
    top_categories: count(projects.map((p) => p.category)).slice(0, 12),
    top_concepts: count(projects.flatMap((p) => p.concepts)).slice(0, 20),
    top_tags: count(projects.flatMap((p) => p.tags)).slice(0, 20),
    top_mined_motifs: count(projects.flatMap((p) => p.mined_motifs)).slice(0, 20),
    top_metaconstellations: count(
      projects.flatMap((p) => p.metaconstellations.map((m) => m.name)),
    ).slice(0, 10),
    top_languages: count(projects.flatMap((p) => p.languages)).slice(0, 10),
    embedding_coverage: {
      ready: embeddingReady,
      total: projects.length,
      pct: projects.length
        ? Number(((embeddingReady / projects.length) * 100).toFixed(2))
        : 0,
    },
  };
}

function normalizeGlobalMeta(raw: unknown): Metaconstellation[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((item): item is Record<string, unknown> =>
      item != null && typeof item === "object",
    )
    .map((item) => ({
      id: String(item.id ?? ""),
      name: String(item.name ?? ""),
      description: String(item.description ?? ""),
      concepts: normalizeStringList(item.concepts),
      categories: normalizeStringList(item.categories),
      include_terms: normalizeStringList(item.include_terms),
      exclude_terms: normalizeStringList(item.exclude_terms),
      project_paths: normalizeStringList(item.project_paths),
      project_count:
        typeof item.project_count === "number"
          ? item.project_count
          : 0,
    }))
    .filter((m) => m.name);
}

function normalizePayload(
  raw: Record<string, unknown>,
): NormalizedPayload {
  const projects = Array.isArray(raw.projects)
    ? raw.projects.map((p: Record<string, unknown>) => normalizeProject(p))
    : [];

  const derived = deriveInterests(projects);
  const serverInterests = raw.interests as
    | Partial<Interests>
    | undefined;
  const interests: Interests = serverInterests
    ? { ...derived, ...serverInterests }
    : derived;

  return {
    projects,
    interests,
    metaconstellations: normalizeGlobalMeta(raw.metaconstellations),
    metaconstellationsGeneratedAt:
      (raw.metaconstellations_generated_at as string) ?? null,
    generatedAt: (raw.generated_at as string) ?? null,
  };
}

// ---------------------------------------------------------------------------
// Snapshot key for change detection
// ---------------------------------------------------------------------------

function computeSnapshotKey(payload: NormalizedPayload): string {
  const projectPart = payload.projects
    .map(projectSignature)
    .sort()
    .join("\n");
  const metaPart = payload.metaconstellations
    .map((m) => `${m.id}|${m.name}|${m.project_count}`)
    .sort()
    .join("\n");
  return `${projectPart}\n---\n${metaPart}`;
}

// ---------------------------------------------------------------------------
// Fetch + Sync
// ---------------------------------------------------------------------------

let syncInFlight = false;
let syncTimer: ReturnType<typeof setInterval> | null = null;

export type ToastFn = (message: string, tone: "ok" | "warn" | "err") => void;

let toastFn: ToastFn | null = null;

export function setToastFn(fn: ToastFn): void {
  toastFn = fn;
}

function toast(message: string, tone: "ok" | "warn" | "err"): void {
  toastFn?.(message, tone);
}

async function fetchPayload(): Promise<NormalizedPayload> {
  const response = await fetch("/api/data", { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const raw = await response.json();
  return normalizePayload(raw);
}

export async function syncData(opts: {
  manual?: boolean;
  announce?: boolean;
}): Promise<void> {
  if (syncInFlight) return;
  syncInFlight = true;

  setSyncStatus({
    text: opts.manual ? "Syncing now..." : "Live syncing...",
    tone: "warn",
  });

  try {
    const payload = await fetchPayload();
    const nextKey = computeSnapshotKey(payload);
    const changed = nextKey !== state.lastSnapshotKey;

    if (!state.initialLoadDone || changed || opts.manual) {
      const diff = applyPayload(payload);
      state.lastSnapshotKey = nextKey;

      if (opts.announce && diff.total > 0) {
        const parts: string[] = [];
        if (diff.added) parts.push(`+${diff.added} new`);
        if (diff.updated) parts.push(`${diff.updated} revised`);
        if (diff.removed) parts.push(`${diff.removed} retired`);
        toast(`Atlas refresh: ${parts.join(", ")}`, "ok");
      }
    }

    if (opts.manual && state.initialLoadDone && !changed) {
      toast("No new stars yet.", "warn");
    }

    setSyncStatus({
      text: state.liveSync ? "Live sync online" : "Live sync paused",
      tone: "ok",
    });
  } catch (error) {
    console.error("Sync failed:", error);
    if (!state.initialLoadDone) {
      toast(
        "Unable to reach /api/data. Start serve.py first.",
        "err",
      );
    } else {
      toast("Sync failed. Retrying on next pulse.", "err");
    }
    setSyncStatus({ text: "Sync fault", tone: "err" });
  } finally {
    syncInFlight = false;
  }
}

export function startSyncLoop(intervalMs = 12000): void {
  if (syncTimer) clearInterval(syncTimer);
  syncTimer = setInterval(() => {
    if (!state.liveSync || document.hidden) return;
    syncData({ announce: true });
  }, intervalMs);
}

export function stopSyncLoop(): void {
  if (syncTimer) {
    clearInterval(syncTimer);
    syncTimer = null;
  }
}

// Re-sync on visibility change
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && state.liveSync) {
    syncData({ announce: true });
  }
});
