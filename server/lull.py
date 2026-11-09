"""When the chat goes quiet: whether she texts first, and when, read from the conversation the
way a person would (on a call she checks in after half a minute or so; texting, a little later):

- you said goodbye: she leaves you be for hours; after a good night, until the morning;
- you said you'd be off doing something ("我去洗澡", "brb"): she checks in after half an hour or so;
- she asked you something and you haven't answered: she follows up after a minute or two, or, if
  you'd gone, a few minutes after you open the chat again;
- the chat just trailed off, or a call just ended: she often picks it up after a few minutes,
  less soon when it closed on its own ("好", "嗯");
- you haven't talked for a long time: now and then a hello, some minutes after you open the chat.

Never more than twice in a row without an answer, and once she has decided to stay quiet, that
holds until one of you writes. Nothing here asks the model anything: the model is only asked (one
ordinary request) once a plan comes due, and may still stay quiet.
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
# off to do something for a while (not "I went to eat yesterday": the doing is now)
AWAY = re.compile(
    r"(?:我|先|那我|我先|我现在|现在|这就|马上)就?(?:要|得)?去(?:洗澡|吃饭|做饭|上课|开会|上班|工作|写|忙|做|买|拿|跑步|健身)|"
    r"先忙|忙去了|等我一下|等一下|稍等|马上回来|一会儿?回来|待会儿?(?:聊|见|回)|"
    r"\bbrb\b|be right back|\bafk\b|gotta run|going to (?:shower|eat|cook|class|work)|"
    r"ちょっと待って|行ってくる|すぐ戻る", re.I)
# a reply that closes the exchange on its own
ACK = re.compile(r"^\s*(?:好|好的|好吧|好嘞|嗯+|哦+|噢|行|可以|ok|okay|k|收到|了解|知道了|👌|👍)[。.!！~～]*\s*$", re.I)
STALE = 3 * HOUR     # a plan this far past due is no longer what the moment calls for
_declined = {"last": None}   # the last message when she chose to stay quiet: no asking again until something's said


def decline():
    """She was asked whether to text first and chose to stay quiet: leave it until someone writes."""
    log = memory.log()
    _declined["last"] = log[-1]["id"] if log else 0


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

    if _declined["last"] == (log[-1]["id"] if log else 0):
        return out("declined")             # she chose to stay quiet, and nothing has been said since
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
        theirs = [m for m in talked if m["role"] == "user"]
        words = memory.said(theirs[-1], lambda s: s or "") if theirs else ""   # what they said last
        if theirs and AWAY.search(words):   # gone to do something: she checks in once it's been a while
            return out("away", since + int((rng.uniform(45, 90) if again else rng.uniform(20, 45)) * MIN),
                       since=since, question=words[:60])
        if q:
            return out("asked", since + int((rng.uniform(4, 8) if again else rng.uniform(1, 2.5)) * MIN), since=since, question=q)
        if log[-1]["role"] == "event" and log[-1].get("kind") == "call_end":
            return out("after_call", since + int(rng.uniform(3, 8) * MIN), since=log[-1]["ts"]) if rng.random() < 0.6 else out("after_call")
        closed = bool(theirs) and bool(ACK.match(words))   # "好" / "嗯": it came to a close by itself
        chance, (lo, hi) = ((0.3, (15, 30)) if again else (0.5, (6, 12))) if closed else ((0.5, (5, 10)) if again else (0.75, (1.5, 3)))
        if rng.random() < chance:
            return out("lull", since + int(rng.uniform(lo, hi) * MIN), since=since)
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
    # a little slack for the page's timer; with follow-ups a minute or two out, no more than that
    return p["wait"] is not None and (p["due"] is None or p["due"] <= now + 15_000)
