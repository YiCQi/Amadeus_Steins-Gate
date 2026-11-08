// Layered sprite avatar: expression sets, blinking, and amplitude lip-sync.
//
// sprites.json lists expressions; each has a full-canvas `base` image plus
// optional full-canvas overlays (eyes_closed, eyes_half, mouth_closed,
// mouth_half, mouth_open). A missing overlay simply means "the base already
// looks like that".

const ROOT = "assets/kurisu/";

// Anime "manpu" symbols drawn over any sprite. Which ones show is decided in CSS
// by #avatar[data-emotion]; positions are % of the 834x1260 portrait.
export const FX_SVG = `
<div class="blush"></div>
<svg class="hatch l" viewBox="0 0 100 40"><g><path d="M18 34 L32 6"/><path d="M40 34 L54 6"/><path d="M62 34 L76 6"/></g></svg>
<svg class="hatch r" viewBox="0 0 100 40"><g><path d="M24 34 L38 6"/><path d="M46 34 L60 6"/><path d="M68 34 L82 6"/></g></svg>
<svg class="vein" viewBox="0 0 100 100"><g><path d="M40 10 Q42 38 12 40"/><path d="M60 10 Q58 38 88 40"/><path d="M40 90 Q42 62 12 60"/><path d="M60 90 Q58 62 88 60"/></g></svg>
<svg class="gloom" viewBox="0 0 100 60" preserveAspectRatio="none">
  <defs><linearGradient id="gloom-g" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="0" y2="56"><stop offset="0" stop-color="#2b3290" stop-opacity=".85"/><stop offset="1" stop-color="#2b3290" stop-opacity="0"/></linearGradient></defs>
  <g stroke="url(#gloom-g)">${Array.from({ length: 11 }, (_, i) => `<path d="M${6 + i * 8.8} 0 V${38 + (i % 3) * 9}"/>`).join("")}</g>
</svg>
<svg class="sparkle s1" viewBox="0 0 40 40"><path d="M20 0 Q22 18 40 20 Q22 22 20 40 Q18 22 0 20 Q18 18 20 0Z"/></svg>
<svg class="sparkle s2" viewBox="0 0 40 40"><path d="M20 0 Q22 18 40 20 Q22 22 20 40 Q18 22 0 20 Q18 18 20 0Z"/></svg>
<svg class="shock" viewBox="0 0 100 100"><g><path d="M50 8 V30"/><path d="M20 22 L34 40"/><path d="M80 22 L66 40"/></g></svg>
<svg class="dots" viewBox="0 0 100 30"><circle cx="20" cy="15" r="7"/><circle cx="50" cy="15" r="7"/><circle cx="80" cy="15" r="7"/></svg>
<svg class="qmark" viewBox="0 0 60 90"><path d="M14 26 Q14 6 31 6 Q48 6 48 24 Q48 36 34 44 Q29 48 29 60"/><circle cx="29" cy="78" r="6"/></svg>
<svg class="moya" viewBox="0 0 120 50"><path d="M6 30 C14 6 24 46 34 22 S52 44 62 20 S82 46 92 22 S110 40 114 26"/><path d="M20 40 C28 26 38 52 48 34 S66 50 76 34"/></svg>
<svg class="steam" viewBox="0 0 200 60"><path d="M8 52 Q2 36 16 32 Q14 16 30 18 Q36 4 50 14 Q60 8 62 22 Q72 26 64 40 Q60 52 46 48 Q36 58 24 50 Q14 58 8 52Z"/><path d="M192 52 Q198 36 184 32 Q186 16 170 18 Q164 4 150 14 Q140 8 138 22 Q128 26 136 40 Q140 52 154 48 Q164 58 176 50 Q186 58 192 52Z"/></svg>
`;

export class Avatar {
  constructor(el) {
    this.el = el;
    this.rig = document.createElement("div");
    this.rig.className = "rig";
    this.el.append(this.rig);
    this.sets = {};          // name -> { node, layers: {eyes_closed: img, ...} }
    this.aliases = {};
    this.current = null;
    this.mouth = 0;          // 0 closed, 1 half, 2 open
    this.analyser = null;
    this._fake = 0;
  }

