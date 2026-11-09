// Hands-free call: the microphone stays open and a voice-activity detector cuts what you
// say into utterances, each sent to speech recognition as soon as you pause. Starting to
// talk while she speaks interrupts her (barge-in); going on after a pause before she has
// said a word joins it to what came before (app.js), so a pause can afford to be short.
//
// Detection is loudness against a tracked noise floor. While she is speaking the bar is
// higher, so her own voice coming back through speakers doesn't cut her off (headphones
// are still best). The floor is only learned while she is quiet: during her lines the
// browser's echo cancelling turns the mic right down, and a floor learned then sank to
// nothing, so afterwards the room itself sounded like talking and the utterance never ended.

const FRAME = 0.02;                       // s per analysis frame
const PREROLL = 0.35;                     // s kept from before speech was detected
const START = 0.12, START_BUSY = 0.25;    // s of voice that opens an utterance (quiet / while she talks)
// s of silence that closes it. Measured on recorded sentences: closed about END after the last word;
// a pause in mid-sentence is kept in up to END - 0.15 s at worst, END - 0.05 s typically (at 0.55,
// no sentence spoken in one go was cut at its commas; at 0.5, 1 in 12). Longer ones: see app.js.
const END = 0.55;
const MIN_SPEECH = 0.3, MAX_UTTERANCE = 15;
const NOISE_MIN = 0.0015;                 // the floor never goes below this (a muted mic is not a quiet room)
const CALIBRATE = 0.4;                    // s of listening to the room before anything counts as speech
const RATE = 16000;                       // what goes to Whisper

const TAP = `
registerProcessor("amadeus-tap", class extends AudioWorkletProcessor {
  process(inputs) { const ch = inputs[0][0]; if (ch) this.port.postMessage(ch.slice()); return true; }
});`;

export class HandsFree {
  // onSpeechStart(): you started talking; onUtterance(wavBlob): you finished (null when it
  // was too short to be speech, e.g. a cough); isBusy(): she is talking right now;
  // onLevel(rms): the mic's loudness, every frame (for the ring around the button)
  constructor({ onSpeechStart, onUtterance, isBusy, onLevel }) {
    Object.assign(this, { onSpeechStart, onUtterance, isBusy, onLevel });
    this.ctx = null;
  }

  get active() { return !!this.ctx; }

  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.ctx = new AudioContext();
    const url = URL.createObjectURL(new Blob([TAP], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);
    const src = this.ctx.createMediaStreamSource(this.stream);
    const tap = new AudioWorkletNode(this.ctx, "amadeus-tap");
    const mute = this.ctx.createGain();
    mute.gain.value = 0;
    src.connect(tap).connect(mute).connect(this.ctx.destination);  // pulled, but never heard
    this.frameLen = Math.round(this.ctx.sampleRate * FRAME);
    this.pending = [];
    this.pendingLen = 0;
    this.preroll = [];
    this.noise = 0;
    this.calib = 0;
    this.recent = [];
    this.utter = null;
    tap.port.onmessage = e => this._feed(e.data);
  }

  stop() {
    this.stream?.getTracks().forEach(t => t.stop());
    this.ctx?.close();
    this.ctx = null;
    this.utter = null;
  }

  get hearing() { return !!this.utter; }

  _feed(chunk) {
    this.pending.push(chunk);
    this.pendingLen += chunk.length;
    while (this.pendingLen >= this.frameLen) {
      const frame = new Float32Array(this.frameLen);
      let off = 0;
      while (off < this.frameLen) {
        const c = this.pending[0], take = Math.min(c.length, this.frameLen - off);
        frame.set(c.subarray(0, take), off);
        off += take;
        if (take === c.length) this.pending.shift(); else this.pending[0] = c.subarray(take);
      }
      this.pendingLen -= this.frameLen;
      this._frame(frame);
    }
  }

