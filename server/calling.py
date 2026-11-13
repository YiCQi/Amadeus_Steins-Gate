"""When she calls you instead of texting back.

Texting first is server/lull.py: rules read the conversation and decide whether she *may* speak
up, and the model decides whether she actually does. Calling works the same way and for the same
reason -- a rule alone would have her ring on a schedule, and a model alone would ring whenever
something felt important, which is most of the time. So the rules only open the door, and the
option exists for her only while it is open: when may_call() is false the chat prompt never
mentions calling at all, so there is nothing there for her to reach for.

What the rules do *not* do is decide whether the feeling warrants it, and that is the finding
this module is built around rather than a corner that was cut. The obvious gate was arousal --
memory.felt_arousal already reads how worked up she got off the emotions she tagged her own
lines with, and it is good enough to decide how long a memory lasts. Run over her own log, the
moments it picks out as her most intense are, in order: "谁承认了啊！", "你、你看了那个ID的帖子？！",
"宝、宝……？！你突然叫什么呢！". All of them her being teased and yelling about it. Her genuine
moments -- "……不过。能听到你这么说，我……嗯。" -- score lower than her flustering does.

That isn't a flaw in the measure. memory.py says it outright: her banter is play rather than
feeling, but it is arousal, and arousal is what makes a memory stick. A silly fight really does
outlive a polite exchange. It simply isn't what decides whether you pick up the phone. What one
number could never do was keep the two apart, because loud and hurt were the same axis.

Six axes can (memory.FEELINGS), and that is what this gate now asks. Fluster and temper are her
fast axes, and her log puts her resting fluster at 0.50 -- half flustered is simply where she
lives. Sadness rests at 0.25 and reaches 0.96. So the door asks how far she has travelled from
her own normal (memory.above) on the slow axes only, and over her own log what opens it is
sadness in 127 cases out of 140 and never once her yelling 変態. The rest is still the model's
call, in the prompt, because no number knows whether this particular sadness is worth a phone
call. What the rules keep is what rules are good at -- how often, and when it would be rude:

- Nothing in the first few messages of a conversation (DEPTH). You cannot be past what text can
  carry on your second line; something has to have built up first.
- Nothing unless a slow feeling is genuinely above where it sits for her anyway (MOVED).
- At most PER_DAY of her calls, a cooldown since her last one and since any call at all (you
  have just hung up: don't ring back), and nothing out of the blue in the small hours.

There is a third door, and leaving it out was a plain oversight: he can simply ask her to call.
Everything above is about her wanting to, so the first thing anyone actually tries -- "你想视频吗"
-- found her unable, and in character she covered for it ("正好，你先打过来不就行了"), which reads
as her refusing rather than as the feature missing. Being asked is read off the turn (ASKED, the
way lull.py reads a goodbye and verbatim.py reads being asked to remember) and it skips all of
the above: the budget and the cooldowns exist to stop her bothering him, and they have no
business standing between him and something he just asked for. Her answer is still hers -- the
prompt tells her she may say no, in character, but never that she cannot.

Two doors of her own, because they are not the same act. Mid-conversation is the one worth having: you are
texting right now and she rings instead of typing. Out of the blue is server/lull.py's states --
there is no live conversation to be past, so the state is the whole reason, the model decides,
and a much longer cooldown is what keeps it rare.

The budget lives in her memory rather than in a counter, so it survives a restart, and so that
every call she makes is in the log whatever became of it -- she tried, and she should remember
trying even if you never picked up.
"""
import re
from datetime import datetime

from . import config, memory

HOUR, DAY = 3600_000, 86400_000

# The slow feelings: the ones she doesn't reach for lightly. Anger, fluster and surprise are not
# here at all, and that is the whole of the finding above -- those are the ones she is in all day.
# They need no weights, because memory.above has already put each on its own scale.
SLOW = ("sadness", "worry", "enjoyment")
MOVED = 0.45                 # this far above her own normal; about one reply in four in her log
DEPTH = 6                    # messages into the conversation before ringing makes any sense
LIVE = HOUR                  # older than this isn't the conversation she is in

PER_DAY = 2                  # her own calls, in any 24 h
COOLDOWN = 2 * HOUR          # since her last call, and since any call ended
COLD_COOLDOWN = 20 * HOUR    # since her last one, before she may ring out of the blue again
QUIET_FROM, QUIET_TO = 0, 8  # no calling out of the blue between these hours
RING = 30                    # seconds the page rings before it counts as missed

