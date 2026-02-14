import { escapeHtml } from "../util";

let el: HTMLDivElement | null = null;

export function initTooltip(parent: HTMLElement): HTMLDivElement {
  el = document.createElement("div");
  el.className = "tooltip";
  parent.appendChild(el);
  return el;
}

export function showTooltip(
  event: MouseEvent,
  title: string,
  subtitle: string,
): void {
  if (!el) return;
  el.innerHTML = `<strong>${escapeHtml(title)}</strong>${escapeHtml(subtitle).replace(/\n/g, "<br>")}`;
  el.style.opacity = "1";
  el.style.left = `${event.pageX + 14}px`;
  el.style.top = `${event.pageY - 14}px`;
}

export function moveTooltip(event: MouseEvent): void {
  if (!el) return;
  el.style.left = `${event.pageX + 14}px`;
  el.style.top = `${event.pageY - 14}px`;
}

export function hideTooltip(): void {
  if (!el) return;
  el.style.opacity = "0";
}
