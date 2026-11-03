import { createAvatar } from "./rig.js";
import { setLang, t } from "./i18n.js";
import { timeline } from "./lipsync.js";
import { HandsFree } from "./call.js";
import { Chat } from "./chat.js";

const $ = s => document.querySelector(s);
const avatar = await createAvatar($("#avatar"));  // mesh rig if one is built, else sprites
window.amadeus = { avatar };  // handy from the devtools console
const statusEl = $("#status");
const subEl = $("#subtitle"), subText = $("#sub-text"), subJa = $("#sub-ja");
const input = $("#input");

let settings = {};
let audioCtx, analyser, gain;
let currentSource = null;
let generation = 0;           // bumps on interrupt so stale playback stops
let voiceWarned = false;
let connected = false, greeted = false;

// ---------------------------------------------------------------- utilities

function setStatus(state) {
  statusEl.dataset.state = state;
  statusEl.textContent = t(`status.${state}`);
  // she reacts to the call itself: attentive while you talk, pensive until her reply's first line
  const mood = $("#avatar").dataset.emotion;
  if (state === "listening" || state === "thinking") avatar.setExpression(state);
  else if (state === "online" && (mood === "listening" || mood === "thinking")) avatar.setExpression("neutral");
}

function applySettings() {
  setLang(settings.language);
  setStatus(statusEl.dataset.state);
}

let toastTimer;
function toast(msg, ms = 4500) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), ms);
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function api(path, opts = {}) {
  let r;
  try {
    r = await fetch(path, opts);
  } catch {
    throw new Error(t("noServer"));
  }
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return r;
}

// ---------------------------------------------------------------- audio

function initAudio() {
  if (audioCtx) return;
  audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  gain = audioCtx.createGain();
  analyser.connect(gain).connect(audioCtx.destination);
  avatar.attach(analyser);
}

// Fetch and decode one line of voice; null (plus a one-time toast) if it fails.
async function fetchAudio(path, opts, gen) {
  try {
    const r = await api(path, opts);
    const buf = await audioCtx.decodeAudioData(await r.arrayBuffer());
    const secs = r.headers.get("X-TTS-Seconds");
    buf.ttsSeconds = secs === null ? null : Number(secs);  // 0 = served from the voice cache
    const kana = r.headers.get("X-Kana");
    if (kana) buf.kana = decodeURIComponent(kana);
    return buf;
  } catch (e) {
    // a reply interrupted by a newer message cancels its voices: not worth a warning
    if (!voiceWarned && gen === generation) { toast(t("voiceDown", e.message), 7000); voiceWarned = true; }
    return null;
  }
}

// kana: the line's reading, so the mouth can take each mora's shape (lipsync.js)
function playBuffer(buf, kana) {
  return new Promise(resolve => {
    const src = audioCtx.createBufferSource();
    src.buffer = buf;
    src.connect(analyser);
    src.onended = () => { avatar.speaking = false; currentSource = null; resolve(); };
    currentSource = src;
    avatar.speaking = true;
    avatar.lipSync?.(timeline(kana, buf), audioCtx.currentTime);
    src.start();
  });
}

function stopSpeaking() {
  generation++;
  if (currentSource) { try { currentSource.stop(); } catch {} }
}

// ---------------------------------------------------------------- subtitles

let typeTimer;
function showSubtitle(text, ja, durationMs) {
  clearInterval(typeTimer);
  subJa.textContent = settings.show_japanese ? ja : "";
  subEl.classList.add("show");
  // type the line out over ~70% of the spoken duration (capped so it never drags)
  // Chinese packs far more meaning per character, so allow a slower per-character pace
  const maxStep = settings.language === "zh" ? 120 : 45;
  const step = Math.max(12, Math.min(maxStep, (durationMs * 0.7) / Math.max(text.length, 1)));
  let i = 0;
  subText.textContent = "";
  typeTimer = setInterval(() => {
    i += 1;
    subText.textContent = text.slice(0, i);
    if (i >= text.length) clearInterval(typeTimer);
  }, step);
}

