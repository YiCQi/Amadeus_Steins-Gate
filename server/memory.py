"""Kurisu's memory: one timeline for calls and chats, kept the way a person keeps one.

    data/memory/log.jsonl      everything ever said, call and chat alike, with times: the archive
    data/memory/state.json     how far the log has been consolidated, and her mood
    data/memory/episodes.json  what she remembers of older conversations, in her own words
    data/memory/facts.json     what she knows about the user

- Working memory is the log since the last consolidation. The model reads its newest part as a
  transcript with times, so she knows what was said in the chat when you call, and the other
  way round; the rest of a long conversation she keeps as a running summary.
- A conversation becomes one episode once it is over (an hour without a word), consolidated
  by the model in the background: a dated gist in her words, how much it mattered, how she
  felt, and the facts it taught her about you. Episodes then merge as they age: a day's into
  that day, later into that week and that month, so they don't pile up. Unforgettable ones
  stay on their own.
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

SHOW_MAX = 60                # messages she reads word for word ...
SHOW_CHARS = 4500            # ... at most this many characters of them (the running summary keeps the rest)
SESSION_GAP = 3600_000       # ms: an hour's silence ends a conversation
ROLL_AFTER = 30              # unread messages of the conversation going on before its running summary is updated
MOOD_BASE, MOOD_TAU = 0.55, 8 * 3600_000   # her usual mood, and how fast she returns to it (ms)
# How each line's emotion moves her mood. Her tsundere banter (snapping back, sulking, getting
# flustered) is how she plays, not how she feels: weighed in full, a friendly evening of it sank
# her to "in a bad mood", which then made her grumpier still.
EMOTION_MOOD = {"laugh": 0.05, "happy": 0.04, "smile": 0.03, "smug": 0.02, "embarrassed": 0.02,
                "puzzled": -0.005, "annoyed": -0.012, "pout": -0.012, "disgusted": -0.03,
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


def retract(ch: str, turn: str) -> int:
    """Take back the message the user sent as `turn`, with her reply to it if one was recorded: in a
    hands-free call they went on talking before she said a word, so it wasn't the end of what they
    were saying, and it comes again with the rest. Returns how many entries were removed."""
    entries = log()
    at = next((i for i in range(len(entries) - 1, -1, -1) if turn and entries[i].get("turn") == turn
               and entries[i]["ch"] == ch and entries[i]["role"] == "user"), None)
    if at is None:
        return 0
    drop = {entries[at]["id"]}
    for m in entries[at + 1:]:
        if m["ch"] != ch:
            continue
        if m["role"] != "assistant":
            break              # her reply to it only: whatever was said after that stays
        drop.add(m["id"])
    _rewrite([m for m in entries if m["id"] not in drop])
    return len(drop)


def reply_in(ch: str, turn: str) -> dict | None:
    """Her recorded reply in the turn the page sent as `turn`, if there is one."""
    return next((m for m in reversed(log()) if turn and m.get("turn") == turn and m["ch"] == ch and m["role"] == "assistant"), None)


def revise(eid: int, content: str | None):
    """Rewrite one message of the log, or remove it (None): her reply cut short to what she said."""
    _rewrite([{**m, "content": content} if m["id"] == eid else m for m in log() if not (m["id"] == eid and content is None)])


def _rewrite(entries: list[dict]):
    """The log is only ever appended to, except for these corrections: written whole, then swapped in."""
    tmp = LOG.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in entries), encoding="utf-8")
    tmp.replace(LOG)


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


def unanswered(channel: str) -> int:
    """How many times in a row she has spoken up on her own here, with no word from the user since."""
    n = 0
    for m in reversed(log()):
        if m["role"] != "assistant":
            break          # the user said something, or a call began or ended
        n += m["ch"] == channel and bool(m.get("idle"))
    return n


def split(entries: list[dict], channel: str, native_max: int = 12, now: int | None = None) -> tuple[list[dict], list[dict]]:
    """(earlier, recent): the trailing run of this channel's messages goes to the model as real
    turns in this channel's own format; everything before it goes in as a transcript, with times.
    The run is this conversation's: an hour's silence ends one (SESSION_GAP), and whatever came
    before it, last night's good night say, is not the turn just before this one."""
    run, after = [], now or now_ms()
    for m in reversed(entries):
        if m["ch"] != channel or m["role"] == "event" or after - m["ts"] > SESSION_GAP:
            break
        run.append(m)
        after = m["ts"]
    run = run[::-1][-native_max:]
    while run and run[0]["role"] != "user":
        run.pop(0)
    return entries[:len(entries) - len(run)], run


def last_before(run: list[dict]) -> int | None:
    """When the last thing before this run of turns happened (a message, or a call), in ms: what
    a pause before the first of them, or before what is said now, is counted from."""
    entries = log()
    if not run:
        return entries[-1]["ts"] if entries else None
    i = next((k for k, m in enumerate(entries) if m["id"] == run[0]["id"]), 0)
    return entries[i - 1]["ts"] if i > 0 else None


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
            if (len(run) > 1 or run.isdigit()) and run not in _STOP:
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


