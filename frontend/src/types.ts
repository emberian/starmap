export interface MotifNeighbor {
  name: string;
  path: string;
  similarity: number | null;
}

export interface ProjectMetaconstellation {
  id: string;
  name: string;
  score: number | null;
  matches: string[];
}

export interface EmbeddingMeta {
  model: string;
  backend_url: string;
  instruction: string;
  updated_at: string;
  dim: number | null;
}

export interface Project {
  name: string;
  description: string;
  category: string;
  concepts: string[];
  tags: string[];
  mined_motifs: string[];
  motif_neighbors: MotifNeighbor[];
  metaconstellations: ProjectMetaconstellation[];
  embedding: EmbeddingMeta | null;
  embedding_ready: boolean;
  attribution: string;
  languages: string[];
  display_path: string;
  umap_x: number | null;
  umap_y: number | null;
  alignment_stale: boolean;
  raw_category: string;
  raw_concepts: string[];
  raw_tags: string[];
  raw_mined_motifs: string[];
  _path: string;
  _hash: string;
  _updated_at: string;
}

export interface InterestItem {
  name: string;
  count: number;
}

export interface Interests {
  top_categories: InterestItem[];
  top_concepts: InterestItem[];
  top_tags: InterestItem[];
  top_mined_motifs: InterestItem[];
  top_metaconstellations: InterestItem[];
  top_languages: InterestItem[];
  embedding_coverage: {
    ready: number;
    total: number;
    pct: number;
  };
}

export interface Metaconstellation {
  id: string;
  name: string;
  description: string;
  concepts: string[];
  categories: string[];
  include_terms: string[];
  exclude_terms: string[];
  project_paths: string[];
  project_count: number;
}

export interface NormalizedPayload {
  projects: Project[];
  interests: Interests;
  metaconstellations: Metaconstellation[];
  metaconstellationsGeneratedAt: string | null;
  generatedAt: string | null;
}

export interface ProjectDiff {
  added: number;
  updated: number;
  removed: number;
  total: number;
}

export type ViewId = "cosmos" | "atlas" | "nexus" | "codex";

export interface SyncStatus {
  text: string;
  tone: "ok" | "warn" | "err";
}

export interface ViewLifecycle {
  readonly id: ViewId;
  mount(container: HTMLElement): void;
  update(): void;
  destroy(): void;
}

export interface GraphNode {
  id: string;
  label: string;
  project: Project;
  category: string;
  attribution: string;
  r: number;
  landmark?: boolean;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

export interface GraphEdge {
  source: string | GraphNode;
  target: string | GraphNode;
  weight: number;
  sharedItems: string[];
}

export interface CategoryCentroid {
  category: string;
  color: string;
  x: number;
  y: number;
  count: number;
  spread: number;
}
