// Searching the chat history the way a messaging app does: by words, by kind (her voice
// messages, stickers, calls), or by date. A result opens the chat around that message.

import { t } from "./i18n.js";
import { callText, formatTime } from "./chat.js";

const $ = s => document.querySelector(s);
const AVATAR = "assets/kurisu/chat_avatar.webp";
const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

export class ChatSearch {
  // api(path) -> Response; jump(params, bi) -> opens the chat there (Chat.jump); toast(msg, ms)
  constructor({ api, jump, toast }) {
    Object.assign(this, { api, jump, toast });
    this.box = $("#find");
    this.input = $("#find-input");
    this.list = $("#find-list");
    this.note = $("#find-note");
    this.date = $("#find-date");
    this.kind = "";
    this.seq = 0;
    $("#find-back").onclick = () => this.close();
    this.input.addEventListener("input", () => { clearTimeout(this.timer); this.timer = setTimeout(() => this.run(), 250); });
    this.box.addEventListener("keydown", e => { if (e.key === "Escape") this.close(); });
    for (const b of this.box.querySelectorAll("[data-kind]")) {
      b.onclick = () => {
        this.kind = this.kind === b.dataset.kind ? "" : b.dataset.kind;
        for (const o of this.box.querySelectorAll("[data-kind]")) o.classList.toggle("on", o.dataset.kind === this.kind);
        this.run();
      };
    }
    $("#find-day").onclick = () => {
      this.date.max = dayKey(Date.now());
      try { this.date.showPicker(); } catch { this.date.focus(); }
    };
    this.date.onchange = () => { const d = this.date.value; this.date.value = ""; this.byDate(d); };
  }

  open() {
    this.box.hidden = false;
    this.input.focus();
    this.input.select();
  }

  close() { this.box.hidden = true; }

  async run() {
    const q = this.input.value.trim(), seq = ++this.seq;
    if (!q && !this.kind) { this.list.replaceChildren(); this.note.textContent = ""; return; }
    try {
      const r = await (await this.api(`/api/text/search?${new URLSearchParams({ q, kind: this.kind })}`)).json();
      if (seq !== this.seq) return;   // you've typed on since: a newer search is coming
      const words = q.toLowerCase().split(/\s+/).filter(Boolean);
      this.list.replaceChildren(...r.items.map(it => this.hit(it, words)));
      this.list.scrollTop = 0;
      this.note.textContent = !r.total ? t("findNone")
        : r.total > r.items.length ? t("findShown", r.total, r.items.length) : t("findCount", r.total);
    } catch (e) {
      this.toast(t("serverDown", e.message));
    }
  }

  // One result: who, when, and what was said with your words marked.
  hit(it, words) {
    const b = document.createElement("button");
    b.type = "button";
    const mine = it.role === "user" || it.role === "event";
    const ava = mine ? Object.assign(document.createElement("span"), { className: "ava", textContent: t("findMe") })
      : Object.assign(new Image(), { src: AVATAR, alt: "", className: "ava" });
    const txt = Object.assign(document.createElement("div"), { className: "txt" });
    const who = Object.assign(document.createElement("div"), { className: "who" });
    who.append(Object.assign(document.createElement("span"), { textContent: mine ? t("findMe") : t("findHer") }),
               Object.assign(document.createElement("time"), { textContent: formatTime(it.ts) }));
    const what = Object.assign(document.createElement("div"), { className: "what" });
    if (it.kind === "sticker") {
      what.append(Object.assign(new Image(), { src: it.url, alt: "", loading: "lazy" }), marked(it.meaning || "", words));
    } else if (it.kind === "voice") {
      what.append(Object.assign(document.createElement("i"), { textContent: `[${t("voiceMsg")}]` }), marked(it.sub || it.ja || "", words));
    } else if (it.kind === "call") {
      what.append(Object.assign(document.createElement("i"), { textContent: `[${t("videoCall")}]` }), callText(it));
    } else {
      what.append(marked(it.text || "", words));
    }
    txt.append(who, what);
    b.append(ava, txt);
    b.onclick = () => { this.close(); this.jump({ around: it.lid }, it.bi || 0); };
    const li = document.createElement("li");
    li.append(b);
    return li;
  }

  // WeChat's "find by date": the chat opens at that day's first message.
  async byDate(date) {
    if (!date) return;
    this.close();
    const p = await this.jump({ date });
    const first = p?.items.find(i => i.lid === p.target);
    if (!first) this.toast(t("findEmpty"), 2500);
    else if (dayKey(first.ts) !== date) this.toast(t("findNoDay"), 3500);
  }
}

// The text with your words marked, starting near the first one so it shows in two lines.
function marked(text, words) {
  const frag = document.createDocumentFragment();
  if (!words.length) { frag.append(text); return frag; }
  const re = new RegExp(words.map(escapeRe).join("|"), "gi");
  const first = text.search(re);
  if (first > 24) text = "…" + text.slice(first - 12);
  let at = 0;
  for (const m of text.matchAll(re)) {
    frag.append(text.slice(at, m.index), Object.assign(document.createElement("mark"), { textContent: m[0] }));
    at = m.index + m[0].length;
  }
  frag.append(text.slice(at));
  return frag;
}

// "2026-11-03" for a time, in local time (what a date input gives).
function dayKey(ts) {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