# Out of the blue, she may only call where she would have texted anyway and the reason is her
# own: a question of hers you left hanging, a conversation that trailed off, a long silence.
# Not "morning" or "after_bye" (she has just said good night), not "away" (you're in the shower).
CALL_IN = {"lull", "asked", "asked_back", "cold"}

# Him asking her to call, in the three languages. Only ever matched against what he just said.
ASKED = re.compile(
    r"打(?:个|一?下)?视频|视频(?:通话|电话|聊天)|想视频|视频吗|打(?:个|一?下)?电话|电话(?:吧|过来)|"
    r"给我打|打给我|你打过来|call me|give me a call|video ?call|ring me|"
    r"ビデオ通話|電話(?:して|かけて)", re.I)

_pending: dict = {"reason": "", "lid": 0, "at": 0}   # she has dialled; why, for when you pick up


def asked_to_call(text: str) -> bool:
    """Whether he has just asked her to call. Then none of the rest applies: the budget is there
    to keep her from pestering him, not to keep her from answering him."""
    return bool(text and ASKED.search(text))


def enabled() -> bool:
    return config.load_settings().get("her_calls", True)


def _calls() -> list[dict]:
    return [m for m in memory.log() if m["role"] == "event" and m.get("kind") == "her_call"]


def _live(now: int) -> list[dict]:
    """The conversation she is in: what was said in the last hour."""
    return [m for m in memory.log() if 0 <= now - m["ts"] < LIVE]


def moved(now: int | None = None) -> float:
    """How far this conversation has actually moved her, on the slow axes only. She is reading
    her state as of her last reply, so this opens a turn late by construction: you say the thing,
    she answers it, and the phone rings after that -- which is also how it goes between people."""
    f = memory.above(memory.feelings(now))
    return round(max(f[k] for k in SLOW), 3)


def may_call(state: str = "", now: int | None = None, asked: bool = False) -> bool:
    """Whether the option to call is open to her at all -- not whether she should. state: ""
    mid-conversation, otherwise one of server/lull.py's (she rings out of the blue). asked: he
    has just asked her to, which opens it on its own."""
    now = now or memory.now_ms()
    if not enabled():
        return False
    if asked:
        return True
    cold = bool(state)
    if cold and state not in CALL_IN:
        return False
    calls = [m for m in _calls() if not m.get("asked")]   # one he asked for was never her idea
    if sum(1 for m in calls if now - m["ts"] < DAY) >= PER_DAY:
        return False
    if calls and now - calls[-1]["ts"] < (COLD_COOLDOWN if cold else COOLDOWN):
        return False
    # not on the heels of a call: you have just been talking, and just hung up
    ended = next((m for m in reversed(memory.log())
                  if m["role"] == "event" and m.get("kind") == "call_end"), None)
    if ended and now - ended["ts"] < COOLDOWN:
        return False
    if cold:
        return not QUIET_FROM <= datetime.fromtimestamp(now / 1000).hour < QUIET_TO
    return len(_live(now)) >= DEPTH and moved(now) >= MOVED


def dialled(reason: str, asked: bool = False) -> dict:
    """She called. It goes into the timeline straight away, before the page has rung even once:
    she tried, and she should remember trying whatever becomes of it (and of the page). How it
    went is written onto this same entry later, by answered()."""
    e = memory.add("chat", "event", kind="her_call", reason=reason[:120],
                   **({"asked": True} if asked else {}))
    _pending.update(reason=reason[:120], lid=e["id"], at=memory.now_ms())
    return e


def answered(lid: int, how: str) -> dict | None:
    """How her call went: "yes", "no" (turned down) or "missed"."""
    if how != "yes":
        _pending.update(reason="", lid=0, at=0)
    return memory.amend(lid, answered=how if how in ("yes", "no", "missed") else "missed")


def why(now: int | None = None) -> str:
    """Why she rang, for the moment the call opens; taken once, and stale after a while (you
    picked up, said nothing and hung up, and the call after that one is your own)."""
    now = now or memory.now_ms()
    reason = _pending["reason"] if now - _pending["at"] < 10 * 60_000 else ""
    _pending.update(reason="", lid=0, at=0)
    return reason
