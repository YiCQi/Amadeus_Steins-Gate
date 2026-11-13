// Mesh-rigged avatar: See-through layers on deformable grids, drawn with WebGL.
//
// rig.json (tools/psd_to_rig.py) lists the parts bottom to top: texture, rectangle,
// grid size and per-vertex depth (px toward the viewer). How strongly each vertex
// follows the head, the hair swing, the eyelids ... is worked out here from the
// part's tag and the face anchors, so tuning needs no re-conversion.
//
// Same interface as the sprite Avatar (load, show, setExpression, attach, speaking,
// fakeTalk); falls back to it when there is no rig or no WebGL.

import { Avatar, FX_SVG } from "./avatar.js";
import { shapeAt } from "./lipsync.js";

const RIG_DIR = "assets/kurisu/rig/";
const DEG = Math.PI / 180;
const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;
const smoothstep = (a, b, x) => { const t = clamp((x - a) / (b - a), 0, 1); return t * t * (3 - 2 * t); };

// Damped spring step for state s = {x, v}; sub-stepped so stiff springs stay stable at low frame rates.
function spring(s, target, w, dt, zeta = 1) {
  const n = Math.max(1, Math.ceil(dt * w / 0.3)), h = dt / n;
  for (let i = 0; i < n; i++) {
    s.v += (w * w * (target - s.x) - 2 * zeta * w * s.v) * h;
    s.x += s.v * h;
  }
}

const FACE_TAGS = new Set(["face", "eyewhite", "irides", "eyelash", "eyebrow", "nose", "mouth", "ears", "earwear", "eyewear", "headwear"]);
const HAIR_TAGS = new Set(["front hair", "back hair"]);

// ---------------------------------------------------------------- parameters

// name: [rest value, spring speed (rad/s)]
const PARAMS = {
  angleX: [0, 4.5], angleY: [0, 4.5], angleZ: [0, 4.5],   // head yaw / pitch / roll, degrees
  bodyX: [0, 2.5], bodyZ: [0, 2.5],                        // body sway: px, degrees
  eyeOpen: [1, 12], eyeSmile: [0, 8],
  eyeAngry: [0, 8], eyeAsym: [0, 8], irisScale: [1, 8],   // glaring lids slant down inward; + narrows the image-right eye
  gazeX: [0, 18], gazeY: [0, 18],                          // -1..1, + = image right / down
  browY: [0, 8], browAngle: [0, 8],                        // browAngle: - angry (inner ends down), + sad
  browAsym: [0, 8],                                        // + raises the image-left brow, lowers the other a little
  mouthForm: [0, 8], mouthWide: [0, 8], mouthSkew: [0, 8], mouthBase: [0, 8],
  mouthPout: [0, 8], mouthWave: [0, 8],                    // pursed, pushed-out lips; a wavy (flustered) line
  cheek: [0, 2.5], hatch: [0, 3], cheekPuff: [0, 4],
};

// Expression poses: only what differs from rest. Angles in degrees; + angleX turns
// her face toward the image's right, + angleY lifts the chin, + angleZ tilts the
// head clockwise (toward the image's right shoulder). Some come in strengths:
// smile < happy < laugh, annoyed < angry < furious.
const POSES = {
  neutral: {},
  smile: { eyeSmile: 0.3, eyeOpen: 0.96, mouthForm: 0.55, browY: 0.15, angleZ: 2.5, angleY: 1.5, cheek: 0.12 },
  happy: { eyeSmile: 0.6, mouthForm: 0.85, browY: 0.35, angleZ: 4, angleY: 2.5, cheek: 0.25 },
  // eyes closed in a happy arc (^^); ?laugh=open squeezes them into crescents instead (笑眼), the iris showing
  laugh: { eyeSmile: 1, eyeOpen: 0.12, mouthForm: 1, mouthBase: 0.55, mouthWide: 0.3, browY: 0.45, browAngle: 0.2, angleY: 5, angleZ: 5, cheek: 0.45 },
  smug: { eyeOpen: 0.8, eyeSmile: 0.12, mouthForm: 0.7, mouthSkew: 0.6, angleY: 5, angleZ: -5, angleX: -4, browY: 0.15 },
  annoyed: { browAngle: -0.9, browY: -0.25, eyeOpen: 0.78, eyeAngry: 0.35, gazeX: -0.45, angleX: -11, angleZ: -2.5, angleY: 1.5, mouthForm: -0.55, cheek: 0.3 },
  angry: { browAngle: -1, browY: -0.45, eyeOpen: 1, eyeAngry: 0.75, mouthForm: -0.7, mouthBase: 0.42, mouthWide: 0.3, angleY: -3, angleX: -4, angleZ: -2, cheek: 0.25 },
  furious: { browAngle: -1, browY: -0.55, eyeOpen: 1.1, eyeAngry: 0.9, irisScale: 0.84, mouthForm: -0.9, mouthBase: 0.62, mouthWide: 0.4, angleY: -4, angleZ: 1.5, cheek: 0.4 },
  pout: { mouthPout: 1, cheekPuff: 1, mouthForm: -0.25, mouthSkew: 0.25, eyeOpen: 0.84, gazeX: -0.6, gazeY: 0.15, browAngle: 0.4, browY: -0.1, angleX: 8, angleZ: 3, angleY: -3, cheek: 0.6 },
  disgusted: { eyeOpen: 0.55, eyeAngry: 0.2, browAngle: -0.35, browY: -0.15, mouthForm: -0.5, mouthSkew: -0.55, angleY: 4, angleX: -3, angleZ: -3, gazeY: 0.1 },
  embarrassed: { cheek: 1, hatch: 1, eyeOpen: 0.8, gazeX: -0.35, gazeY: 0.55, angleY: -7, angleX: 8, angleZ: 3.5, browAngle: 0.55, mouthForm: -0.25, mouthWide: -0.35 },
  sad: { browAngle: 0.95, browY: -0.1, eyeOpen: 0.66, gazeY: 0.55, angleY: -8, angleZ: -2, mouthForm: -0.65 },
  // worried is sad's brows on an open face looking straight at you: sadness turns away and down,
  // worry holds your eye, because it is about you rather than about her
  worried: { browAngle: 0.8, browY: 0.12, eyeOpen: 1.02, gazeY: 0.05, angleX: 3, angleZ: -3,
             mouthForm: -0.4, mouthWide: -0.2, irisScale: 1.03 },
  surprised: { eyeOpen: 1.18, irisScale: 0.9, browY: 1, mouthBase: 0.4, angleY: 4.5, gazeY: -0.05 },
  puzzled: { angleZ: 9, angleX: 4, angleY: 1, browAsym: 0.9, browY: 0.15, eyeOpen: 1.04, eyeAsym: 0.6, mouthForm: -0.1, gazeY: -0.1 },
  thinking: { gazeX: 0.55, gazeY: -0.65, angleZ: -5, angleX: 7, angleY: 4, browAngle: -0.25, browY: 0.1, mouthForm: -0.15 },
  listening: { angleZ: 3.5, angleY: -1.5, browY: 0.2, eyeOpen: 1.04, gazeY: 0.05 },   // UI state, not an LLM emotion
};
// the eyelids as they were before 2026-11-08 (a smiling, closed lash line bent into a wave): ?lids=old
const OLD_LIDS = typeof location !== "undefined" && new URLSearchParams(location.search).get("lids") === "old";
// the laugh with crescent eyes instead, for comparing: open the page with ?laugh=open
if (typeof location !== "undefined" && new URLSearchParams(location.search).get("laugh") === "open")
  Object.assign(POSES.laugh, { eyeSmile: 0.9, eyeOpen: 0.45 });