_STICKER = re.compile(r"\s*(?:/ )?\[sticker: [^\]]*\]")


def words(m: dict, meaning) -> str:
    """What one message said, its stickers left out: they colour a moment, they aren't its topic."""
    return _STICKER.sub("", said(m, meaning)).strip(" /")


def event_text(m: dict) -> str:
    if m.get("kind") == "call_start":
        return "(the user started a video call)"
    if m.get("kind") == "call_end":
        who = "you hung up" if m.get("by") == "kurisu" else "the user hung up"
        return f"(the video call ended after {seconds_text(m.get('seconds'))}; {who})"
    return f"({m.get('kind', 'event')})"


def transcript(entries: list[dict], meaning) -> str:
    from . import persona
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
        if m.get("idle"):   # she spoke up on her own
            out.append(f"[{t}] (the user had been quiet for {persona.quiet_for(m['idle'])})")
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


def _as_note(ep: dict) -> dict:
    """An episode in the shape notes.py matches and scores."""
    return {"text": f"{ep.get('title', '')}。{ep.get('gist', '')}", "subject": ep.get("title", ""),
            "cues": ep.get("keywords") or []}


def recall(query: str, k: int = 3, now: int | None = None) -> list[tuple[dict, float]]:
    """The episodes this moment brings back, with how clearly (strength). Ranked like notes
    (notes.py: S = ALPHA*R + BETA*I + GAMMA*T, only past the relevance gate), where time is the
    time asked about if the message names one, else how fresh the memory still is."""
    from . import notes
    now = now or now_ms()
    pool = [ep for ep in episodes() if not (ep.get("sealed") or ep.get("raw"))]
    if not pool or not query.strip():
        return []
    if notes.enabled():
        R, _ = notes.relevance(query, [_as_note(ep) for ep in pool])
        window = notes.asked_time(query, now)
        picks = []
        for ep, r in zip(pool, R):
            if r < notes.R_MIN:
                continue
            st = strength(ep, now)   # a strong reminder brings back even a faded memory: R decides, not strength
            if window:
                gap = max(0, window[0] - ep["end"], ep["start"] - window[1]) / 86400_000
                t = math.exp(-gap / max(2.0, (window[1] - window[0]) / 86400_000 / 4))
            else:
                t = st
            picks.append((notes.ALPHA * r + notes.BETA * ep.get("importance", 0.5) + notes.GAMMA * t, ep, st))
        picks.sort(key=lambda p: -p[0])
        return [(ep, st) for _, ep, st in picks[:k]]
    q = tokens(query)       # the old way, by shared words (before notes)
    if not q:
        return []
    picks = []
    for ep in pool:
        sim = similar(q, _ep_tokens(ep))
        st = strength(ep, now)
        score = sim * (0.35 + 0.65 * st)
        if sim >= 0.3:           # a strong reminder brings back even a faded memory
            score = max(score, 0.8 * sim)
        if score >= 0.12:
            picks.append((score, ep, st))
    picks.sort(key=lambda p: -p[0])
    return [(ep, st) for _, ep, st in picks[:k]]


def brought_up(user_text: str, eps: list[dict]) -> list[dict]:
    """The memories the user's own words bring up (not ones that merely came to her mind)."""
    from . import notes
    if not user_text.strip() or not eps:
        return []
    if notes.enabled():
        R, _ = notes.relevance(user_text, [_as_note(ep) for ep in eps])
        return [ep for ep, r in zip(eps, R) if r >= 0.7]
    q = tokens(user_text)
    return [ep for ep in eps if similar(q, _ep_tokens(ep)) >= 0.3]


def remembered(eps: list[dict]):
    """Being brought up again strengthens a memory (the spacing effect): count it, unless it
    was already counted in the last half hour. Only for what the user brings up: a memory that
    merely came to her mind isn't strengthened by that."""
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
    """Seal an episode, a fact or note, or the current unconsolidated conversation ("recent").
    A forgotten note is a tombstone (notes.py); the memories and summary that may say it are
    marked to be rewritten without it (scrub, in the background), and until then the prompt
    leaves out whatever matches it."""
    from . import notes
    now = now_ms()
    if kind == "note":
        n = notes.forget(fid, now=now)
        if n:
            _mark_scrub(n)
        return bool(n)
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
            for n in notes.learned_only_from(fid):
                notes.forget(n["id"], now=now, why=f"forgotten with memory #{fid}")
    return hit


def restore(kind: str, fid) -> bool:
    from . import notes
    if kind == "note":
        n = notes.restore(fid)
        if n:
            _unscrub(fid)
        return bool(n)
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
            for n in notes.load():
                if n.get("status") == "forgotten" and n.get("why") == f"forgotten with memory #{fid}":
                    notes.restore(n["id"])
    return hit


# strict forgetting: older memories that may say what was forgotten are rewritten without it

