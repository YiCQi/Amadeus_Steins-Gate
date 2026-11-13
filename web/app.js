import { createAvatar } from "./rig.js";
import { setLang, t } from "./i18n.js";
import { timeline } from "./lipsync.js";
import { HandsFree } from "./call.js";
import { setLevel, showLevel, toLevel } from "./level.js";
import { Chat } from "./chat.js";
import { ChatSearch } from "./search.js";
import { MemoryPanel } from "./memory.js";

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
// this open page: a new turn cancels only its own unfinished lines on the server, not another tab's
const PAGE = Math.random().toString(36).slice(2, 10);
// The reply in progress while she still has something to say: { id, message, ctrl, spoke, logged }.
// Until she starts speaking (spoke) it can be dropped, when a hands-free call hears you go on (dropTurn).
let turn = null;
// What you said before a pause, when you went on talking before she answered it: { text, retract }
// (retract: the turn it was sent as, which the server then takes back). It goes out with what follows.
let carry = null;
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
// quiet: a retry follows, so no warning yet
async function fetchAudio(path, opts, gen, quiet = false) {
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
    if (gen === generation) console.warn("[voice]", path, e.message);
    if (!quiet && !voiceWarned && gen === generation) { toast(t("voiceDown", e.message), 7000); voiceWarned = true; }
    return null;
  }
}

// A line's voice, started on the server as soon as its Japanese was written. If that fails, it
// is asked for once more on its own before the line goes unvoiced.
async function lineVoice(data, gen) {
  const buf = await fetchAudio(`/api/voice/${data.id}`, {}, gen, true);
  if (buf || gen !== generation) return buf;
  return fetchAudio("/api/tts", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: data.ja, emotion: data.emotion || "neutral" }),
  }, gen);
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

// In a hands-free call you went on talking before she said a word: what you'd said wasn't all of
// it. Her reply is dropped (its request cancelled, so the server keeps none of it, and the log
// forgets it) and the turn returned, for what you said to go out again with the rest.
function dropTurn() {
  const t = turn;
  turn = null;
  t.ctrl.abort();
  stopSpeaking();
  t.logged.forEach(li => li.remove());
  return t;
}

// She was cut off mid-reply (talked over, the mic, a new message, the call hung up): her request
// stops, so the model stops writing, and the server keeps only the lines she got to say, the last
// marked as cut off if she was in the middle of it. Settles when the server has it.
function cutTurn() {
  const t = turn;
  if (!t) return Promise.resolve();
  turn = null;
  const lines = t.said, midway = t.playing;
  t.ctrl.abort();
  stopSpeaking();
  t.mine.slice(lines).forEach(li => li.remove());   // the log forgets what she never said
  return api("/api/call/said", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ turn: t.id, lines, midway }),
  }).catch(e => console.warn("[call] couldn't say where she was cut off:", e.message));
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
  let s = /[…‥]$|\.\.\.$/.test(end) ? 1.25 : /[？?]$/.test(end) ? 0.9 : /[！!]$/.test(end) ? 0.55 : 0.8;
  if (/^[…‥]|^\.\.\./.test(start)) s += 0.5;   // she hesitates before this one
  s += Math.min(0.3, end.length * 0.012);         // and draws breath after a long one
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