// how hard a new expression jolts the hair (default 0.4)
const HAIR_KICK = { neutral: 0.2, listening: 0.15, sad: 0.25, thinking: 0.3, smile: 0.3, puzzled: 0.35, pout: 0.5,
  surprised: 1, furious: 1, angry: 0.9, annoyed: 0.8, laugh: 0.8, happy: 0.7, embarrassed: 0.6,
  worried: 0.35 };
// how long she goes between blinks, relative to usual: a glare or a shock holds the eyes open
const BLINK_GAP = { surprised: 1.8, angry: 1.5, furious: 2.2, thinking: 1.3, sad: 0.8, puzzled: 1.2,
  worried: 1.15 };

class Motion {
  constructor() {
    this.p = {};
    this.v = {};
    for (const [k, [rest]] of Object.entries(PARAMS)) { this.p[k] = rest; this.v[k] = 0; }
    this.pose = POSES.neutral;
    this.poseAt = 0;
    this.override = {};            // debug panel: fixed values
    this.blink = { next: 1.5, t: -1, double: false };
    this.gaze = { x: 0, y: 0, next: 1 };
    this.mouth = 0; this.mouthV = 0;
    this.energy = 0; this.energySlow = 0;
    // hair: left lock, bangs, right lock swing sideways; y bobs the tips
    this.hair = { L: { x: 0, v: 0 }, C: { x: 0, v: 0 }, R: { x: 0, v: 0 }, Y: { x: 0, v: 0 } };
    // how she feels, in six parts (server/memory.py): each 0..1 and independent of the others,
    // so she can be flustered and sad at once. Colours every expression from underneath.
    this.feelings = { enjoyment: 0, anger: 0, sadness: 0, fluster: 0, surprise: 0, worry: 0 };
    this.seed = Math.random() * 100;
  }

  setPose(name, t) {
    const was = this.poseName;
    this.pose = POSES[name] || POSES.neutral;
    this.poseName = POSES[name] ? name : "neutral";
    this.poseAt = t;
    // a new expression jolts the hair a little (a hop when surprised, a toss when annoyed)
    if (was && was !== this.poseName) {
      const k = HAIR_KICK[this.poseName] ?? 0.4;
      this.hair.Y.v -= 90 * k;
      this.hair.L.v += 50 * k; this.hair.R.v -= 35 * k; this.hair.C.v += 25 * k;
    }
  }

  // Motion that belongs to an expression itself, too quick for the pose springs: laughing
  // shakes her, anger lands with a shudder, sulking mumbles under her breath, a puzzled head rocks.
  live(out, t, since, silent) {
    const name = this.poseName, s = this.seed;
    if (name === "laugh") {
      // a burst of chuckles as the laugh lands, settling within a second into a broad smile (it
      // used to keep shaking her, at a third of the strength, for as long as the line lasted)
      const a = 1.6 * Math.exp(-since / 0.45), bob = Math.abs(Math.sin(2 * Math.PI * 2.4 * t));
      out.angleY += a * (bob - 0.64);
      out.angleZ += 0.3 * a * Math.sin(2 * Math.PI * 2.4 * t);
      out.hairY += 2 * a * bob;
      if (silent) out.mouthOpen += 0.1 * Math.exp(-since / 0.6) * bob;
    } else if (name === "furious" || name === "angry") {
      // a short shudder as it lands, gone within a second
      const a = (name === "furious" ? 0.5 : 0.25) * Math.exp(-since / 0.35);
      out.angleZ += a * (0.7 * Math.sin(2 * Math.PI * 7.1 * t) + 0.3 * Math.sin(2 * Math.PI * 10.3 * t + 1));
      out.bodyX += a * 0.8 * Math.sin(2 * Math.PI * 8.3 * t);
    } else if (name === "pout" && silent) {
      // bursts of mumbling a few times a minute, a few quick syllables each
      const burst = smoothstep(0.55, 0.9, Math.sin(2 * Math.PI * 0.42 * t + s));
      out.mouthOpen = Math.max(out.mouthOpen, burst * 0.14 * (0.5 + 0.5 * Math.sin(2 * Math.PI * 6.2 * t)));
    } else if (name === "puzzled") {
      out.angleZ += 1.2 * Math.sin(2 * Math.PI * 0.35 * t + s);
    }
  }

