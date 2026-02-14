import { setSearchTerm, state } from "../state";
import { debounce } from "../util";

let inputEl: HTMLInputElement | null = null;

export function initSearch(parent: HTMLElement): HTMLElement {
  const container = document.createElement("div");
  container.className = "search-container";

  inputEl = document.createElement("input");
  inputEl.id = "searchInput";
  inputEl.type = "search";
  inputEl.placeholder = "Search project or concept";
  inputEl.autocomplete = "off";
  inputEl.value = state.searchTerm;

  const clear = document.createElement("span");
  clear.className = "search-clear";
  clear.textContent = "\u00d7";
  clear.addEventListener("click", () => {
    if (inputEl) inputEl.value = "";
    setSearchTerm("");
  });

  const onInput = debounce(() => {
    setSearchTerm(inputEl?.value ?? "");
  }, 150);
  inputEl.addEventListener("input", onInput);

  container.appendChild(inputEl);
  container.appendChild(clear);
  parent.appendChild(container);
  return container;
}

export function focusSearch(): void {
  inputEl?.focus();
}
