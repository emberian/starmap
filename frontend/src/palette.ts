export const TAXONOMY_CATEGORIES = [
  "Cryptography & Security",
  "Formal Verification",
  "Systems & Embedded",
  "Compilers & Languages",
  "Graphics & Rendering",
  "Simulation & Modeling",
  "Games",
  "Multimedia & Creative",
  "Distributed Systems",
  "Networking",
  "Serialization & Formats",
  "Data & Storage",
  "Concurrency & Async",
  "AI & Machine Learning",
  "Infrastructure & Ops",
  "Web & Services",
  "Developer Tools",
  "Exploratory",
] as const;

export type TaxonomyCategory = (typeof TAXONOMY_CATEGORIES)[number];

const PALETTE_18: Record<string, string> = {
  "Cryptography & Security": "#c5ae75",
  "Formal Verification": "#b8a0d4",
  "Systems & Embedded": "#d4917a",
  "Compilers & Languages": "#87c1d9",
  "Graphics & Rendering": "#d94f8c",
  "Simulation & Modeling": "#72b8a5",
  "Games": "#e8c547",
  "Multimedia & Creative": "#d47ab8",
  "Distributed Systems": "#5ba3cf",
  "Networking": "#4fc4a1",
  "Serialization & Formats": "#a3c478",
  "Data & Storage": "#cf8f4f",
  "Concurrency & Async": "#7a9ed9",
  "AI & Machine Learning": "#e07b5f",
  "Infrastructure & Ops": "#8bb5a2",
  "Web & Services": "#c478a3",
  "Developer Tools": "#9bb8d3",
  "Exploratory": "#7a9ab5",
};

export function getCategoryColor(category: string): string {
  return PALETTE_18[category] ?? PALETTE_18["Exploratory"];
}

export function getAllCategoryColors(): [string, string][] {
  return TAXONOMY_CATEGORIES.map((cat) => [cat, PALETTE_18[cat]]);
}