  // level: current voice loudness, 0..1 (lip-sync); null when silent
  // shape: {open, wide} of the mora being spoken (lipsync.js), when the line's kana is known
  // vowel: else the spectral tilt of the voice, higher for i/e, lower for o/u
  update(dt, t, level, vowel = null, shape = null) {
    const pose = this.pose, p = this.p, s = this.seed;
    const target = {};
    for (const [k, [rest]] of Object.entries(PARAMS)) target[k] = pose[k] ?? rest;

    // How she feels underneath whatever she's showing. Each of the six lands where it would on
    // a real face, and they add: enjoyment lifts the mouth and softens the eyes, sadness and
    // worry raise the inner brows, anger slants the lids. Never in heavier lids: at rest those
    // read as sleepy, not as cross. The one that earns the six on its own is fluster -- a flush
    // outlasts the line that caused it by a good while, and that lingering is most of what tells
    // you she was embarrassed a minute ago rather than right now.
    const f = this.feelings;
    target.mouthForm += 0.5 * f.enjoyment - 0.45 * f.sadness - 0.35 * f.anger;
    target.eyeSmile += 0.3 * f.enjoyment;
    target.browAngle += 0.45 * f.sadness + 0.35 * f.worry;
    target.eyeAngry += 0.3 * f.anger;
    target.cheek += 0.5 * f.fluster;
    target.eyeOpen += 0.06 * f.worry;

    // idle: slow head drift, so she never freezes
    target.angleX += 2.4 * Math.sin(0.29 * t + s) + 1.1 * Math.sin(0.83 * t + 2 * s);
    target.angleY += 1.6 * Math.sin(0.23 * t + 3 * s) + 0.7 * Math.sin(0.67 * t + s);
    target.angleZ += 1.4 * Math.sin(0.19 * t + 5 * s);
    target.bodyX += 2.0 * Math.sin(0.17 * t + 7 * s);
    target.bodyZ += 0.5 * Math.sin(0.21 * t + 4 * s);

    // gaze: small saccades around the pose's direction; annoyed or sulking, she glances
    // back at you now and then
    if (t > this.gaze.next) {
      const lookBack = (this.poseName === "annoyed" || this.poseName === "pout") && Math.random() < 0.35;
      this.gaze.x = lookBack ? 0.5 : (Math.random() - 0.5) * 0.35;
      this.gaze.y = lookBack ? 0 : (Math.random() - 0.5) * 0.25;
      this.gaze.next = t + (lookBack ? 0.9 : 1.2 + Math.random() * 2.8);
    }
    target.gazeX += this.gaze.x;
    target.gazeY += this.gaze.y;

    // voice: mouth follows loudness fast; head nods on emphasis
    // with a mora timeline the kana picks the shape and loudness only gates it
    const gate = level == null ? 0 : clamp((level - 0.06) / 0.3, 0, 1);
    const want = shape ? shape.open * smoothstep(0.02, 0.25, gate) * (0.6 + 0.4 * gate)
      : level == null ? 0 : clamp((level - 0.1) / 0.7, 0, 1);
    const ms = { x: this.mouth, v: this.mouthV };
    spring(ms, want, want > this.mouth ? 38 : 22, dt);
    this.mouth = clamp(ms.x, 0, 1.1); this.mouthV = ms.v;
    this.energy += ((level ?? 0) - this.energy) * Math.min(1, dt * 10);
    this.energySlow += (this.energy - this.energySlow) * Math.min(1, dt * 1.5);
    // vowel shape relative to this voice's own average: + wide (i, e), - round (o, u)
    if (shape) this.vowelWant = shape.wide;
    else if (vowel != null && level > 0.15) {
      this.vowelMean = this.vowelMean == null ? vowel : this.vowelMean + (vowel - this.vowelMean) * Math.min(1, dt * 0.8);
      this.vowelWant = clamp((vowel - this.vowelMean) * 1.6, -1, 1);
    } else this.vowelWant = 0;
    this.vowel = (this.vowel ?? 0) + ((this.vowelWant ?? 0) - (this.vowel ?? 0)) * Math.min(1, dt * 18);
    target.angleY -= 4 * Math.max(0, this.energy - this.energySlow);
    target.browY += 0.25 * this.energy;

    // a fresh expression lands with a little extra motion
    const since = t - this.poseAt;
    if (since < 0.6) {
      const kick = Math.sin(clamp(since / 0.6, 0, 1) * Math.PI), name = this.poseName;
      if (name === "surprised") { target.angleY += 5 * kick; target.eyeOpen += 0.12 * kick; }
      if (name === "annoyed") target.angleX -= 5 * kick;
      if (name === "embarrassed") target.angleY -= 3 * kick;
      if (name === "angry" || name === "furious") { target.angleY -= 4 * kick; target.browY -= 0.2 * kick; }  // leans in
      if (name === "puzzled") target.angleZ += 4 * kick;
      if (name === "disgusted") target.angleY += 3 * kick;   // pulls back
      if (name === "pout") target.angleX += 4 * kick;        // turns away with a huff
    }

    Object.assign(target, this.override);
    const st = { x: 0, v: 0 };
    for (const k in PARAMS) {
      st.x = p[k]; st.v = this.v[k];
      spring(st, target[k], PARAMS[k][1], dt);
      p[k] = st.x; this.v[k] = st.v;
    }

    // blinking (skipped while the debug panel holds the eyes)
    let blink = 1;
    const b = this.blink;
    if (this.override.eyeOpen == null) {
      if (b.t < 0 && t > b.next) { b.t = 0; b.double = Math.random() < 0.15; }
      if (b.t >= 0) {
        b.t += dt;
        const close = 0.07, hold = 0.045, open = 0.11, len = close + hold + open;
        const u = b.t;
        blink = u < close ? 1 - u / close : u < close + hold ? 0 : clamp((u - close - hold) / open, 0, 1);
        blink = blink * blink * (3 - 2 * blink);
        if (u > len) {
          b.t = -1;
          b.next = t + (b.double ? 0.12 : (2.2 + Math.random() * 4) * (BLINK_GAP[this.poseName] ?? 1));
          if (b.double) b.double = false;
        }
      }
    }

    // hair lags behind head and body moves. The two side locks and the bangs hang on
    // springs of different lengths, so they swing out of step, and a breeze that never
    // quite repeats keeps them moving when she is still. Values are px at the tips.
    const vel = (k, prev) => (p[k] - (this[prev] ?? p[k])) / Math.max(dt, 1e-3);
    const vx = vel("angleX", "prevX"), vz = vel("angleZ", "prevZ"), vy = vel("angleY", "prevY"), vb = vel("bodyX", "prevB");
    this.prevX = p.angleX; this.prevZ = p.angleZ; this.prevY = p.angleY; this.prevB = p.bodyX;
    const push = clamp(-(vx + 0.8 * vz) * 0.75 - vb * 0.8, -36, 36);
    const breeze = (f, ph) => 4.5 * Math.sin(f * t + ph + s) + 2.2 * Math.sin(1.73 * f * t + 2 * ph + 3 * s) + Math.sin(3.1 * f * t + ph);
    const H = this.hair;
    spring(H.L, push + breeze(0.8, 0), 5.2, dt, 0.2);
    spring(H.R, push + breeze(0.95, 2.1), 6, dt, 0.2);
    spring(H.C, 0.7 * push + 0.6 * breeze(1.4, 4.2), 8.5, dt, 0.3);
    spring(H.Y, clamp(vy * 0.6, -14, 14), 7.5, dt, 0.3);

    const out = { ...p };
    out.eyeOpen = Math.max(0, p.eyeOpen) * blink;
    out.mouthOpen = Math.max(p.mouthBase, this.mouth);
    out.mouthWide = p.mouthWide + this.vowel * (shape ? 0.9 : 0.7) * clamp(this.mouth * 2, 0, 1);
    out.hairL = H.L.x; out.hairC = H.C.x; out.hairR = H.R.x; out.hairY = H.Y.x;
    this.live(out, t, since, level == null);
    out.breath = Math.sin(t * 2 * Math.PI / 4.2);
    return out;
  }
}

// ---------------------------------------------------------------- geometry

class Mesh {
  // A grid over rect [x, y, w, h] in rig px; z: per-vertex depth (px toward viewer)
  constructor(name, tag, side, rect, cols, rows, z) {
    Object.assign(this, { name, tag, side, rect, cols, rows });
    const n = cols * rows;
    this.n = n;
    this.rest = new Float32Array(2 * n);
    this.uv = new Float32Array(2 * n);
    this.pos = new Float32Array(2 * n);
    this.z = z ? Float32Array.from(z) : new Float32Array(n);
    const [x, y, w, h] = rect;
    for (let j = 0; j < rows; j++) {
      for (let i = 0; i < cols; i++) {
        const k = j * cols + i, u = i / (cols - 1), v = j / (rows - 1);
        this.rest[2 * k] = x + u * w; this.rest[2 * k + 1] = y + v * h;
        this.uv[2 * k] = u; this.uv[2 * k + 1] = v;
      }
    }
    const idx = [];
    for (let j = 0; j < rows - 1; j++) {
      for (let i = 0; i < cols - 1; i++) {
        const a = j * cols + i, b = a + 1, c = a + cols, d = c + 1;
        idx.push(a, c, b, b, c, d);
      }
    }
    this.index = new Uint16Array(idx);
    this.head = new Float32Array(n).fill(0);
    this.breath = new Float32Array(n).fill(1);
    this.sway = null;
    this.alpha = 1;
  }

  zAt(x, y) {
    const [rx, ry, w, h] = this.rect;
    const fx = clamp((x - rx) / w, 0, 1) * (this.cols - 1), fy = clamp((y - ry) / h, 0, 1) * (this.rows - 1);
    const i = Math.min(Math.floor(fx), this.cols - 2), j = Math.min(Math.floor(fy), this.rows - 2);
    const tx = fx - i, ty = fy - j, c = this.cols, z = this.z;
    const a = z[j * c + i], b = z[j * c + i + 1], d = z[(j + 1) * c + i], e = z[(j + 1) * c + i + 1];
    return lerp(lerp(a, b, tx), lerp(d, e, tx), ty);
  }
}

function interp(xs, vals, x) {
  const n = xs.length;
  if (x <= xs[0]) return vals[0];
  if (x >= xs[n - 1]) return vals[n - 1];
  let i = 1;
  while (xs[i] < x) i++;
  return lerp(vals[i - 1], vals[i], (x - xs[i - 1]) / (xs[i] - xs[i - 1]));
}

// ---------------------------------------------------------------- the rig