// event: "connect" (the call just started) or "idle" (the line has gone quiet, idle s: she may speak up);
// retract: the turn whose message this one repeats with the rest (see dropTurn)
async function converse({ message, event, idle, retract }) {
  const cut = cutTurn();   // she was still answering the last one: it ends where she got to
  stopSpeaking();
  const gen = generation;
  // said: how many of her lines she has started on; playing: one is being said right now;
  // logged: this turn's lines in the log (mine: hers, in order)
  const me = turn = { id: Math.random().toString(36).slice(2, 10), message, ctrl: new AbortController(),
    spoke: false, said: 0, playing: false, logged: [], mine: [] };
  initAudio();
  if (audioCtx.state === "suspended") await audioCtx.resume();

  if (message) { me.logged.push(addLog({ role: "user", sub: message })); heard(); nudges = 0; }
  if (event !== "idle") setStatus("thinking");   // speaking up unasked: no tell-tale pause first
  const t0 = performance.now();
  const timing = {};

  // One item per spoken line. The server sends "voice" as soon as a line's Japanese
  // is written (synthesis starts right then) and "segment" once its subtitle follows,
  // so audio can be ready before the subtitle is.
  const queue = makeQueue();
  const lines = new Map();
  let current = null, hangup = false;
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
        me.spoke = true;
        setStatus("speaking");
        timing.firstAudio = (performance.now() - t0) / 1000;
        timing.tts = buf?.ttsSeconds;
      }
      current = item;
      me.said = item.n == null ? me.said + 1 : Math.max(me.said, item.n + 1);
      me.playing = true;
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
      me.playing = false;
      if (gen !== generation) return;
      prev = item;
      prevEnd = performance.now();
    }
    if (turn === me) turn = null;   // all said
    if (gen !== generation) return;
    setStatus("online");
    await sleep(2500);
    if (gen === generation) { hideSubtitle(); avatar.setExpression("neutral"); }
  })();

  try {
    await cut;   // the server knows where she was cut off before it reads the conversation again
    const r = await api("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, event, idle, page: PAGE, turn: me.id, retract }),
      signal: me.ctrl.signal,
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
          item.voice = data.ja ? lineVoice(data, gen) : Promise.resolve(null);
          lines.set(data.id, item);
          queue.push(item);
        } else if (ev === "segment") {
          const item = lines.get(data.id);
          if (item) {
            item.resolveSub(data.sub);
            const li = addLog({ role: "assistant", sub: data.sub, ja: item.ja });
            me.logged.push(li);
            me.mine.push(li);
          }
        } else if (ev === "hangup") {
          hangup = true;
        } else if (ev === "mood") {
          avatar.setFeeling?.(data.feelings);
        } else if (ev === "done") {
          Object.assign(timing, data.timing);
        } else if (ev === "error") {
          throw new Error(data.message);
        }
      }
    }
  } catch (e) {
    if (!me.ctrl.signal.aborted) {   // (aborted: dropped, you went on talking)
      if (event === "idle") console.warn("[call] she didn't get to speak up:", e.message);
      else toast(t("amadeusErr", e.message), 8000);
      setStatus("online");
    }
  } finally {
    // a line cut off before its subtitle arrived still shows its Japanese
    for (const item of lines.values()) item.resolveSub(item.ja);
    queue.close();
  }
  await player;
  if (turn === me) turn = null;
  if (gen === generation && timing.firstAudio) logTiming(timing);
  // she said her last word and hung up: a beat of silence, then the line goes dead
  if (hangup && gen === generation && connected) { await sleep(900); if (gen === generation) endCall("kurisu"); }
  else if (gen === generation) armIdle();
}

// ---------------------------------------------------------------- when the line goes quiet
// Now and then she speaks up herself (server/persona.py IDLE_CALL); after a long silence she may hang up.

const CALL_IDLE = [25, 45], CALL_IDLE_MAX = 3;   // s of silence before she might; times in a row, unanswered
let idleTimer, nudges = 0, quietSince = 0;

function heard() {              // the user did something: the silence starts over
  quietSince = Date.now();
  clearTimeout(idleTimer);
}

function armIdle() {
  clearTimeout(idleTimer);
  if (!connected || nudges >= CALL_IDLE_MAX) return;
  const [lo, hi] = CALL_IDLE;
  idleTimer = setTimeout(nudge, (lo + Math.random() * (hi - lo)) * (1 + 0.5 * nudges) * 1000);
}

function nudge() {
  if (!connected) return;
  const busy = currentSource || recorder || recognition || callBtn.classList.contains("hearing") ||
    statusEl.dataset.state !== "online" || input.value.trim();
  if (!settings.model || settings.idle_talk === false || busy) { armIdle(); return; }
  nudges++;
  converse({ event: "idle", idle: Math.round((Date.now() - quietSince) / 1000) });
}

input.addEventListener("input", heard);

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
  return li;
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
let recorder = null, recognition = null, micHinted = false;

// Speech -> text on the server (faster-whisper); "" when nothing was understood.
// quiet: no "didn't catch that" toast (hands-free mode hears coughs and door clicks too)
async function transcribe(blob, name, quiet = false) {
  setStatus("thinking");
  try {
    const fd = new FormData();
    fd.append("audio", blob, name);
    const { text } = await (await api("/api/stt", { method: "POST", body: fd })).json();
    if (!text) { if (!quiet) toast(t("notCaught")); settle(); }
    return text;
  } catch (e) {
    toast(t("sttFail", e.message));
    settle();
    return "";
  }
}

// back to "online", unless you've started talking again meanwhile (hands-free)
function settle() {
  if (statusEl.dataset.state === "thinking") setStatus("online");
}

