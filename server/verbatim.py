"""What was actually said, searchable: her memory of the words themselves.

server/notes.py is what she knows and the episodes in server/memory.py are what she remembers.
Both are written by the model and both are summaries -- together about 3 KB standing in for the
12 KB that has actually been said to her. Whatever didn't make the cut was unreachable, because
nothing ever searched the log. Tell her something small ("黄仁勋出轨了"), ask two days later, and
she had no way to know: the message was on disk, in no note and in no gist, and no code path
could reach it. That isn't forgetting, it's a missing index.

So this is the third layer, and the only one that is not a summary. Every message is embedded
once and kept, and a specific enough question brings the words back. Because this exists, the
other two layers are free to stay general: a gist no longer has to carry every detail to keep it.

What stops it turning her into a search engine is not how good a match has to be. That was the
first thing tried, and measuring it killed it: over her own log the best-match scores of real
questions about the past (median 0.53) and of perfectly ordinary turns (median 0.52) sit on top
of each other, and "我有点累了" scores 0.71 against an older "真有点累了" -- an excellent match, and
a pointless thing to bring up. Rarity fails the same way: in a corpus this size "嗯嗯" is rarer
than "自行车". Match quality measures similarity; what is needed is whether recalling is wanted.

So the gate is intent, read from the turn the way server/lull.py reads a goodbye:

- She looks here only when she is actually being asked to remember (PROBE) -- "你还记得", "我之前
  跟你说过", "...来着", "did I tell you", "だっけ". Nothing else reaches this layer at all, which
  is also the honest behaviour: people don't answer "晚安" with what you said three weeks ago.
- Because she only looks when asked, the bar to find something is low, and it leans on the
  literal: SEM/LEX is 0.35/0.65, the opposite of notes.py. What people test each other on is
  names, numbers and proper nouns -- what embeddings blur and what tf-idf is good at.
- Old words still need a sharper cue: the bar rises with age (gate), the way it takes a better
  prompt to dig up something from months ago, and only a few lines come back at once.
- What comes back is a cue, not a transcript. She is told she half-remembers it, so she puts it
  in her own words. Perfect recitation would be less human than not remembering at all.

Nothing is kept here that the log doesn't already hold (verbatim.npy is an index of it), anything
forgotten is out of reach here too, and none of it leaves the machine: the vectors come from the
local model in server/embed.py, and only the few lines that surface ever reach the prompt.
"""
import json
import math
import re

import numpy as np

from . import config, embed

DIR = config.DATA_DIR / "memory"
VECS, META = DIR / "verbatim.npy", DIR / "verbatim.json"

MIN_CHARS = 6                # shorter than this says nothing on its own ("好", "嗯")
BATCH_MAX = 500              # messages indexed in one pass, so a first build doesn't hold the lock
SEM, LEX = 0.35, 0.65        # here the literal carries it, unlike notes.py (0.7 meaning / 0.3 words)
RARE_IDF = 3.0               # a shared term counts as a cue at about this (in under 5% of messages)
# Measured over her own log, once the asking is taken out of the question: lines that really
# answer it score 0.43 to 0.84, and what is left over -- a stray word in common -- sits at 0.29 to
# 0.32. 0.33 is that gap. Below it she simply doesn't come up with anything, which is the honest
# answer when the words aren't there: the 自行车 and the name of the man who wrote to him are in
# her notes, because they were never typed in the chat at all.
GATE_MIN, GATE_RISE, GATE_TAU = 0.33, 0.18, 45.0    # the bar now, how much it rises, over what days
K = 3                        # never more than this many lines at once
DAY = 86400_000

# Being asked to remember: she looks no further back than her notes and her memories unless the
# turn is one of these. Deliberately about the asking, not the topic -- "你之前说的那个", "what was
# that thing you mentioned", not a word list of what the two of them talk about.
PROBE = re.compile(
    r"记得|想起|忘了|忘记|说过|讲过|提过|提到过|告诉过|聊过|问过|来着|是谁|叫什么|什么时候|"
    r"之前|以前|上次|上回|那次|当初|前几天|那天|有没有跟我?你?说|我跟你说过|你说的那个|那件事|"
    r"\bremember|\bforgot|\bforgotten|did (?:i|you) (?:ever )?(?:tell|say|mention)|"
    r"i (?:told|mentioned)|you (?:said|told|mentioned)|last time|the other day|back then|"
    r"what was that|who was that|used to say|"
    r"覚えて|思い出|忘れ|言った|話した|だっけ|この前|前に", re.I)