SCRUB = """The user asked Makise Kurisu to forget the things listed below. Rewrite her memory without them: remove whatever states them or only makes sense with them, change nothing else, keep her voice and language. If the memory isn't about them, return it unchanged.
To forget:
{forget}
Output the memory as the same JSON object only: {{"title": "...", "gist": "...", "keywords": ["..."]}}"""

SCRUB_SUMMARY = """The user asked Makise Kurisu to forget the things listed below. Rewrite this summary without them: remove whatever states them, change nothing else. If it isn't about them, return it unchanged.
To forget:
{forget}
Output the summary only."""


def _mark_scrub(tomb: dict):
    """Mark the memories that may mention a forgotten note (the scrub step decides, with the model)."""
    from . import notes
    all_eps, changed = episodes(), False
    for ep in all_eps:
        if ep.get("raw"):
            continue
        sents = [s for s in re.split(r"(?<=[。！？!?.])\s*", f"{ep.get('title', '')}。{ep.get('gist', '')}") if s.strip()]
        _, sim = notes.closest(tomb["text"], [{"text": s} for s in sents])
        if tomb["id"] not in ep.get("scrub", []) and (sim >= 0.85 or ep["id"] in (tomb.get("source") or [])):
            ep.setdefault("scrub", []).append(tomb["id"])
            changed = True
    if changed:
        _write(EPISODES, all_eps)
    s = state()
    if s.get("rolling"):
        s["rolling"].setdefault("scrub", []).append(tomb["id"])
        _write(STATE, s)


def _unscrub(tid: int):
    """A forgotten note restored: memories rewritten because of it get back what they said
    before. Anything else forgotten that the older text still holds is cleaned out again."""
    def put_back(item: dict, fields: dict):
        others = set(item.get("scrubbed_for", [])) - {tid}
        item.update(fields)
        item["scrubbed_for"] = sorted(others)
        item["scrub"] = sorted(set(item.get("scrub", [])) | others)

    all_eps, changed = episodes(), False
    for ep in all_eps:
        if tid in ep.get("scrub", []):
            ep["scrub"].remove(tid)
            changed = True
        saved = (ep.get("unscrubbed") or {}).pop(str(tid), None)
        if saved:
            put_back(ep, saved)
            changed = True
    if changed:
        _write(EPISODES, all_eps)
    s = state()
    roll = s.get("rolling")
    if roll:
        if tid in roll.get("scrub", []):
            roll["scrub"].remove(tid)
        saved = (roll.get("unscrubbed") or {}).pop(str(tid), None)
        if saved:
            put_back(roll, saved)
        _write(STATE, s)


async def _scrub_step(summarize) -> bool:
    """One memory (or the running summary) still marked as maybe saying something forgotten:
    rewritten without it, the earlier text kept out of her reach for a restore."""
    from . import notes
    tombs = {n["id"]: n for n in notes.load() if n.get("status") == "forgotten"}
    all_eps = episodes()
    for ep in all_eps:
        todo = [t for t in ep.get("scrub", []) if t in tombs]
        if not todo:
            if ep.get("scrub"):          # restored meanwhile: nothing to do
                ep["scrub"] = []
                _write(EPISODES, all_eps)
            continue
        listing = "\n".join(f"- {tombs[t]['text']}" for t in todo)
        before = {k: ep.get(k) for k in ("title", "gist", "keywords")}
        d = _json(await summarize(SCRUB.format(forget=listing), json.dumps(before, ensure_ascii=False)))
        if not d or "gist" not in d:
            return False
        for t in todo:
            ep.setdefault("unscrubbed", {})[str(t)] = before
        ep.update(title=str(d.get("title") or before["title"])[:80], gist=str(d.get("gist") or "")[:700],
                  keywords=[str(k)[:30] for k in d.get("keywords") or [] if k][:8])
        ep["scrubbed_for"] = sorted(set(ep.get("scrubbed_for", [])) | set(todo))
        ep["scrub"] = [t for t in ep["scrub"] if t not in todo]
        _write(EPISODES, all_eps)
        print(f"[memory] memory #{ep['id']} rewritten without what you asked her to forget", flush=True)
        return True
    s = state()
    roll = s.get("rolling")
    todo = [t for t in (roll or {}).get("scrub", []) if t in tombs]
    if roll and todo:
        listing = "\n".join(f"- {tombs[t]['text']}" for t in todo)
        text = (await summarize(SCRUB_SUMMARY.format(forget=listing), roll["text"])).strip()
        for t in todo:
            roll.setdefault("unscrubbed", {})[str(t)] = {"text": roll["text"]}
        roll["text"] = text[:1200]
        roll["scrubbed_for"] = sorted(set(roll.get("scrubbed_for", [])) | set(todo))
        roll["scrub"] = [t for t in roll["scrub"] if t not in todo]
        _write(STATE, s)
        return True
    return False