mic.onclick = async () => {
  if (recorder) { recorder.stop(); return; }
  if (recognition) { recognition.stop(); return; }
  cutTurn();   // pressing the mic while she talks cuts her off
  stopSpeaking();
  heard();
  if (settings.stt_mode === "browser") return startBrowserSTT();
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
    const chunks = [];
    recorder = new MediaRecorder(stream);
    const stopLevel = showLevel(stream, mic);   // the ring follows your voice while it records
    recorder.ondataavailable = e => e.data.size && chunks.push(e.data);
    recorder.onstop = async () => {
      stopLevel();
      stream.getTracks().forEach(t => t.stop());
      recorder = null;
      mic.classList.remove("rec");
      mic.classList.add("busy");
      const text = await transcribe(new Blob(chunks, { type: chunks[0]?.type || "audio/webm" }), "speech.webm");
      mic.classList.remove("busy");
      if (text) converse({ message: text }); else armIdle();
    };
    recorder.start();
    mic.classList.add("rec");
    setStatus("listening");
    if (!micHinted) { toast(t("micHint"), 3500); micHinted = true; }
  } catch (e) {
    toast(location.protocol === "http:" && location.hostname !== "localhost" && location.hostname !== "127.0.0.1"
      ? t("micHttps")
      : t("micFail", e.message));
  }
};

// Hands-free call (call.js): just talk; talking over her interrupts her. A pause closes what you
// said (call.js END) and she starts on her reply at once; if you go on before she has said a word,
// you weren't done: her reply is dropped, and what you said goes out again with the rest.
// (a page cached from before this button existed has no #btn-call: make one rather than crash)
const callBtn = $("#btn-call") ?? Object.assign(document.createElement("button"), { type: "button", hidden: true });
let shownLevel = 0, speechSeq = 0;

// "我觉得。" + "这个……" -> "我觉得……这个……": the pause stays in it
function joinSaid(a, b) {
  if (!a || !b) return a || b || "";
  return settings.language === "zh" ? a.replace(/[。.]$/, "……") + b : `${a.replace(/\.$/, "...")} ${b}`;
}

const call = new HandsFree({
  isBusy: () => !!currentSource,
  onLevel: rms => { shownLevel = setLevel(callBtn, toLevel(rms), shownLevel); },
  onSpeechStart: () => {
    speechSeq++;
    if (turn && !turn.spoke) {         // she hadn't said a word yet: you weren't done
      const t = dropTurn();
      if (t.message) carry = { text: t.message, retract: t.id };
    } else if (turn) cutTurn();        // talking over her stops her, between her lines too
    heard();
    callBtn.classList.add("hearing");
    setStatus("listening");
  },
  onUtterance: async wav => {
    callBtn.classList.remove("hearing");
    const seq = speechSeq;
    const text = wav ? await transcribe(wav, "speech.wav", true) : "";
    if (seq !== speechSeq) {           // you went on talking while it was being recognised
      if (text) carry = { text: joinSaid(carry?.text, text), retract: carry?.retract };
      return;
    }
    const c = carry;
    carry = null;
    const message = joinSaid(c?.text, text);   // (a cough after a pause: what came before it, again)
    if (message) converse({ message, retract: c?.retract });
    else { if (statusEl.dataset.state === "listening") setStatus("online"); armIdle(); }
  },
});