class Rig {
  constructor(spec) {
    this.spec = spec;
    const A = spec.anchors;
    this.size = spec.size;
    const h = A.head, chinY = h.chin[1];
    // L, eye line to chin, is the unit for everything measured on the face
    this.face = { cx: h.cx, eyeY: h.eyeY, chinY, L: chinY - h.eyeY, top: h.skullTop ?? h.top, W: h.headW ?? h.faceBox[2] };
    this.meshes = spec.parts.map(p => {
      const m = new Mesh(p.name, p.tag, p.side, p.rect, p.grid[0], p.grid[1], p.z);
      m.file = p.file;
      return m;
    });
    const faceMesh = this.meshes.find(m => m.tag === "face");
    const F = this.face;
    this.pivot = {
      cx: F.cx, cy: F.eyeY + F.L * 0.3, cz: h.pivotZ ?? 0,   // yaw / pitch: the head's axis
      nx: F.cx, ny: chinY + F.L * 0.12,                      // roll: top of the neck
    };
    this.eyes = {};
    for (const e of A.eyes || []) {
      const [x, y, w, h] = e.box;
      this.eyes[e.side] = { ...e, x, y, w, h, cx: x + w / 2, cy: y + h / 2 };
    }
    this.brows = {};
    for (const b of A.brows || []) {
      const [x, y, w, h] = b.box;
      this.brows[b.side] = { x, y, w, h, cx: x + w / 2, cy: y + h / 2 };
    }
    this.bodyTop = A.body?.top ?? chinY + F.L * 0.5;
    for (const m of this.meshes) this.weigh(m);
    this.extras = this.makeExtras(faceMesh, A);
  }

  weigh(m) {
    const F = this.face, H = this.size[1];
    for (let k = 0; k < m.n; k++) {
      const y = m.rest[2 * k + 1];
      let w = 0;
      if (FACE_TAGS.has(m.tag)) w = 1;
      else if (HAIR_TAGS.has(m.tag)) w = lerp(1, 0.18, smoothstep(F.chinY + F.L * 0.1, F.chinY + F.L * 1.8, y));
      else if (m.tag === "neck") w = lerp(0.7, 0, smoothstep(F.chinY - F.L * 0.1, this.bodyTop + F.L * 0.2, y));
      m.head[k] = w;
      // breathing lifts everything above the chest; the bottom edge stays put
      m.breath[k] = smoothstep(H * 1.02, this.bodyTop + F.L * 0.5, y);
    }
    if (HAIR_TAGS.has(m.tag)) {
      // how far each vertex swings (0 at the roots, most at the tips), and which lock it
      // belongs to: the left side, the bangs in the middle, or the right side
      m.sway = new Float32Array(m.n);
      m.lock = new Float32Array(3 * m.n);
      const root = F.top;
      const tip = m.rect[1] + m.rect[3];
      const reach = 0.9;   // the same for front and back hair, so where they overlap they swing together
      for (let k = 0; k < m.n; k++) {
        const x = m.rest[2 * k], y = m.rest[2 * k + 1];
        const t = clamp((y - root) / Math.max(tip - root, 1), 0, 1);
        m.sway[k] = reach * Math.pow(t, 1.6);
        const u = (x - F.cx) / (F.W / 2), l = smoothstep(-0.15, -0.55, u), r = smoothstep(0.15, 0.55, u);
        m.lock.set([l, 1 - l - r, r], 3 * k);
      }
    }
    // eyelids: precompute each vertex's lid lines; the deformation itself is per frame
    if ((m.tag === "eyewhite" || m.tag === "eyelash") && this.eyes[m.side]) {
      const e = this.eyes[m.side];
      const T = new Float32Array(m.n), B = new Float32Array(m.n), U = new Float32Array(m.n);
      const G = new Float32Array(m.n), FU = new Float32Array(m.n);
      const x0 = e.xs[0], x1 = e.xs[e.xs.length - 1], half = (x1 - x0) / 2, mid = (x0 + x1) / 2;
      for (let k = 0; k < m.n; k++) {
        const x = m.rest[2 * k], y = m.rest[2 * k + 1];
        T[k] = interp(e.xs, e.top, x);
        B[k] = interp(e.xs, e.bot, x);
        U[k] = clamp((x - mid) / half, -1, 1);
        const out = Math.max(x0 - x, x - x1, 0);
        G[k] = 1 - smoothstep(0, half * 0.6, out);
        // the lash line rides the lid as one piece; anything high above it (a crease) only partly
        const lashTop = e.lashTop ? interp(e.xs, e.lashTop, x) : T[k] - e.h * 0.3;
        FU[k] = y >= lashTop - 2 ? 1 : lerp(1, 0.35, smoothstep(lashTop - 2, lashTop - 2 - e.h * 0.35, y));
      }
      // the eye's full height, in the middle: what a smiling lid moves by (see deform)
      const Hc = Math.max(...e.xs.map((_, i) => e.bot[i] - e.top[i]));
      m.lid = { e, T, B, U, G, FU, Hc };
    }
    if (m.tag === "irides" && this.eyes[m.side]) m.iris = this.eyes[m.side];
    if (m.tag === "eyebrow" && this.brows[m.side]) {
      const b = this.brows[m.side];
      m.brow = { ...b, sign: m.side === "l" ? -1 : 1, lift: (this.eyes[m.side]?.h ?? b.h * 3) * 0.28 };
    }
    if (m.tag === "mouth") m.alpha = 0;   // the drawn mouth takes over
  }

  // procedural parts: blush, blush lines, mouth
  makeExtras(faceMesh, A) {
    const extras = [];
    const eyes = Object.values(this.eyes);
    if (eyes.length === 2) {
      const ew = (eyes[0].w + eyes[1].w) / 2;
      const left = Math.min(...eyes.map(e => e.x)) - ew * 0.25, right = Math.max(...eyes.map(e => e.x + e.w)) + ew * 0.25;
      const top = Math.max(...eyes.map(e => e.y + e.h)) - ew * 0.05, h = ew * 0.75;
      const rect = [left, top, right - left, h];
      const cheeks = eyes.map(e => ({ x: e.cx + (e.cx < this.face.cx ? -1 : 1) * ew * 0.08 - left, y: e.h * 0.05 + ew * 0.3, r: ew * 0.52 }));
      const blush = makeCanvas(rect, 2, (ctx, s) => {
        for (const c of cheeks) {
          const g = ctx.createRadialGradient(c.x * s, c.y * s, 0, c.x * s, c.y * s, c.r * s);
          g.addColorStop(0, "rgba(255,92,118,0.55)");
          g.addColorStop(0.55, "rgba(255,92,118,0.32)");
          g.addColorStop(1, "rgba(255,92,118,0)");
          ctx.save();
          ctx.translate(c.x * s, c.y * s); ctx.scale(1, 0.42); ctx.translate(-c.x * s, -c.y * s);
          ctx.fillStyle = g; ctx.fillRect(0, 0, ctx.canvas.width, ctx.canvas.height * 3);
          ctx.restore();
        }
      });
      const hatch = makeCanvas(rect, 2, (ctx, s) => {
        ctx.strokeStyle = "rgba(225,62,90,0.75)"; ctx.lineWidth = ew * 0.035 * s; ctx.lineCap = "round";
        for (const c of cheeks) {
          for (let i = -1; i <= 1; i++) {
            const x = (c.x + i * c.r * 0.33) * s, y = c.y * s;
            ctx.beginPath(); ctx.moveTo(x - c.r * 0.09 * s, y + c.r * 0.17 * s); ctx.lineTo(x + c.r * 0.09 * s, y - c.r * 0.17 * s); ctx.stroke();
          }
        }
      });
      for (const [name, canvas, param] of [["blush", blush, "cheek"], ["hatch", hatch, "hatch"]]) {
        const m = this.surfaceMesh(name, rect, faceMesh, 1);
        m.canvas = canvas; m.param = param;
        extras.push(m);
      }
    }
    if (A.mouth) {
      const [x, y, w, h] = A.mouth.box;
      const rect = [x + w / 2 - w * 1.2, y - w * 0.35, w * 2.4, w * 1.6];
      const m = this.surfaceMesh("drawn mouth", rect, faceMesh, 1.5);
      // drawn at the portrait's own resolution, so it is no sharper than the art around it
      m.mouth = { box: A.mouth.box, line: A.mouth.line, scale: 1 };
      m.canvas = document.createElement("canvas");
      m.canvas.width = Math.ceil(rect[2] * m.mouth.scale); m.canvas.height = Math.ceil(rect[3] * m.mouth.scale);
      extras.push(m);
    }
    return extras;
  }