def scrubbed(text: str) -> str:
    """Last line of defence for the prompt: sentences that match something forgotten are left out."""
    from . import notes
    tombs = [n for n in notes.load() if n.get("status") == "forgotten"]
    if not tombs or not text:
        return text
    keep = []
    for s in re.split(r"(?<=[。！？!?.])\s*", text):
        if s.strip():
            n, sim = notes.closest(s, tombs)
            if sim < notes.MAYBE_COS:
                keep.append(s)
    return "".join(keep)


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
    from . import notes
    now = now_ms()
    w = working()
    earlier, recent = split(w, channel, now=now)
    dt = datetime.now()
    use_notes = notes.enabled()
    out = ["", "", "# Your memory"]
    core = notes.core_text() if use_notes else ""
    if core:   # changes at most once a conversation, so it goes first: a provider's prompt cache can keep it
        out.append(core + "\n")
    out.append(f"It is now {dt:%A %d %B %Y, %H:%M} ({part_of_day(dt.hour)}) where the user lives.")
    said_before = [m for m in log() if m["role"] != "event"]
    if said_before:
        last = said_before[-1]
        out.append(f"You last talked {ago(last['ts'], now)} (by {'text' if last['ch'] == 'chat' else 'video call'}).")
    else:
        out.append("You have never talked with the user before: this is your first conversation.")
    v = mood(now)
    out.append(f"Your mood right now: {v:.2f} on a 0–1 scale ({mood_words(v)}). Let it colour how you react; don't announce it.")

    cue = " ".join([query] + [words(m, meaning) for m in w[-3:] if m["role"] != "event"]).strip()
    known = picked = []
    if use_notes:   # what else she knows that this moment calls for (the archive)
        picked = notes.recall(cue, now=now)
        if picked:
            out.append("\n" + notes.recall_text(picked))
        if query.strip():
            notes.reinforce(query, now)   # the user's own words only: being recalled reinforces nothing
    else:           # before notes: the old fact list
        known = known_facts()
        if known:
            out.append("\nWhat you know about the user:")
            out += [f"- {f['text']}" for f in known]

    picks = recall(cue)
    eps = [e for e in episodes() if not e.get("sealed") and not e.get("raw")]
    # if this conversation is new, the last one comes to mind by itself
    if eps and (not w or w[0]["ts"] - eps[-1]["end"] > SESSION_GAP) and all(p[0]["id"] != eps[-1]["id"] for p in picks):
        picks.append((eps[-1], strength(eps[-1], now)))
    if picks:
        out.append("\nWhat comes back to you:")
        for ep, st in picks:
            stamp = f"{ago(ep['end'], now)} ({when(ep['end'])})"
            gist = scrubbed(ep.get("gist", "")) if use_notes else ep.get("gist", "")
            out.append(f"- {stamp}: {ep['title']}. {gist}" if st >= 0.2 else
                       f"- {stamp}: you only vaguely remember — {ep['title']}.")
        remembered(brought_up(query, [ep for ep, _ in picks]))
    if use_notes and (picked or picks):
        print("[recall]", " | ".join([f"#{p['id']} {p['_S']:.2f} (R{p['_R']:.2f} I{p['_I']:.2f} T{p['_T']:.2f}) {p['text'][:16]}"
                                      for p in picked] + [f"memory #{ep['id']} {ep['title'][:16]}" for ep, _ in picks]), flush=True)
    if core or known or picked or picks:
        out.append("Bring memories up the way a person does: only when they fit, without reciting them; a vague one "
                   "stays vague (\"didn't you say something about...?\"). You don't remember anything that isn't here.")
    # the newest part of working memory; anything older is waiting to be consolidated
    shown, chars = [], 0
    for m in reversed(earlier):
        chars += len(m.get("content", ""))
        if len(shown) >= SHOW_MAX or chars > SHOW_CHARS:
            break
        shown.append(m)
    # a long conversation: what she no longer reads word for word, she remembers in outline
    roll = state().get("rolling")
    if roll and (not shown or roll["from"] < shown[-1]["id"]):
        text = scrubbed(roll["text"]) if use_notes else roll["text"]
        out.append(f"\n# Earlier in this conversation (you remember it in outline)\n{text}")
    if shown:
        out.append('\n# Recent messages, with their times (most recent last; "text" = typed in the chat, "call" = said on a video call)')
        out.append(transcript(shown[::-1], meaning))
    return "\n".join(out), recent


# ---------------------------------------------------------------- consolidation
#
# How often she makes a memory decides how quickly they pile up, so:
# - a conversation becomes one memory, only once it is over (an hour without a word);
# - while a long one goes on, the part she no longer reads word for word is kept as a running
#   summary of "earlier tonight", not as a memory;
# - memories then merge as they age, the way a person's do: one day's into a memory of that
#   day, a fortnight later into that week, after three months into that month. Only the
#   unforgettable ones (importance >= VIVID) stay on their own;
# - facts are compacted when there are too many.
# Each step is one request to the model, at most a few after a reply, in the background.

VIVID = 0.85
LEVELS = {"session": 0, "day": 1, "week": 2, "month": 3}
MERGE_AFTER = {"day": 1, "week": 14, "month": 90}     # days old before merging into that level
FACTS_MAX, FACTS_KEEP = 40, 25

