"""Kurisu's memory: one timeline for calls and chats, kept the way a person keeps one.

    data/memory/log.jsonl      everything ever said, call and chat alike, with times: the archive
    data/memory/state.json     how far the log has been consolidated, and her mood
    data/memory/episodes.json  what she remembers of older conversations, in her own words
    data/memory/facts.json     what she knows about the user

- Working memory is the log since the last consolidation. The model reads it as a transcript
  with times, so she knows what was said in the chat when you call, and the other way round.
- When it grows long, or a conversation has been over for a while, its oldest stretch is
  consolidated (by the model, in the background) into an episode: a dated gist in her words,
  how much it mattered, how she felt, and the facts it taught her about you.
- Episodes fade with time unless they come up again; each recall makes one last longer (the
  spacing effect). A faded one isn't gone: it is a deep memory, and a strong enough reminder
  brings it back. She is told only what this moment reminds her of, so the prompt stays small.
- Forgetting is selective and reversible: a forgotten episode or fact is sealed, kept in the
  archive but out of her reach, until it is restored.
- Her mood (0..1) moves with how each reply felt to her and drifts back to her usual self
  over hours.
"""
import asyncio
import json
import math
import re
import time
from datetime import datetime
from pathlib import Path

from . import config

DIR = config.DATA_DIR / "memory"
LOG, STATE, EPISODES, FACTS = (DIR / n for n in ("log.jsonl", "state.json", "episodes.json", "facts.json"))

WORKING_MAX = 60             # messages of working memory before its oldest stretch is consolidated
WORKING_CHARS = 7000         # ... or this many characters of it
SESSION_GAP = 3600_000       # ms: an hour's silence ends a conversation
CHUNK = 30                   # messages consolidated at a time out of one long conversation
MOOD_BASE, MOOD_TAU = 0.55, 8 * 3600_000   # her usual mood, and how fast she returns to it (ms)
EMOTION_MOOD = {"laugh": 0.05, "happy": 0.04, "smile": 0.03, "smug": 0.02, "embarrassed": 0.01,
                "puzzled": -0.005, "annoyed": -0.03, "pout": -0.03, "disgusted": -0.03,
                "sad": -0.05, "angry": -0.05, "furious": -0.08}

_lock = asyncio.Lock()
_cache: dict = {"key": None, "log": []}


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- storage

def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(path: Path, data):
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def state() -> dict:
    s = _read(STATE, {})
    s.setdefault("consolidated", 0)       # the log up to this id has become episodes
    s.setdefault("next_id", 1)
    s.setdefault("mood", {"value": MOOD_BASE, "ts": now_ms()})
    return s


def episodes() -> list[dict]:
    return _read(EPISODES, [])


def facts() -> list[dict]:
    return _read(FACTS, [])


def log() -> list[dict]:
    migrate()
    try:
        st = LOG.stat()
    except OSError:
        return []
    key = (st.st_size, st.st_mtime_ns)
    if key != _cache["key"]:
        out = []
        for line in LOG.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        _cache.update(key=key, log=out)
    return _cache["log"]


def add(ch: str, role: str, content: str = "", **extra) -> dict:
    """Append one message ("chat"/"call"; "user"/"assistant"/"event") to the timeline."""
    migrate()
    s = state()
    entry = {"id": s["next_id"], "ts": extra.pop("ts", None) or now_ms(), "ch": ch, "role": role, "content": content, **extra}
    s["next_id"] += 1
    _write(STATE, s)
    DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def migrate():
    """First run: bring the old separate call (history.json) and chat (chat.json) threads into one timeline."""
    if LOG.exists() or getattr(migrate, "done", False):
        return
    migrate.done = True
    from . import persona
    entries = []
    for e in _read(config.CHAT_PATH, []):
        entries.append({"ts": e.get("ts") or 0, "ch": "chat", "role": e["role"], "content": e["content"]})
    call = _read(config.HISTORY_PATH, [])
    if call:   # these were never timed: put them just before the file was last written, 30 s apart
        end = int(config.HISTORY_PATH.stat().st_mtime * 1000)
        for i, e in enumerate(call):
            ts = end - (len(call) - i) * 30_000
            if e["role"] == "user" and persona.is_connect_event(e["content"]):
                entries.append({"ts": ts, "ch": "call", "role": "event", "content": "", "kind": "call_start"})
            else:
                entries.append({"ts": ts, "ch": "call", "role": e["role"], "content": e["content"]})
    if not entries:
        return
    entries.sort(key=lambda e: e["ts"])
    DIR.mkdir(parents=True, exist_ok=True)
    with LOG.open("w", encoding="utf-8") as f:
        for i, e in enumerate(entries, 1):
            f.write(json.dumps({"id": i, **e}, ensure_ascii=False) + "\n")
    s = state()
    s["next_id"] = len(entries) + 1
    _write(STATE, s)
    for p in (config.CHAT_PATH, config.HISTORY_PATH):
        if p.exists():
            p.replace(p.with_suffix(p.suffix + ".bak"))   # kept, just no longer read
    print(f"[memory] moved {len(entries)} old messages into data/memory/log.jsonl", flush=True)