  surfaceMesh(name, rect, faceMesh, lift) {
    const m = new Mesh(name, "face", "", rect, 6, 5, null);
    for (let k = 0; k < m.n; k++) m.z[k] = faceMesh.zAt(m.rest[2 * k], m.rest[2 * k + 1]) + lift;
    m.head.fill(1);
    m.breath.fill(1);
    return m;
  }

  // Puffed cheeks (sulking): how far each vertex of a face-surface mesh moves at full
  // puff, a round bulge out and a little down on each cheek. Worked out on first use.
  puffOf(m) {
    if (m.puff) return m.puff;
    const F = this.face, A = this.spec.anchors.head, half = (A.cheekW ?? F.W * 0.65) / 2;
    const cy = F.eyeY + 0.58 * F.L, R = 0.45 * F.L, out = new Float32Array(2 * m.n);
    for (let k = 0; k < m.n; k++) {
      const x = m.rest[2 * k], y = m.rest[2 * k + 1];
      for (const side of [-1, 1]) {
        const cx = F.cx + side * 0.62 * half, d2 = ((x - cx) ** 2 + (y - cy) ** 2) / (R * R);
        if (d2 >= 1) continue;
        const w = (1 - d2) ** 2;
        out[2 * k] += side * 12 * w;
        out[2 * k + 1] += 3.5 * w;
      }
    }
    return (m.puff = out);
  }

  // ---- per frame
  pose(P) {
    const pv = this.pivot;
    this.H = {
      cx: pv.cx, cy: pv.cy, cz: pv.cz, nx: pv.nx, ny: pv.ny,
      cy_: Math.cos(P.angleX * DEG), sy: Math.sin(P.angleX * DEG),
      cp: Math.cos(P.angleY * DEG), sp: Math.sin(P.angleY * DEG),
      cr: Math.cos(P.angleZ * DEG), sr: Math.sin(P.angleZ * DEG),
      bc: Math.cos(P.bodyZ * DEG), bs: Math.sin(P.bodyZ * DEG),
    };
    // the eye white fades as the lids meet, so its soft rim never shows as a line under a closed eye;
    // so does the iris, a little later: clipped to the white, it showed as a blue band under the
    // closed lids of a laugh (^^)
    const opening = P.eyeOpen * (1 - 0.55 * P.eyeSmile);
    for (const m of this.meshes) {
      if (m.tag === "eyewhite") m.alpha = smoothstep(0.03, 0.2, opening);
      else if (m.tag === "irides") m.alpha = smoothstep(0.05, 0.16, opening);
    }
    for (const m of this.meshes) this.deform(m, P);
    for (const m of this.extras) this.deform(m, P);
  }

  deform(m, P) {
    const H = this.H, rest = m.rest, out = m.pos, z = m.z;
    const W = this.size[0], Hh = this.size[1];
    const breathDy = -P.breath * Hh * 0.0022;
    const bodyPx = W / 2, bodyPy = Hh * 1.1;
    const lid = m.lid, iris = m.iris, brow = m.brow, sway = m.sway;
    let open = 1, smile = 0, angry = 0;
    const inward = m.side === "l" ? 1 : -1;   // the eye's inner corner is toward the face's middle
    if (lid) {
      open = P.eyeOpen * (1 - 0.35 * Math.max(0, inward < 0 ? P.eyeAsym : -P.eyeAsym));
      smile = P.eyeSmile; angry = P.eyeAngry;
    }
    const puff = m.tag === "face" && P.cheekPuff > 0.002 ? this.puffOf(m) : null;
    for (let k = 0; k < m.n; k++) {
      let x = rest[2 * k], y = rest[2 * k + 1];
      if (puff) { x += P.cheekPuff * puff[2 * k]; y += P.cheekPuff * puff[2 * k + 1]; }

      if (lid) {
        const T = lid.T[k], B = lid.B[k], u = lid.U[k], Hl = B - T;
        // where the lids meet when closed: low for a blink (following the eye's shape), an upward
        // arch (^) when smiling. The arch is measured on the eye's full height (Hc), not the height
        // at this point: the eye narrows to almost nothing at its corners, and measured there the
        // lash line dropped less at the very corners than just inside them, a wave with a hook.
        const L = OLD_LIDS ? T + Hl * lerp(0.8, 0.3 + 0.45 * u * u, smile)
          : T + lerp(Hl * 0.8, lid.Hc * (0.27 + 0.18 * u * u), smile);
        // smiling pushes the lower lid up into an arch (most in the middle) even while
        // the eye is open; the upper lid only drops a little
        const Ts = T + (OLD_LIDS ? Hl : lid.Hc) * 0.08 * smile, Bs = B - Hl * smile * (0.5 - 0.3 * u * u);
        let Tn, Bn;
        if (open <= 1) { Tn = Ts + (L - Ts) * (1 - open); Bn = Bs + (L - Bs) * (1 - open); }
        else { Tn = T - (open - 1) * Hl * 0.9; Bn = B + (open - 1) * Hl * 0.12; }
        // a glare: the upper lid comes down, most at the inner corner
        if (angry > 0) Tn = Math.min(Tn + Hl * angry * (0.05 + 0.3 * smoothstep(-0.1, 0.9, u * inward)), Bn - 1);
        let dy;
        if (y <= T) dy = (Tn - T) * lid.FU[k];
        else if (y >= B) dy = Bn - B;
        else dy = Tn + (y - T) / Math.max(B - T, 0.01) * (Bn - Tn) - y;
        y += dy * lid.G[k];
      } else if (iris) {
        if (P.irisScale !== 1) { x = iris.cx + (x - iris.cx) * P.irisScale; y = iris.cy + (y - iris.cy) * P.irisScale; }
        x += P.gazeX * iris.w * 0.15;
        y += P.gazeY * iris.h * 0.13 + (1 - Math.min(P.eyeOpen, 1)) * iris.h * 0.06;
      } else if (brow) {
        const dx = x - brow.cx, dy = y - brow.cy;
        const a = P.browAngle * 11 * DEG * brow.sign, c = Math.cos(a), s = Math.sin(a);
        x = brow.cx + dx * c - dy * s;
        y = brow.cy + dx * s + dy * c - P.browY * brow.lift + (1 - Math.min(P.eyeOpen, 1)) * brow.lift * 0.3;
        y -= P.browAsym * brow.lift * (brow.sign < 0 ? 0.8 : -0.25);   // one brow up (puzzled)
      }

      if (sway) {
        const w = sway[k], g = m.lock, dx = w * (g[3 * k] * P.hairL + g[3 * k + 1] * P.hairC + g[3 * k + 2] * P.hairR);
        x += dx;
        y += w * P.hairY * 0.4 + Math.abs(dx) * 0.08;   // the tips bob, and rise a little as they swing out
      }

      const wh = m.head[k];
      if (wh > 0) {
        // yaw then pitch around the head centre, orthographic, then roll at the neck
        const dx = x - H.cx, dy = y - H.cy, dz = z[k] - H.cz;
        const x1 = dx * H.cy_ + dz * H.sy;
        const z1 = -dx * H.sy + dz * H.cy_;
        const y1 = dy * H.cp - z1 * H.sp;
        const X = H.cx + x1 - H.nx, Y = H.cy + y1 - H.ny;
        const rx = H.nx + X * H.cr - Y * H.sr, ry = H.ny + X * H.sr + Y * H.cr;
        x += (rx - x) * wh;
        y += (ry - y) * wh;
      }

      // breathing, then the whole body sways from below
      y += breathDy * m.breath[k];
      const bx = x - bodyPx, by = y - bodyPy;
      x = bodyPx + bx * H.bc - by * H.bs + P.bodyX;
      y = bodyPy + bx * H.bs + by * H.bc;
      out[2 * k] = x; out[2 * k + 1] = y;
    }
  }
}