# ---------------------------------------------------------------- the index

def _meta() -> dict:
    try:
        return json.loads(META.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"ids": [], "upto": 0}


def _vectors() -> np.ndarray:
    try:
        return np.load(VECS)
    except (OSError, ValueError):
        return np.zeros((0, embed.DIM), np.float32)


def _store() -> tuple[list[int], np.ndarray]:
    """The index, and only if its two halves agree: they are written one after the other, so a
    crash in between would leave ids without vectors. Mismatched, it is dropped and rebuilt."""
    meta, vecs = _meta(), _vectors()
    if len(meta["ids"]) != len(vecs):
        print(f"[verbatim] index out of step ({len(meta['ids'])} ids, {len(vecs)} vectors), rebuilding",
              flush=True)
        reset()
        return [], np.zeros((0, embed.DIM), np.float32)
    return meta["ids"], vecs


def text_of(m: dict) -> str:
    """What a message says, for matching: its words with stickers left out (they colour a moment,
    they are not what it is about), and for her the subtitle rather than the Japanese."""
    from . import memory
    return memory.words(m, lambda s: s or "").strip()


def indexable(m: dict) -> bool:
    return (m.get("role") in ("user", "assistant") and m.get("ch") in ("chat", "call")
            and len(text_of(m)) >= MIN_CHARS)


def index(limit: int = BATCH_MAX) -> int:
    """Embed the messages not in the index yet; how many were added. Runs in the background
    (memory.maintain), a few hundred at a time, and does nothing once it has caught up."""
    from . import memory
    if not embed.available():
        return 0
    ids, vecs = _store()
    upto = _meta()["upto"]          # 0 again if _store() had to drop a half-written index
    entries = memory.log()
    fresh = [m for m in entries if m["id"] > upto and indexable(m)][:limit]
    last = entries[-1]["id"] if entries else 0
    if not fresh:
        if last > upto:                       # only unindexable ones left: don't look at them again
            _write(ids, vecs, last)
        return 0
    _write(ids + [m["id"] for m in fresh],
           np.concatenate([vecs, embed.encode([text_of(m) for m in fresh], "passage")]),
           fresh[-1]["id"])
    print(f"[verbatim] {len(fresh)} more messages indexed ({len(ids) + len(fresh)} in all)", flush=True)
    return len(fresh)


def _write(ids: list[int], vecs: np.ndarray, upto: int):
    DIR.mkdir(parents=True, exist_ok=True)
    np.save(VECS, vecs)       # vectors first, ids second: _store() checks that the two agree and
    META.write_text(json.dumps({"ids": ids, "upto": upto}, ensure_ascii=False), encoding="utf-8")


def reset():
    """Drop the index. It rebuilds itself in the background."""
    for p in (VECS, META):
        p.unlink(missing_ok=True)


def overview() -> dict:
    ids = _meta()["ids"]
    return {"indexed": len(ids), "upto": _meta()["upto"], "model": embed.available()}


# ---------------------------------------------------------------- recall

def _sealed_ranges() -> list[tuple[int, int]]:
    from . import memory
    return [tuple(e["range"]) for e in memory.episodes() if e.get("sealed") and e.get("range")]


def _rare(query: str, doc_terms: list[list[str]]) -> set[str]:
    """The query's own terms that are distinctive enough to count as a cue at all. Document
    frequency is measured over her messages, so what is rare is rare *for the two of them*."""
    from . import notes
    df: dict[str, int] = {}
    for ts in doc_terms:
        for t in set(ts):
            df[t] = df.get(t, 0) + 1
    n = len(doc_terms)
    return {t for t in set(notes.terms(query))
            if t not in notes.GENERIC and math.log((n + 1) / (df.get(t, 0) + 0.5)) >= RARE_IDF}