# ---------------------------------------------------------------- working memory

def working() -> list[dict]:
    upto = state()["consolidated"]
    return [m for m in log() if m["id"] > upto]


def split(entries: list[dict], channel: str, native_max: int = 12) -> tuple[list[dict], list[dict]]:
    """(earlier, recent): the trailing run of this channel's messages goes to the model as real
    turns in this channel's own format; everything before it goes in as a transcript."""
    run = []
    for m in reversed(entries):
        if m["ch"] != channel or m["role"] == "event":
            break
        run.append(m)
    run = run[::-1][-native_max:]
    while run and run[0]["role"] != "user":
        run.pop(0)
    return entries[:len(entries) - len(run)], run


# ---------------------------------------------------------------- text helpers

_TOKEN = re.compile(r"[a-z0-9]+|[぀-ヿ㐀-鿿]+")
_STOP = set("the a an and or but to of in on at for with is are was were be been it its this that "
            "you your i me my we our he she they them what do did does not no so just like really "
            "have has had can will would about there here then than very too also".split()) | {
    "你们", "我们", "他们", "什么", "一个", "这个", "那个", "就是", "不是", "没有", "可以", "怎么", "因为",
    "所以", "但是", "然后", "还是", "已经", "知道", "觉得", "自己", "时候", "真的", "现在", "一下", "一点",
    "这样", "那样", "的话", "是不", "不会", "我的", "你的", "了吗", "好的", "还有", "其实", "有点"}


def tokens(text: str) -> set[str]:
    """Words for Latin text, character pairs for Chinese and Japanese (no word breaks there)."""
    out = set()
    for run in _TOKEN.findall(text.lower()):
        if run.isascii():
            if len(run) > 1 and run not in _STOP:
                out.add(run)
        elif len(run) == 1:
            out.add(run)
        else:
            out.update(run[i:i + 2] for i in range(len(run) - 1))
    return out - _STOP


def similar(a: set[str], b: set[str]) -> float:
    return len(a & b) / math.sqrt(len(a) * len(b)) if a and b else 0.0


def ago(ts: int, now: int | None = None) -> str:
    s = max(0, ((now or now_ms()) - ts) / 1000)
    if s < 90:
        return "just now"
    if s < 3600:
        return f"{round(s / 60)} minutes ago"
    if s < 86400:
        return f"{round(s / 3600)} hours ago" if s >= 5400 else "an hour ago"
    d = s / 86400
    if d < 2:
        return "yesterday"
    if d < 7:
        return f"{round(d)} days ago"
    if d < 14:
        return "last week"
    if d < 60:
        return f"{round(d / 7)} weeks ago"
    return f"{round(d / 30)} months ago"


def when(ts: int) -> str:
    return datetime.fromtimestamp(ts / 1000).strftime("%a %d %b %Y")


def part_of_day(h: int) -> str:
    return ("late at night" if h < 5 else "early morning" if h < 8 else "morning" if h < 12 else
            "afternoon" if h < 17 else "evening" if h < 22 else "late at night")


def seconds_text(sec: float) -> str:
    sec = int(round(sec or 0))
    return f"{sec // 60}:{sec % 60:02d}"