CONSOLIDATE = """You are the long-term memory of Makise Kurisu (the AI built from her memory data). Below is one conversation between Kurisu ("You") and the user, with times. Write what Kurisu will keep of it, as one JSON object:
{{"title": "...", "gist": "...", "facts": ["..."], "importance": 0.5, "feeling": "neutral", "keywords": ["..."]}}
- title: a few words for what it was about, in {lang}.
- gist: 1–3 sentences in {lang}, in Kurisu's own voice (first person), what happened and how she felt about it. Keep names, numbers, plans and promises; drop small talk.
- facts: lasting things learned about the user (their life, likes, people, plans, promises made), one short sentence each in {lang}; [] if none. Nothing about Kurisu herself.
- importance: 0.1 small talk ... 0.5 a real conversation ... 0.8 something personal, a promise, a first ... 1.0 unforgettable.
- feeling: one of {emotions}.
- keywords: 3–8 words someone might use to bring this up again (names, places, topics).
Output the JSON only."""

CONSOLIDATE_NOTES = """You are the long-term memory of Makise Kurisu (the AI built from her memory data). Below is one conversation between Kurisu ("You") and the user, with times, and then what she already knows. Write what Kurisu keeps of it, as one JSON object:
{{"title": "...", "gist": "...", "importance": 0.5, "feeling": "neutral", "keywords": ["..."],
  "notes": {{"add": [], "update": [], "retire": [], "confirm": []}}}}
- title: a few words for what it was about, in {lang}.
- gist: 1–3 sentences in {lang}, in Kurisu's own voice (first person): what happened and how she felt about it. Keep names, numbers, plans and promises; drop small talk.
- importance: 0.1 small talk ... 0.5 a real conversation ... 0.8 something personal, a promise, a first ... 1.0 unforgettable.
- feeling: one of {emotions}.
- keywords: 3–8 words someone might use to bring this up again (names, places, topics).
- notes: what this conversation changes in what Kurisu knows. Often little or nothing: empty lists are fine.
- Anything listed under "Forgotten" stays out of the gist and keywords too.
"""

NOTES_ONLY = """You are the long-term memory of Makise Kurisu (the AI built from her memory data). Below is one conversation between Kurisu ("You") and the user, with times, and then what she already knows. Write what this conversation changes in what Kurisu knows, as one JSON object:
{{"notes": {{"add": [], "update": [], "retire": [], "confirm": []}}}}
Often little or nothing: empty lists are fine.
"""


def consolidate_prompt(lang: str, emotions: str, notes_only: bool = False) -> str:
    """The consolidation prompt for a profile that keeps notes (notes.py): the episode and the
    edits, or (for a conversation already sorted into a memory) the edits alone."""
    from . import notes
    head = NOTES_ONLY if notes_only else CONSOLIDATE_NOTES
    return (head + notes.NOTES_RULES + "\nOutput the JSON only.").format(lang=lang, emotions=emotions)


ROLLING = """You keep Makise Kurisu's running memory of the conversation she is in right now. Below are the summary so far (it may be empty) and what was said after it. Write the updated summary: 2–6 sentences in {lang}, in Kurisu's own voice (first person), what has been talked about, in order, keeping names, plans and anything that matters. Output the summary only."""

MERGE = """You are the long-term memory of Makise Kurisu. Below are several of her memories from {span}, oldest first. Merge them into ONE memory of that {unit}, as one JSON object:
{{"title": "...", "gist": "...", "importance": 0.5, "feeling": "neutral", "keywords": ["..."]}}
- title: a few words for that {unit}, in {lang}.
- gist: 2–4 sentences in {lang}, in Kurisu's own voice (first person): what mattered most, how things went with the user, anything promised or planned. Small talk goes.
- importance: as much as the most important of them.
- feeling: one of {emotions}. keywords: 3–8 words someone might use to bring it up again.
Output the JSON only."""

FACTS_COMPACT = """Below is what Makise Kurisu knows about the user, oldest first. Rewrite it as at most {n} facts: merge duplicates and near-duplicates, keep the newer one when two disagree, drop trivia, keep names, dates, plans and promises. One short sentence each, in {lang}. Output a JSON array of strings only."""


def sessions(entries: list[dict]) -> list[list[dict]]:
    """Split messages into conversations at every hour-long silence."""
    out = []
    for m in entries:
        if out and m["ts"] - out[-1][-1]["ts"] < SESSION_GAP:
            out[-1].append(m)
        else:
            out.append([m])
    return out


def _closed(text: str) -> str:
    """The text with whatever brackets (and string) it left open closed: models sometimes stop
    one brace short."""
    stack, in_str, esc = [], False, False
    for ch in text:
        if in_str:
            esc, in_str = (not esc and ch == "\\"), in_str and not (ch == '"' and not esc)
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]" and stack:
            stack.pop()
    text = re.sub(r",\s*$", "", text + ('"' if in_str else "")) + "".join(reversed(stack))
    return re.sub(r",\s*([}\]])", r"\1", text)   # and the stray comma before a closing bracket


