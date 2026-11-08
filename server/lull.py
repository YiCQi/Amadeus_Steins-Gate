"""When the chat goes quiet: whether she texts first, and when, read from the conversation the
way a person would:

- you said goodbye: she leaves you be for hours; after a good night, until the morning;
- she asked you something and you haven't answered: she follows up after a few minutes, or, if
  you'd gone, a few minutes after you open the chat again;
- the chat just trailed off, or a call just ended: now and then she picks it up after a while;
- you haven't talked for a long time: now and then a hello, some minutes after you open the chat.

Never more than twice in a row without an answer. Nothing here asks the model anything: the
model is only asked (one ordinary request) once a plan comes due, and may still stay quiet.
"""
import random
import re
from datetime import datetime, timedelta

from . import memory, persona

MIN, HOUR, DAY = 60_000, 3600_000, 86400_000
FAREWELL = re.compile(
    r"再见|拜拜|晚安|(改天|下次|明天|回头)再?聊|回聊|先走了|先撤了|下线了|睡了|睡觉去|"
    r"\bbye\b|good ?night|\bgn\b|see (you|ya)|talk (to you )?later|\bttyl\b|gotta go|going to bed|"
    r"おやすみ|またね|じゃあね|さようなら|バイバイ|また明日", re.I)
NIGHT = re.compile(r"晚安|睡了|睡觉|good ?night|\bgn\b|going to bed|おやすみ", re.I)
STALE = 3 * HOUR     # a plan this far past due is no longer what the moment calls for


def _text(m: dict) -> str:
    """Everything a message says: the shown text and, for her, the Japanese too."""
    return memory.said(m, lambda s: s or "") + " " + m.get("content", "")


def _question(m: dict) -> str:
    """Her question, if one of the last bubbles of this message of hers asks one."""
    if m["ch"] == "chat":
        texts = [b.get("text") or b.get("sub") or "" for b in persona.chat_bubbles(m["content"]) if b["kind"] != "sticker"]
    else:
        texts = [s["sub"] for s in map(persona.parse_line, m["content"].splitlines()) if s]
    return next((t for t in reversed(texts[-2:]) if re.search(r"[?？]", t)), "")


def _morning(ts: int, rng: random.Random) -> int:
    """Some time between 7:30 and 9:30 the morning after, and at least six hours on."""
    d = datetime.fromtimestamp(ts / 1000)
    day = d.date() if d.hour < 5 else d.date() + timedelta(days=1)
    t = datetime.combine(day, datetime.min.time()) + timedelta(hours=7.5 + rng.uniform(0, 2))
    return max(int(t.timestamp() * 1000), ts + 6 * HOUR + int(rng.uniform(0, HOUR)))


def _unanswered_lately(now: int) -> int:
    """Her own texts in a row lately (12 h) with no word from the user since: the cap."""
    n = 0
    for m in reversed(memory.log()):
        if m["role"] != "assistant":
            break
        n += m["ch"] == "chat" and bool(m.get("idle")) and now - m["ts"] < 12 * HOUR
    return n


def plan(now: int | None = None) -> dict:
    """{"state", "wait": s from now (None: she won't), "due": ms (None when it's counted from
    the page instead: after that long without you doing anything there), and what the note
    needs: "since" (the last message's time), "question"}."""
    now = now or memory.now_ms()
    log = memory.log()
    said = [m for m in log if m["role"] != "event"]

    def out(state, due=None, wait=None, **extra):
        if due is not None:
            wait = max(0, round((due - now) / 1000))
        return {"state": state, "wait": wait, "due": due, **extra}

    if not said:
        return out("first", wait=180)      # never talked: a hello, a few minutes after the chat opens
    if _unanswered_lately(now) >= 2:
        return out("waiting")              # she has texted twice with no answer: it's your turn
    last = said[-1]
    since = last["ts"]
    rng = random.Random(f"{last['id']}:{memory.unanswered('chat')}")   # the same answer until something changes
    talked = [m for m in said[-4:] if since - m["ts"] < 30 * MIN]
    byes = [i for i, m in enumerate(talked) if FAREWELL.search(_text(m))]
    # a goodbye, unless the user went on talking after it
    bye = byes and not any(m["role"] == "user" for m in talked[byes[-1] + 1:])
    if bye:
        hour = datetime.fromtimestamp(since / 1000).hour
        if any(NIGHT.search(_text(talked[i])) for i in byes) or hour >= 22 or hour < 5:
            due, state = _morning(since, rng), "morning"
        else:
            due, state = since + int(rng.uniform(3, 5) * HOUR), "after_bye"
        if now < due + STALE:
            return out(state, due, since=since)
    elif now - since < HOUR:
        again = memory.unanswered("chat") > 0
        q = _question(last) if last["role"] == "assistant" else ""
        if q:
            return out("asked", since + int((rng.uniform(10, 20) if again else rng.uniform(2.5, 5)) * MIN), since=since, question=q)
        if log[-1]["role"] == "event" and log[-1].get("kind") == "call_end":
            return out("after_call", since + int(rng.uniform(15, 30) * MIN), since=log[-1]["ts"]) if rng.random() < 0.5 else out("after_call")
        if rng.random() < (0.4 if again else 0.6):
            return out("lull", since + int((rng.uniform(20, 40) if again else rng.uniform(8, 15)) * MIN), since=since)
        return out("lull")
    # it's been a long while (or the goodbye is long past)
    q = _question(last) if last["role"] == "assistant" and not bye and now - since < 3 * DAY else ""
    if q:   # you left her question hanging and went: she brings it up once you're back
        return out("asked_back", wait=round(rng.uniform(2, 4) * 60), since=since, question=q)
    return out("cold", wait=round(rng.uniform(4, 10) * 60), since=since) if rng.random() < 0.5 else out("cold")


def due_now(p: dict, now: int | None = None) -> bool:
    """Whether the plan allows her to text right now (the page counts the quiet; this keeps
    a stale page, or a second one open, from making her text early or twice)."""
    now = now or memory.now_ms()
    return p["wait"] is not None and (p["due"] is None or p["due"] <= now + MIN)
