// Text chat with Kurisu, messaging-app style: her texts, her stickers (web/assets/stickers/,
// see server/stickers.py), and now and then a Japanese voice message with its translation.
// The server keeps the thread (data/chat.json); this page only shows it.

import { t } from "./i18n.js";

const $ = s => document.querySelector(s);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const AVATAR = "assets/kurisu/chat_avatar.webp";
const GAP = 5 * 60 * 1000;            // a time stamp goes between messages further apart than this
const PLAYED_KEY = "amadeus.playedVoice";

// Server-sent events of a fetch response, as {event, data}.
async function* sseEvents(r) {
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) return;
    buf += value;
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, i);
      buf = buf.slice(i + 2);
      yield { event: /^event: (.*)$/m.exec(raw)?.[1], data: JSON.parse(/^data: (.*)$/m.exec(raw)?.[1] || "{}") };
    }
  }
}

export class Chat {
  // api(path, opts) -> Response, toast(msg, ms), settings() -> current settings,
  // onCall(): start a video call, onSettings(), onMemory(): open those, onMood(v): her mood changed
  constructor({ api, toast, settings, onCall, onSettings, onMemory, onMood }) {
    Object.assign(this, { api, toast, settings, onCall, onMood });
    this.list = $("#chat-list");
    this.input = $("#chat-input");
    this.status = $("#chat-status");
    this.panel = $("#sticker-panel");
    this.lastTs = 0;
    this.busy = false;
    this.pending = [];                // what you sent while she was still replying
    this.loaded = false;
    try { this.played = new Set(JSON.parse(localStorage.getItem(PLAYED_KEY) || "[]")); } catch { this.played = new Set(); }

    $("#chat-bar").addEventListener("submit", e => { e.preventDefault(); this.submit(); });
    this.input.addEventListener("keydown", e => {
      // Enter sends, Shift+Enter is a new line; never while an IME (Chinese input) is composing
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.submit(); }
    });
    this.input.addEventListener("input", () => this.fit());
    $("#chat-sticker").onclick = () => this.togglePanel();
    $("#chat-call").onclick = () => onCall();
    $("#chat-settings").onclick = () => onSettings();
    $("#chat-memory").onclick = () => onMemory();
    $("#chat-mic").onclick = () => this.mic();
    this.list.addEventListener("click", () => { this.panel.hidden = true; });
  }

  async open() {
    if (!this.loaded) await this.load();
    this.scroll();
    if (matchMedia("(pointer: fine)").matches) this.input.focus();
  }

  // Re-read the thread, e.g. after the memory was erased.
  async load() {
    try {
      const hist = await (await this.api("/api/text/history")).json();
      this.list.replaceChildren();
      this.lastTs = 0;
      for (const m of hist) this.render(m);
      this.loaded = true;
    } catch (e) {
      this.toast(t("serverDown", e.message));
    }
  }

  setStatus(text) { this.status.textContent = text; }
  scroll() { this.list.scrollTop = this.list.scrollHeight; }
  fit() {
    this.input.style.height = "auto";
    this.input.style.height = Math.min(this.input.scrollHeight, 120) + "px";
    $("#chat-send").classList.toggle("ready", !!this.input.value.trim());
  }

  // ---- showing messages

  stamp(ts) {
    ts ||= Date.now();
    if (ts - this.lastTs > GAP) {
      const li = document.createElement("li");
      li.className = "time";
      li.textContent = formatTime(ts);
      this.list.append(li);
    }
    this.lastTs = ts;
  }

  render(m) {
    this.stamp(m.ts);
    const li = document.createElement("li");
    if (m.role === "event") return this.record(li, m);
    li.className = "msg " + (m.role === "user" ? "me" : "her");
    if (m.role !== "user") {
      const ava = Object.assign(new Image(), { src: AVATAR, alt: "", className: "ava" });
      li.append(ava);
    }
    if (m.kind === "sticker") {
      const img = Object.assign(new Image(), { src: m.url, alt: m.meaning || "", title: m.meaning || "", className: "sticker" });
      // a sticker whose file was removed or renamed: say what it was instead of a broken image
      img.onerror = () => img.replaceWith(Object.assign(document.createElement("div"), {
        className: "bubble", textContent: `[${t("stickers")}] ${m.meaning || ""}`.trim(),
      }));
      li.append(img);
    } else if (m.kind === "voice") {
      li.append(this.voiceBubble(m));
    } else {
      const b = document.createElement("div");
      b.className = "bubble";
      b.textContent = m.text;
      li.append(b);
    }
    this.list.append(li);
    this.scroll();
    return li;
  }

  // A finished video call, like a messaging app's call record: on your side, since you called.
  record(li, m) {
    li.className = "msg me";
    const b = document.createElement("div");
    b.className = "bubble call-record";
    const secs = Math.round(m.seconds || 0);
    const len = `${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`;
    b.innerHTML = `<svg viewBox="0 0 24 24"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/></svg>`;
    b.append(secs < 2 ? t("callCancelled") : t("callRecord", len));
    if (m.by === "kurisu" && secs >= 2) b.append(Object.assign(document.createElement("small"), { textContent: t("callHerHung") }));
    li.append(b);
    this.list.append(li);
    this.scroll();
    return li;
  }

  voiceBubble(m) {
    const wrap = document.createElement("div");
    wrap.className = "voice-wrap";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "bubble voice";
    btn.title = t("voiceMsg");
    btn.innerHTML = `<svg viewBox="0 0 24 24"><path d="M7 10a3 3 0 0 1 0 4"/><path d="M10.5 7.5a7 7 0 0 1 0 9"/><path d="M14 5a11 11 0 0 1 0 14"/></svg><span class="len"></span>`;
    const len = btn.querySelector(".len");
    const show = secs => {
      len.textContent = `${Math.max(1, Math.round(secs))}″`;
      btn.style.width = `${Math.round(84 + Math.min(secs, 30) * 6)}px`;   // longer messages, wider bubbles
    };
    if (m.seconds) show(m.seconds);
    if (!this.played.has(m.file)) btn.classList.add("unplayed");
    btn.onclick = () => this.play(m, btn, show);
    const text = document.createElement("p");
    text.className = "voice-text";
    text.textContent = m.sub;
    if (this.settings().show_japanese && m.ja !== m.sub) {
      const ja = document.createElement("small");
      ja.lang = "ja";
      ja.textContent = m.ja;
      text.append(ja);
    }
    wrap.append(btn, text);
    return wrap;
  }

  play(m, btn, show) {
    const same = this.audioBtn === btn;
    if (this.audio) { this.audio.pause(); this.audioBtn.classList.remove("playing"); this.audio = this.audioBtn = null; }
    if (same || !m.file) return;
    const a = new Audio(`/api/voicemsg/${encodeURIComponent(m.file)}`);
    this.audio = a;
    this.audioBtn = btn;
    btn.classList.add("playing");
    btn.classList.remove("unplayed");
    this.played.add(m.file);
    try { localStorage.setItem(PLAYED_KEY, JSON.stringify([...this.played].slice(-500))); } catch {}
    a.onloadedmetadata = () => { if (!m.seconds && isFinite(a.duration)) show(a.duration); };
    a.onended = a.onerror = () => { btn.classList.remove("playing"); if (this.audio === a) this.audio = this.audioBtn = null; };
    a.play().catch(e => { btn.classList.remove("playing"); this.toast(t("voiceDown", e.message)); });
  }

  // ---- sending

  submit() {
    const text = this.input.value.trim();
    if (!text) return;
    this.input.value = "";
    this.fit();
    this.send({ message: text }, { role: "user", kind: "text", text, ts: Date.now() });
  }

  sendSticker(s) {
    this.panel.hidden = true;
    this.send({ sticker: s.id }, { role: "user", kind: "sticker", url: s.url, meaning: s.meaning, ts: Date.now() });
  }

  async send(payload, mine) {
    this.render(mine);
    if (this.busy) { this.pending.push(payload); return; }
    await this.exchange(payload);
    // texts you sent while she was replying go together, as one turn
    while (this.pending.length) {
      const next = this.pending.splice(0);
      const texts = next.filter(p => p.message).map(p => p.message);
      for (const p of next.filter(p => p.sticker)) await this.exchange(p);
      if (texts.length) await this.exchange({ messages: texts });
    }
  }

  async exchange(payload) {
    this.busy = true;
    this.setStatus(t("chatTyping"));
    const t0 = performance.now();
    let shown = 0;
    try {
      const r = await this.api("/api/text", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
      });
      for await (const { event, data } of sseEvents(r)) {
        if (event === "message") {
          // she "types" each bubble: longer ones take longer; the first one overlaps the model's own wait
          const wait = this.typingTime(data) - (shown ? 0 : performance.now() - t0);
          if (wait > 0) await sleep(wait);
          this.render({ role: "assistant", ts: Date.now(), ...data });
          shown++;
        } else if (event === "mood") {
          this.onMood?.(data.value);
        } else if (event === "error") {
          throw new Error(data.message);
        }
      }
    } catch (e) {
      this.toast(t("amadeusErr", e.message), 8000);
    } finally {
      this.busy = false;
      this.setStatus("");
    }
  }

  typingTime(m) {
    if (m.kind === "sticker") return 700;
    if (m.kind === "voice") return 400;   // the server already waited for the recording
    const perChar = this.settings().language === "zh" ? 110 : 45;
    return Math.min(2600, Math.max(500, 300 + perChar * (m.text || "").length));
  }

  // ---- stickers you can send her

  // read again each time, so stickers you just dropped into the folder show up
  async togglePanel() {
    if (!this.panel.hidden) { this.panel.hidden = true; return; }
    try {
      const list = await (await this.api("/api/stickers")).json();
      this.panel.replaceChildren(...list.map(s => {
        const b = document.createElement("button");
        b.type = "button";
        b.title = s.meaning;
        const img = Object.assign(new Image(), { src: s.url, alt: s.meaning, loading: "lazy" });
        img.onerror = () => b.remove();
        b.append(img);
        b.onclick = () => this.sendSticker(s);
        return b;
      }));
    } catch (e) {
      this.toast(t("serverDown", e.message));
      return;
    }
    this.panel.hidden = false;
  }

  // ---- voice input: what you say lands in the box, to check before sending

  async mic() {
    const btn = $("#chat-mic");
    if (this.rec) { this.rec.stop(); return; }
    if (this.settings().stt_mode === "browser") {
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) { this.toast(t("noBrowserSR")); return; }
      const r = new SR();
      r.lang = t("speechLang");
      r.onresult = e => { this.input.value += e.results[0][0].transcript; this.fit(); };
      r.onend = () => { this.rec = null; btn.classList.remove("rec"); };
      r.onerror = e => this.toast(t("srErr", e.error));
      this.rec = r;
      r.start();
      btn.classList.add("rec");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const chunks = [];
      const rec = new MediaRecorder(stream);
      rec.ondataavailable = e => e.data.size && chunks.push(e.data);
      rec.onstop = async () => {
        stream.getTracks().forEach(tr => tr.stop());
        this.rec = null;
        btn.classList.remove("rec");
        btn.classList.add("busy");
        try {
          const fd = new FormData();
          fd.append("audio", new Blob(chunks, { type: chunks[0]?.type || "audio/webm" }), "speech.webm");
          const { text } = await (await this.api("/api/stt", { method: "POST", body: fd })).json();
          if (text) { this.input.value += text; this.fit(); this.input.focus(); } else this.toast(t("notCaught"));
        } catch (e) {
          this.toast(t("sttFail", e.message));
        }
        btn.classList.remove("busy");
      };
      this.rec = rec;
      rec.start();
      btn.classList.add("rec");
    } catch (e) {
      this.toast(t("micFail", e.message));
    }
  }
}

// 14:05 today, "Yesterday 14:05", else the date too.
function formatTime(ts) {
  const d = new Date(ts), now = new Date();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff === 0) return hm;
  if (diff === 1) return `${t("yesterday")} ${hm}`;
  const lang = document.documentElement.lang || undefined;
  return `${d.toLocaleDateString(lang, { month: "short", day: "numeric", ...(d.getFullYear() !== now.getFullYear() && { year: "numeric" }) })} ${hm}`;
}