function makeCanvas(rect, scale, draw) {
  const c = document.createElement("canvas");
  c.width = Math.ceil(rect[2] * scale); c.height = Math.ceil(rect[3] * scale);
  draw(c.getContext("2d"), scale);
  return c;
}

// The mouth is drawn rather than warped: the portrait only has one mouth shape. It copies
// the portrait's soft, slightly blurred line art: lines that thin out toward the corners, a
// soft edge (shadowBlur, which every browser has), and muted colours inside.
function drawMouth(m, P) {
  const ctx = m.canvas.getContext("2d"), s = m.mouth.scale;
  const [bx, by, bw, bh] = m.mouth.box, [rx, ry] = m.rect;
  const cvs = m.canvas;
  ctx.clearRect(0, 0, cvs.width, cvs.height);
  const open = clamp(P.mouthOpen, 0, 1.1), form = P.mouthForm, wide = P.mouthWide, skew = P.mouthSkew;
  const pout = clamp(P.mouthPout ?? 0, 0, 1), wave = clamp(P.mouthWave ?? 0, 0, 1);
  const cx = (bx + bw / 2 - rx) * s, cy = (by + bh * 0.42 - ry) * s;
  const mw = bw * s;
  // pursed lips draw the corners in
  const half = mw * (0.4 + 0.1 * wide - 0.1 * open + 0.06 * Math.max(form, 0)) * (1 - 0.5 * pout);
  const lift = -form * mw * 0.1 * (1 - 0.5 * pout);
  const tilt = skew * mw * 0.07;
  const lx = cx - half, ly = cy + lift + tilt, rxp = cx + half, ryp = cy + lift - tilt;
  const O = open * mw * 0.42 * (1 - 0.25 * wide);
  const arch = cy - mw * 0.025 + Math.max(form, 0) * mw * 0.03;
  const soft = (on) => { ctx.shadowColor = m.mouth.line; ctx.shadowBlur = on ? 1.6 * s : 0; };
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  ctx.strokeStyle = ctx.fillStyle = m.mouth.line;
  soft(true);
  if (O < 1.6 * s) {
    // a wavy line when flustered or puzzled (the anime "~" mouth)
    taper(ctx, lx, ly, cx, arch + O + form * mw * 0.06, rxp, ryp, 2 * s, 0.6 * s, wave * mw * 0.045);
    if (pout > 0.05) {   // the pushed-out lower lip, a short faint curve under the line
      ctx.globalAlpha = 0.5 * pout;
      taper(ctx, cx - half * 0.7, cy + mw * 0.06, cx, cy + mw * 0.14, cx + half * 0.7, cy + mw * 0.06, 1.4 * s, 0.4 * s);
      ctx.globalAlpha = 1;
    }
    return;
  }
  const bottom = cy + O * (1 + 0.15 * Math.max(form, 0));
  ctx.beginPath();
  ctx.moveTo(lx, ly);
  ctx.quadraticCurveTo(cx, arch, rxp, ryp);
  ctx.quadraticCurveTo(cx + half * 0.15, bottom + O * 0.25, cx, bottom);
  ctx.quadraticCurveTo(cx - half * 0.15, bottom + O * 0.25, lx, ly);
  ctx.closePath();
  ctx.fillStyle = "#5a2a33";
  ctx.fill();
  ctx.save();
  ctx.clip();
  soft(false);
  ctx.fillStyle = "#e6dedd";                       // upper teeth
  ctx.fillRect(lx, arch - s, half * 2, Math.min(O * 0.3, mw * 0.08) + s);
  if (O > mw * 0.14) {                               // tongue
    ctx.fillStyle = "#b5717a";
    ctx.beginPath();
    ctx.ellipse(cx, bottom + O * 0.05, half * 0.62, O * 0.42, 0, 0, Math.PI * 2);
    ctx.fill();
  }
  ctx.restore();
  ctx.fillStyle = m.mouth.line;
  soft(true);
  taper(ctx, lx, ly, cx, arch, rxp, ryp, 2.2 * s, 0.8 * s);
  ctx.globalAlpha = 0.5; ctx.lineWidth = 1.1 * s;
  ctx.beginPath(); ctx.moveTo(lx, ly); ctx.quadraticCurveTo(cx - half * 0.1, bottom + O * 0.2, cx, bottom);
  ctx.quadraticCurveTo(cx + half * 0.1, bottom + O * 0.2, rxp, ryp); ctx.stroke();
  ctx.globalAlpha = 1;
}

// A quadratic curve filled as a stroke that is wMid wide in the middle and wEnd at the ends,
// optionally rippled by `wave` px (one and a half waves along it).
function taper(ctx, x0, y0, qx, qy, x1, y1, wMid, wEnd, wave = 0) {
  const n = 24, a = [], b = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t;
    const x = u * u * x0 + 2 * u * t * qx + t * t * x1;
    const y = u * u * y0 + 2 * u * t * qy + t * t * y1 + wave * Math.sin(3 * Math.PI * t) * Math.sin(Math.PI * t);
    const dx = u * (qx - x0) + t * (x1 - qx), dy = u * (qy - y0) + t * (y1 - qy), len = Math.hypot(dx, dy) || 1;
    const w = (wEnd + (wMid - wEnd) * Math.sin(Math.PI * t)) / 2;
    a.push([x - dy / len * w, y + dx / len * w]);
    b.push([x + dy / len * w, y - dx / len * w]);
  }
  ctx.beginPath();
  for (const [x, y] of a) ctx.lineTo(x, y);
  for (const [x, y] of b.reverse()) ctx.lineTo(x, y);
  ctx.closePath();
  ctx.fill();
}

// ---------------------------------------------------------------- WebGL

const VS = `
attribute vec2 aPos;
attribute vec2 aUV;
uniform vec2 uSize;
varying vec2 vUV;
void main() {
  vUV = aUV;
  gl_Position = vec4(aPos.x / uSize.x * 2.0 - 1.0, 1.0 - aPos.y / uSize.y * 2.0, 0.0, 1.0);
}`;

const FS = `
precision mediump float;
uniform sampler2D uTex;
uniform float uAlpha;
uniform float uCut;
uniform vec2 uScan;   // scanline period (device px), strength
varying vec2 vUV;
void main() {
  vec4 c = texture2D(uTex, vUV);
  if (c.a < uCut) discard;
  c *= uAlpha;
  c.rgb *= 1.0 - uScan.y * (0.5 + 0.5 * cos(6.2831853 * gl_FragCoord.y / uScan.x));
  gl_FragColor = c;
}`;

