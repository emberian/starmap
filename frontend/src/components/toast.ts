let stack: HTMLDivElement | null = null;

export function initToastStack(parent: HTMLElement): HTMLDivElement {
  stack = document.createElement("div");
  stack.className = "toast-stack";
  parent.appendChild(stack);
  return stack;
}

export function showToast(
  message: string,
  tone: "ok" | "warn" | "err" = "ok",
): void {
  if (!stack) return;
  const toast = document.createElement("div");
  toast.className = `toast ${tone}`;
  toast.textContent = message;
  stack.prepend(toast);

  while (stack.childElementCount > 4) {
    stack.lastElementChild?.remove();
  }

  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(6px)";
    setTimeout(() => toast.remove(), 200);
  }, 3600);
}