function hideSubtitle() { subEl.classList.remove("show"); }

// ---------------------------------------------------------------- conversation

// Pause before a line (s), from how the previous one ended and how this one starts: she
// trails off after ……, waits a moment after a question, snaps back after an exclamation,
// and takes longer when thinking or sad. A little randomness keeps it from sounding metronomic.
const PAUSE_MOOD = { thinking: 1.3, sad: 1.25, puzzled: 1.2, pout: 1.15, smug: 1.1, disgusted: 1.05,
  laugh: 0.85, surprised: 0.85, annoyed: 0.8, angry: 0.75, furious: 0.65 };
function linePause(prev, next) {
  const end = (prev.ja || "").trim(), start = (next.ja || "").trim();
  let s = /[…‥]$|\.\.\.$/.test(end) ? 0.75 : /[？?]$/.test(end) ? 0.5 : /[！!]$/.test(end) ? 0.3 : 0.42;
  if (/^[…‥]|^\.\.\./.test(start)) s += 0.35;  // she hesitates before this one
  return s * (PAUSE_MOOD[next.emotion] ?? 1) * (0.85 + Math.random() * 0.3);
}

// Async queue: segments arrive from the stream; voices are requested at once
// (the server serialises them) and played strictly in order.
function makeQueue() {
  const items = [];
  let wake = null, closed = false;
  return {
    push(x) { items.push(x); wake?.(); },
    close() { closed = true; wake?.(); },
    async *[Symbol.asyncIterator]() {
      while (true) {
        if (items.length) { yield items.shift(); continue; }
        if (closed) return;
        await new Promise(r => (wake = r));
        wake = null;
      }
    },
  };
}

async function converse({ message, event }) {
  stopSpeaking();
  const gen = generation;
  initAudio();
  if (audioCtx.state === "suspended") await audioCtx.resume();

  if (message) addLog({ role: "user", sub: message });
  setStatus("thinking");
  const t0 = performance.now();
  const timing = {};

  // One item per spoken line. The server sends "voice" as soon as a line's Japanese
  // is written (synthesis starts right then) and "segment" once its subtitle follows,
  // so audio can be ready before the subtitle is.
  const queue = makeQueue();
  const lines = new Map();
  let current = null;
  const player = (async () => {
    let prev = null, prevEnd = 0;
    for await (const item of queue) {
      if (gen !== generation) return;
      const buf = await item.voice;
      if (gen !== generation) return;
      if (prev) {
        // counted from the end of the previous line, so synthesis time overlaps the pause
        const wait = linePause(prev, item) * 1000 - (performance.now() - prevEnd);
        if (wait > 0) await sleep(wait);
        if (gen !== generation) return;
      }
      if (!timing.firstAudio) {
        setStatus("speaking");
        timing.firstAudio = (performance.now() - t0) / 1000;
        timing.tts = buf?.ttsSeconds;
      }
      current = item;
      avatar.setExpression(item.emotion);
      if (buf) {
        const started = performance.now();
        const playing = playBuffer(buf, item.kana ?? item.ja);
        item.sub.then(text => {
          if (gen === generation && current === item)
            showSubtitle(text, item.ja, buf.duration * 1000 - (performance.now() - started));
        });
        await playing;
      } else {
        const text = await item.sub;
        const dur = 900 + text.length * (settings.language === "zh" ? 180 : 55);
        showSubtitle(text, item.ja, dur);
        await avatar.fakeTalk(dur);
      }
      if (gen !== generation) return;
      prev = item;
      prevEnd = performance.now();
    }
    if (gen !== generation) return;
    setStatus("online");
    await sleep(2500);
    if (gen === generation) { hideSubtitle(); avatar.setExpression("neutral"); }
  })();

  try {
    const r = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, event }),
    });
    const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
    let buf = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += value;
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const ev = /^event: (.*)$/m.exec(raw)?.[1];
        const data = JSON.parse(/^data: (.*)$/m.exec(raw)?.[1] || "{}");
        if (ev === "voice") {
          const item = { ...data };
          item.sub = new Promise(r => (item.resolveSub = r));
          item.voice = data.ja ? fetchAudio(`/api/voice/${data.id}`, {}, gen) : Promise.resolve(null);
          lines.set(data.id, item);
          queue.push(item);
        } else if (ev === "segment") {
          const item = lines.get(data.id);
          if (item) {
            item.resolveSub(data.sub);
            addLog({ role: "assistant", sub: data.sub, ja: item.ja });
          }
        } else if (ev === "done") {
          Object.assign(timing, data.timing);
        } else if (ev === "error") {
          throw new Error(data.message);
        }
      }
    }
  } catch (e) {
    toast(t("amadeusErr", e.message), 8000);
    setStatus("online");
  } finally {
    // a line cut off before its subtitle arrived still shows its Japanese
    for (const item of lines.values()) item.resolveSub(item.ja);
    queue.close();
  }
  await player;
  if (gen === generation && timing.firstAudio) logTiming(timing);
}