def _json(raw: str, kind=dict):
    start = raw.find("{" if kind is dict else "[")
    if start < 0:
        return None
    m = re.search(r"\{.*\}" if kind is dict else r"\[.*\]", raw, re.S)
    for text in ([m.group()] if m else []) + [_closed(raw[start:])]:
        try:
            d = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(d, kind):
            return d
    return None


def _parse(raw: str) -> dict | None:
    from . import persona
    d = _json(raw)
    if not d or not d.get("title"):
        return None
    try:
        importance = max(0.0, min(1.0, float(d.get("importance", 0.5) or 0.5)))
    except (TypeError, ValueError):
        importance = 0.5
    return {"title": str(d["title"])[:80], "gist": str(d.get("gist", ""))[:700],
            "facts": [str(f)[:200] for f in d.get("facts", []) if f][:8], "importance": importance,
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


async def maintain(summarize, language: str, rounds: int = 3) -> int:
    """Look after her memory after a reply: a few steps at most, each one request to the model.
    summarize(system, text) -> the model's reply. Runs in the background."""
    if _lock.locked():
        return 0
    async with _lock:
        _start_notes()
        done = 0
        while done < rounds:
            try:
                if not await _step(summarize, language):
                    break
            except Exception as e:  # network, API: try again after the next reply
                print(f"[memory] {type(e).__name__}: {e}", flush=True)
                break
            done += 1
        return done


def _start_notes():
    """A profile that has never decided: a new one keeps notes from the start; an older one moves
    to notes after a full backup, and learns them from its past conversations, a few at a time.
    Switched off (tools/memory_notes.py off), it stays off."""
    from . import notes
    if "enabled" in notes.state():
        return
    if episodes() or facts():
        notes.backup("before-notes")
    notes.set_state(enabled=True, replayed_upto=0)


async def _step(summarize, language: str) -> bool:
    from . import persona
    lang = persona.LANGUAGES.get(language, persona.LANGUAGES["en"])["name"]
    emotions = ", ".join(persona.EMOTIONS)
    meaning = lambda sid: sid or ""  # noqa: E731
    now = now_ms()

    from . import notes
    use_notes = notes.enabled()

    async def consolidate(chunk: list[dict], text: str) -> tuple[dict | None, dict]:
        """One request: the episode, and (with notes) what the conversation changes in what she knows."""
        if not use_notes:
            return _parse(await summarize(CONSOLIDATE.format(lang=lang, emotions=emotions), text[-14000:])), {}
        raw = await summarize(consolidate_prompt(lang, emotions),
                              text[-14000:] + "\n\n" + notes.known_block(text, now=chunk[-1]["ts"]))
        return _parse(raw), (_json(raw) or {}).get("notes") or {}

    async def note(ops: dict, eid, chunk: list[dict]):
        if use_notes:
            did = await notes.apply(ops, source=eid, now=chunk[-1]["ts"], judge=summarize)
            notes.set_state(replayed_upto=max(notes.state().get("replayed_upto", 0), chunk[-1]["id"]))
            if did:
                print(f"[memory] what she knows: {'; '.join(did)[:300]}", flush=True)

    if use_notes:
        notes.snapshot()                       # once a day, a copy of her whole memory
        if await _scrub_step(summarize):       # 0. strict forgetting goes first
            return True
        behind = _not_yet_noted()             # 0b. conversations sorted before she kept notes
        if behind:
            text = transcript(behind, meaning)
            d = _json(await summarize(consolidate_prompt(lang, emotions, notes_only=True),
                                      text[-14000:] + "\n\n" + notes.known_block(text, now=behind[-1]["ts"])))
            if d is None:
                return False
            eid = next((e["id"] for e in episodes() if e.get("range") and e["range"][0] <= behind[0]["id"] <= e["range"][1]), None)
            await note(d.get("notes") or {}, eid, behind)
            return True

    # 1. a conversation that was forgotten and then restored: sort it into a memory now
    pending = next((e for e in episodes() if e.get("pending")), None)
    if pending:
        chunk = [m for m in log() if pending["range"][0] <= m["id"] <= pending["range"][1]]
        ep, ops = await consolidate(chunk, transcript(chunk, meaning))
        if not ep:
            return False
        await note(ops, _save_episode(ep, chunk, replace=pending["id"]), chunk)
        return True

    # 2. a conversation that is over becomes one memory (a very long one, in parts)
    convs = sessions(working())
    if not convs:
        return False
    done_convs = convs[:-1] if now - convs[-1][-1]["ts"] < SESSION_GAP else convs
    long_open = convs[-1] if len(convs[-1]) > 400 else None
    if done_convs or long_open:
        chunk = done_convs[0] if done_convs else long_open[:300]
        roll = state().get("rolling")
        text = transcript(chunk, meaning)
        if roll and chunk[0]["id"] <= roll["from"] <= chunk[-1]["id"]:
            rest = [m for m in chunk if m["id"] > roll["upto"]]
            text = f"(Summary of how it started: {roll['text']})\n\n" + transcript(rest, meaning)
        ep, ops = await consolidate(chunk, text)
        if not ep:
            if len(working()) < 600:
                return False                     # try again after the next reply
            ep, ops = _fallback(chunk), {}       # never let working memory grow without bound
        await note(ops, _save_episode(ep, chunk), chunk)
        s = state()
        s["consolidated"] = max(s["consolidated"], chunk[-1]["id"])
        if s.get("rolling") and s["rolling"]["upto"] <= chunk[-1]["id"]:
            s.pop("rolling")
        _write(STATE, s)
        return True

    # 3. the conversation going on now: summarise what she no longer reads word for word
    open_conv = convs[-1]
    unshown = open_conv[:max(0, len(open_conv) - SHOW_MAX)]
    roll = state().get("rolling") or {}
    fresh = [m for m in unshown if m["id"] > roll.get("upto", 0)]
    if len(fresh) >= ROLL_AFTER:
        text = f"Summary so far: {roll.get('text', '(none)')}\n\nSaid after it:\n{transcript(fresh, meaning)}"
        system = ROLLING.format(lang=lang)
        if use_notes:   # nothing she was asked to forget goes into it
            tombs = [n for n in notes.load() if n.get("status") == "forgotten"]
            if tombs:
                system += "\nLeave out anything about these (the user asked Kurisu to forget them):\n" + \
                          "\n".join(f"- {n['text']}" for n in tombs[-20:])
        summary = (await summarize(system, text[-12000:])).strip()
        if not summary:
            return False
        s = state()
        s["rolling"] = {"from": roll.get("from", open_conv[0]["id"]), "upto": fresh[-1]["id"], "text": summary[:1200]}
        _write(STATE, s)
        print(f"[memory] running summary of this conversation updated ({len(fresh)} more messages)", flush=True)
        return True

    # 4. memories merge as they age: a day, then a week, then a month
    group = _merge_due(now)
    if group:
        level, members, span = group
        listing = "\n".join(f"- {when(e['end'])}: {e['title']}. {e.get('gist', '')}" for e in members)
        unit = {"day": "day", "week": "week", "month": "month"}[level]
        ep = _parse(await summarize(MERGE.format(span=span, unit=unit, lang=lang, emotions=emotions), listing))
        if not ep:
            return False
        _merge_episodes(level, members, ep)
        return True

    # 5. now and then she looks over what she knows: moments out, outdated notes updated, the core kept lean
    if use_notes:
        if notes.review_due():
            did = await review_notes(summarize, lang)
            print(f"[memory] she looked over what she knows: {'; '.join(did)[:300] or 'nothing to change'}", flush=True)
            return True
        return False

    # 5'. before notes: too many facts, compact them
    live = [f for f in facts() if not f.get("sealed")]
    if len(live) > FACTS_MAX:
        listing = "\n".join(f"- {f['text']}" for f in sorted(live, key=lambda f: f.get("ts", 0)))
        new = _json(await summarize(FACTS_COMPACT.format(n=FACTS_KEEP, lang=lang), listing), list)
        if not new:
            return False
        sealed = [f for f in facts() if f.get("sealed")]
        nid = max((f["id"] for f in facts()), default=0)
        _write(FACTS, sealed + [{"id": nid + i + 1, "text": str(t)[:200], "ts": now, "importance": 0.6, "source": None}
                                for i, t in enumerate(new[:FACTS_KEEP]) if t])
        print(f"[memory] facts compacted: {len(live)} -> {min(len(new), FACTS_KEEP)}", flush=True)
        return True
    return False


def _save_episode(ep: dict, chunk: list[dict], replace: int | None = None) -> int:
    all_eps = [e for e in episodes() if e["id"] != replace]
    eid = replace if replace is not None else _next_ep_id()
    all_eps.append({"id": eid, "start": chunk[0]["ts"], "end": chunk[-1]["ts"], "range": [chunk[0]["id"], chunk[-1]["id"]],
                    "level": "session", "parts": 1, "recalls": 0, "last_recalled": None, "created": now_ms(),
                    **{k: v for k, v in ep.items() if k != "facts"}})
    all_eps.sort(key=lambda e: e["end"])
    _write(EPISODES, all_eps)
    _merge_facts(ep["facts"], eid)   # the old fact list (empty once she keeps notes)
    print(f"[memory] remembered: {ep['title']} ({len(chunk)} messages, importance {ep['importance']:.1f})", flush=True)
    return eid


async def review_notes(summarize, lang: str) -> list[str]:
    """She looks over what she knows (notes.review); the one-off moments among her notes move
    to the memory of the conversation they came from."""
    from . import notes
    did, moves = await notes.review(summarize, lang)
    for n in moves:
        eid = await _keep_in_episode(n, summarize)
        notes.moved(n["id"], eid, n["move_why"])
        did.append(f"move #{n['id']} to memory #{eid}" if eid else f"move #{n['id']} (a moment)")
    return did


async def _keep_in_episode(n: dict, judge=None) -> int | None:
    """Make sure the memory of the conversation a note came from tells that moment; its id.
    Whether it already does, in other words, is the model's call in the grey zone."""
    from . import notes
    all_eps = episodes()
    live = {e["id"]: e for e in all_eps if not (e.get("sealed") or e.get("raw"))}
    src = [s for s in n.get("source") or [] if s in live]
    if not src:
        return None
    ep = live[src[-1]]
    sents = [{"text": s} for s in re.split(r"(?<=[。！？!?.])\s*", ep.get("gist", "")) if s.strip()]
    if not sents or (await notes.same_facts([n["text"]], sents, judge))[0] is None:   # not told there yet
        ep["gist"] = f"{ep.get('gist', '').rstrip()} {n['text'].rstrip('。')}。".strip()
        _write(EPISODES, all_eps)
    return ep["id"]


def _not_yet_noted() -> list[dict]:
    """The first conversation sorted into a memory before she kept notes (or while they were
    switched off), and not looked at for notes since. What was forgotten stays out of it."""
    from . import notes
    upto, done = notes.state().get("replayed_upto", 0), state()["consolidated"]
    if upto >= done:
        return []
    sealed = [e["range"] for e in episodes() if e.get("sealed") and e.get("range")]
    left = [m for m in log() if upto < m["id"] <= done and not any(a <= m["id"] <= b for a, b in sealed)]
    convs = sessions(left)
    if not convs:
        notes.set_state(replayed_upto=done)
        return []
    return convs[0][:300]


def _merge_due(now: int):
    """(level, members, span) for the coarsest group of old memories ready to merge, or None."""
    from datetime import date
    today = date.today()
    eps = [e for e in episodes() if not (e.get("sealed") or e.get("raw") or e.get("pending"))]
    day_of = lambda e: datetime.fromtimestamp(e["end"] / 1000).date()  # noqa: E731
    keys = {"month": lambda d: (d.year, d.month), "week": lambda d: tuple(d.isocalendar()[:2]), "day": lambda d: d}
    for level in ("month", "week", "day"):
        groups: dict = {}
        for e in eps:
            if e.get("importance", 0.5) >= VIVID or LEVELS.get(e.get("level", "session"), 0) >= LEVELS[level]:
                continue
            d = day_of(e)
            if (today - d).days < MERGE_AFTER[level]:
                continue
            groups.setdefault(keys[level](d), []).append(e)
        for k, members in sorted(groups.items()):
            if len(members) >= 2:
                first, last = day_of(members[0]), day_of(members[-1])
                span = f"{first:%d %B %Y}" if first == last else f"{first:%d %B} to {last:%d %B %Y}"
                return level, sorted(members, key=lambda e: e["end"]), span
    return None


def _merge_episodes(level: str, members: list[dict], ep: dict):
    ids = {e["id"] for e in members}
    eid = _next_ep_id()
    merged = {"id": eid, "start": min(e["start"] for e in members), "end": max(e["end"] for e in members),
              "range": [min(e["range"][0] for e in members), max(e["range"][1] for e in members)],
              "level": level, "parts": sum(e.get("parts", 1) for e in members),
              "recalls": sum(e.get("recalls", 0) for e in members),
              "last_recalled": max((e.get("last_recalled") or 0) for e in members) or None, "created": now_ms(),
              **{k: v for k, v in ep.items() if k != "facts"},
              "importance": max([ep["importance"]] + [e.get("importance", 0.5) for e in members])}
    all_eps = [e for e in episodes() if e["id"] not in ids] + [merged]
    all_eps.sort(key=lambda e: e["end"])
    _write(EPISODES, all_eps)
    fs = facts()
    for f in fs:
        if f.get("source") in ids:
            f["source"] = eid
    _write(FACTS, fs)
    from . import notes
    notes.remap_sources(ids, eid)
    print(f"[memory] {len(members)} memories merged into one of that {level}: {ep['title']}", flush=True)


# ---------------------------------------------------------------- for the memory panel

def overview() -> dict:
    now = now_ms()
    w = working()
    eps = []
    for e in episodes():
        eps.append({**{k: e.get(k) for k in ("id", "title", "gist", "start", "end", "importance", "feeling", "level",
                                               "parts", "recalls", "sealed", "sealed_at", "raw", "pending")},
                    "strength": round(strength(e, now), 3)})
    from . import notes
    return {
        "now": now, "mood": round(mood(now), 3), "mood_words": mood_words(mood(now)),
        "working": {"count": len([m for m in w if m["role"] != "event"]), "since": w[0]["ts"] if w else None},
        "notes": notes.overview() if notes.enabled() else None,
        "facts": [] if notes.enabled() else [{k: f.get(k) for k in ("id", "text", "ts", "sealed", "sealed_at")} for f in facts()],
        "episodes": eps,
        "total": len(log()),
    }
