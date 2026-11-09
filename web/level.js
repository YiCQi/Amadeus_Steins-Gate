// While a button listens, a ring around it follows your voice: you can see it hears you
// (a blink only says that it's on). The ring's size is the CSS variable --level, 0..1.

// loudness (RMS of samples) -> 0..1, roughly: room noise ~0.1, normal speech 0.4–0.9
export const toLevel = rms => Math.min(1, Math.sqrt(rms) * 2.4);

// Sets el's --level from a microphone stream until the returned function is called.
export function showLevel(stream, el) {
  const ctx = new AudioContext();
  const analyser = ctx.createAnalyser();
  analyser.fftSize = 1024;
  ctx.createMediaStreamSource(stream).connect(analyser);
  const buf = new Float32Array(analyser.fftSize);
  let level = 0, on = true;
  (function tick() {
    if (!on) return;
    analyser.getFloatTimeDomainData(buf);
    let sum = 0;
    for (const v of buf) sum += v * v;
    level = setLevel(el, toLevel(Math.sqrt(sum / buf.length)), level);
    requestAnimationFrame(tick);
  })();
  return () => { on = false; el.style.removeProperty("--level"); ctx.close(); };
}

// rises at once, falls back gently; returns the level shown
export function setLevel(el, target, shown = 0) {
  const level = target > shown ? target : shown * 0.85 + target * 0.15;
  el.style.setProperty("--level", level.toFixed(3));
  return level;
}