function logTiming(tm) {
  const f = v => (v == null ? "–" : Number(v).toFixed(1));
  console.info("[timing]", tm);
  addLog({ role: "timing", sub: t("timing", f(tm.firstAudio), f(tm.first_voice_text), f(tm.tts)) });
}

// ---------------------------------------------------------------- log

function addLog(m) {
  const li = document.createElement("li");
  li.className = m.role;
  li.textContent = m.sub;
  if (m.ja) {
    const s = document.createElement("small");
    s.textContent = m.ja;
    li.append(s);
  }
  const list = $("#log-list");
  list.append(li);
  list.scrollTop = list.scrollHeight;
}

$("#btn-log").onclick = () => { $("#log").hidden = !$("#log").hidden; };
$("#log-close").onclick = () => { $("#log").hidden = true; };

// ---------------------------------------------------------------- input

$("#bar").addEventListener("submit", e => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  converse({ message: text });
});

// Microphone: "local" records and sends audio to faster-whisper on the server,
// "browser" uses the Web Speech API (Chrome / Edge).
const mic = $("#btn-mic");
let recorder = null, recognition = null;

// Speech -> text on the server (faster-whisper); "" when nothing was understood.
// quiet: no "didn't catch that" toast (hands-free mode hears coughs and door clicks too)
async function transcribe(blob, name, quiet = false) {
  setStatus("thinking");
  try {
    const fd = new FormData();
    fd.append("audio", blob, name);
    const { text } = await (await api("/api/stt", { method: "POST", body: fd })).json();
    if (!text) { if (!quiet) toast(t("notCaught")); setStatus("online"); }
    return text;
  } catch (e) {
    toast(t("sttFail", e.message));
    setStatus("online");
    return "";
  }
}

mic.onclick = async () => {
  if (recorder) { recorder.stop(); return; }
  if (recognition) { recognition.stop(); return; }
  stopSpeaking();
  if (settings.stt_mode === "browser") return startBrowserSTT();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const chunks = [];
    recorder = new MediaRecorder(stream);
    recorder.ondataavailable = e => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      stream.getTracks().forEach(t => t.stop());
      recorder = null;
      mic.classList.remove("rec");
      mic.classList.add("busy");
      const text = await transcribe(new Blob(chunks, { type: chunks[0]?.type || "audio/webm" }), "speech.webm");
      mic.classList.remove("busy");
      if (text) converse({ message: text });
    };
    recorder.start();
    mic.classList.add("rec");
    setStatus("listening");
  } catch (e) {
    toast(location.protocol === "http:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1"
      ? t("micHttps")
      : t("micFail", e.message));
  }
};