  async load() {
    const spec = await fetch(ROOT + "sprites.json").then(r => r.json());
    this.aliases = spec.aliases || {};
    const defaultName = spec.default || Object.keys(spec.expressions)[0];
    const loads = [];
    for (const [name, def] of Object.entries(spec.expressions)) {
      const node = document.createElement("div");
      node.className = "expr";
      node.hidden = true;
      const layers = {};
      for (const key of ["base", "eyes_half", "eyes_closed", "mouth_closed", "mouth_half", "mouth_open"]) {
        if (!def[key]) continue;
        const img = new Image();
        img.src = ROOT + def[key];
        img.alt = "";
        img.draggable = false;
        if (key !== "base") img.classList.add("off");
        // onload rather than img.decode(): decode() can stall while the page is hidden
        loads.push(new Promise(r => { img.onload = img.onerror = r; }));
        node.append(img);
        layers[key] = img;
      }
      this.rig.append(node);
      this.sets[name] = { node, layers };
    }
    const fx = document.createElement("div");
    fx.className = "fx";
    fx.innerHTML = FX_SVG;
    this.rig.append(fx);
    await Promise.all(loads);
    this.defaultName = defaultName;
    this.setExpression(this.el.dataset.emotion || defaultName);
    this._scheduleBlink();
    this._loop();
  }

  show() { this.el.classList.add("on"); }

  setExpression(emotion) {
    this.el.dataset.emotion = emotion;
    if (!this.defaultName) return;  // sprites not loaded yet
    const name = this.sets[emotion] ? emotion : (this.sets[this.aliases[emotion]] ? this.aliases[emotion] : this.defaultName);
    if (name === this.current) return;
    if (this.current) this.sets[this.current].node.hidden = true;
    this.sets[name].node.hidden = false;
    this.current = name;
    this._applyMouth(true);
  }

  get layers() { return this.sets[this.current]?.layers || {}; }

  // ----- blinking -----
  _scheduleBlink() {
    const delay = 2200 + Math.random() * 3800;
    setTimeout(() => this._blink().then(() => this._scheduleBlink()), delay);
  }

  async _blink() {
    const L = this.layers;
    const wait = ms => new Promise(r => setTimeout(r, ms));
    if (!L.eyes_closed) return;
    const twice = Math.random() < 0.15;
    for (let i = 0; i < (twice ? 2 : 1); i++) {
      if (L.eyes_half) { L.eyes_half.classList.remove("off"); await wait(45); L.eyes_half.classList.add("off"); }
      L.eyes_closed.classList.remove("off"); await wait(110); L.eyes_closed.classList.add("off");
      if (L.eyes_half) { L.eyes_half.classList.remove("off"); await wait(45); L.eyes_half.classList.add("off"); }
      if (twice) await wait(140);
    }
  }

  // ----- mouth -----
  _applyMouth(force = false) {
    const L = this.layers;
    const want = this.mouth;
    if (!force && want === this._shown) return;
    this._shown = want;
    for (const k of ["mouth_closed", "mouth_half", "mouth_open"]) L[k]?.classList.add("off");
    const pick = want === 0 ? L.mouth_closed : want === 1 ? (L.mouth_half || L.mouth_open) : L.mouth_open;
    pick?.classList.remove("off");
  }

  attach(analyser) {
    this.analyser = analyser;
    this._buf = new Float32Array(analyser.fftSize);
    this._peak = 0.08;
  }

  // Flap the mouth for `ms` when there is no audio (voice offline).
  fakeTalk(ms) {
    this._fake = performance.now() + ms;
    return new Promise(r => setTimeout(r, ms));
  }

  _loop() {
    // A timer rather than requestAnimationFrame: rAF pauses whenever the page
    // is hidden, and 30 fps is plenty for mouth flaps.
    let held = 0;
    const tick = () => {
      const now = performance.now();
      let level = 0;
      if (this.analyser && this.speaking) {
        this.analyser.getFloatTimeDomainData(this._buf);
        let sum = 0;
        for (const v of this._buf) sum += v * v;
        const rms = Math.sqrt(sum / this._buf.length);
        this._peak = Math.max(rms, this._peak * 0.995, 0.04);
        const rel = rms / this._peak;
        level = rel > 0.55 ? 2 : rel > 0.28 ? 1 : 0;
      } else if (now < this._fake) {
        level = Math.floor(now / 110) % 3 === 0 ? 0 : 2;
      }
      // hold each mouth shape for at least ~60ms so it doesn't buzz
      if (level !== this.mouth && now - held > 60) {
        this.mouth = level;
        held = now;
        this._applyMouth();
      }
    };
    setInterval(tick, 33);
  }
}
