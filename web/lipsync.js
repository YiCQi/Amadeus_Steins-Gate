// Mouth shapes per mora, timed to her voice.
//
// The server sends each line's kana reading (kanji already read), and the decoded audio
// shows where the voiced stretches are. Japanese is mora-timed, so spreading each phrase's
// morae evenly over its stretch of voice lines up well, with no aligner and no extra wait.

const ROWS = {
  a: "あかさたなはまやらわがざだばぱぁゃゎ",
  i: "いきしちにひみりぎじぢびぴぃ",
  u: "うくすつぬふむゆるぐずづぶぷぅゅゔ",
  e: "えけせてねへめれげぜでべぺぇ",
  o: "おこそとのほもよろをごぞどぼぽぉょ",
};
const VOWEL = {};
for (const [v, chars] of Object.entries(ROWS)) for (const c of chars) VOWEL[c] = v;
const SMALL = "ぁぃぅぇぉゃゅょゎ";
const PAUSE = "、。！？!?…,.．・「」『』（）()";

// [open 0..1, wide -1 (round) .. 1 (spread)]
const SHAPES = { a: [1, 0.15], i: [0.38, 0.85], u: [0.3, -0.75], e: [0.62, 0.55], o: [0.75, -0.6], N: [0.1, -0.1], Q: [0.06, 0] };

// kana -> [{v, w}] morae and {pause} markers
export function morae(kana) {
  const out = [];
  for (let ch of kana || "") {
    const code = ch.charCodeAt(0);
    if (code >= 0x30a1 && code <= 0x30f6) ch = String.fromCharCode(code - 0x60);  // katakana -> hiragana
    const last = out[out.length - 1];
    if (SMALL.includes(ch) && last && !last.pause && last.v !== "Q" && last.v !== "N") last.v = VOWEL[ch];  // きゃ, てぃ
    else if (ch === "っ") out.push({ v: "Q", w: 0.6 });
    else if (ch === "ん") out.push({ v: "N", w: 0.9 });
    else if (ch === "ー" || ch === "〜") { if (last && !last.pause) out.push({ v: last.v, w: 1 }); }
    else if (VOWEL[ch]) out.push({ v: VOWEL[ch], w: 1 });
    else if (PAUSE.includes(ch) || /\s/.test(ch)) { if (last && !last.pause) out.push({ pause: true }); }
  }
  return out;
}

// Runs of equal values in a boolean array: [[value, start, end), ...]
function runs(arr) {
  const out = [];
  let s = 0;
  for (let i = 1; i <= arr.length; i++) if (i === arr.length || arr[i] !== arr[s]) { out.push([arr[s], s, i]); s = i; }
  return out;
}

// kana + AudioBuffer -> [{v, t0, t1}] in seconds from the start of the buffer, or null
export function timeline(kana, buffer) {
  const all = morae(kana);
  const phrases = [[]];
  for (const m of all) m.pause ? phrases.at(-1).length && phrases.push([]) : phrases.at(-1).push(m);
  if (!phrases.at(-1).length) phrases.pop();
  if (!phrases.length || !buffer) return null;

  // 10 ms loudness frames; voiced = above 8% of the peak, with short gaps (stops) closed
  const data = buffer.getChannelData(0), hop = Math.max(1, Math.round(buffer.sampleRate * 0.01));
  const n = Math.floor(data.length / hop), env = new Float32Array(n);
  let peak = 0;
  for (let f = 0; f < n; f++) {
    let s = 0;
    for (let j = f * hop, e = j + hop; j < e; j++) s += data[j] * data[j];
    env[f] = Math.sqrt(s / hop);
    if (env[f] > peak) peak = env[f];
  }
  const voiced = Array.from(env, e => e > peak * 0.08);
  for (const [v, a, b] of runs(voiced)) if (!v && b - a < 7 && a > 0 && b < n) voiced.fill(true, a, b);
  for (const [v, a, b] of runs(voiced)) if (v && b - a < 3) voiced.fill(false, a, b);
  const first = voiced.indexOf(true), last = voiced.lastIndexOf(true);
  if (first < 0) return null;

  // one stretch of voice per phrase: split at the longest pauses, if there are enough of them
  let spans = [[first, last + 1]];
  const gaps = runs(voiced.slice(first, last + 1)).filter(([v, a, b]) => !v && b - a >= 12).map(([, a, b]) => [a + first, b + first]);
  if (phrases.length > 1 && gaps.length >= phrases.length - 1) {
    const cut = gaps.sort((x, y) => (y[1] - y[0]) - (x[1] - x[0])).slice(0, phrases.length - 1).sort((x, y) => x[0] - y[0]);
    spans = [];
    let s = first;
    for (const [a, b] of cut) { spans.push([s, a]); s = b; }
    spans.push([s, last + 1]);
  } else {
    phrases.splice(0, phrases.length, phrases.flat());
  }

  const out = [];
  phrases.forEach((ms, k) => {
    const [a, b] = spans[k];
    const frames = [];
    for (let f = a; f < b; f++) if (voiced[f]) frames.push(f);
    if (!frames.length) return;
    const total = ms.reduce((s, m) => s + m.w, 0);
    let acc = 0;
    for (const m of ms) {
      const i0 = Math.floor(acc / total * frames.length);
      const i1 = Math.max(i0, Math.ceil((acc + m.w) / total * frames.length) - 1);
      out.push({ v: m.v, t0: frames[i0] * 0.01, t1: (frames[i1] + 1) * 0.01 });
      acc += m.w;
    }
  });
  return out.length ? out : null;
}

// Mouth shape at time t (s): {open, wide}, easing into the next mora; null between phrases.
export function shapeAt(tl, t) {
  if (!tl) return null;
  let i = 0;
  while (i < tl.length && tl[i].t1 <= t) i++;
  const cur = tl[i];
  if (!cur || t < cur.t0) return null;
  const [o, w] = SHAPES[cur.v];
  const next = tl[i + 1];
  if (!next || next.t0 - cur.t1 > 0.03) return { open: o, wide: w };
  const k = Math.min(1, Math.max(0, (t - (cur.t0 + 0.6 * (cur.t1 - cur.t0))) / (0.4 * (cur.t1 - cur.t0))));
  const [o2, w2] = SHAPES[next.v];
  return { open: o + (o2 - o) * k, wide: w + (w2 - w) * k };
}