// Hands-free call (call.js): just talk; talking over her interrupts her
// (a page cached from before this button existed has no #btn-call: make one rather than crash)
const callBtn = $("#btn-call") ?? Object.assign(document.createElement("button"), { type: "button", hidden: true });
const call = new HandsFree({
  isBusy: () => !!currentSource,
  onSpeechStart: () => {
    if (currentSource) stopSpeaking();
    callBtn.classList.add("hearing");
    setStatus("listening");
  },
  onUtterance: async wav => {
    callBtn.classList.remove("hearing");
    if (!wav) { if (statusEl.dataset.state === "listening") setStatus("online"); return; }
    const text = await transcribe(wav, "speech.wav", true);
    if (text) converse({ message: text });
  },
});

callBtn.onclick = async () => {
  if (call.active) {
    call.stop();
    callBtn.classList.remove("live", "hearing");
    mic.disabled = false;
    if (statusEl.dataset.state === "listening") setStatus("online");
    toast(t("callOff"), 2000);
    return;
  }
  try {
    initAudio();
    await audioCtx.resume();
    await call.start();
    callBtn.classList.add("live");
    mic.disabled = true;
    toast(t("callOn"), 5000);
  } catch (e) {
    toast(t("micFail", e.message));
  }
};

function startBrowserSTT() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { toast(t("noBrowserSR")); return; }
  recognition = new SR();
  recognition.lang = t("speechLang");
  recognition.interimResults = true;
  recognition.onresult = e => {
    const r = e.results[e.results.length - 1];
    input.value = r[0].transcript;
    if (r.isFinal) { const t = input.value.trim(); input.value = ""; if (t) converse({ message: t }); }
  };
  recognition.onend = () => { recognition = null; mic.classList.remove("rec"); if (statusEl.dataset.state === "listening") setStatus("online"); };
  recognition.onerror = e => toast(t("srErr", e.error));
  recognition.start();
  mic.classList.add("rec");
  setStatus("listening");
}

// ---------------------------------------------------------------- settings

const dlg = $("#settings"), form = $("#settings-form");
const DEFAULT_URLS = { openai: "https://api.openai.com/v1", anthropic: "https://api.anthropic.com" };

function fillForm() {
  for (const el of form.elements) {
    if (!el.name || !(el.name in settings)) continue;
    if (el.type === "checkbox") el.checked = !!settings[el.name];
    else el.value = settings[el.name] ?? "";
  }
  $("#settings-msg").textContent = "";
  $("#settings-msg").classList.remove("error");
}

form.elements.provider.onchange = e => {
  const base = form.elements.base_url;
  if (!base.value || Object.values(DEFAULT_URLS).includes(base.value)) base.value = DEFAULT_URLS[e.target.value];
};

$("#btn-settings").onclick = () => { fillForm(); dlg.showModal(); };

form.addEventListener("submit", async e => {
  if (e.submitter?.value !== "save") return;
  e.preventDefault();
  const body = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    body[el.name] = el.type === "checkbox" ? el.checked : el.type === "number" ? Number(el.value) : el.value.trim();
  }
  const msg = $("#settings-msg");
  msg.classList.remove("error");
  msg.textContent = t("saving");
  try {
    settings = await (await api("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })).json();
    applySettings();
    dlg.close();
    toast(t("saved"), 2000);
    // First-time setup happened right after Connect: let her say hello now.
    if (connected && !greeted && settings.model) greet();
  } catch (err) {
    msg.classList.add("error");
    msg.textContent = err.message;
  }
});

$("#btn-test-voice").onclick = async () => {
  initAudio();
  await audioCtx.resume();
  $("#settings-msg").textContent = t("synth");
  voiceWarned = false;
  const buf = await fetchAudio("/api/tts", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: "テスト中よ。ちゃんと聞こえてる？", emotion: "neutral" }),
  }, generation);
  if (buf) { $("#settings-msg").textContent = t("playing"); await playBuffer(buf, buf.kana); }
  else $("#settings-msg").textContent = t("voiceFail");
};

