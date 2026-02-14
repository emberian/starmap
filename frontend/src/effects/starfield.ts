interface Star {
  x: number;
  y: number;
  r: number;
  b: number;
  drift: number;
}

export class Starfield {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D | null = null;
  private stars: Star[] = [];

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.setup();
    window.addEventListener("resize", () => this.setup());
  }

  setup(): void {
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = Math.floor(window.innerWidth * ratio);
    this.canvas.height = Math.floor(window.innerHeight * ratio);
    this.canvas.style.width = `${window.innerWidth}px`;
    this.canvas.style.height = `${window.innerHeight}px`;

    this.ctx = this.canvas.getContext("2d");
    if (this.ctx) {
      this.ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    }

    const count = Math.max(
      120,
      Math.floor(
        (window.innerWidth * window.innerHeight) / 9000,
      ),
    );
    this.stars = Array.from({ length: count }, () => ({
      x: Math.random() * window.innerWidth,
      y: Math.random() * window.innerHeight,
      r: 0.4 + Math.random() * 1.4,
      b: 0.25 + Math.random() * 0.7,
      drift: 0.002 + Math.random() * 0.008,
    }));
  }

  render(t: number): void {
    const ctx = this.ctx;
    if (!ctx) return;

    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    this.stars.forEach((star, i) => {
      const pulse = 0.55 + 0.45 * Math.sin(t * star.drift + i);
      ctx.beginPath();
      ctx.arc(star.x, star.y, star.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(214, 229, 240, ${star.b * pulse})`;
      ctx.fill();
    });
  }
}
