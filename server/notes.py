"""What Kurisu knows, as opposed to what she remembers happening (memory.py's episodes).

Two layers:
- the core, always on her mind: a short profile in four sections (who you are; the two of you;
  what you've told her about herself and her world; plans and what's going on), at most
  CORE_CHARS; when it's full, its least important lines move to the archive;
- the archive: any number of notes, recalled only when the moment calls for them.

Every note keeps its earlier versions, and every change is logged (changes.jsonl) with who
made it, so any change can be undone. A note is active, or:
- expired: a dated plan or temporary state has passed (the system);
- retired: the model judged it no longer true, and why;
- forgotten: only the user. It becomes a tombstone: never written down again, from any source,
  until restored (blocked() is checked before anything is added or rewritten, and memory.py
  uses it to scrub what older memories say).

Recall ranks the archive by S = ALPHA*R + BETA*I + GAMMA*T, among notes relevant enough to be
candidates at all (R >= R_MIN):
- R, relevance: meaning (embed.py, E5 cosines rescaled) and shared words (TF-IDF, so a rare
  character like 爸 counts for more than a common one);
- I, importance: set when written; raised only when the user brings the note up again or a
  later conversation supports it, never by being recalled;
- T, time: if the message names a time ("最近", "上个月", "yesterday"), how close the note's
  event is to it; otherwise how recent. Timeless notes are neutral on time.
When something happened and when she learned it are kept apart (happened / recorded).
"""
import hashlib
import json
import math
import re
import shutil
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

from . import config, embed

DIR = config.DATA_DIR / "memory"
SECTIONS = {"you": "About them", "us": "The two of you",
            "truths": "What they've told you about yourself and your world", "plans": "Plans and what's going on"}
CORE_CHARS = 1000
RECALL_K = 6
ALPHA, BETA, GAMMA = 0.55, 0.2, 0.25
# Calibrated on real conversations (5 Nov 2026: 24 notes, 19 test questions): 90% of unrelated
# question-note pairs stay below COS_LO, a typical related pair reaches COS_HI. R_MIN: 0.35 finds
# 14 of 15 answers against 12 at 0.42, for 2 more stray notes over 19 questions; a stray note
# costs a line in the prompt, a missed one a memory she fails to bring up. Recheck as the archive
# grows: tools/memory_notes.py check --calibrate (results logged to recall_checks.jsonl).
COS_LO, COS_HI = 0.863, 0.896
R_MIN = 0.35                       # below this a note isn't a candidate at all
COS_STRONG = 0.90                  # the user's words this close to what a note says: they brought it up again (calibrated)
# Whether two statements are the same fact: E5 alone can't tell a restatement (0.90-0.99) from a
# different fact about the same thing (up to 0.93). So: certain above SURE_COS; between MAYBE_COS
# and SURE_COS the model is asked to compare the actual facts; below, different.
SURE_COS, MAYBE_COS = 0.95, 0.90
GENERIC = set("喜欢 我们 你们 他们 时间 说话 聊天 东西 事情 今天 明天 感觉 心情 自己 一起 知道 觉得 "
              "like love time talk chat thing feel today".split())
HALF_LIFE_DAYS = 45
DAY = 86400_000


def now_ms() -> int:
    return int(time.time() * 1000)


# ---------------------------------------------------------------- storage

def _path(name: str) -> Path:
    return DIR / name