class Renderer {
  constructor(canvas, rig) {
    this.canvas = canvas;
    this.rig = rig;
    // no MSAA: every edge is a texture's alpha, not geometry, and it costs memory on phones
    const opts = { alpha: false, antialias: false, stencil: true, premultipliedAlpha: true };
    const gl = canvas.getContext("webgl2", opts) || canvas.getContext("webgl", opts);
    if (!gl) throw new Error("WebGL unavailable");
    this.gl = gl;
    this.gl2 = typeof WebGL2RenderingContext !== "undefined" && gl instanceof WebGL2RenderingContext;
    const prog = gl.createProgram();
    for (const [type, src] of [[gl.VERTEX_SHADER, VS], [gl.FRAGMENT_SHADER, FS]]) {
      const sh = gl.createShader(type);
      gl.shaderSource(sh, src);
      gl.compileShader(sh);
      if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(sh));
      gl.attachShader(prog, sh);
    }
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    gl.useProgram(prog);
    this.loc = {
      pos: gl.getAttribLocation(prog, "aPos"), uv: gl.getAttribLocation(prog, "aUV"),
      size: gl.getUniformLocation(prog, "uSize"), alpha: gl.getUniformLocation(prog, "uAlpha"),
      cut: gl.getUniformLocation(prog, "uCut"), scan: gl.getUniformLocation(prog, "uScan"),
    };
    gl.enableVertexAttribArray(this.loc.pos);
    gl.enableVertexAttribArray(this.loc.uv);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.uniform2f(this.loc.size, rig.size[0], rig.size[1]);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    for (const m of [...rig.meshes, ...rig.extras]) this.upload(m);
  }

  upload(m) {
    const gl = this.gl;
    m.vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, m.vbo);
    gl.bufferData(gl.ARRAY_BUFFER, m.pos.byteLength, gl.DYNAMIC_DRAW);
    m.tbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, m.tbo);
    gl.bufferData(gl.ARRAY_BUFFER, m.uv, gl.STATIC_DRAW);
    m.ibo = gl.createBuffer();
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.ibo);
    gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, m.index, gl.STATIC_DRAW);
    m.tex = gl.createTexture();
  }

  texture(m, source, mipmap = true) {
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, m.tex);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    if (mipmap && this.gl2) {
      gl.generateMipmap(gl.TEXTURE_2D);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    } else {
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    }
  }

  resize() {
    const c = this.canvas, dpr = Math.min(devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.round(c.clientWidth * dpr)), h = Math.max(1, Math.round(c.clientHeight * dpr));
    if (c.width === w && c.height === h && this.scan) return;
    c.width = w; c.height = h;
    // the browser may give a big canvas a smaller drawing buffer; draw into what we really got
    const gl = this.gl, bw = gl.drawingBufferWidth, bh = gl.drawingBufferHeight;
    gl.viewport(0, 0, bw, bh);
    // the source's scanlines: a 4 px period in rig px; fade out where the screen can't resolve it
    const period = 4 * bh / this.rig.size[1];
    this.scan = [period, 0.05 * smoothstep(1.8, 3, period)];
  }

  draw(m, alpha, cut = 0) {
    const gl = this.gl;
    gl.bindBuffer(gl.ARRAY_BUFFER, m.vbo);
    gl.bufferSubData(gl.ARRAY_BUFFER, 0, m.pos);
    gl.vertexAttribPointer(this.loc.pos, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ARRAY_BUFFER, m.tbo);
    gl.vertexAttribPointer(this.loc.uv, 2, gl.FLOAT, false, 0, 0);
    gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, m.ibo);
    gl.bindTexture(gl.TEXTURE_2D, m.tex);
    gl.uniform1f(this.loc.alpha, alpha);
    gl.uniform1f(this.loc.cut, cut);
    gl.drawElements(gl.TRIANGLES, m.index.length, gl.UNSIGNED_SHORT, 0);
  }

  frame(P) {
    const gl = this.gl, rig = this.rig;
    gl.clearColor(0.012, 0.014, 0.016, 1);
    gl.clearStencil(0);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.STENCIL_BUFFER_BIT);
    gl.uniform2f(this.loc.scan, this.scan[0], this.scan[1]);
    const extras = Object.fromEntries(rig.extras.map(m => [m.name, m]));
    const stencilRef = {};
    for (const m of rig.meshes) if (m.tag === "eyewhite" && m.side) stencilRef[m.side] = m.side === "l" ? 1 : 2;
    for (const m of rig.meshes) {
      if (m.tag === "irides" && stencilRef[m.side]) {
        gl.enable(gl.STENCIL_TEST);
        gl.stencilFunc(gl.EQUAL, stencilRef[m.side], 0xff);
        gl.stencilOp(gl.KEEP, gl.KEEP, gl.KEEP);
        this.draw(m, m.alpha);
        gl.disable(gl.STENCIL_TEST);
      } else if (m.alpha > 0) {
        this.draw(m, m.alpha);
      }
      if (m.tag === "eyewhite" && stencilRef[m.side]) {
        // mark the (blinking) eye white so the iris above it is clipped to it
        gl.enable(gl.STENCIL_TEST);
        gl.stencilFunc(gl.ALWAYS, stencilRef[m.side], 0xff);
        gl.stencilOp(gl.KEEP, gl.KEEP, gl.REPLACE);
        gl.colorMask(false, false, false, false);
        this.draw(m, 1, 0.3);
        gl.colorMask(true, true, true, true);
        gl.disable(gl.STENCIL_TEST);
      }
      if (m.tag === "face") {
        for (const name of ["blush", "hatch"]) {
          const e = extras[name];
          if (e && P[e.param] > 0.01) this.draw(e, clamp(P[e.param], 0, 1));
        }
      }
      if (m.tag === "mouth" || (m.tag === "nose" && !rig.meshes.some(q => q.tag === "mouth"))) {
        const e = extras["drawn mouth"];
        if (e) this.draw(e, 1);
      }
    }
  }
}

// ---------------------------------------------------------------- avatar

export class RigAvatar {
  constructor(el, spec) {
    this.el = el;
    this.spec = spec;
    this.analyser = null;
    this.speaking = false;
    this._fake = 0;
    this.motion = new Motion();
  }

  async load() {
    try {
      await this._load();
    } catch (e) {
      console.warn("rig failed, using sprites:", e);
      this.el.classList.remove("gl");
      this.el.replaceChildren();
      this.fallback = new Avatar(this.el);
      if (this.analyser) this.fallback.attach(this.analyser);
      await this.fallback.load();
    }
  }