def gate(age_days: float) -> float:
    """How good a match has to be to bring words back, given how long ago they were said."""
    return GATE_MIN + GATE_RISE * (1 - math.exp(-max(0.0, age_days) / GATE_TAU))


def asked_to_remember(query: str) -> bool:
    """Whether this turn is someone asking her to remember, rather than just talking to her."""
    return bool(query and PROBE.search(query))


def recall(query: str, now: int, skip: set[int] | None = None, k: int = K) -> list[dict]:
    """The lines this moment brings back word for word, best first. Empty unless she is being
    asked to remember (PROBE) and something distinctive was named: an ordinary turn never reaches
    this layer. Messages she is already reading (skip) are left out -- she has those in front of
    her, and a message on the screen is not something coming back to her."""
    from . import memory, notes
    query, skip = (query or "").strip(), skip or set()
    ids, vecs = _store()
    if not query or not ids or not embed.available() or not asked_to_remember(query):
        return []
    sealed = _sealed_ranges()
    by_id = {m["id"]: m for m in memory.log()}
    rows = [(i, by_id[mid]) for i, mid in enumerate(ids)
            if mid in by_id and mid not in skip
            and not any(lo <= mid <= hi for lo, hi in sealed)]   # a forgotten conversation is gone
    if not rows:
        return []
    texts = [text_of(m) for _, m in rows]
    doc_terms = [notes.terms(t) for t in texts]
    # What is being asked about, with the asking taken out. "你还记得我说过黄仁勋的事吗" is about
    # 黄仁勋, and matching on 记得 or 说过 instead pulls up every time either of them said those
    # words. The whole question still goes to the embedding, where the phrasing is context.
    topic = PROBE.sub(" ", query)
    rare = _rare(topic, doc_terms)
    if not rare:                     # asked to remember, but nothing distinctive was named
        return []
    lex = notes.lexical(topic, texts)
    cos = vecs[[i for i, _ in rows]] @ embed.encode([query], "query")[0]
    sem = np.clip((cos - notes.COS_LO) / (notes.COS_HI - notes.COS_LO), 0, 1)
    picks = []
    for j, (_, m) in enumerate(rows):
        if not rare & set(doc_terms[j]):                 # no distinctive word in common
            continue
        score = SEM * float(sem[j]) + LEX * float(lex[j])
        if score >= gate((now - m["ts"]) / DAY):
            picks.append({"id": m["id"], "ts": m["ts"], "ch": m["ch"], "role": m["role"],
                          "text": texts[j], "score": round(score, 3), "cos": round(float(cos[j]), 3)})
    picks.sort(key=lambda p: -p["score"])
    return [p for p in picks if _sayable(p["text"])][:k]


def _sayable(text: str) -> bool:
    """Forgetting has to hold here too, or the index would quietly undo it. Checked on the few
    lines that got through rather than on every message: memory.scrubbed loads her notes, and
    when nothing has been forgotten (the usual case) it costs nothing at all."""
    from . import memory
    return memory.scrubbed(text) == text


_END = re.compile(r"[。．.!！?？~～\s]+$")


def recall_text(picks: list[dict], now: int) -> str:
    """The block she reads. It says outright that this is half-remembered: an AI quoting a message
    from two months ago back at you word for word is unsettling, not warm."""
    from . import memory
    if not picks:
        return ""
    out = ["\nSomething said a while ago comes back to you now. You only half-remember it: you know"
           " it came up and roughly what was said, not the words. Say it that way -- bring it up as"
           " something you think you remember, don't quote it back:"]
    for p in sorted(picks, key=lambda p: p["ts"]):
        who = "they said" if p["role"] == "user" else "you said"
        where = "in the chat" if p["ch"] == "chat" else "on a call"
        out.append(f'- {memory.ago(p["ts"], now)} ({memory.when(p["ts"])}), {where}, {who}: '
                   f'"{_END.sub("", p["text"])[:160]}"')
    return "\n".join(out)