def _read(name: str, default):
    try:
        return json.loads(_path(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write(name: str, data):
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(_path(name))


def load() -> list[dict]:
    return _read("notes.json", [])


def save(notes: list[dict]):
    _write("notes.json", notes)


def state() -> dict:
    return _read("notes_state.json", {})


def set_state(**kw):
    _write("notes_state.json", {**state(), **kw})


def enabled() -> bool:
    """Whether this profile keeps notes (else the old fact list, facts.json, is used). Switched
    on by the migration (tools/memory_notes.py); switching it off is the rollback."""
    return state().get("enabled") is True


FIELDS = ("text", "section", "tier", "subject", "importance", "cues", "happened", "valid_until", "status", "why")


def _snap(n: dict) -> dict:
    return {k: n.get(k) for k in ("v", *FIELDS, "recorded", "evidence")}


def _log(op: str, n: dict, by: str, why: str = "", before: dict | None = None, source=None, ts: int | None = None):
    seq = state().get("seq", 0) + 1
    set_state(seq=seq)
    entry = {"n": seq, "ts": ts or now_ms(), "op": op, "id": n["id"], "by": by, "why": why,
             "before": before, "after": _snap(n), "source": source}
    with _path("changes.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def changes(n: int = 40) -> list[dict]:
    try:
        lines = _path("changes.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [json.loads(l) for l in lines[-n:]][::-1]


def backup(label: str) -> Path:
    """A full copy of her memory folder, beside it (data/memory-backups/<label>-<time>/)."""
    dest = DIR.parent / "memory-backups" / f"{label}-{datetime.now():%Y%m%d-%H%M%S}"
    shutil.copytree(DIR, dest, ignore=shutil.ignore_patterns("backups", "*.tmp"))
    print(f"[memory] full backup: {dest}", flush=True)
    return dest


def snapshot(keep_days: int = 14):
    """Once a day, a copy of her whole memory (backups/YYYY-MM-DD/): a last resort."""
    day = DIR / "backups" / date.today().isoformat()
    if day.exists():
        return
    day.mkdir(parents=True)
    for name in ("notes.json", "episodes.json", "state.json", "facts.json", "changes.jsonl"):
        if _path(name).exists():
            shutil.copy2(_path(name), day / name)
    for old in sorted((DIR / "backups").iterdir())[:-keep_days]:
        shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------- matching

_TERM = re.compile(r"[a-z0-9]+|[぀-ヿ㐀-鿿]+")
_STOP = set("the a an and or but to of in on at for with is are was were be been it its this that you your i me "
            "my we he she they them what do did does not no so just like have has had can will would about".split())


def terms(text: str) -> list[str]:
    """Words for Latin text; single characters and character pairs for Chinese and Japanese."""
    out = []
    for run in _TERM.findall(text.lower()):
        if run.isascii():
            if (len(run) > 1 or run.isdigit()) and run not in _STOP:
                out.append(run)
        else:
            out += list(run) + [run[i:i + 2] for i in range(len(run) - 1)]
    return out


def matter(n: dict) -> str:
    """What a note is about, for matching: its text, subject and cues."""
    return " ".join([n.get("text", ""), n.get("subject", ""), " ".join(n.get("cues") or [])])


def lexical(query: str, docs: list[str]) -> np.ndarray:
    """TF-IDF cosine of the query against each doc (IDF over the docs themselves)."""
    if not docs:
        return np.zeros(0)
    doc_terms = [terms(d) for d in docs]
    df: dict[str, int] = {}
    for ts in doc_terms:
        for t in set(ts):
            df[t] = df.get(t, 0) + 1
    idf = lambda t: math.log((len(docs) + 1) / (df.get(t, 0) + 0.5))  # noqa: E731

    def vec(ts):
        v: dict[str, float] = {}
        for t in ts:
            v[t] = v.get(t, 0) + 1
        return {t: c * idf(t) for t, c in v.items()}

    q = vec(terms(query))
    qn = math.sqrt(sum(x * x for x in q.values())) or 1
    out = np.zeros(len(docs))
    for i, ts in enumerate(doc_terms):
        d = vec(ts)
        dn = math.sqrt(sum(x * x for x in d.values())) or 1
        out[i] = sum(w * d.get(t, 0) for t, w in q.items()) / (qn * dn)
    return out


_vec_cache: dict = {"vectors": None}


def vectors(texts: list[str], kind: str = "passage") -> np.ndarray | None:
    """E5 vectors for texts, cached on disk by content (vectors.npz). None without the model."""
    if not embed.available():
        return None
    cache = _vec_cache["vectors"]
    if cache is None:
        try:
            with np.load(_path("vectors.npz")) as z:
                cache = {k: z[k] for k in z.files}
        except (OSError, ValueError):
            cache = {}
        _vec_cache["vectors"] = cache
    keys = [f"{kind[0]}{hashlib.sha1(t.encode('utf-8')).hexdigest()[:20]}" for t in texts]
    missing = [(k, t) for k, t in dict(zip(keys, texts)).items() if k not in cache]
    if missing:
        for (k, _), v in zip(missing, embed.encode([t for _, t in missing], kind)):
            cache[k] = v
        DIR.mkdir(parents=True, exist_ok=True)
        np.savez(_path("vectors.npz"), **cache)
    return np.stack([cache[k] for k in keys]) if keys else np.zeros((0, embed.DIM), np.float32)


def views(n: dict) -> list[str]:
    """What a note is matched by: what it says, and each cue on its own, so a question in
    another language meets the cue in that language."""
    return [f"{n.get('subject', '')}：{n.get('text', '')}"] + [c for c in (n.get("cues") or []) if c][:8]


def cue_hit(query: str, n: dict) -> float:
    """1 when the message names the note's subject or one of its specific cues outright."""
    q = query.lower()
    for c in [n.get("subject", "")] + list(n.get("cues") or []):
        c = (c or "").lower().strip()
        if c in GENERIC or not ((len(c) >= 2 and not c.isascii()) or len(c) >= 3):
            continue
        if c in q:
            return 1.0
    return 0.0


def relevance(query: str, items: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """(R, best raw cosine) of each note against the query: R = 0.7 meaning + 0.3 words.
    Without the model, words only."""
    if not items:
        return np.zeros(0), np.zeros(0)
    lex = np.maximum(lexical(query, [matter(n) for n in items]), [cue_hit(query, n) for n in items])
    vs = [views(n) for n in items]
    p = vectors([v for group in vs for v in group], "passage")
    if p is None:
        return lex, np.zeros(len(items))
    sims = p @ embed.encode([query], "query")[0]
    cos, i = np.zeros(len(items)), 0
    for k, group in enumerate(vs):
        cos[k] = sims[i:i + len(group)].max()
        i += len(group)
    sem = np.clip((cos - COS_LO) / (COS_HI - COS_LO), 0, 1)
    return 0.7 * sem + 0.3 * lex, cos


SAME_FACTS = """For each numbered pair: does A state the same fact as B, one a restatement of the other? Statements about the same person or thing that say different things are NOT the same fact.
{pairs}
Answer with one line per pair, like "1 yes" or "2 no", nothing else."""


def closest(text: str, pool: list[dict]) -> tuple[dict | None, float]:
    """The note in pool closest in meaning to text, and how close (query: on both sides, E5's
    recipe for comparing like with like). Without the model, by shared words."""
    if not pool:
        return None, 0.0
    if not embed.available():
        lex = lexical(text, [n["text"] for n in pool])
        i = int(np.argmax(lex))
        return pool[i], (SURE_COS if lex[i] >= 0.8 else 0.0)
    sims = vectors([n["text"] for n in pool], "query") @ embed.encode([text], "query")[0]
    i = int(np.argmax(sims))
    return pool[i], float(sims[i])


async def verdicts(pairs: list[tuple[str, str]], judge=None) -> list[bool]:
    """Whether each (a, b) states the same fact: one question to the model for all of them.
    Without a judge they count as different."""
    if not pairs or judge is None:
        return [False] * len(pairs)
    listing = "\n".join(f"{k}. A: {a}\n   B: {b}" for k, (a, b) in enumerate(pairs, 1))
    answer = await judge("You compare statements carefully.", SAME_FACTS.format(pairs=listing))
    out = [False] * len(pairs)
    for m in re.finditer(r"(\d+)\s*[.:：、)]?\s*(yes|no|是|否)", answer, re.I):
        if 0 < int(m[1]) <= len(pairs):
            out[int(m[1]) - 1] = m[2].lower() in ("yes", "是")
    return out


async def same_facts(texts: list[str], pool: list[dict], judge=None) -> list[dict | None]:
    """For each text, the note in pool that states the same fact, or None: certain matches on
    the vectors, the grey zone asked of the model in one batch."""
    out, grey = [], []
    for k, text in enumerate(texts):
        n, sim = closest(text, pool)
        out.append(n if sim >= SURE_COS else None)
        if MAYBE_COS <= sim < SURE_COS:
            grey.append((k, text, n))
    for (k, _, n), yes in zip(grey, await verdicts([(t, n["text"]) for _, t, n in grey], judge)):
        if yes:
            out[k] = n
    return out


async def blocked(text: str, forgotten: list[dict] | None = None, judge=None) -> dict | None:
    """The forgotten note this text would bring back, if any."""
    tomb = forgotten if forgotten is not None else [n for n in load() if n.get("status") == "forgotten"]
    return (await same_facts([text], tomb, judge))[0]


# ---------------------------------------------------------------- time

_WHEN = re.compile(r"(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?")


def span(when: str) -> tuple[int, int] | None:
    """"2026-11-05" / "2026-11" / "2026" -> (start, end) in ms, local time."""
    m = _WHEN.fullmatch((when or "").strip())
    if not m:
        return None
    y, mo, d = int(m[1]), int(m[2] or 0), int(m[3] or 0)
    try:
        if d:
            a = datetime(y, mo, d)
            b = a + timedelta(days=1)
        elif mo:
            a = datetime(y, mo, 1)
            b = datetime(y + (mo == 12), mo % 12 + 1, 1)
        else:
            a, b = datetime(y, 1, 1), datetime(y + 1, 1, 1)
    except ValueError:
        return None
    return int(a.timestamp() * 1000), int(b.timestamp() * 1000)


def _day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def asked_time(text: str, now: int | None = None) -> tuple[int, int] | None:
    """The time a message asks about, if it names one: (start, end) in ms."""
    t = datetime.fromtimestamp((now or now_ms()) / 1000)
    today = _day(t)
    ms = lambda a, b: (int(a.timestamp() * 1000), int(b.timestamp() * 1000))  # noqa: E731
    week = today - timedelta(days=today.weekday())
    month = today.replace(day=1)
    last_month = (month - timedelta(days=1)).replace(day=1)
    rules = [
        (r"前天|おととい|day before yesterday", ms(today - timedelta(days=2), today - timedelta(days=1))),
        (r"昨天|昨晚|昨日|きのう|yesterday|last night", ms(today - timedelta(days=1), today)),
        (r"今天|今晚|今日|きょう|today|tonight", ms(today, today + timedelta(days=1))),
        (r"上周|上星期|上个星期|先週|last week", ms(week - timedelta(days=7), week)),
        (r"这周|这星期|本周|今週|this week", ms(week, week + timedelta(days=7))),
        (r"上个月|上月|先月|last month", ms(last_month, month)),
        (r"这个月|本月|今月|this month", ms(month, today + timedelta(days=1))),
        (r"去年|昨年|last year", ms(datetime(t.year - 1, 1, 1), datetime(t.year, 1, 1))),
        (r"今年|this year", ms(datetime(t.year, 1, 1), today + timedelta(days=1))),
        (r"最近|这几天|这阵子|这段时间|近来|最近の|recently|lately|these days", ms(today - timedelta(days=7), today + timedelta(days=1))),
    ]
    m = re.search(r"(\d+)\s*(?:天前|日前|days? ago)", text, re.I)
    if m:
        d = today - timedelta(days=int(m[1]))
        return ms(d, d + timedelta(days=1))
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]", text)
    if m:
        try:
            d = today.replace(month=int(m[1]), day=int(m[2]))
            if d > today + timedelta(days=60):
                d = d.replace(year=d.year - 1)
            return ms(d, d + timedelta(days=1))
        except ValueError:
            pass
    for pat, window in rules:
        if re.search(pat, text, re.I):
            return window
    return None


def time_fit(n: dict, window: tuple[int, int] | None, now: int) -> float:
    """T: how well the note's time fits the question. Timeless notes: 0.5 either way."""
    ev = span(n.get("happened", ""))
    if ev is None:
        if n.get("section") != "plans":
            return 0.5
        ev = (n.get("recorded") or now, (n.get("recorded") or now) + 1)
    if window:   # outside the time asked about, it fades within a few days (a week for a month)
        gap = max(0, window[0] - ev[1], ev[0] - window[1]) / DAY
        return math.exp(-gap / max(2.0, (window[1] - window[0]) / DAY / 4))
    age = max(0, now - ev[1]) / DAY
    return math.exp(-age * math.log(2) / HALF_LIFE_DAYS)


def weight(n: dict) -> float:
    """I: importance, plus a little for each time the user brought it up again."""
    return min(1.0, float(n.get("importance", 0.5)) + 0.05 * n.get("evidence", 0))


# ---------------------------------------------------------------- recall

def recall(query: str, k: int = RECALL_K, now: int | None = None, pool: list[dict] | None = None) -> list[dict]:
    """The archive notes this moment brings back, best first, each with its scores."""
    now = now or now_ms()
    pool = pool if pool is not None else [n for n in load() if n.get("status") == "active" and n.get("tier") != "core"]
    if not pool or not query.strip():
        return []
    R, cos = relevance(query, pool)
    window = asked_time(query, now)
    picks = []
    for n, r, c in zip(pool, R, cos):
        if r < R_MIN:
            continue
        i, t = weight(n), time_fit(n, window, now)
        picks.append({**n, "_R": round(float(r), 3), "_cos": round(float(c), 3), "_I": round(i, 3), "_T": round(t, 3),
                      "_S": round(float(ALPHA * r + BETA * i + GAMMA * t), 3)})
    picks.sort(key=lambda p: -p["_S"])
    return picks[:k]


def reinforce(user_text: str, now: int | None = None) -> list[int]:
    """The user brought these notes up again themselves: count it (at most once per half hour
    each). It takes what the note actually says, not just its subject: "my supervisor's bike"
    is about the bike note, not every note about the supervisor."""
    now = now or now_ms()
    notes = load()
    live = [n for n in notes if n.get("status") == "active"]
    if not live or not user_text.strip() or not embed.available():
        return []
    text_cos = vectors([views(n)[0] for n in live], "passage") @ embed.encode([user_text], "query")[0]
    best = float(text_cos.max())
    hit = []
    for n, c in zip(live, text_cos):
        if c >= COS_STRONG and c >= best - 0.01 and now - (n.get("last_evidence") or 0) > 1800_000:
            n["evidence"] = n.get("evidence", 0) + 1
            n["last_evidence"] = now
            hit.append(n["id"])
    if hit:
        save(notes)
    return hit


# ---------------------------------------------------------------- what the model reads

def when_text(when: str) -> str:
    """"2026-11-05" -> "5 Nov 2026", "2026-11" -> "Nov 2026", "2026" -> "2026"."""
    m = _WHEN.fullmatch((when or "").strip())
    if not m or not span(when):
        return when or ""
    d = datetime(int(m[1]), int(m[2] or 1), int(m[3] or 1))
    return f"{d.day} {d:%b %Y}" if m[3] else f"{d:%b %Y}" if m[2] else m[1]


def stamp(n: dict) -> str:
    """When it happened and when she learned it, if they differ: "[Oct 2026 · noted 4 Nov 2026]"."""
    d = datetime.fromtimestamp((n.get("recorded") or now_ms()) / 1000)
    noted = f"{d.day} {d:%b %Y}"
    shown = when_text(n.get("happened", ""))
    if not shown:
        return f"[noted {noted}]"
    return f"[{shown}]" if shown == noted else f"[{shown} · noted {noted}]"


def line(n: dict) -> str:
    """A core note as she reads it: dated only when it's an event or a plan."""
    when = when_text(n.get("happened", ""))
    return f"- {n['text']}" + (f" [{when}]" if when else "")


def core_text(notes: list[dict] | None = None) -> str:
    notes = notes if notes is not None else load()
    core = [n for n in notes if n.get("status") == "active" and n.get("tier") == "core"]
    if not core:
        return ""
    out = ["What you know about them (always on your mind):"]
    for sec, title in SECTIONS.items():
        lines = [n for n in core if n.get("section") == sec]
        if lines:
            out.append(f"{title}:")
            out += [line(n) for n in lines]
    return "\n".join(out)


def recall_text(picks: list[dict]) -> str:
    if not picks:
        return ""
    return "\n".join(["Other things you know that come to mind now:"] + [f"- {stamp(n)} {n['text']}" for n in picks])


# ---------------------------------------------------------------- writing it down

NOTES_RULES = """Notes are what Kurisu knows lastingly, as short facts. What she knows is not the same as what she thinks of all the time: a one-off moment (a particular late-night call, something tender or funny said once, how a conversation felt) belongs in the gist, where it comes back when the moment calls for it; it is not a note. It becomes a note only when it marks a lasting change, and then the note states the change ("冈伦辞掉了打工，现在专心备考"), not the scene.
Each note is ONE fact, written plainly in {lang}: at most about 40 characters (25 words in English), the subject first, no story and no feelings narrated, keeping names, numbers, places and dates. Call the user by name (or he/she) and Kurisu herself "I" (我). Good: "冈伦在大阪读大学，一个人住" · "室友（男性）健太在便利店打工" · "两人约好周六一起看电影" · "冈伦养了一只叫小白的猫". Not a note: "冈伦考试没考好，晚上打来视频，我陪他聊到很晚……". Two facts are two notes.
Sections: "you" = who the user is (their life, work or studies, the people in it, likes, habits, how they've been lately); "us" = the two of you (how you talk, nicknames, what you mean to each other, shared secrets, promises between you); "truths" = what the user has told you about yourself and your world (for example that you are Amadeus, what happened to the original Kurisu); "plans" = plans, appointments, promises still to keep and questions still open.
Each note: {{"text", "section", "tier", "subject", "importance", "cues", "happened", "valid_until"}}
- tier: "core" only for what should shape every conversation: stable facts about them, how things stand between you, the essential truths, current plans. Everything else "archive": it comes back when it's relevant.
- subject: one or two words for who or what it's about (室友, 导师, 考试, 父亲...).
- importance: 0.1 trivia ... 0.5 useful ... 0.8 important ... 1.0 unforgettable.
- cues: 3-8 specific words someone might use when it comes up (names, places, objects, distinctive terms), in Chinese, English and Japanese: 导师, supervisor, 先生. Not generic words like 喜欢, 我们, 时间, 说话.
- happened: only for an event or a plan: the day (or month, or year) it actually takes or took place, YYYY-MM-DD, YYYY-MM or YYYY, worked out from the conversation's dates ("明天" said on 4 Nov 2026 is 2026-11-05). "" for lasting facts and states (where someone lives, who someone is, what they like). Never just the date of this conversation: "冈伦在大阪读大学" is "", "两人第一次视频通话" happened 2026-11-04, "明天的考试" (said on 4 Nov 2026) is 2026-11-05.
- valid_until: for plans and temporary states, the last day it matters (YYYY-MM-DD); else "".
Operations, against "What you already know" (by id):
- "add": new notes. If it's already known, confirm or update instead; don't add it twice.
- "update": [{{"id", "text", "why", ...any other fields that change}}], "text" being the whole note as it should read now: only when this conversation changes or corrects that specific fact. Compare the actual facts: two notes on the same subject that don't contradict each other are both true; add instead. Keep every detail of the old note that is still true.
- "retire": [{{"id", "why"}}]: only when it's clearly no longer true and nothing replaces it (a plan that was called off). A plan that happened: update it to how it went.
- "confirm": [ids]: notes the user brought up again, or that this conversation shows matter more.
Check what you already know against this conversation: a question it answers, a promise kept, a plan done, a detail it explains: update that note to how things stand now, or retire it.
Never write down anything listed under "Forgotten", nor anything that says the same thing: the user asked you to forget it."""

REVIEW = """Below is everything Makise Kurisu knows (id · tier · section · subject · when · text). Tidy it the way she would, looking over her own notes. Most notes stay exactly as they are.
- A one-off moment (a particular call, something said once, how a conversation felt) is not knowledge: "move" it to her dated memories, where it comes back when the moment calls for it. If it marks a lasting change, update it to state the change instead ("冈伦辞掉了打工，现在专心备考").
- A note made outdated by another (a question since answered, a promise kept, a plan done, a detail later explained): update it to how things stand now, or retire it naming the note that replaces it ("superseded by #9").
- Two notes stating the same fact: update one to hold both, retire the other naming the one that holds both.
- The core is for what should shape every conversation: stable facts about them, how things stand between you, the essential truths, current plans. Anything else: update its tier to "archive" (it comes back when relevant).
- Never invent anything. Keep each note one short fact in {lang}.
Output one JSON object only: {{"update": [{{"id", "text", "why", ...any other fields that change}}], "retire": [{{"id", "why"}}], "move": [{{"id", "why"}}]}}, "text" being the whole note as it should read now (for a move to the archive, its text unchanged and "tier": "archive")."""

REVIEW_EVERY = 10   # added or rewritten notes between two reviews


def review_due() -> bool:
    since = state().get("reviewed_seq", 0)
    return sum(1 for c in changes(500) if c["n"] > since and c["op"] in ("add", "update") and c["by"] == "model") >= REVIEW_EVERY


def review_text() -> str:
    live = [n for n in load() if n.get("status") == "active"]
    return "\n".join(f"#{n['id']} · {n.get('tier')} · {n.get('section')} · {n.get('subject', '')} · "
                     f"{n.get('happened') or '-'} · {n['text']}" for n in live)


async def review(summarize, lang: str, now: int | None = None) -> tuple[list[str], list[dict]]:
    """Look over everything she knows once in a while: outdated notes updated or superseded,
    duplicates merged, the core kept to what matters. One request; every edit logged. Returns
    what was done, and the one-off moments to move to their dated memories (memory.py does that)."""
    listing = review_text()
    if not listing:
        return [], []
    from .memory import _json
    ops = _json(await summarize(REVIEW.format(lang=lang), listing)) or {}
    did = await apply({"update": ops.get("update") or [], "retire": ops.get("retire") or []}, now=now, judge=summarize, by="review")
    by_id = {n["id"]: n for n in load()}
    moves = [{**by_id[i], "move_why": str(m.get("why", ""))[:200]} for m in ops.get("move") or [] if isinstance(m, dict)
             and (i := _id(m)) in by_id and by_id[i].get("status") == "active"]
    set_state(reviewed_seq=state().get("seq", 0))
    return did, moves


def moved(nid: int, episode: int | None, why: str, now: int | None = None) -> dict | None:
    """A one-off moment now kept in its dated memory instead: out of what she knows (undoable)."""
    notes = load()
    n = next((n for n in notes if n["id"] == nid and n.get("status") == "active"), None)
    if not n:
        return None
    before = _snap(n)
    n.update(status="retired", why=f"moved to memory #{episode}: {why}" if episode else f"a moment: {why}")
    save(notes)
    _log("move", n, "review", n["why"], before, episode, now)
    return n


def remap_sources(old: set, new: int):
    """Episodes merged into one: the notes they taught now point at the merged one."""
    notes = load()
    changed = False
    for n in notes:
        if set(n.get("source") or []) & old:
            n["source"] = sorted((set(n["source"]) - old) | {new})
            changed = True
    if changed:
        save(notes)


def learned_only_from(episode: int) -> list[dict]:
    """The notes that came from this one episode and nowhere else."""
    return [n for n in load() if (n.get("source") or []) == [episode]]


def overview() -> dict:
    """For the memory panel."""
    store = load()
    keep = ("id", "v", "text", "section", "tier", "subject", "importance", "happened", "valid_until", "status",
            "why", "recorded", "first_recorded", "evidence", "forgotten_at")
    show = lambda n: {**{k: n.get(k) for k in keep}, "history": [{k: h.get(k) for k in ("v", "text", "recorded", "why")}  # noqa: E731
                                                                  for h in n.get("history") or []]}
    active = [n for n in store if n.get("status") == "active"]
    return {"sections": SECTIONS,
            "core": [show(n) for n in active if n.get("tier") == "core"],
            "archive": [show(n) for n in sorted((n for n in active if n.get("tier") != "core"), key=lambda n: -n.get("recorded", 0))],
            "forgotten": [show(n) for n in store if n.get("status") == "forgotten"],
            "changes": [{k: c.get(k) for k in ("n", "ts", "op", "id", "by", "why")} | {"text": (c.get("after") or {}).get("text")}
                        for c in changes(30)],
            "core_chars": len(core_text(store)), "core_max": CORE_CHARS}


def known_block(conversation: str, now: int | None = None) -> str:
    """What the model is shown of what she already knows, to edit against: the core, and the
    archive notes related to this conversation; plus the forgotten ones it might bring back."""
    now = now or now_ms()
    notes = load()
    live = [n for n in notes if n.get("status") in ("active", "expired")]
    core = [n for n in live if n.get("tier") == "core" and n.get("status") == "active"]
    rest = [n for n in live if n not in core]
    related = []
    if rest:
        R, _ = relevance(conversation[-4000:], rest)
        ranked = sorted(zip(R, range(len(rest))), reverse=True)[:15]
        related = [rest[i] for r, i in ranked if r >= R_MIN * 0.8]
    tomb = [n for n in notes if n.get("status") == "forgotten"]
    if tomb:
        R, _ = relevance(conversation[-4000:], tomb)
        tomb = [n for r, n in zip(R, tomb) if r >= R_MIN * 0.8][:10]
    out = ["What you already know (id · section · subject · when · text):"]
    for n in core + related:
        when = n.get("happened") or "-"
        state = "" if n.get("status") == "active" else f" ({n['status']})"
        out.append(f"#{n['id']} · {n.get('section')} · {n.get('subject', '')} · {when}{state} · {n['text']}")
    if len(out) == 1:
        out.append("(nothing yet)")
    if tomb:
        out.append("\nForgotten (never write these down again):")
        out += [f"- {n['text']}" for n in tomb]
    return "\n".join(out)


_DATE = re.compile(r"\d{4}(-\d{1,2}(-\d{1,2})?)?")


def _id(v) -> int | None:
    """A note id as the model wrote it: 8, "8" or "#8"."""
    if isinstance(v, dict):
        v = v.get("id")
    try:
        return int(str(v).strip().lstrip("#"))
    except (TypeError, ValueError):
        return None


def _clean(op: dict) -> dict:
    """A note's fields as the model gave them, made safe."""
    out = {}
    if op.get("text"):
        out["text"] = str(op["text"]).strip()[:300]
    if op.get("section") in SECTIONS:
        out["section"] = op["section"]
    if op.get("tier") in ("core", "archive"):
        out["tier"] = op["tier"]
    if op.get("subject") is not None:
        out["subject"] = str(op["subject"]).strip()[:20]
    if op.get("importance") is not None:
        try:
            out["importance"] = round(max(0.0, min(1.0, float(op["importance"]))), 2)
        except (TypeError, ValueError):
            pass
    if isinstance(op.get("cues"), list):
        out["cues"] = [str(c).strip()[:30] for c in op["cues"] if str(c).strip()][:10]
    for k in ("happened", "valid_until"):
        if k in op:
            v = str(op[k] or "").strip()
            out[k] = v if _DATE.fullmatch(v) else ""
    return out


async def apply(ops: dict, source: int | None = None, now: int | None = None, judge=None, by: str = "model") -> list[str]:
    """Carry out the model's edits (add / update / retire / confirm), each logged, none of them
    able to bring back what was forgotten. judge(system, text) -> the model's answer, for the
    statements that may or may not be a fact already known or forgotten (at most two short
    questions, and only when there are any: see same_facts()). Returns what happened, for the log."""
    now = now or now_ms()
    notes = load()
    by_id = {n["id"]: n for n in notes}
    forgotten = [n for n in notes if n.get("status") == "forgotten"]
    did, bumped = [], set()

    def bump(n: dict, why: str = ""):
        """More evidence for a note: at most once per conversation."""
        if n["id"] in bumped:
            return
        bumped.add(n["id"])
        before = _snap(n)
        n["evidence"] = n.get("evidence", 0) + 1
        n["last_evidence"] = now
        _log("confirm", n, by, why, before, source, now)
        did.append(f"confirm #{n['id']}" + (f" ({why})" if why else ""))

    for c in ops.get("confirm") or []:
        n = by_id.get(_id(c))
        if n and n.get("status") in ("active", "expired"):   # a passed plan comes back only by an update (a new date, how it went)
            bump(n)

    updates = [(by_id.get(_id(u)), _clean(u), u) for u in ops.get("update") or [] if isinstance(u, dict)]
    updates = [(n, new, u) for n, new, u in updates if n and n.get("status") in ("active", "expired")]
    adds = [new for new in (_clean(a) for a in ops.get("add") or [] if isinstance(a, dict)) if new.get("text")]
    # what would bring back something forgotten, and what's already known: decided all at once
    texts = [new["text"] for _, new, _ in updates if new.get("text")] + [new["text"] for new in adds]
    tombs = dict(zip(texts, await same_facts(texts, forgotten, judge))) if forgotten else {}
    live = [n for n in notes if n.get("status") in ("active", "expired")]
    known = dict(zip([a["text"] for a in adds], await same_facts([a["text"] for a in adds], live, judge)))

    for n, new, u in updates:
        if new.get("text") and tombs.get(new["text"]):
            did.append(f"update #{n['id']} blocked: forgotten")
            continue
        if not any(new.get(k) is not None and new.get(k) != n.get(k) for k in new):
            did.append(f"update #{n['id']} changed nothing (no new text given)")
            continue
        before = _snap(n)
        n.setdefault("history", []).append({**before, "recorded": n.get("recorded")})
        n.update(new)
        n.update(v=n.get("v", 1) + 1, recorded=now, status="active", why=str(u.get("why", ""))[:200])
        n["source"] = sorted(set((n.get("source") or []) + ([source] if source is not None else [])))
        _log("update", n, by, n["why"], before, source, now)
        did.append(f"update #{n['id']}: {n['text'][:40]}")

    for r in ops.get("retire") or []:
        n = by_id.get(_id(r)) if isinstance(r, dict) else None
        if not n or n.get("status") != "active":
            continue
        before, why = _snap(n), str(r.get("why", ""))[:200]
        by_note = next((by_id[k] for k in map(int, re.findall(r"#(\d+)", why))
                        if k != n["id"] and k in by_id and by_id[k].get("status") == "active"), None)
        if weight(n) >= 0.8 and by_note:   # an important note goes only for one that replaces it
            n.update(status="retired", why=why, superseded_by=by_note["id"])
            _log("retire", n, by, why, before, source, now)
            did.append(f"retire #{n['id']}, superseded by #{by_note['id']}")
        elif weight(n) >= 0.8:   # otherwise the model can't throw away what matters: it goes to the archive
            n.update(tier="archive", why=f"kept in the archive ({why})")
            _log("demote", n, by, n["why"], before, source, now)
            did.append(f"kept #{n['id']} (important), moved to the archive")
        else:
            n.update(status="retired", why=why)
            _log("retire", n, by, why, before, source, now)
            did.append(f"retire #{n['id']}")

    nid, added = max((n["id"] for n in notes), default=0), []
    for new in adds:
        tomb = tombs.get(new["text"])
        if tomb:
            did.append(f"add blocked (forgotten #{tomb['id']}): {new['text'][:40]}")
            continue
        twin = known.get(new["text"])
        if twin is None and added:          # the same fact twice in this one batch
            n, sim = closest(new["text"], added)
            twin = n if sim >= SURE_COS else None
        if twin is not None:   # already known: that's a confirmation, not a second copy
            bump(twin, "said again")
            continue
        nid += 1
        n = {"id": nid, "v": 1, "section": "you", "tier": "archive", "subject": "", "importance": 0.5, "cues": [],
             "happened": "", "valid_until": "", **new, "status": "active", "why": "", "recorded": now,
             "first_recorded": now, "source": [source] if source is not None else [], "evidence": 0,
             "last_evidence": None, "history": []}
        notes.append(n)
        added.append(n)
        by_id[nid] = n
        _log("add", n, by, source=source, ts=now)
        did.append(f"add #{nid} [{n['tier']}/{n['section']}] {n['text'][:40]}")
    save(notes)
    did += tidy(now)
    return did


def tidy(now: int | None = None) -> list[str]:
    """The system's own upkeep: dated things that have passed expire; an over-full core moves
    its least important lines to the archive."""
    now = now or now_ms()
    notes, did = load(), []
    today = datetime.fromtimestamp(now / 1000).date().isoformat()
    for n in notes:
        if n.get("status") == "active" and n.get("valid_until") and n["valid_until"] < today:
            before = _snap(n)
            n.update(status="expired", why=f"{n['valid_until']} has passed")
            _log("expire", n, "system", n["why"], before, ts=now)
            did.append(f"expire #{n['id']}")
    core = sorted((n for n in notes if n.get("status") == "active" and n.get("tier") == "core"),
                  key=lambda n: (n.get("section") == "plans", weight(n), n.get("recorded", 0)))
    while core and len(core_text(core)) > CORE_CHARS:   # measured as she reads it, dates and headings included
        n = core.pop(0)
        before = _snap(n)
        n.update(tier="archive", why="the core was full")
        _log("demote", n, "system", n["why"], before, ts=now)
        did.append(f"demote #{n['id']} to the archive")
    if did:
        save(notes)
    return did


# ---------------------------------------------------------------- the user's say

def forget(nid: int, now: int | None = None, why: str = "you asked her to forget it") -> dict | None:
    """The user's "forget this": a tombstone. Returns the note (memory.py scrubs older memories with it)."""
    notes = load()
    n = next((n for n in notes if n["id"] == nid and n.get("status") != "forgotten"), None)
    if not n:
        return None
    before = _snap(n)
    n.update(status="forgotten", why=why, forgotten_at=now or now_ms())
    save(notes)
    _log("forget", n, "user", n["why"], before, ts=now)
    return n


def no_longer_true(nid: int, now: int | None = None) -> dict | None:
    """The user's "that's not true any more": retired, not forgotten (nothing is blocked from
    being learned again), and undoable like any change."""
    notes = load()
    n = next((n for n in notes if n["id"] == nid and n.get("status") == "active"), None)
    if not n:
        return None
    before = _snap(n)
    n.update(status="retired", why="you marked it as no longer true")
    save(notes)
    _log("retire", n, "user", n["why"], before, ts=now)
    return n


def restore(nid: int, now: int | None = None) -> dict | None:
    notes = load()
    n = next((n for n in notes if n["id"] == nid and n.get("status") == "forgotten"), None)
    if not n:
        return None
    before = _snap(n)
    n.update(status="active", why="restored")
    n.pop("forgotten_at", None)
    save(notes)
    _log("restore", n, "user", "restored", before, ts=now)
    return n


def undo(seq: int, now: int | None = None) -> bool:
    """Put a note back the way it was before change #seq (that undo is itself a logged change)."""
    entry = next((c for c in changes(10_000) if c["n"] == seq), None)
    if not entry or entry["op"] in ("forget", "restore"):
        return False   # forgetting is undone by restoring
    notes = load()
    n = next((n for n in notes if n["id"] == entry["id"]), None)
    if not n:
        return False
    before = _snap(n)
    if entry["before"] is None:      # it was added: take it back out
        n.update(status="retired", why="undone")
    else:
        if entry["op"] == "update":
            n.setdefault("history", []).append({**before, "recorded": n.get("recorded")})
            n["v"] = n.get("v", 1) + 1
        n.update({k: entry["before"].get(k) for k in FIELDS if k in entry["before"]})
        n["evidence"] = entry["before"].get("evidence", n.get("evidence", 0))
        n["why"] = f"undid change #{seq}"
    save(notes)
    _log("undo", n, "user", f"undid change #{seq}", before, ts=now)
    return True