def said(m: dict, meaning) -> str:
    """One message as plain text for the transcript. meaning(sticker id) -> its meaning."""
    from . import persona
    if m["role"] == "user":
        sid = persona.user_sticker(m["content"])
        return f"[sticker: {meaning(sid)}]" if sid else m["content"]
    if m["ch"] == "chat":
        parts = []
        for b in persona.chat_bubbles(m["content"]):
            parts.append(b["text"] if b["kind"] == "text" else f"[sticker: {meaning(b['name'])}]"
                         if b["kind"] == "sticker" else f"(voice message) {b['sub']}")
        return " / ".join(parts)
    # what she meant (the subtitle); her recent call turns still reach the model in Japanese
    return " ".join(s["sub"] for s in map(persona.parse_line, m["content"].splitlines()) if s)


def event_text(m: dict) -> str:
    if m.get("kind") == "call_start":
        return "(the user started a video call)"
    if m.get("kind") == "call_end":
        who = "you hung up" if m.get("by") == "kurisu" else "the user hung up"
        return f"(the video call ended after {seconds_text(m.get('seconds'))}; {who})"
    return f"({m.get('kind', 'event')})"


def transcript(entries: list[dict], meaning) -> str:
    out, day = [], None
    for m in entries:
        d = datetime.fromtimestamp(m["ts"] / 1000)
        if d.date() != day:
            day = d.date()
            out.append(f"— {d:%A %d %B %Y} —")
        t = d.strftime("%H:%M")
        if m["role"] == "event":
            out.append(f"[{t}] {event_text(m)}")
            continue
        via = "text" if m["ch"] == "chat" else "call"
        who = "User" if m["role"] == "user" else "You"
        text = said(m, meaning)
        if text:
            out.append(f"[{t} · {via}] {who}: {text[:600]}")
    return "\n".join(out)


# ---------------------------------------------------------------- recall and forgetting

def strength(ep: dict, now: int | None = None) -> float:
    """1 when fresh, 0.5 after one half-life. Important memories, and ones that keep coming
    up again, have longer half-lives; the clock restarts from the last time one was recalled."""
    now = now or now_ms()
    ref = max(ep.get("end", 0), ep.get("last_recalled") or 0)
    days = max(0.0, (now - ref) / 86400_000)
    half = 5 * (1 + ep.get("recalls", 0)) * (0.4 + 1.6 * ep.get("importance", 0.5))
    return math.exp(-days * math.log(2) / half)


def _ep_tokens(ep: dict) -> set[str]:
    return tokens(" ".join([ep.get("title", ""), ep.get("gist", ""), " ".join(ep.get("keywords", []))]))


def recall(query: str, k: int = 3, now: int | None = None) -> list[tuple[dict, float]]:
    """The episodes this moment brings back, with how clearly (strength)."""
    now = now or now_ms()
    q = tokens(query)
    if not q:
        return []
    picks = []
    for ep in episodes():
        if ep.get("sealed") or ep.get("raw"):
            continue
        sim = similar(q, _ep_tokens(ep))
        st = strength(ep, now)
        score = sim * (0.35 + 0.65 * st)
        if sim >= 0.3:           # a strong reminder brings back even a faded memory
            score = max(score, 0.8 * sim)
        if score >= 0.12:
            picks.append((score, ep, st))
    picks.sort(key=lambda p: -p[0])
    return [(ep, st) for _, ep, st in picks[:k]]


def remembered(eps: list[dict]):
    """Recalling strengthens: count it, unless it was already recalled in the last half hour."""
    if not eps:
        return
    now, ids = now_ms(), {e["id"] for e in eps}
    all_eps = episodes()
    changed = False
    for e in all_eps:
        if e["id"] in ids and now - (e.get("last_recalled") or 0) > 1800_000:
            e["recalls"] = e.get("recalls", 0) + 1
            e["last_recalled"] = now
            changed = True
    if changed:
        _write(EPISODES, all_eps)


def known_facts(n: int = 15) -> list[dict]:
    live = [f for f in facts() if not f.get("sealed")]
    return sorted(live, key=lambda f: (f.get("importance", 0.5), f.get("ts", 0)), reverse=True)[:n]


