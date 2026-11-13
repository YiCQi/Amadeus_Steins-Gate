// Her memory, as she keeps it (server/memory.py, server/notes.py): her mood, what she knows
// about you (folded: the core she always has in mind, and the archive she recalls from), what
// she remembers and how clearly, what changed lately (each change can be undone), and what she
// was asked to forget. Forgetting seals rather than destroys, so it can always be restored.

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
    const body = what === "undo" ? { change: id } : { kind, id };
    try {
      const r = await this.api(`/api/memory/${what}`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
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

    if (d.notes) this.knows(d.notes);
    else {   // before notes: the old fact list
      const facts = d.facts.filter(f => !f.sealed);
      const fs = this.section(t("mem.facts"));
      if (!facts.length) fs.append(el("p", "mem-empty", t("mem.factsNone")));
      for (const f of facts.slice().reverse()) {
        const row = this.item(f.text);
        row.append(this.button(t("mem.forget"), () => this.act("forget", "fact", f.id, f.text)));
        fs.append(row);
      }
    }

    const eps = d.episodes.filter(e => !e.sealed).reverse();
    const es = this.section(t("mem.episodes"));
    if (!eps.length) es.append(el("p", "mem-empty", t("mem.episodesNone")));
    // the words themselves stay searchable behind the memories, so say so: the memories are
    // summaries, and without this line it would look as though the detail in them is all there is
    if (d.verbatim?.indexed) es.append(el("p", "mem-empty", t("mem.verbatim", d.verbatim.indexed)));
    for (const e of eps) {
      const row = this.item(e.raw ? t("mem.unsorted") : e.title, e.gist);
      const meta = el("small", "mem-meta");
      const clarity = e.pending ? t("mem.pending") : e.strength >= 0.6 ? t("mem.clear") : e.strength >= 0.2 ? t("mem.faded") : t("mem.deep");
      // how worked up she was at the time: it is what keeps the memory alive longest, and what is
      // left of it once the details have gone, so it belongs next to how clearly she still has it
      const word = e.feeling && e.feeling !== "neutral" ? t(`feel.${e.feeling}`) : "";
      const felt = word && e.arousal != null
        ? t("mem.felt", t(`feel.lvl${e.arousal < 0.25 ? 0 : e.arousal < 0.6 ? 1 : 2}`), word) : "";
      meta.textContent = [stamp(e.end), clarity, felt, e.recalls ? t("mem.recalled", e.recalls) : ""].filter(Boolean).join(" · ");
      const s = el("div", "mem-strength");
      s.append(Object.assign(el("i"), { style: `width:${Math.round(e.strength * 100)}%` }));
      row.querySelector(".mem-text").append(meta, s);
      row.append(this.button(t("mem.forget"), () => this.act("forget", "episode", e.id, e.title)));
      es.append(row);
    }

    if (d.notes?.changes.length) this.changes(d.notes.changes);

    const sealed = [...d.episodes.filter(e => e.sealed).map(e => ({ ...e, kind: "episode", label: e.raw ? t("mem.unsorted") : e.title })),
                    ...d.facts.filter(f => f.sealed).map(f => ({ ...f, kind: "fact", label: f.text })),
                    ...(d.notes?.forgotten || []).map(n => ({ ...n, kind: "note", label: n.text, sealed_at: n.forgotten_at }))];
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

  // What she knows, folded: open it for the core by section, and the archive with a search box.
  knows(n) {
    const fold = this.fold(t("mem.knows"), t("mem.knowsSummary", n.core.length, n.core_chars, n.core_max, n.archive.length));
    for (const [sec, title] of Object.entries(n.sections)) {
      const lines = n.core.filter(x => x.section === sec);
      if (!lines.length) continue;
      fold.append(el("h4", null, t(`mem.sec.${sec}`) || title));
      for (const x of lines) fold.append(this.noteRow(x));
    }
    if (!n.core.length) fold.append(el("p", "mem-empty", t("mem.factsNone")));
    fold.append(el("h4", null, t("mem.archive")));
    if (n.archive.length) {
      const find = Object.assign(el("input", "mem-find"), { type: "search", placeholder: t("findPh") });
      const rows = n.archive.map(x => this.noteRow(x));
      find.oninput = () => {
        const q = find.value.trim().toLowerCase();
        for (const [i, r] of rows.entries()) r.hidden = !!q && !n.archive[i].text.toLowerCase().includes(q);
      };
      fold.append(find, ...rows);
    } else fold.append(el("p", "mem-empty", t("mem.archiveNone")));
  }

  noteRow(x) {
    const row = this.item(x.text);
    const when = x.happened ? `${x.happened} · ` : "";
    const meta = el("small", "mem-meta", `${when}${t("mem.noted")} ${stamp(x.recorded)}` + (x.evidence ? ` · ${t("mem.cameUp", x.evidence)}` : ""));
    const text = row.querySelector(".mem-text");
    text.append(meta);
    if (x.history?.length) {
      const h = Object.assign(el("details", "mem-history"));
      h.append(el("summary", null, t("mem.versions", x.history.length)));
      for (const v of x.history.slice().reverse()) h.append(el("p", null, `v${v.v} · ${stamp(v.recorded)} · ${v.text}`));
      text.append(h);
    }
    const btns = el("div", "mem-btns");
    btns.append(this.button(t("mem.outdated"), () => this.act("outdated", "note", x.id)),
                this.button(t("mem.forget"), () => this.act("forget", "note", x.id, x.text)));
    row.append(btns);
    return row;
  }

  // What changed lately in what she knows, and by whom; anything but forgetting can be undone here.
  changes(list) {
    const fold = this.fold(t("mem.changes"), t("mem.changesSummary", list.length));
    for (const c of list) {
      const row = this.item(`${t(`mem.op.${c.op}`)} · ${c.text || ""}`);
      const why = c.by === "model" || c.by === "review" ? c.why : "";   // her own reasons; yours and the system's are the label
      row.querySelector(".mem-text").append(el("small", "mem-meta", [stamp(c.ts), t(`mem.by.${c.by}`), why].filter(Boolean).join(" · ")));
      if (!["forget", "restore", "undo"].includes(c.op)) row.append(this.button(t("mem.undo"), () => this.act("undo", null, c.n)));
      fold.append(row);
    }
  }

  fold(title, summary) {
    const d = el("details", "mem-fold");
    const s = el("summary");
    s.append(el("h3", null, title), el("small", null, summary));
    d.append(s);
    this.body.append(d);
    return d;
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