  _frame(frame) {
    let sum = 0;
    for (const v of frame) sum += v * v;
    const rms = Math.sqrt(sum / frame.length);
    this.onLevel?.(rms);
    if (!this.utter) {
      const busy = this.isBusy();
      this.preroll.push(frame);
      if (this.preroll.length * FRAME > PREROLL) this.preroll.shift();
      if ((this.calib = (this.calib ?? 0) + 1) <= CALIBRATE / FRAME) {   // first, a moment to hear the room
        this.noise = Math.max(this.noise ? this.noise * 0.8 + rms * 0.2 : rms, NOISE_MIN);
        return;
      }
      const loud = rms > Math.max(this.noise * (busy ? 6 : 3.2), 0.004);
      // learn the room's noise floor from quiet frames only, and only while she's quiet too:
      // quickly down, slowly up, never below NOISE_MIN
      if (!loud && !busy) {
        const n = rms < this.noise ? this.noise * 0.9 + rms * 0.1 : this.noise * 0.995 + rms * 0.005;
        this.noise = Math.max(n, NOISE_MIN);
      }
      // open an utterance when most recent frames are loud (speech dips between syllables).
      // The window is shorter while she's quiet than while she talks: trim it to size, all of it
      // (trimmed one frame at a time, it stayed at her length for good, and nothing opened again).
      const need = Math.round((busy ? START_BUSY : START) / FRAME);
      (this.recent ??= []).push(loud);
      while (this.recent.length > need) this.recent.shift();
      const hits = this.recent.filter(Boolean).length;
      if (loud && this.recent.length >= need && hits >= need * 0.7) {
        this.utter = { frames: [...this.preroll], silent: 0, speech: hits, level: rms };
        this.preroll = [];
        this.recent = [];
        this.onSpeechStart();
      }
      return;
    }
    const u = this.utter;
    u.frames.push(frame);
    // a pause is quiet against the room, or against how loud you were speaking (a noisy room
    // or a fan never counts as you still talking)
    if (rms < Math.max(this.noise * 2, 0.003, u.level * 0.15)) u.silent++;
    else { u.silent = 0; u.speech++; u.level = u.level * 0.9 + rms * 0.1; }
    if (u.silent * FRAME >= END || u.frames.length * FRAME >= MAX_UTTERANCE) {
      this.utter = null;
      this.onUtterance(u.speech * FRAME >= MIN_SPEECH ? wav(u.frames, this.ctx.sampleRate) : null);
    }
  }
}

// Float32 frames at `rate` -> 16 kHz 16-bit mono WAV
function wav(frames, rate) {
  const total = frames.reduce((n, f) => n + f.length, 0);
  const all = new Float32Array(total);
  let o = 0;
  for (const f of frames) { all.set(f, o); o += f.length; }
  const step = rate / RATE, n = Math.floor(total / step);
  const out = new DataView(new ArrayBuffer(44 + n * 2));
  const str = (p, s) => [...s].forEach((c, i) => out.setUint8(p + i, c.charCodeAt(0)));
  str(0, "RIFF"); out.setUint32(4, 36 + n * 2, true); str(8, "WAVEfmt ");
  out.setUint32(16, 16, true); out.setUint16(20, 1, true); out.setUint16(22, 1, true);
  out.setUint32(24, RATE, true); out.setUint32(28, RATE * 2, true); out.setUint16(32, 2, true); out.setUint16(34, 16, true);
  str(36, "data"); out.setUint32(40, n * 2, true);
  for (let i = 0; i < n; i++) {
    // average the samples this output sample covers (a crude low-pass before decimating)
    const a = Math.floor(i * step), b = Math.max(a + 1, Math.floor((i + 1) * step));
    let s = 0;
    for (let j = a; j < b; j++) s += all[j];
    out.setInt16(44 + i * 2, Math.max(-1, Math.min(1, s / (b - a))) * 0x7fff, true);
  }
  return new Blob([out], { type: "audio/wav" });
}