def forget(kind: str, fid) -> bool:
    """Seal an episode, a fact, or the current unconsolidated conversation ("recent")."""
    now = now_ms()
    if kind == "recent":
        w = working()
        if not w:
            return False
        ep = {"id": _next_ep_id(), "raw": True, "sealed": True, "sealed_at": now, "start": w[0]["ts"],
              "end": w[-1]["ts"], "range": [w[0]["id"], w[-1]["id"]], "title": "(an unsorted conversation)",
              "gist": " / ".join(m["content"][:40] for m in w if m["role"] == "user")[:200], "importance": 0.5}
        _write(EPISODES, episodes() + [ep])
        s = state()
        s["consolidated"] = w[-1]["id"]
        _write(STATE, s)
        return True
    path = EPISODES if kind == "episode" else FACTS
    items = _read(path, [])
    hit = False
    for it in items:
        if it["id"] == fid and not it.get("sealed"):
            it["sealed"], it["sealed_at"], hit = True, now, True
    if hit:
        _write(path, items)
        if kind == "episode":   # what she learned in it goes with it
            fs = facts()
            for f in fs:
                if f.get("source") == fid and not f.get("sealed"):
                    f["sealed"], f["sealed_at"] = True, now
            _write(FACTS, fs)
    return hit


def restore(kind: str, fid) -> bool:
    path = EPISODES if kind == "episode" else FACTS
    items = _read(path, [])
    hit = False
    for it in items:
        if it["id"] == fid and it.get("sealed"):
            it["sealed"], hit = False, True
            it.pop("sealed_at", None)
            if it.get("raw"):
                it["pending"] = True        # never sorted: the next consolidation makes it a memory
    if hit:
        _write(path, items)
        if kind == "episode":
            fs = facts()
            for f in fs:
                if f.get("source") == fid and f.get("sealed"):
                    f["sealed"] = False
                    f.pop("sealed_at", None)
            _write(FACTS, fs)
    return hit


def _next_ep_id() -> int:
    return max((e["id"] for e in episodes()), default=0) + 1


# ---------------------------------------------------------------- mood

def mood(now: int | None = None) -> float:
    now = now or now_ms()
    m = state()["mood"]
    return MOOD_BASE + (m["value"] - MOOD_BASE) * math.exp(-max(0, now - m["ts"]) / MOOD_TAU)


def feel(emotions: list[str], extra: float = 0.0) -> float:
    """How a reply felt moves her mood a little (at most 0.1 a reply)."""
    v = mood()
    d = max(-0.1, min(0.1, sum(EMOTION_MOOD.get(e, 0) for e in emotions) + extra))
    s = state()
    s["mood"] = {"value": round(max(0.0, min(1.0, v + d)), 3), "ts": now_ms()}
    _write(STATE, s)
    return s["mood"]["value"]


def mood_words(v: float) -> str:
    return ("in a bad mood" if v < 0.2 else "a bit grumpy" if v < 0.4 else "your usual self" if v < 0.6
            else "in a good mood" if v < 0.8 else "in a great mood")


# ---------------------------------------------------------------- what the model reads

