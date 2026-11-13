// Text chat with Kurisu, messaging-app style: her texts, her stickers (web/assets/stickers/,
// see server/stickers.py), and now and then a Japanese voice message with its translation.
// The server keeps the thread (server/memory.py). The page holds a stretch of it, a page at a
// time: scrolling up loads older messages, a search result (search.js) opens it around that
// message. When the chat goes quiet, she may text first: server/lull.py reads the conversation
// to decide whether and when (not after a goodbye; soon after a question of hers you left hanging).

import { t } from "./i18n.js";
import { showLevel } from "./level.js";

const $ = s => document.querySelector(s);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const AVATAR = "assets/kurisu/chat_avatar.webp";
const GAP = 5 * 60 * 1000;            // a time stamp goes between messages further apart than this
const PLAYED_KEY = "amadeus.playedVoice";
const PAGE = 40;                      // timeline entries loaded at a time
const KEEP = 200;                     // entries the page holds; scrolling on lets the far end go
const EDGE = 400;                     // px from either end at which the next page loads
const MIN_QUIET = 120;                // s: she never texts first sooner than this after you last did anything here

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
  // onCall(): start a video call, onSettings(), onMemory(), onFind(): open those;
  // onIncoming({lid, ring}): she is calling, inCall(): a call is live right now (app.js);
  // onFeel({feelings, value}): how she feels has moved (server/memory.py's six)
  constructor({ api, toast, settings, onCall, onSettings, onMemory, onFind, onFeel, onIncoming, inCall }) {
    Object.assign(this, { api, toast, settings, onCall, onFeel, onIncoming, inCall });
    this.list = $("#chat-list");
    this.input = $("#chat-input");
    this.status = $("#chat-status");
    this.panel = $("#sticker-panel");
    this.latestBtn = $("#chat-latest");
    this.loading = $("#chat-loading");
    this.lastTs = 0;
    this.busy = false;
    this.pending = [];                // what you sent while she was still replying
    this.loaded = false;
    this.moreBefore = this.moreAfter = false;   // the thread goes on beyond what the page holds
    this.paging = false;
    this.fresh = 0;                   // her messages that came while you were reading further up
    this.quietSince = Date.now();     // when you last did anything in the chat
    this.unread = 0;
    this.title = document.title;
    try { this.played = new Set(JSON.parse(localStorage.getItem(PLAYED_KEY) || "[]")); } catch { this.played = new Set(); }

    $("#chat-bar").addEventListener("submit", e => { e.preventDefault(); this.submit(); });
    this.input.addEventListener("keydown", e => {
      // Enter sends, Shift+Enter is a new line; never while an IME (Chinese input) is composing
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.submit(); }
    });
    this.input.addEventListener("input", () => { this.fit(); this.quiet(); });
    $("#chat-sticker").onclick = () => this.togglePanel();
    $("#chat-call").onclick = () => onCall();
    $("#chat-settings").onclick = () => onSettings();
    $("#chat-memory").onclick = () => onMemory();
    $("#chat-find").onclick = () => onFind();
    $("#chat-mic").onclick = () => this.mic();
    this.list.addEventListener("click", () => { this.panel.hidden = true; });
    this.list.addEventListener("scroll", () => this.scrolled(), { passive: true });
    this.latestBtn.onclick = () => this.latest();
    // she texts whenever the chat is open, also in a background tab: the tab's title counts what's unread
    addEventListener("focus", () => { this.unread = 0; document.title = this.title; });
  }

  async open() {
    if (!this.loaded || this.moreAfter) await this.load(); else this.scroll();
    this.quiet();
    if (matchMedia("(pointer: fine)").matches) this.input.focus();
  }

  setStatus(text) { this.status.textContent = text; }
  scroll() { this.list.scrollTop = this.list.scrollHeight; this.fresh = 0; this.edges(); }
  fit() {
    this.input.style.height = "auto";
    this.input.style.height = Math.min(this.input.scrollHeight, 120) + "px";
    $("#chat-send").classList.toggle("ready", !!this.input.value.trim());
  }

  // ---- the stretch of the thread the page holds

  async page(params) {
    return (await this.api(`/api/text/history?${new URLSearchParams({ limit: PAGE, ...params })}`)).json();
  }

  // The newest messages: opening the chat, or back to the latest.
  async load() {
    try {
      this.fill(await this.page({}));
      this.loaded = true;
      this.scroll();
    } catch (e) {
      this.toast(t("serverDown", e.message));
    }
  }

  fill(p) {
    this.list.replaceChildren(...p.items.map(m => this.item(m)));
    this.moreBefore = p.more_before;
    this.moreAfter = p.more_after;
    this.fresh = 0;
    this.restamp();
  }

  // Open the thread at a search result ({around: lid}, which bubble) or at a day ({date: "2026-11-03"}).
  async jump(params, bi = 0) {
    this.panel.hidden = true;
    try {
      const p = await this.page(params);
      this.fill(p);
      this.loaded = true;
      const hit = this.list.querySelector(`li[data-lid="${p.target}"][data-bi="${bi}"]`) ||
                  this.list.querySelector(`li[data-lid="${p.target}"]`);
      if (!hit) { this.scroll(); return p; }
      const stamp = hit.previousElementSibling?.classList.contains("time") ? hit.previousElementSibling : hit;
      this.list.scrollTop = params.date ? stamp.offsetTop - 8 : hit.offsetTop - (this.list.clientHeight - hit.offsetHeight) / 2;
      if (!params.date) {
        hit.classList.add("found");
        setTimeout(() => hit.classList.remove("found"), 2500);
      }
      this.edges();
      return p;
    } catch (e) {
      this.toast(t("serverDown", e.message));
      return null;
    }
  }

  async latest() {
    if (this.moreAfter) await this.load();
    else this.list.scrollTo({ top: this.list.scrollHeight, behavior: "smooth" });
    this.fresh = 0;
    this.edges();
  }

  scrolled() {
    const l = this.list;
    if (l.scrollTop < EDGE) this.older();
    if (l.scrollHeight - l.scrollTop - l.clientHeight < EDGE) this.newer();
    this.edges();
  }

  // Near the top: the page before. Near the bottom of an older stretch: the page after.
  async older() {
    const first = this.list.querySelector("li[data-lid]");
    if (this.paging || !this.moreBefore || !first) return;
    await this.paged({ before: first.dataset.lid }, p => this.steady(() => {
      this.list.prepend(...p.items.map(m => this.item(m)));
      this.moreBefore = p.more_before;
      this.trim("bottom");
    }));
  }

  async newer() {
    const withLid = this.list.querySelectorAll("li[data-lid]");
    const last = withLid[withLid.length - 1];
    if (this.paging || !this.moreAfter || !last) return;
    await this.paged({ after: last.dataset.lid }, p => this.steady(() => {
      this.list.append(...p.items.map(m => this.item(m)));
      this.moreAfter = p.more_after;
      this.trim("top");
    }));
  }

  // Change what the page holds without moving what you're reading: the first message on
  // screen stays exactly where it was (also when the browser had to clamp the scroll).
  steady(change) {
    const l = this.list, top = l.getBoundingClientRect().top;
    const anchor = [...l.querySelectorAll("li.msg")].find(li => li.getBoundingClientRect().bottom > top);
    const y = anchor?.getBoundingClientRect().top;
    change();
    this.restamp();
    if (anchor?.isConnected) l.scrollTop += anchor.getBoundingClientRect().top - y;
  }

  async paged(params, apply) {
    this.paging = true;
    this.loading.hidden = false;
    try {
      apply(await this.page(params));
    } catch (e) {
      this.toast(t("serverDown", e.message));
    } finally {
      this.paging = false;
      this.loading.hidden = true;
      this.edges();
    }
  }

  // Hold at most KEEP entries: the far end goes, to be loaded again when you scroll back to it.
  trim(end) {
    const lids = [...new Set([...this.list.querySelectorAll("li[data-lid]")].map(li => li.dataset.lid))];
    if (lids.length <= KEEP) return;
    const items = [...this.list.children];
    if (end === "bottom") {
      const keep = lids[KEEP - 1];
      items.slice(items.findLastIndex(li => li.dataset.lid === keep) + 1).forEach(li => li.remove());
      this.moreAfter = true;
    } else {
      const keep = lids[lids.length - KEEP];
      items.slice(0, items.findIndex(li => li.dataset.lid === keep)).forEach(li => li.remove());
      this.moreBefore = true;
    }
  }

  // The "latest" pill, while the newest messages are out of sight.
  edges() {
    const l = this.list, below = l.scrollHeight - l.scrollTop - l.clientHeight;
    if (!this.moreAfter && below < 60) this.fresh = 0;
    this.latestBtn.hidden = !(this.moreAfter || this.fresh || below > l.clientHeight * 2);
    this.latestBtn.querySelector("span").textContent = this.fresh ? t("chatNew", this.fresh) : t("chatLatest");
  }

  // ---- showing messages

  // Time stamps above the first message and between ones further apart than GAP.
  restamp() {
    for (const s of this.list.querySelectorAll("li.time")) s.remove();
    let last = 0;
    for (const li of [...this.list.children]) {
      const ts = Number(li.dataset.ts);
      if (ts - last > GAP) li.before(this.stamp(ts));
      last = ts;
    }
    this.lastTs = last;
  }

  stamp(ts) {
    const li = document.createElement("li");
    li.className = "time";
    li.textContent = formatTime(ts);
    return li;
  }

  // A message as it happens (yours, hers, a call record): at the end of the thread when the
  // page holds the newest messages, else counted on the "latest" pill. Returns its <li>, or null.
  live(m) {
    if (m.role === "assistant" && !document.hasFocus()) { this.unread++; document.title = `(${this.unread}) ${this.title}`; }
    if (m.lid && this.list.querySelector(`li[data-lid="${m.lid}"]`)) return null;   // already there
    if (this.moreAfter) { this.fresh++; this.edges(); return null; }
    const l = this.list, atEnd = l.scrollHeight - l.scrollTop - l.clientHeight < 80;
    const li = this.item(m);
    const ts = Number(li.dataset.ts);
    if (ts - this.lastTs > GAP) l.append(this.stamp(ts));
    this.lastTs = ts;
    l.append(li);
    if (atEnd || m.role === "user") this.scroll(); else { this.fresh++; this.edges(); }
    return li;
  }

  // One message's <li>. lid: its entry in the server's timeline; bi: which bubble of that entry.
  item(m) {
    const li = document.createElement("li");
    li.dataset.ts = m.ts || Date.now();
    li.dataset.bi = m.bi || 0;
    if (m.lid) li.dataset.lid = m.lid;
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
    return li;
  }

  // A video call's record, the way a messaging app keeps one: a finished call on your side,
  // since you are the one who calls; one she made on hers, marked when you never picked it up.
  record(li, m) {
    const hers = m.kind === "her_call" || m.rang === "kurisu";   // a call of hers, rung or finished
    li.className = "msg " + (hers ? "her" : "me");
    const b = document.createElement("div");
    b.className = "bubble call-record" + (m.kind === "her_call" && m.answered !== "yes" ? " missed" : "");
    b.innerHTML = `<svg viewBox="0 0 24 24"><rect x="3" y="6" width="13" height="12" rx="2"/><path d="M16 10l5-3v10l-5-3z"/></svg>`;
    b.append(m.kind === "her_call" ? t(`herCall.${m.answered || "missed"}`) : callText(m));
    if (m.kind !== "her_call" && m.by === "kurisu" && m.seconds >= 2) b.append(Object.assign(document.createElement("small"), { textContent: t("callHerHung") }));
    li.append(b);
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
    if (this.moreAfter) await this.load();     // reading old messages: back to the newest first
    this.pending.push({ payload, li: this.live(mine) });
    this.quiet();
    this.idleAbort?.abort();     // she was only thinking of texting first: you go first
    if (!this.busy) await this.drain();
  }

  // Texts you sent while she was replying go together, as one turn.
  async drain() {
    while (this.pending.length) {
      const next = this.pending.splice(0);
      const texts = next.filter(p => p.payload.message);
      for (const p of next.filter(p => p.payload.sticker)) await this.exchange(p.payload, [p.li]);
      if (texts.length) await this.exchange({ messages: texts.map(p => p.payload.message) }, texts.map(p => p.li));
    }
  }

  // mine: the <li>s of what you sent, to learn their place in the timeline.
  // idle: she's texting first, so no "typing…" until she really writes, no error toasts, and
  // until her first bubble you can still cut in (send() aborts it).
  async exchange(payload, mine = [], idle = false) {
    this.busy = true;
    if (!idle) this.setStatus(t("chatTyping"));
    const t0 = performance.now();
    const hers = [];
    if (idle) this.idleAbort = new AbortController();
    try {
      const r = await this.api("/api/text", {
        method: "POST", headers: { "Content-Type": "application/json" },
        // in_call: she won't ring you in the middle of a call you are already on
        body: JSON.stringify({ ...payload, in_call: !!this.inCall?.() }),
        signal: idle ? this.idleAbort.signal : undefined,
      });
      for await (const { event, data } of sseEvents(r)) {
        if (event === "message") {
          // she "types" each bubble: longer ones take longer; the first one overlaps the model's own wait
          if (idle && !hers.length) { this.idleAbort = null; this.setStatus(t("chatTyping")); }
          const wait = this.typingTime(data) - (hers.length || idle ? 0 : performance.now() - t0);
          if (wait > 0) await sleep(wait);
          hers.push(this.live({ role: "assistant", ts: Date.now(), bi: hers.length, ...data }));
        } else if (event === "sent") {
          data.lids.forEach((lid, i) => { if (mine[i]) mine[i].dataset.lid = lid; });
        } else if (event === "saved") {
          for (const li of hers) if (li) li.dataset.lid = data.lid;
        } else if (event === "call") {
          // she didn't finish in text: she is ringing you (server/calling.py). Her bubbles are
          // already on screen, and the ring goes over them.
          this.onIncoming?.(data);
        } else if (event === "mood") {
          this.onFeel?.(data);
        } else if (event === "error") {
          throw new Error(data.message);
        }
      }
    } catch (e) {
      if (idle) console.warn("[chat] she didn't get to text first:", e.message);
      else this.toast(t("amadeusErr", e.message), 8000);
    } finally {
      this.busy = false;
      this.idleAbort = null;
      this.setStatus("");
    }
    this.armIdle();             // what comes next depends on what was just said
  }

  typingTime(m) {
    if (m.kind === "sticker") return 700;
    if (m.kind === "voice") return 400;   // the server already waited for the recording
    const perChar = this.settings().language === "zh" ? 110 : 45;
    return Math.min(2600, Math.max(500, 300 + perChar * (m.text || "").length));
  }

  // ---- when it goes quiet, she may text first. The server reads the conversation and says
  // whether and after how long (server/lull.py); the page counts the quiet. Asking costs nothing:
  // the model only hears of it when she actually might write.

  quiet() {                     // you did something: she waits a while again
    this.quietSince = Date.now();
    clearTimeout(this.idleTimer);
    clearTimeout(this.planTimer);
    this.planTimer = setTimeout(() => this.armIdle(), 3000);   // once you've stopped typing
  }

  async armIdle() {
    clearTimeout(this.idleTimer);
    const s = this.settings();
    if (!s.model || s.idle_talk === false || this.busy) return;   // after a reply, exchange() asks again
    let plan;
    try {
      plan = await (await this.api("/api/text/lull")).json();
    } catch {   // the server is away (restarting?): ask again in a minute rather than give up
      this.idleTimer = setTimeout(() => this.armIdle(), 60_000);
      return;
    }
    if (plan.wait == null || this.busy) return;
    const guard = MIN_QUIET - (Date.now() - this.quietSince) / 1000;
    clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => this.nudge(), Math.max(plan.wait, guard, 5) * 1000);
  }

  async nudge() {
    const s = this.settings();
    if (!s.model || s.idle_talk === false) return;
    if ($("#phone").dataset.mode !== "chat" || !this.loaded || this.busy || this.pending.length || this.rec ||
        this.input.value.trim()) {
      this.idleTimer = setTimeout(() => this.nudge(), 60_000);   // not now (on a call, writing...): in a minute
      return;
    }
    await this.exchange({ event: "idle", idle: Math.round((Date.now() - this.quietSince) / 1000) }, [], true);
    this.drain();               // anything you sent while she was thinking about it
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
      const stopLevel = showLevel(stream, btn);   // the ring follows your voice while it records
      rec.ondataavailable = e => e.data.size && chunks.push(e.data);
      rec.onstop = async () => {
        stopLevel();
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

// "Call 03:12", or "Call cancelled" for one that never really started.
export function callText(m) {
  const secs = Math.round(m.seconds || 0);
  return secs < 2 ? t("callCancelled") : t("callRecord", `${String(Math.floor(secs / 60)).padStart(2, "0")}:${String(secs % 60).padStart(2, "0")}`);
}

// 14:05 today, "Yesterday 14:05", else the date too.
export function formatTime(ts) {
  const d = new Date(ts), now = new Date();
  const hm = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 86400000);
  if (diff === 0) return hm;
  if (diff === 1) return `${t("yesterday")} ${hm}`;
  const lang = document.documentElement.lang || undefined;
  return `${d.toLocaleDateString(lang, { month: "short", day: "numeric", ...(d.getFullYear() !== now.getFullYear() && { year: "numeric" }) })} ${hm}`;
}
