export class NebulaBackground {
  private canvas: HTMLCanvasElement;
  private gl: WebGLRenderingContext | null;
  private program: WebGLProgram | null = null;

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    this.gl = canvas.getContext("webgl");
    if (!this.gl) return;
    this.program = this.createProgram();
    this.initBuffers();
    this.resize();
    window.addEventListener("resize", () => this.resize());
  }

  private createProgram(): WebGLProgram | null {
    const gl = this.gl!;

    const vs = `
      attribute vec2 position;
      void main() { gl_Position = vec4(position, 0.0, 1.0); }
    `;

    const fs = `
      precision highp float;
      uniform float time;
      uniform vec2 resolution;

      float noise(vec2 p) {
        return fract(sin(dot(p, vec2(12.9898, 78.233))) * 43758.5453);
      }

      float smooth_noise(vec2 p) {
        vec2 i = floor(p);
        vec2 f = fract(p);
        f = f * f * (3.0 - 2.0 * f);
        float a = noise(i);
        float b = noise(i + vec2(1.0, 0.0));
        float c = noise(i + vec2(0.0, 1.0));
        float d = noise(i + vec2(1.0, 1.0));
        return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
      }

      float fbm(vec2 p) {
        float v = 0.0;
        float a = 0.5;
        for (int i = 0; i < 5; i++) {
          v += a * smooth_noise(p);
          p *= 2.0;
          a *= 0.5;
        }
        return v;
      }

      void main() {
        vec2 uv = gl_FragCoord.xy / resolution.xy;
        vec2 p = uv - 0.5;
        p.x *= resolution.x / resolution.y;

        float r = length(p);
        float swirlIntensity = 0.6 * sin(time * 0.2);
        float a = atan(p.y, p.x) + swirlIntensity / (r + 0.8);
        vec2 uvWarped = vec2(cos(a), sin(a)) * r;

        vec2 uvDrift = p + vec2(sin(time * 0.05) * 0.1, time * 0.02);
        vec2 uvFinal = mix(uvDrift, uvWarped, 0.4);

        float n = fbm(uvFinal * 2.0 + time * 0.03);
        float n2 = fbm(uvFinal * 3.0 - time * 0.02);
        float n3 = fbm(uvFinal * 4.0 + n * 0.4);

        vec3 color1 = vec3(0.01, 0.03, 0.07);
        vec3 color2 = vec3(0.15, 0.05, 0.25);
        vec3 color3 = vec3(0.05, 0.2, 0.3);

        vec3 finalColor = mix(color1, color2, n * 1.3);
        finalColor = mix(finalColor, color3, n2 * n3 * 1.6);
        finalColor += pow(n3, 10.0) * 0.4;
        finalColor *= 1.3 - r;

        gl_FragColor = vec4(finalColor, 1.0);
      }
    `;

    const vShader = this.compileShader(gl.VERTEX_SHADER, vs);
    const fShader = this.compileShader(gl.FRAGMENT_SHADER, fs);
    if (!vShader || !fShader) return null;

    const prog = gl.createProgram()!;
    gl.attachShader(prog, vShader);
    gl.attachShader(prog, fShader);
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
      console.error("Program link error:", gl.getProgramInfoLog(prog));
      return null;
    }
    return prog;
  }

  private compileShader(
    type: number,
    src: string,
  ): WebGLShader | null {
    const gl = this.gl!;
    const s = gl.createShader(type);
    if (!s) return null;
    gl.shaderSource(s, src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) {
      console.error("Shader compile error:", gl.getShaderInfoLog(s));
      gl.deleteShader(s);
      return null;
    }
    return s;
  }

  private initBuffers(): void {
    const gl = this.gl!;
    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]),
      gl.STATIC_DRAW,
    );
    const pos = gl.getAttribLocation(this.program!, "position");
    gl.enableVertexAttribArray(pos);
    gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);
  }

  resize(): void {
    const ratio = window.devicePixelRatio || 1;
    this.canvas.width = window.innerWidth * ratio;
    this.canvas.height = window.innerHeight * ratio;
    this.gl?.viewport(0, 0, this.canvas.width, this.canvas.height);
  }

  render(t: number): void {
    const gl = this.gl;
    if (!gl || !this.program) return;
    gl.useProgram(this.program);
    gl.uniform1f(
      gl.getUniformLocation(this.program, "time"),
      t * 0.001,
    );
    gl.uniform2f(
      gl.getUniformLocation(this.program, "resolution"),
      this.canvas.width,
      this.canvas.height,
    );
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }
}
