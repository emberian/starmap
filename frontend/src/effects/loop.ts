import { NebulaBackground } from "./nebula";
import { Starfield } from "./starfield";

let nebula: NebulaBackground | null = null;
let starfield: Starfield | null = null;
let starfieldActive = true;
let running = false;

export function initEffects(
  nebulaCanvas: HTMLCanvasElement,
  starfieldCanvas: HTMLCanvasElement,
): { nebula: NebulaBackground; starfield: Starfield } {
  nebula = new NebulaBackground(nebulaCanvas);
  starfield = new Starfield(starfieldCanvas);
  return { nebula, starfield };
}

export function setStarfieldActive(active: boolean): void {
  starfieldActive = active;
}

export function startLoop(): void {
  if (running) return;
  running = true;

  function tick(t: number): void {
    if (!running) return;
    nebula?.render(t);
    if (starfieldActive) starfield?.render(t);
    requestAnimationFrame(tick);
  }

  requestAnimationFrame(tick);
}

export function stopLoop(): void {
  running = false;
}
