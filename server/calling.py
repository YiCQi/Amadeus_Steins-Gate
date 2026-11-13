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
outlive a polite exchange. It simply isn't what decides whether you pick up the phone. And no
statistic over those tags can separate the two, because they carry the same tag: "embarrassed"
is both "変態！" and a confession. (Weighting by mood instead doesn't help -- "angry" is her
banter emotion as much as her angry one.) This is the same shape as the lesson in verbatim.py:
the cheap number measures something real, and not the thing being asked about.

So the model is the only part of this system that can tell her yelling from her meaning it, and
that judgement is left to it entirely, in the prompt. What the rules keep is what rules are good
at -- how often, and when it would be rude:

- Nothing in the first few messages of a conversation (DEPTH). You cannot be past what text can
  carry on your second line; something has to have built up first.
- Nothing out of a dead-flat stretch (CALM). This is arousal used as a floor, not as a gate: it
  cannot tell feeling from flustering, but it can tell that nothing whatever has been going on.
- At most PER_DAY of her calls, a cooldown since her last one and since any call at all (you
  have just hung up: don't ring back), and nothing out of the blue in the small hours.

Two doors, because they are not the same act. Mid-conversation is the one worth having: you are
texting right now and she rings instead of typing. Out of the blue is server/lull.py's states --
there is no live conversation to be past, so the state is the whole reason, the model decides,
and a much longer cooldown is what keeps it rare.

The budget lives in her memory rather than in a counter, so it survives a restart, and so that
every call she makes is in the log whatever became of it -- she tried, and she should remember
trying even if you never picked up.
"""
from datetime import datetime

from . import config, memory

HOUR, DAY = 3600_000, 86400_000

# A floor, not a gate (see above). Her replies run 0.47 at the median and 0.70 at the ninth
# decile on memory.felt_arousal's scale; a fifth of them sit below 0.3, and those are the
# stretches where nothing is happening at all -- "嗯", "好", neutral tag after neutral tag.
CALM = 0.3
DEPTH = 6                    # messages into the conversation before ringing makes any sense
RECENT = 20                  # of them, at most, when reading how the conversation has been going
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

_pending: dict = {"reason": "", "lid": 0, "at": 0}   # she has dialled; why, for when you pick up


def enabled() -> bool:
    return config.load_settings().get("her_calls", True)


def _calls() -> list[dict]:
    return [m for m in memory.log() if m["role"] == "event" and m.get("kind") == "her_call"]


def _live(now: int) -> list[dict]:
    """The conversation she is in: what was said in the last hour, the newest RECENT of it."""
    return [m for m in memory.log() if 0 <= now - m["ts"] < LIVE][-RECENT:]


def heat(now: int) -> float:
    """How worked up the conversation has been, on the scale a memory is kept by. Only ever
    asked whether it is near zero -- it cannot tell her flustering from her meaning it."""
    live = _live(now)
    return memory.felt_arousal(live) if live else 0.0


def may_call(state: str = "", now: int | None = None) -> bool:
    """Whether the option to call is open to her at all -- not whether she should. state: ""
    mid-conversation, otherwise one of server/lull.py's (she rings out of the blue)."""
    now = now or memory.now_ms()
    if not enabled():
        return False
    cold = bool(state)
    if cold and state not in CALL_IN:
        return False
    calls = _calls()
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
    return len(_live(now)) >= DEPTH and heat(now) >= CALM


def dialled(reason: str) -> dict:
    """She called. It goes into the timeline straight away, before the page has rung even once:
    she tried, and she should remember trying whatever becomes of it (and of the page). How it
    went is written onto this same entry later, by answered()."""
    e = memory.add("chat", "event", kind="her_call", reason=reason[:120])
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
