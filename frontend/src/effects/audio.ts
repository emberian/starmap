export class GenerativeComposer {
  private ctx: AudioContext | null = null;
  private masterGain: GainNode | null = null;
  private compressor: DynamicsCompressorNode | null = null;
  private timers: ReturnType<typeof setTimeout>[] = [];
  playing = false;
  private initialized = false;
  private scale = [174.61, 207.65, 233.08, 261.63, 311.13, 349.23];

  init(): void {
    if (this.initialized) return;
    this.ctx = new AudioContext();
    this.compressor = this.ctx.createDynamicsCompressor();
    this.compressor.threshold.value = -24;
    this.compressor.knee.value = 30;
    this.compressor.ratio.value = 12;
    this.compressor.attack.value = 0.003;
    this.compressor.release.value = 0.25;

    this.masterGain = this.ctx.createGain();
    this.masterGain.gain.value = 0.2;
    this.masterGain.connect(this.compressor);
    this.compressor.connect(this.ctx.destination);

    this.startBass();
    this.startPads();
    this.startMelodyLoop();
    this.startPercussionLoop();
    this.playing = true;
    this.initialized = true;
  }

  private scheduleLoop(fn: () => void, intervalMs: number | (() => number)): void {
    const tick = () => {
      fn();
      const ms = typeof intervalMs === "function" ? intervalMs() : intervalMs;
      const id = setTimeout(tick, ms);
      this.timers.push(id);
    };
    tick();
  }

  private createNoiseBuffer(): AudioBuffer {
    const bufferSize = this.ctx!.sampleRate * 2;
    const buffer = this.ctx!.createBuffer(1, bufferSize, this.ctx!.sampleRate);
    const data = buffer.getChannelData(0);
    for (let i = 0; i < bufferSize; i++) {
      data[i] = Math.random() * 2 - 1;
    }
    return buffer;
  }

  private startPercussionLoop(): void {
    const noiseBuffer = this.createNoiseBuffer();
    this.scheduleLoop(() => {
      if (!this.playing || !this.ctx || !this.masterGain) return;
      const source = this.ctx.createBufferSource();
      source.buffer = noiseBuffer;
      const filter = this.ctx.createBiquadFilter();
      filter.type = "highpass";
      filter.frequency.value = 5000;
      const gain = this.ctx.createGain();
      gain.gain.setValueAtTime(0.02, this.ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + 0.1);
      source.connect(filter);
      filter.connect(gain);
      gain.connect(this.masterGain);
      source.start();
      source.stop(this.ctx.currentTime + 0.1);
    }, 500);
  }

  private startBass(): void {
    this.scheduleLoop(() => {
      if (!this.playing || !this.ctx || !this.masterGain) return;
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = "triangle";
      osc.frequency.setValueAtTime(43.65, this.ctx.currentTime);
      osc.frequency.exponentialRampToValueAtTime(30, this.ctx.currentTime + 0.5);
      gain.gain.setValueAtTime(0, this.ctx.currentTime);
      gain.gain.linearRampToValueAtTime(0.3, this.ctx.currentTime + 0.1);
      gain.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + 0.8);
      osc.connect(gain);
      gain.connect(this.masterGain);
      osc.start();
      osc.stop(this.ctx.currentTime + 1);
    }, 2000);
  }

  private startPads(): void {
    if (!this.ctx || !this.masterGain) return;
    const freqs = [87.31, 130.81, 174.61, 207.65, 261.63];
    for (const f of freqs) {
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      const lfo = this.ctx.createOscillator();
      const lfoGain = this.ctx.createGain();

      osc.type = "sawtooth";
      osc.frequency.value = f + (Math.random() - 0.5);

      const filter = this.ctx.createBiquadFilter();
      filter.type = "lowpass";
      filter.frequency.value = 400;
      filter.Q.value = 5;

      lfo.frequency.value = 0.1 + Math.random() * 0.1;
      lfoGain.gain.value = 200;
      lfo.connect(lfoGain);
      lfoGain.connect(filter.frequency);
      lfo.start();

      gain.gain.value = 0;
      osc.connect(filter);
      filter.connect(gain);
      gain.connect(this.masterGain);
      osc.start();
      gain.gain.linearRampToValueAtTime(0.05, this.ctx.currentTime + 5);
    }
  }

  private startMelodyLoop(): void {
    if (!this.ctx || !this.masterGain) return;
    const delay = this.ctx.createDelay();
    delay.delayTime.value = 0.4;
    const feedback = this.ctx.createGain();
    feedback.gain.value = 0.4;
    delay.connect(feedback);
    feedback.connect(delay);
    delay.connect(this.masterGain);

    this.scheduleLoop(() => {
      if (!this.playing || !this.ctx || !this.masterGain || Math.random() <= 0.4) return;
      const freq =
        this.scale[Math.floor(Math.random() * this.scale.length)] *
        (Math.random() > 0.8 ? 2 : 1);
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, this.ctx.currentTime);
      gain.gain.setValueAtTime(0, this.ctx.currentTime);
      gain.gain.linearRampToValueAtTime(0.1, this.ctx.currentTime + 0.05);
      gain.gain.exponentialRampToValueAtTime(0.001, this.ctx.currentTime + 0.5);
      osc.connect(gain);
      gain.connect(delay);
      gain.connect(this.masterGain);
      osc.start();
      osc.stop(this.ctx.currentTime + 1);
    }, () => 500 + Math.random() * 1500);
  }

  toggle(): boolean {
    if (!this.initialized) {
      this.init();
      return true;
    }
    if (this.playing) {
      this.pause();
    } else {
      this.resume();
    }
    return this.playing;
  }

  pause(): void {
    if (!this.playing || !this.ctx) return;
    this.playing = false;
    // Ramp gain to 0, then suspend the context for true silence
    if (this.masterGain) {
      this.masterGain.gain.setTargetAtTime(0, this.ctx.currentTime, 0.3);
    }
    // Suspend after brief fade-out
    setTimeout(() => {
      if (!this.playing && this.ctx && this.ctx.state === "running") {
        this.ctx.suspend();
      }
    }, 400);
  }

  resume(): void {
    if (this.playing || !this.ctx) return;
    this.playing = true;
    // Resume context first, then ramp gain back up
    if (this.ctx.state === "suspended") {
      this.ctx.resume().then(() => {
        if (this.masterGain && this.ctx) {
          this.masterGain.gain.setTargetAtTime(0.2, this.ctx.currentTime, 0.8);
        }
      });
    } else if (this.masterGain) {
      this.masterGain.gain.setTargetAtTime(0.2, this.ctx.currentTime, 0.8);
    }
  }
}