$("#btn-reset").onclick = async () => {
  if (!confirm(t("confirmReset"))) return;
  await api("/api/reset", { method: "POST" });
  $("#log-list").replaceChildren();
  if (chat.loaded) chat.load();
  $("#settings-msg").textContent = t("erased");
};

// ---------------------------------------------------------------- text chat

const chat = new Chat({
  api, toast,
  settings: () => settings,
  onCall: () => { setMode("call"); if (!connected) $("#connect").click(); },
  onBack: () => setMode("call"),   // the call, or the start screen if she hasn't been called yet
});

function setMode(mode) {
  $("#phone").dataset.mode = mode;
  if (mode === "chat") {
    stopSpeaking();
    if (call.active) callBtn.click();   // a hands-free call would hear you typing
    chat.open();
  }
}

$("#open-chat").onclick = () => setMode("chat");
$("#btn-chat").onclick = () => setMode("chat");

// Preview the UI language as soon as it's picked; Cancel restores the saved one.
form.elements.language.onchange = e => setLang(e.target.value);
dlg.addEventListener("close", () => setLang(settings.language));

// ---------------------------------------------------------------- data rain

function startRain() {
  const c = $("#rain"), ctx = c.getContext("2d");
  let cols = [], w = 0, h = 0;
  const size = 13;
  const resize = () => {
    w = c.width = c.clientWidth * devicePixelRatio;
    h = c.height = c.clientHeight * devicePixelRatio;
    cols = Array.from({ length: Math.ceil(w / (size * devicePixelRatio * 1.4)) }, () => ({ y: Math.random() * h, v: 0.4 + Math.random() * 1.2 }));
  };
  resize();
  addEventListener("resize", resize);
  const draw = () => {
    ctx.fillStyle = "rgba(0,0,0,0.12)";
    ctx.fillRect(0, 0, w, h);
    ctx.font = `${size * devicePixelRatio}px "Share Tech Mono", monospace`;
    cols.forEach((col, i) => {
      const x = i * size * devicePixelRatio * 1.4;
      ctx.fillStyle = Math.random() < 0.04 ? "#c9ffe0" : "#2bd47a";
      ctx.fillText(Math.random() < 0.5 ? "0" : "1", x, col.y);
      col.y += col.v * size * devicePixelRatio * 0.5;
      if (col.y > h && Math.random() < 0.03) col.y = 0;
    });
    setTimeout(() => requestAnimationFrame(draw), 60);
  };
  draw();
}

// ---------------------------------------------------------------- clock

const t0 = Date.now();
setInterval(() => {
  const s = Math.floor((Date.now() - t0) / 1000);
  $("#clock").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}, 1000);

// ---------------------------------------------------------------- boot

async function bootLog(lines) {
  const el = $("#boot-log");
  for (const l of lines) {
    el.textContent += l + "\n";
    await sleep(260 + Math.random() * 220);
  }
}

$("#connect").onclick = async () => {
  $("#connect").disabled = true;
  initAudio();
  await audioCtx.resume();
  const st = await api("/api/status").then(r => r.json()).catch(() => ({}));
  await bootLog([
    "> establishing secure link ......... ok",
    "> loading memory data: makise_kurisu",
    `> voice module ...................... ${st.engine === "gpt-sovits" || st.engine === "vits" ? "ok" : st.engine === "edge" ? "fallback" : "offline"}`,
    "> Amadeus system online.",
  ]);
  $("#boot").classList.add("gone");
  avatar.show();
  setStatus(st.tts_enabled === undefined ? "offline" : "online");
  connected = true;
  if (!settings.model) {
    toast(t("needApi"), 6000);
    fillForm();
    dlg.showModal();
    return;
  }
  await sleep(700);
  greet();
};

function greet() {
  greeted = true;
  converse({ event: "connect" });
}

(async function init() {
  startRain();
  try {
    settings = await (await api("/api/settings")).json();
    applySettings();
    const hist = await (await api("/api/history")).json();
    hist.forEach(addLog);
  } catch (e) {
    toast(t("serverDown", e.message));
  }
  await avatar.load();
})();