callBtn.onclick = async () => {
  if (call.active) {
    call.stop();
    carry = null;
    callBtn.classList.remove("live", "hearing");
    callBtn.style.removeProperty("--level");
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

// her memory: there is no "erase everything", only forgetting (and restoring) single things
const memoryPanel = new MemoryPanel({ api, toast });
$("#btn-memories").onclick = () => { dlg.close(); memoryPanel.open(); };

// ---------------------------------------------------------------- text chat (the home screen)

const chat = new Chat({
  api, toast,
  settings: () => settings,
  onCall: () => startCall(),
  onIncoming: d => incoming(d),
  inCall: () => connected,
  onSettings: () => { fillForm(); dlg.showModal(); },
  onMemory: () => memoryPanel.open(),
  onFind: () => search.open(),
  onFeel: d => avatar.setFeeling?.(d.feelings),
});
const search = new ChatSearch({ api, toast, jump: (params, bi) => chat.jump(params, bi) });
window.amadeus.chat = chat;

function setMode(mode) {
  $("#phone").dataset.mode = mode;
  if (mode === "chat") chat.open();
}

$("#open-chat").onclick = () => setMode("chat");

// ---------------------------------------------------------------- she is calling you
// server/calling.py decides whether she may; she decides whether she does. Here it only has to
// look like a phone: it rings over whatever is on screen, and it gives up on its own. However it
// ends, the server is told, because that one entry is both her memory of it and the chat record.

let ring = null;

// A ringtone made rather than shipped: two notes, a pair of rings, then a pause -- nothing to
// download, nothing anyone else owns, and it stops the moment the screen goes.
function startRinging() {
  initAudio();
  audioCtx.resume().catch(() => {});
  const beep = at => {
    for (const [i, f] of [440, 523.25].entries()) {
      const o = audioCtx.createOscillator(), g = audioCtx.createGain();
      o.type = "sine";
      o.frequency.value = f;
      const t0 = at + i * 0.42;
      g.gain.setValueAtTime(0, t0);
      g.gain.linearRampToValueAtTime(0.09, t0 + 0.03);
      g.gain.setValueAtTime(0.09, t0 + 0.26);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.36);
      o.connect(g).connect(audioCtx.destination);
      o.start(t0);
      o.stop(t0 + 0.4);
    }
  };
  const tick = () => beep(audioCtx.currentTime + 0.02);
  tick();
  return setInterval(tick, 2400);
}

function incoming({ lid, ring: seconds = 30 }) {
  if (ring || connected) { answerCall(lid, "missed"); return; }   // already ringing, or you're on a call
  const screen = $("#incoming");
  $("#ring-face").src = "assets/kurisu/chat_avatar.webp";
  screen.hidden = false;
  ring = { lid, beat: startRinging(), give_up: setTimeout(() => answerCall(lid, "missed"), seconds * 1000) };
}

// how: "yes" (you picked up), "no" (you turned it down), "missed" (it rang out)
async function answerCall(lid, how) {
  if (!lid) return;
  if (ring?.lid === lid) {
    clearInterval(ring.beat);
    clearTimeout(ring.give_up);
    ring = null;
    $("#incoming").hidden = true;
  }
  if (how === "yes") startCall();      // she opens it: she knows what she rang about
  try {
    const rec = await (await api("/api/call/incoming", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ lid, answered: how }),
    })).json();
    if (how !== "yes" && chat.loaded) chat.live(rec);   // a missed call is a record, like any other
  } catch (e) {
    console.warn("[call] couldn't say how her call went:", e.message);
  }
}

$("#ring-yes").onclick = () => answerCall(ring?.lid, "yes");
$("#ring-no").onclick = () => answerCall(ring?.lid, "no");

// ---------------------------------------------------------------- the call: from the chat, back to it

let callStart = 0;

function startCall() {
  setMode("call");
  if (!connected) $("#connect").click();
}

// by: "user" (the hang-up button) or "kurisu" (she hung up). The call goes into the chat as a record.
async function endCall(by) {
  if (!connected) { setMode("chat"); return; }
  connected = false;
  greeted = false;
  clearTimeout(idleTimer);
  nudges = 0;
  const cut = cutTurn();   // hung up on her mid-reply: only what she said goes into her memory
  stopSpeaking();
  if (call.active) { call.stop(); callBtn.classList.remove("live", "hearing"); callBtn.style.removeProperty("--level"); mic.disabled = false; }
  carry = null;
  hideSubtitle();
  setStatus("offline");
  const seconds = (Date.now() - callStart) / 1000;
  toast(t(by === "kurisu" ? "herHangup" : "callEnded"), 2500);
  let rec = { role: "event", kind: "call", seconds, by, ts: Date.now() };
  try {
    await cut;   // her reply, cut short, before the end of the call in the log
    rec = await (await api("/api/call/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ seconds, by }) })).json();
  } catch {}
  await sleep(by === "kurisu" ? 1200 : 400);
  // ready for the next call, then back to the chat
  $("#boot").classList.remove("gone");
  $("#boot-log").textContent = "";
  $("#connect").disabled = false;
  $("#avatar").classList.remove("on");
  setMode("chat");
  if (chat.loaded) chat.live(rec);
}

$("#btn-hangup").onclick = () => endCall("user");

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

// ---------------------------------------------------------------- clock: how long this call has lasted

setInterval(() => {
  const s = connected ? Math.floor((Date.now() - callStart) / 1000) : 0;
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
  voiceWarned = false;   // a voice that fails gets said once per call, not once per page
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
  avatar.setFeeling?.(st.feelings);
  avatar.show();
  setStatus(st.tts_enabled === undefined ? "offline" : "online");
  connected = true;
  callStart = Date.now();
  heard();
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
    setMode("chat");   // the chat is home; the video call starts from there
    const hist = await (await api("/api/history")).json();
    hist.forEach(addLog);
  } catch (e) {
    toast(t("serverDown", e.message));
  }
  await avatar.load();
})();
