import type { Project, ViewLifecycle } from "../types";
import { getCategoryColor } from "../palette";
import { selectProject, state } from "../state";
import { escapeHtml, projectKey, truncate } from "../util";

type SortField =
  | "name"
  | "category"
  | "languages"
  | "concepts"
  | "tags"
  | "description";

export function createCodexView(): ViewLifecycle {
  let root: HTMLElement | null = null;
  let sortField: SortField = "name";
  let sortAsc = true;

  function sorted(projects: Project[]): Project[] {
    const arr = [...projects];
    const dir = sortAsc ? 1 : -1;
    arr.sort((a, b) => {
      let cmp = 0;
      switch (sortField) {
        case "name":
          cmp = a.name.localeCompare(b.name);
          break;
        case "category":
          cmp = a.category.localeCompare(b.category);
          break;
        case "languages":
          cmp = (a.languages[0] || "").localeCompare(
            b.languages[0] || "",
          );
          break;
        case "concepts":
          cmp = a.concepts.length - b.concepts.length;
          break;
        case "tags":
          cmp = a.tags.length - b.tags.length;
          break;
        case "description":
          cmp = a.description.localeCompare(b.description);
          break;
      }
      return cmp * dir;
    });
    return arr;
  }

  function highlight(text: string): string {
    const search = state.searchTerm.trim();
    if (!search) return escapeHtml(text);
    const escaped = escapeHtml(text);
    const regex = new RegExp(
      `(${search.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`,
      "gi",
    );
    return escaped.replace(regex, "<mark>$1</mark>");
  }

  function render(): void {
    if (!root) return;

    const projects = sorted(state.filteredProjects);
    const arrow = (field: SortField) =>
      sortField === field ? (sortAsc ? " \u25b2" : " \u25bc") : "";

    let html = `
      <div class="codex-wrapper">
        <table class="codex-table">
          <thead>
            <tr>
              <th data-sort="name" class="codex-sortable">Name${arrow("name")}</th>
              <th data-sort="category" class="codex-sortable">Category${arrow("category")}</th>
              <th data-sort="languages" class="codex-sortable">Languages${arrow("languages")}</th>
              <th data-sort="concepts" class="codex-sortable codex-num">#C${arrow("concepts")}</th>
              <th data-sort="tags" class="codex-sortable codex-num">#T${arrow("tags")}</th>
              <th>Description</th>
            </tr>
          </thead>
          <tbody>
    `;

    for (const p of projects) {
      const key = projectKey(p);
      const color = getCategoryColor(p.category);
      const langs = p.languages.slice(0, 3).join(", ");

      html += `
        <tr class="codex-row" data-key="${escapeHtml(key)}">
          <td class="codex-name">${highlight(p.name)}</td>
          <td><span class="codex-cat-dot" style="background:${color}"></span>${highlight(p.category)}</td>
          <td class="codex-langs">${highlight(langs)}</td>
          <td class="codex-num">${p.concepts.length}</td>
          <td class="codex-num">${p.tags.length}</td>
          <td class="codex-desc">${highlight(truncate(p.description, 80))}</td>
        </tr>
      `;
    }

    html += `
          </tbody>
        </table>
      </div>
    `;

    root.innerHTML = html;

    // Sort handlers
    root
      .querySelectorAll<HTMLElement>(".codex-sortable")
      .forEach((th) => {
        th.addEventListener("click", () => {
          const field = th.dataset.sort as SortField;
          if (sortField === field) {
            sortAsc = !sortAsc;
          } else {
            sortField = field;
            sortAsc = true;
          }
          render();
        });
      });

    // Row click
    root
      .querySelectorAll<HTMLElement>(".codex-row")
      .forEach((tr) => {
        tr.addEventListener("click", () => {
          selectProject(tr.dataset.key!);
        });
      });
  }

  return {
    id: "codex",

    mount(container: HTMLElement) {
      root = document.createElement("div");
      root.className = "codex-view";
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