  async _load() {
    const rig = new Rig(this.spec);
    this.rig = rig;
    const wrap = document.createElement("div");
    wrap.className = "rig";
    const canvas = document.createElement("canvas");
    wrap.append(canvas);
    const fx = document.createElement("div");
    fx.className = "fx";
    fx.innerHTML = FX_SVG;
    wrap.append(fx);
    this.el.append(wrap);
    this.el.classList.add("gl");
    this.renderer = new Renderer(canvas, rig);
    await Promise.all(rig.meshes.map(m => new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => { this.renderer.texture(m, img); resolve(); };
      img.onerror = () => reject(new Error("missing " + m.file));
      img.src = RIG_DIR + m.file + "?v=" + (this.spec.build ?? "");  // a rebuilt rig is never served stale
    })));
    for (const e of rig.extras) if (!e.mouth) this.renderer.texture(e, e.canvas);
    this.renderer.resize();
    this.setExpression(this.el.dataset.emotion || "neutral");
    if (new URLSearchParams(location.search).has("rigdebug")) debugPanel(this);
    this._loop();
  }

  show() { this.el.classList.add("on"); }

  setExpression(emotion) {
    this.el.dataset.emotion = emotion;
    if (this.fallback) return this.fallback.setExpression(emotion);
    if (emotion !== this.motion.poseName) this.motion.setPose(emotion, performance.now() / 1000);
  }

  // how she feels, six parts each 0..1 (server/memory.py); it eases in through the pose springs
  setFeeling(f) {
    if (!f) return;
    for (const k of Object.keys(this.motion.feelings))
      if (Number.isFinite(f[k])) this.motion.feelings[k] = Math.min(1, Math.max(0, f[k]));
  }

  attach(analyser) {
    this.analyser = analyser;
    this._buf = new Float32Array(analyser.fftSize);
    this._peak = 0.08;
    this.fallback?.attach(analyser);
  }

  get speaking() { return this._speaking; }
  set speaking(v) {
    this._speaking = v;
    if (!v) this._tl = null;
    if (this.fallback) this.fallback.speaking = v;
  }

  // Mora timeline (lipsync.js) of the line that starts playing at audio-clock time t0
  lipSync(tl, t0) { this._tl = tl; this._tl0 = t0; }

  fakeTalk(ms) {
    if (this.fallback) return this.fallback.fakeTalk(ms);
    this._fake = performance.now() + ms;
    return new Promise(r => setTimeout(r, ms));
  }

  _level(now) {
    this._vowel = null;
    this._shape = null;
    if (this.analyser && this._speaking) {
      const an = this.analyser;
      if (this._tl) this._shape = shapeAt(this._tl, an.context.currentTime - this._tl0);
      an.getFloatTimeDomainData(this._buf);
      let sum = 0;
      for (const v of this._buf) sum += v * v;
      const rms = Math.sqrt(sum / this._buf.length);
      this._peak = Math.max(rms, this._peak * 0.995, 0.04);
      // F1 band vs F2 band energy: front vowels (i, e) carry more above 1.2 kHz
      this._spec ??= new Float32Array(an.frequencyBinCount);
      an.getFloatFrequencyData(this._spec);
      const hz = an.context.sampleRate / an.fftSize, band = (a, b) => {
        let e = 0;
        for (let i = Math.floor(a / hz); i < Math.min(this._spec.length, Math.ceil(b / hz)); i++) e += 10 ** (this._spec[i] / 10);
        return e;
      };
      this._vowel = Math.log10((band(1200, 3200) + 1e-12) / (band(250, 900) + 1e-12));
      return rms / this._peak;
    }
    if (now < this._fake) {
      const t = now / 1000;
      return Math.max(0, Math.sin(t * 2 * Math.PI * 3.6) * 0.6 + Math.sin(t * 2 * Math.PI * 5.3) * 0.4) * 0.95;
    }
    return null;
  }

  _loop() {
    let last = performance.now();
    const step = () => {
      const now = performance.now();
      const dt = clamp((now - last) / 1000, 0, 0.05);
      last = now;
      const level = this._level(now);
      const P = this.motion.update(dt, now / 1000, level, this._vowel, this._shape);
      this.P = P;
      this.rig.pose(P);
      const mouth = this.rig.extras.find(e => e.mouth);
      const shape = mouth && [P.mouthOpen, P.mouthForm, P.mouthWide, P.mouthSkew, P.mouthPout, P.mouthWave].map(v => Math.round(v * 200)).join();
      if (mouth && shape !== this._mouthShape) {
        this._mouthShape = shape;
        drawMouth(mouth, P);
        this.renderer.texture(mouth, mouth.canvas, false);
      }
      this.renderer.resize();   // cheap when nothing changed; catches zoom / devicePixelRatio changes
      this.renderer.frame(P);
      this._last = now;
    };
    const raf = () => { step(); requestAnimationFrame(raf); };
    requestAnimationFrame(raf);
    // requestAnimationFrame stops while the page is hidden; keep the mouth honest anyway
    setInterval(() => { if (performance.now() - (this._last || 0) > 120) step(); }, 100);
  }
}

// Rig if one has been built (tools/psd_to_rig.py) and WebGL works, else sprites.
// ?avatar=sprites forces the sprites.
export async function createAvatar(el) {
  if (new URLSearchParams(location.search).get("avatar") !== "sprites") {
    try {
      const r = await fetch(RIG_DIR + "rig.json", { cache: "no-cache" });
      if (r.ok) return new RigAvatar(el, await r.json());
    } catch {}
  }
  return new Avatar(el);
}

// ---------------------------------------------------------------- ?rigdebug

function debugPanel(avatar) {
  window.rigAvatar = avatar;   // for tuning from the console
  // redraws the mouth for a pose rendered by hand (frame(P) alone keeps the last mouth)
  window.rigDrawMouth = P => {
    const mouth = avatar.rig.extras.find(e => e.mouth);
    if (mouth) { drawMouth(mouth, P); avatar.renderer.texture(mouth, mouth.canvas, false); }
  };
  const m = avatar.motion;
  const box = document.createElement("div");
  box.style.cssText = "position:fixed;right:8px;top:8px;z-index:50;width:250px;max-height:96vh;overflow:auto;padding:10px;background:#000c;border:1px solid #333;font:12px monospace;color:#ccc";
  const ranges = {
    angleX: [-30, 30], angleY: [-20, 20], angleZ: [-20, 20], bodyX: [-20, 20], bodyZ: [-5, 5],
    eyeOpen: [0, 1.3], eyeSmile: [0, 1], eyeAngry: [0, 1], eyeAsym: [-1, 1], irisScale: [0.7, 1.2], gazeX: [-1, 1], gazeY: [-1, 1], browY: [-1, 1], browAngle: [-1, 1],
    mouthForm: [-1, 1], mouthWide: [-1, 1], mouthSkew: [-1, 1], mouthBase: [0, 1], mouthPout: [0, 1], mouthWave: [0, 1],
    browAsym: [-1, 1], cheek: [0, 1], hatch: [0, 1], cheekPuff: [0, 1],
  };
  const btns = document.createElement("div");
  btns.style.cssText = "display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px";
  for (const name of Object.keys(POSES)) {
    const b = document.createElement("button");
    b.textContent = name;
    b.onclick = () => avatar.setExpression(name);
    btns.append(b);
  }
  const talk = document.createElement("button");
  talk.textContent = "talk 3s";
  talk.onclick = () => avatar.fakeTalk(3000);
  btns.append(talk);
  box.append(btns);
  for (const [k, [a, b]] of Object.entries(ranges)) {
    const row = document.createElement("label");
    row.style.cssText = "display:grid;grid-template-columns:78px 1fr 34px;gap:4px;align-items:center";
    const input = Object.assign(document.createElement("input"), { type: "range", min: a, max: b, step: (b - a) / 200, value: PARAMS[k][0] });
    const val = document.createElement("span");
    const hold = Object.assign(document.createElement("input"), { type: "checkbox", title: "hold" });
    const sync = () => {
      val.textContent = Number(input.value).toFixed(2);
      if (hold.checked) m.override[k] = Number(input.value); else delete m.override[k];
    };
    input.oninput = () => { hold.checked = true; sync(); };
    hold.onchange = sync;
    row.append(Object.assign(document.createElement("span"), { textContent: k }), input, val);
    row.prepend(hold);
    row.style.gridTemplateColumns = "14px 78px 1fr 34px";
    box.append(row);
    sync();
  }
  document.body.append(box);
}