def context(channel: str, query: str, meaning) -> tuple[str, list[dict]]:
    """(text for the system prompt, recent messages to send as real turns)."""
    now = now_ms()
    w = working()
    earlier, recent = split(w, channel)
    dt = datetime.now()
    out = ["", "", "# Your memory",
           f"It is now {dt:%A %d %B %Y, %H:%M} ({part_of_day(dt.hour)}) where the user lives."]
    said_before = [m for m in log() if m["role"] != "event"]
    if said_before:
        last = said_before[-1]
        out.append(f"You last talked {ago(last['ts'], now)} (by {'text' if last['ch'] == 'chat' else 'video call'}).")
    else:
        out.append("You have never talked with the user before: this is your first conversation.")
    v = mood(now)
    out.append(f"Your mood right now: {v:.2f} on a 0–1 scale ({mood_words(v)}). Let it colour how you react; don't announce it.")

    known = known_facts()
    if known:
        out.append("\nWhat you know about the user:")
        out += [f"- {f['text']}" for f in known]

    cue = " ".join([query] + [said(m, meaning) for m in w[-3:] if m["role"] != "event"])
    picks = recall(cue)
    eps = [e for e in episodes() if not e.get("sealed") and not e.get("raw")]
    # if this conversation is new, the last one comes to mind by itself
    if eps and (not w or w[0]["ts"] - eps[-1]["end"] > SESSION_GAP) and all(p[0]["id"] != eps[-1]["id"] for p in picks):
        picks.append((eps[-1], strength(eps[-1], now)))
    if picks:
        out.append("\nWhat comes back to you:")
        for ep, st in picks:
            stamp = f"{ago(ep['end'], now)} ({when(ep['end'])})"
            out.append(f"- {stamp}: {ep['title']}. {ep.get('gist', '')}" if st >= 0.2 else
                       f"- {stamp}: you only vaguely remember — {ep['title']}.")
        remembered([ep for ep, _ in picks])
    if known or picks:
        out.append("Bring memories up the way a person does: only when they fit, without reciting them; a vague one "
                   "stays vague (\"didn't you say something about...?\"). You don't remember anything that isn't here.")
    # the newest part of working memory; anything older is waiting to be consolidated
    shown, chars = [], 0
    for m in reversed(earlier):
        chars += len(m.get("content", ""))
        if len(shown) >= WORKING_MAX or chars > WORKING_CHARS:
            break
        shown.append(m)
    if shown:
        out.append('\n# The conversation so far (most recent last; "text" = typed in the chat, "call" = said on a video call)')
        out.append(transcript(shown[::-1], meaning))
    return "\n".join(out), recent


# ---------------------------------------------------------------- consolidation

CONSOLIDATE = """You are the long-term memory of Makise Kurisu (the AI built from her memory data). Below is a stretch of conversation between Kurisu ("You") and the user, with times. Write what Kurisu will keep of it, as one JSON object:
{{"title": "...", "gist": "...", "facts": ["..."], "importance": 0.5, "feeling": "neutral", "keywords": ["..."]}}
- title: a few words for what it was about, in {lang}.
- gist: 1–3 sentences in {lang}, in Kurisu's own voice (first person), what happened and how she felt about it. Keep names, numbers, plans and promises.
- facts: lasting things learned about the user (their life, likes, people, plans, promises made), one short sentence each in {lang}; [] if none. Nothing about Kurisu herself.
- importance: 0.1 small talk ... 0.5 a real conversation ... 0.8 something personal, a promise, a first ... 1.0 unforgettable.
- feeling: one of {emotions}.
- keywords: 3–8 words someone might use to bring this up again (names, places, topics).
Output the JSON only."""


def _due(w: list[dict]) -> bool:
    if not w:
        return False
    if len(w) > WORKING_MAX or sum(len(m.get("content", "")) for m in w) > WORKING_CHARS:
        return True
    gaps = [i for i in range(1, len(w)) if w[i]["ts"] - w[i - 1]["ts"] >= SESSION_GAP]
    return bool(gaps) and gaps[0] >= 4 and now_ms() - w[gaps[0] - 1]["ts"] > SESSION_GAP


def _oldest_stretch(w: list[dict]) -> list[dict]:
    gaps = [i for i in range(1, len(w)) if w[i]["ts"] - w[i - 1]["ts"] >= SESSION_GAP]
    if gaps and gaps[0] >= 4:
        chunk = w[:gaps[0]]
        return chunk if len(chunk) <= WORKING_MAX else chunk[:CHUNK]
    return w[:CHUNK]


def _parse(raw: str) -> dict | None:
    from . import persona
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group())
    except json.JSONDecodeError:
        return None
    if not isinstance(d, dict) or not d.get("title"):
        return None
    return {"title": str(d["title"])[:80], "gist": str(d.get("gist", ""))[:600],
            "facts": [str(f)[:200] for f in d.get("facts", []) if f][:8],
            "importance": max(0.0, min(1.0, float(d.get("importance", 0.5) or 0.5))),
            "feeling": persona.norm_emotion(str(d.get("feeling", "neutral"))),
            "keywords": [str(k)[:30] for k in d.get("keywords", []) if k][:8]}


