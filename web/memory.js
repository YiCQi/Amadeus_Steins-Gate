// Her memory, as she keeps it (server/memory.py): her mood, what she knows about you, what
// she remembers and how clearly, and what she was asked to forget. Forgetting seals a memory
// rather than destroying it, so anything forgotten here can be brought back.

import { t } from "./i18n.js";

const $ = s => document.querySelector(s);
const el = (tag, cls, text) => Object.assign(document.createElement(tag), cls ? { className: cls } : {}, text != null ? { textContent: text } : {});

export class MemoryPanel {
  constructor({ api, toast }) {
    Object.assign(this, { api, toast });
    this.dlg = $("#memory");
    this.body = $("#mem-body");
    $("#mem-close").onclick = () => this.dlg.close();
    this.dlg.addEventListener("click", e => { if (e.target === this.dlg) this.dlg.close(); });   // click outside
  }

  async open() {
    try {
      this.render(await (await this.api("/api/memory")).json());
      this.dlg.showModal();
    } catch (e) {
      this.toast(t("serverDown", e.message));
    }
  }

  async act(what, kind, id, label) {
    if (what === "forget" && !confirm(t("mem.confirm", label))) return;
    try {
      const r = await this.api(`/api/memory/${what}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, id }),
      });
      this.render(await r.json());
    } catch (e) {
      this.toast(e.message);
    }
  }

  render(d) {
    const b = this.body;
    b.replaceChildren();

    // mood: a meter from bad to great
    const mood = el("section", "mem-mood");
    const level = Math.min(4, Math.floor(d.mood * 5));
    mood.append(el("h3", null, t("mem.mood")), el("span", "mem-mood-word", t(`mood.${level}`)));
    const bar = el("div", "mem-bar");
    bar.append(Object.assign(el("i"), { style: `width:${Math.round(d.mood * 100)}%` }));
    mood.append(bar, el("span", "mem-num", d.mood.toFixed(2)));
    b.append(mood);

    // the conversation not yet sorted into memories
    const now = this.section(t("mem.now"));
    if (d.working.count) {
      const row = this.item(t("mem.nowCount", d.working.count, stamp(d.working.since)));
      row.append(this.button(t("mem.forget"), () => this.act("forget", "recent", null, t("mem.unsorted"))));
      now.append(row);
    } else now.append(el("p", "mem-empty", t("mem.nowNone")));

    const facts = d.facts.filter(f => !f.sealed);
    const fs = this.section(t("mem.facts"));
    if (!facts.length) fs.append(el("p", "mem-empty", t("mem.factsNone")));
    for (const f of facts.slice().reverse()) {
      const row = this.item(f.text);
      row.append(this.button(t("mem.forget"), () => this.act("forget", "fact", f.id, f.text)));
      fs.append(row);
    }

    const eps = d.episodes.filter(e => !e.sealed).reverse();
    const es = this.section(t("mem.episodes"));
    if (!eps.length) es.append(el("p", "mem-empty", t("mem.episodesNone")));
    for (const e of eps) {
      const row = this.item(e.raw ? t("mem.unsorted") : e.title, e.gist);
      const meta = el("small", "mem-meta");
      const clarity = e.pending ? t("mem.pending") : e.strength >= 0.6 ? t("mem.clear") : e.strength >= 0.2 ? t("mem.faded") : t("mem.deep");
      meta.textContent = [stamp(e.end), clarity, e.recalls ? t("mem.recalled", e.recalls) : ""].filter(Boolean).join(" · ");
      const s = el("div", "mem-strength");
      s.append(Object.assign(el("i"), { style: `width:${Math.round(e.strength * 100)}%` }));
      row.querySelector(".mem-text").append(meta, s);
      row.append(this.button(t("mem.forget"), () => this.act("forget", "episode", e.id, e.title)));
      es.append(row);
    }

    const sealed = [...d.episodes.filter(e => e.sealed).map(e => ({ ...e, kind: "episode", label: e.raw ? t("mem.unsorted") : e.title })),
                    ...d.facts.filter(f => f.sealed).map(f => ({ ...f, kind: "fact", label: f.text }))];
    if (sealed.length) {
      const ss = this.section(t("mem.sealed"));
      ss.classList.add("mem-sealed");
      for (const x of sealed.sort((a, b) => (b.sealed_at || 0) - (a.sealed_at || 0))) {
        const row = this.item(x.label);
        row.append(this.button(t("mem.restore"), () => this.act("restore", x.kind, x.id)));
        ss.append(row);
      }
    }
  }

  section(title) {
    const s = el("section");
    s.append(el("h3", null, title));
    this.body.append(s);
    return s;
  }

  item(text, detail) {
    const row = el("div", "mem-item");
    const body = el("div", "mem-text");
    body.append(el("span", null, text));
    if (detail) body.append(el("p", "mem-gist", detail));
    row.append(body);
    return row;
  }

  button(label, onclick) {
    return Object.assign(el("button", "mem-btn", label), { type: "button", onclick });
  }
}

// "3 Nov 14:05" (with the year when it isn't this one)
function stamp(ts) {
  if (!ts) return "";
  const d = new Date(ts), lang = document.documentElement.lang || undefined;
  const day = d.toLocaleDateString(lang, { month: "short", day: "numeric", ...(d.getFullYear() !== new Date().getFullYear() && { year: "numeric" }) });
  return `${day} ${d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}