def _fallback(chunk: list[dict]) -> dict:
    users = [m["content"] for m in chunk if m["role"] == "user"]
    return {"title": (users[0] if users else "a conversation")[:30], "gist": " / ".join(u[:40] for u in users[:4]),
            "facts": [], "importance": 0.3, "feeling": "neutral", "keywords": []}


def _merge_facts(new: list[str], source: int):
    if not new:
        return
    fs, now = facts(), now_ms()
    nid = max((f["id"] for f in fs), default=0)
    for text in new:
        t = tokens(text)
        same = max(((similar(t, tokens(f["text"])), f) for f in fs if not f.get("sealed")), default=(0, None), key=lambda p: p[0])
        if same[1] is not None and same[0] > 0.55:
            same[1].update(text=text, ts=now, source=source)   # learned again: keep it fresh
        else:
            nid += 1
            fs.append({"id": nid, "text": text, "ts": now, "importance": 0.5, "source": source})
    _write(FACTS, fs)


async def consolidate(summarize, language: str, rounds: int = 4) -> int:
    """Fold the oldest stretches of working memory (and restored, unsorted ones) into episodes,
    a few at a time. summarize(system, text) -> the model's reply. Run in the background."""
    if _lock.locked():
        return 0
    async with _lock:
        done = 0
        while done < rounds and await _consolidate_one(summarize, language):
            done += 1
        return done


async def _consolidate_one(summarize, language: str) -> bool:
    """One stretch (or one restored, unsorted conversation) into one episode; False if none is due."""
    from . import persona
    lang = persona.LANGUAGES.get(language, persona.LANGUAGES["en"])["name"]
    system = CONSOLIDATE.format(lang=lang, emotions=", ".join(persona.EMOTIONS))
    pending = next((e for e in episodes() if e.get("pending")), None)
    if pending:
        chunk = [m for m in log() if pending["range"][0] <= m["id"] <= pending["range"][1]]
    else:
        w = working()
        if not _due(w):
            return False
        chunk = _oldest_stretch(w)
    meaning = lambda sid: sid or ""  # noqa: E731
    try:
        ep = _parse(await summarize(system, transcript(chunk, meaning)))
    except Exception as e:  # network, API
        print(f"[memory] consolidation failed: {type(e).__name__}: {e}", flush=True)
        ep = None
    if ep is None:
        if pending or len(working()) < WORKING_MAX * 2:
            return False                     # try again after the next reply
        ep = _fallback(chunk)                # never let working memory grow without bound
    all_eps = episodes()
    if pending:
        all_eps = [e for e in all_eps if e["id"] != pending["id"]]
        eid = pending["id"]
    else:
        eid = _next_ep_id()
    all_eps.append({"id": eid, "start": chunk[0]["ts"], "end": chunk[-1]["ts"],
                    "range": [chunk[0]["id"], chunk[-1]["id"]], "recalls": 0, "last_recalled": None,
                    "created": now_ms(), **{k: v for k, v in ep.items() if k != "facts"}})
    all_eps.sort(key=lambda e: e["end"])
    _write(EPISODES, all_eps)
    _merge_facts(ep["facts"], eid)
    if not pending:
        s = state()
        s["consolidated"] = max(s["consolidated"], chunk[-1]["id"])
        _write(STATE, s)
    print(f"[memory] remembered: {ep['title']} ({len(chunk)} messages, importance {ep['importance']:.1f})", flush=True)
    return True


# ---------------------------------------------------------------- for the memory panel

def overview() -> dict:
    now = now_ms()
    w = working()
    eps = []
    for e in episodes():
        eps.append({**{k: e.get(k) for k in ("id", "title", "gist", "start", "end", "importance", "feeling",
                                               "recalls", "sealed", "sealed_at", "raw", "pending")},
                    "strength": round(strength(e, now), 3)})
    return {
        "now": now, "mood": round(mood(now), 3), "mood_words": mood_words(mood(now)),
        "working": {"count": len([m for m in w if m["role"] != "event"]), "since": w[0]["ts"] if w else None},
        "facts": [{k: f.get(k) for k in ("id", "text", "ts", "sealed", "sealed_at")} for f in facts()],
        "episodes": eps,
        "total": len(log()),
    }
