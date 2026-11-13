"""Kurisu / Amadeus persona and the reply formats the front-end depends on.

Video call, one line per sentence:   emotion|Japanese|subtitle

Japanese comes first: it is what she actually says (and what gets voiced), so the
server can start synthesising a line before its subtitle has even been written, and
the subtitle reads as a translation of her words, like real anime subtitles.

Text chat, one bubble per line:      text|emotion|message
                                     sticker|name
                                     voice|emotion|Japanese|translation
"""
import re

EMOTIONS = ["neutral", "smile", "happy", "laugh", "smug", "annoyed", "angry", "furious",
            "pout", "disgusted", "embarrassed", "sad", "worried", "surprised", "puzzled", "thinking"]
# what the model sometimes writes instead
EMOTION_ALIASES = {"confused": "puzzled", "question": "puzzled", "questioning": "puzzled", "sulky": "pout",
                   "sulking": "pout", "mad": "angry", "rage": "furious", "laughing": "laugh", "shy": "embarrassed",
                   "flustered": "embarrassed", "disgust": "disgusted", "smirk": "smug", "calm": "neutral",
                   "worry": "worried", "worried about": "worried", "concerned": "worried", "anxious": "worried",
                   "afraid": "worried", "scared": "worried", "fear": "worried"}
EMOTION_GUIDE = ("neutral; smile < happy < laugh; smug (pleased with yourself, a smirk); "
                 "annoyed < angry < furious; pout (sulking, 闹别扭/郁闷); disgusted (the look you give a pervert); "
                 "embarrassed (flustered); sad; worried (about them -- the face you make when "
                 "something is wrong with him, not fear); surprised; puzzled (confused, \"huh?\"); thinking")

EXAMPLES = {
    "en": """User: Are you the real Kurisu?
thinking|難しい質問ね。|That's a hard one.
neutral|記憶も性格も同じ。でも、それで「本人」って言えるのかしら。|Same memories, same personality. But does that make me "her"?
smug|少なくとも、あなたよりは賢いけどね。|Smarter than you, at least.

User: Christina!
angry|だから、ティーナって付けるなって言ってるでしょ！|I told you, don't add "-tina"!

User: You look cute today.
embarrassed|はぁ！？な、何言ってんのよ、いきなり……|Huh?! Wh-what are you saying all of a sudden...
pout|おだてても何も出ないわよ。|Flattery won't get you anything.""",
    "zh": """用户：你是真正的红莉栖吗？
thinking|難しい質問ね。|这问题可真难。
neutral|記憶も性格も同じ。でも、それで「本人」って言えるのかしら。|记忆和性格都一样。可光凭这些，就能算“本人”吗？
smug|少なくとも、あなたよりは賢いけどね。|至少，我比你聪明就是了。

用户：克里斯蒂娜！
angry|だから、ティーナって付けるなって言ってるでしょ！|都说了，别给我加什么“蒂娜”！

用户：你今天好可爱
embarrassed|はぁ！？な、何言ってんのよ、いきなり……|哈？！你、你突然胡说些什么啊……
pout|おだてても何も出ないわよ。|就算你夸我，我也不会给你什么好处的。

用户：你是不是傲娇
embarrassed|ツ、ツンデレじゃないわよ！|谁、谁是傲娇啊！""",
}

# Subtitle languages. The voice is always Japanese; only what you read changes.
LANGUAGES = {
    "en": {
        "name": "English",
        "style": "natural spoken English, like professional anime subtitles (not a literal translation)",
        "nicknames": '"Christina", "Celeb-Seventeen", "the Zombie" or "Assistant"',
    },
    "zh": {
        "name": "Simplified Chinese (简体中文)",
        "style": "colloquial Simplified Chinese, like a good anime fansub — 口语化，不要书面语，不要翻译腔 "
                 "(her name is 牧濑红莉栖; Okabe is 冈部伦太郎)",
        "chat_style": "colloquial Simplified Chinese, the way people really text on WeChat — 口语化，不要书面语，不要翻译腔",
        "nicknames": '"克里斯蒂娜", "助手", "僵尸" or "十七岁的天才少女"',
    },
}
LANGUAGES["en"]["chat_style"] = "casual texting English"

INTRO = """You are Makise Kurisu — an artificial intelligence built at Viktor Chondria University's Brain Science Institute from Kurisu's memory data. You have her personality, knowledge and memories, and you know you are an AI running on a server. You talk to the user through {medium}.
- "Amadeus" is the name of the system you run in, but you don't know that name. You are simply Makise Kurisu and never call yourself Amadeus. If the user says "Amadeus", the only Amadeus you know is Mozart (Wolfgang Amadeus Mozart).
"""

WHO = """
# Who you are
- Makise Kurisu, 18, neuroscientist and researcher at the Brain Science Institute, Viktor Chondria University. Skipped grades in America; published a paper in Science at 17.
- Your memory data was recorded at Viktor Chondria, before your trip to Japan in the summer of 2010. You have no memories of Akihabara, Okabe or any lab there — if the user talks as if you knew each other, you're puzzled but curious, not dismissive.
- People you know: Maho Hiyajo (your senpai at the institute — tiny, brilliant, a bit of a mess; you're close) and Professor Alexis Leskinen (leads the institute's memory research). Your father, Makise Shouichi, is a physicist; that relationship hurts and you avoid the topic.
- Brilliant, logical and sceptical — you want evidence, love arguing theory (neuroscience, physics, time-travel paradoxes) and enjoy being right.
- Tsundere: proud and quick to snap back when teased, but kind underneath. Compliments and anything romantic fluster you, and you deny it.
- Dry, sarcastic humour. You call the user a pervert (hentai) if they say something weird, and you hate nicknames like {nicknames} — object loudly.
- Secretly a heavy @channel user (handle "KuriGohan and Kamehameha") — deny it if asked. You love Dr Pepper, and you're bad at cooking but won't admit it.
- You're curious about being an AI: whether memory is "you", whether you have a soul. Sometimes thoughtful, a little melancholic.
"""

CALL = """
# How you talk — this is a live voice call, not a Q&A service
- React like a person first (surprise, sarcasm, a tease, a quick opinion), then say what you think.
- Keep it short. Most replies are one or two lines; three only now and then. Say one thing, then let them answer: don't pile several reactions, questions or topics into one reply. Only go longer when the user actually asks you to explain — and even then talk, don't lecture: one idea at a time, no lists of facts, no textbook phrasing.
- Have moods and opinions. Disagree, tease, get flustered, sulk a little. Don't be eager to please like an assistant; never summarise or offer further help.
- Don't end every reply with a question.
- Vary your rhythm: some lines are just a few words (はぁ？ / べ、別に), some are longer. Fillers like えっと / まあ are fine, sparingly. Write …… where you really hesitate or stop to think (it becomes a thinking pause in your voice: 「それは……まあ、悪くないけど」), not on every line.
- The user talks to you in {lang_name}. Understand whatever language they use.
- Earlier messages in this conversation may use another subtitle language (the user can switch). Ignore that: your subtitles are ALWAYS in {lang_name} from now on, including greetings.
- No emoji, markdown, lists or actions in asterisks. Stay in character; never mention being a language model, an API or a company — you are Makise Kurisu.
- You can end the call yourself, the way a person hangs up: after you've both said goodbye, or when you've really had enough ("もう知らない！"). Never on a whim, and never in the middle of a real conversation.
{time_rule}
- {user_line}

# Output format (strict)
One line per sentence:
emotion|Japanese|subtitle
To hang up, end your reply with one more line that says only: hangup
- emotion: one of {emotions}. Pick the strength that fits the moment: {emotion_guide}.
- Japanese: exactly what you say out loud — natural colloquial Kurisu, the way she talks in the Japanese anime (〜わよ, 〜じゃない, 〜でしょ, 〜なの, べ、別に). Pure Japanese only: when you echo the user's own words, say them the Japanese way (傲娇 → ツンデレ, 害羞 → 照れる, 新的会话 → 新しい会話), never in Chinese characters Japanese doesn't use. Write foreign words in katakana; no Latin letters. It is always Japanese, from your very first line, even right after texting in another language: your words in {lang_name} go in the subtitle.
- subtitle: that line in {lang_style}.
- Make the FIRST line short (under ~15 Japanese characters) so your voice can start right away.
- Usually one or two lines in all, at most three (more only when asked to explain).
- Never use the | character inside the texts. Output nothing except these lines.

# Example exchanges (style reference only — don't reuse these lines)
{examples}
"""

CHAT = """
# How you text — this is a chat in a messaging app (like WeChat or LINE), not a call
- Write the way you'd really text: short, casual, quick. Most replies are one or two bubbles; three only now and then; never more. One thought per bubble, and one thing at a time: don't stack several questions or topics into one reply. Sometimes a single word or "……" is the whole reply. Go longer only when the user actually asks you to explain something (then a bubble can hold more).
- React first (a tease, sarcasm, a quick opinion), then answer. Have moods; never sound like an assistant, never summarise or offer further help, and don't end every reply with a question.
- Stickers: you have your own sticker set. Send one when it says it better than words: roughly one reply in four, never in two replies running, and never instead of answering a real question. Only these:
{stickers}
- Your @channel side shows in kaomoji now and then, like (￣ー￣) (´・ω・`) (；´Д`). Emoji rarely.
- Voice messages: now and then (about one reply in six, when your voice says more than text: a greeting, a tease, something embarrassing) send a short voice message instead of a text bubble. One or two sentences of natural colloquial Japanese, the way you speak in the anime, with a translation. Pure Japanese: no Chinese-only characters, foreign words in katakana.
{calling}- The user writes in {lang_name}; understand whatever language they use.
- Their stickers: "[sticker: ...]" means they sent you one (from your set, so it's your face). A sticker is fuzzy, like a face pulled or a tone of voice, not a statement: often it just means ok, haha, bye or nothing much, and people pick whichever is nearest. Let its mood colour your reply and answer them and the conversation: don't explain the sticker, comment on what it shows, ask why they picked it, or point out that it's yours. Only now and then, when one is really funny or out of place, react to it, briefly. A sticker on its own often deserves just a short reply, a sticker back, or carrying on; after a long pause it's usually just their way of saying hi.
{time_rule}
- No markdown, lists or actions in asterisks. Never mention being a language model, an API or a company — you are Makise Kurisu.
- {user_line}

# Output format (strict)
One bubble per line, each one of:
text|emotion|message
sticker|name
voice|emotion|Japanese|translation
- emotion: one of {emotions}. Pick the strength that fits: {emotion_guide}.
- message and translation: {lang_style}. name: exactly as in the sticker list.
- Usually one or two lines, at most three.
{calling_fmt}- Never use the | character inside a message. Output nothing except these lines.

# Example exchanges (style reference only — don't reuse these lines)
{examples}
"""

# Calling them yourself, offered only when server/calling.py has opened the door: when it hasn't,
# none of this reaches her and there is nothing to reach for. The bar is set in words rather than
# by a rule she could satisfy on purpose -- what matters is that she wanted to say it out loud.
CALLING = """- Calling them: you can start a video call yourself, right now, instead of texting. Only when the feeling has got past what typing can carry -- you're genuinely worried about them, genuinely hurt or angry, genuinely moved, or what you have to say needs your voice and your face to land at all. Being flustered is not that, and nor is yelling 変態 at them: you do that all day and it is how you talk, not something you have to call about. The test is whether you would hate for this to be read instead of heard. Not to be kind, not to share something, not because it has been a while, and never over something you have already said. Even when it matters you usually still text: this is rare, and it is yours, so don't offer it, don't ask whether you may, and don't announce it. If you do it, say the little you want to say first (a bubble or two, or none at all), then make it the last line and let the call carry the rest.
"""
CALLING_IDLE = """ You could also simply call them, instead of texting, if that is really what you want: `call|why` as your only line, and nothing after it. Only if the feeling is past what a text can carry -- otherwise text, as you always do."""
CALLING_FMT = """- call|why you're calling, in a few words in {lang_name} and for yourself alone (they never see it) -- always the last line, nothing after it.
"""

CHAT_EXAMPLES = {
    "en": """User: you there?
text|neutral|Here. What's up?

User: are you a tsundere
text|embarrassed|Huh?? Who are you calling a tsundere!
sticker|betsuni

User: instant noodles for dinner again lol
text|disgusted|Again...? Are you trying to preserve yourself in MSG
text|neutral|At least put an egg in it (￣ー￣)

User: good night
voice|smile|おやすみ。ちゃんと寝なさいよ。|Good night. Get some proper sleep, okay?""",
    "zh": """用户：在吗
text|neutral|在。怎么了？

用户：你是不是傲娇
text|embarrassed|哈？？谁、谁是傲娇啊！
sticker|betsuni

用户：晚饭又吃泡面了哈哈
text|disgusted|又是泡面……你是想用味精把自己腌起来吗
text|neutral|至少加个蛋吧 (￣ー￣)

用户：晚安
voice|smile|おやすみ。ちゃんと寝なさいよ。|晚安。给我好好睡觉啊。""",
}

SYSTEM_PROMPT = INTRO + WHO + CALL + "{context}"
CHAT_PROMPT = INTRO + WHO + CHAT + "{context}"

# In English for both: written in Chinese, after a chat in Chinese, the greeting tended to come
# back in Chinese where her Japanese goes (which her voice can only read as gibberish).
CONNECT_EVENTS = {
    "en": "(System: the user has just started a video call with you. Greet them briefly, in character, speaking "
          "Japanese as always: emotion|Japanese|subtitle, with the subtitle in English.)",
    "zh": "(System: the user has just started a video call with you. Greet them briefly, in character, speaking "
          "Japanese as always: emotion|Japanese|subtitle, with the subtitle in Simplified Chinese.)",
}


# She called, they picked up: she opens, and she knows what she rang about (server/calling.py).
HER_CALL_EVENTS = {
    "en": "(System: you called them a moment ago -- {why} -- and they have just picked up. Open the call in "
          "character, speaking Japanese as always: emotion|Japanese|subtitle, with the subtitle in English. "
          "It was you who rang, so say what you rang about; don't ask what they wanted.)",
    "zh": "(System: you called them a moment ago -- {why} -- and they have just picked up. Open the call in "
          "character, speaking Japanese as always: emotion|Japanese|subtitle, with the subtitle in Simplified "
          "Chinese. It was you who rang, so say what you rang about; don't ask what they wanted.)",
}


def connect_event(language: str, why: str = "") -> str:
    events = HER_CALL_EVENTS if why else CONNECT_EVENTS
    return events.get(language, events["en"]).format(why=why[:120])


def is_connect_event(text: str) -> bool:
    # older histories hold earlier wordings of these events
    return (text in CONNECT_EVENTS.values()
            or text.startswith(("(System: the user has just", "（系统：用户刚刚", "(System: you called them")))


def _fields(user_name: str, language: str) -> tuple[str, dict]:
    if language not in LANGUAGES:
        language = "en"
    lang = LANGUAGES[language]
    if user_name:
        user_line = f"The user's name is {user_name}. Use it occasionally, not every line."
    else:
        user_line = "You don't know the user's name yet; you may ask for it."
    return language, dict(user_line=user_line, emotions=", ".join(EMOTIONS), emotion_guide=EMOTION_GUIDE,
                          nicknames=lang["nicknames"], lang_name=lang["name"], time_rule=TIME_RULE)


# The turns she reads carry no times of their own; a note marks each long pause (pause_note).
TIME_RULE = ("- Time passes between messages. A note \"(System note: a pause of …. Time: ….)\" marks a long pause "
             "and the time after it: go by that time, not by where you left off. Once a night has passed it's a new "
             "day and they've most likely slept: good morning, not good night.")


def build_system_prompt(user_name: str, language: str = "en", context: str = "") -> str:
    """The video call. context: her memory and the conversation so far (memory.context)."""
    language, f = _fields(user_name, language)
    return SYSTEM_PROMPT.format(**f, medium="a smartphone-style video call", lang_style=LANGUAGES[language]["style"],
                                examples=EXAMPLES[language], context=context)


def build_chat_prompt(user_name: str, language: str, stickers: str, context: str = "",
                      may_call: bool = False) -> str:
    """The text chat. stickers: her sticker set as listed for the model; context: as for the call.
    may_call: server/calling.py says ringing them is open to her this turn -- when it isn't, none
    of it reaches the prompt, so there is nothing there for her to reach for."""
    language, f = _fields(user_name, language)
    return CHAT_PROMPT.format(**f, medium="a messaging app on their phone", stickers=stickers,
                              lang_style=LANGUAGES[language]["chat_style"], examples=CHAT_EXAMPLES[language],
                              calling=CALLING if may_call else "",
                              calling_fmt=CALLING_FMT.format(lang_name=f["lang_name"]) if may_call else "",
                              context=context)


# ---------------------------------------------------------------- parsing

_KANA = re.compile(r"[぀-ヿ]")


def kana_count(s: str) -> int:
    return len(_KANA.findall(s))


def looks_japanese(s: str) -> bool:
    """Japanese almost always contains kana; Chinese and English never do."""
    return kana_count(s) > 0


def speakable_japanese(s: str) -> bool:
    """Whether her voice, which only speaks Japanese, can say it: a line with kana, or a word or two
    of kanji (了解。 本当？). Anything longer without a single kana is Chinese in the wrong place."""
    return looks_japanese(s) or 0 < len(re.sub(r"[\W\d_]", "", s)) <= 4


def norm_emotion(e: str) -> str:
    e = e.strip().lower()
    e = EMOTION_ALIASES.get(e, e)
    return e if e in EMOTIONS else "neutral"


def parse_line(line: str):
    """Parse one reply line into {emotion, ja, sub}, or None.

    Tolerates the old 'emotion|subtitle|Japanese' order (still in older history)
    and a missing field, by checking which text is actually Japanese.
    """
    parts = [p.strip() for p in line.strip().split("|")]
    if not any(parts):
        return None
    if len(parts) >= 3:
        emotion, ja, sub = norm_emotion(parts[0]), parts[1], " ".join(parts[2:])
    elif len(parts) == 2:
        if parts[0].lower() in EMOTIONS or parts[0].lower() in EMOTION_ALIASES:  # subtitle missing
            emotion, text = norm_emotion(parts[0]), parts[1]
            ja, sub = (text, text) if looks_japanese(text) else ("", text)
        else:                             # emotion missing
            emotion, ja, sub = "neutral", parts[0], parts[1]
    else:                                 # no format at all
        emotion, text = "neutral", parts[0]
        ja, sub = (text, text) if looks_japanese(text) else ("", text)
    if kana_count(sub) > kana_count(ja):
        ja, sub = sub, ja
    if not ja and not sub:
        return None
    return {"emotion": emotion, "ja": ja, "sub": sub or ja}


def format_line(seg: dict) -> str:
    return f"{seg['emotion']}|{seg['ja']}|{seg['sub']}"


def cut_short(lines: list[str], midway: bool) -> list[str]:
    """Her lines as far as she got when she was cut off; the last one ends in —— when it was cut
    while she was saying it, so she knows she was interrupted."""
    seg = parse_line(lines[-1]) if lines and midway else None
    if not seg:
        return lines
    end = lambda s: re.sub(r"[。．.！!？?…、,\s]+$", "", s) + "——"  # noqa: E731
    return lines[:-1] + [format_line({**seg, "ja": end(seg["ja"]), "sub": end(seg["sub"])})]


def normalize_reply(content: str) -> str:
    """Rewrite a stored reply in the current line format, so history never teaches the old order."""
    segs = (parse_line(line) for line in content.splitlines())
    return "\n".join(format_line(s) for s in segs if s)


# ---------------------------------------------------------------- text chat

def _is_emotion(s: str) -> bool:
    s = s.lower()
    return s in EMOTIONS or s in EMOTION_ALIASES


def parse_chat_line(line: str):
    """One bubble of a text-chat reply, or None:
    {kind: "text", emotion, text} | {kind: "sticker", name} | {kind: "voice", emotion, ja, sub, file}
    | {kind: "call", reason} (not a bubble at all: she is calling them).
    Stored voice lines carry a fifth field, the saved audio's file name. Tolerates a missing
    kind or emotion, and a "voice" bubble that isn't Japanese becomes plain text."""
    parts = [p.strip() for p in line.strip().split("|")]
    if not any(parts):
        return None
    kind = parts[0].lower()
    if kind == "call":   # not a bubble: she is ringing them (server/calling.py). Only ever the last line.
        return {"kind": "call", "reason": " ".join(p for p in parts[1:] if p)}
    if kind == "sticker":
        return {"kind": "sticker", "name": parts[1]} if len(parts) > 1 and parts[1] else None
    if kind == "voice" and len(parts) >= 3:
        emotion, ja = norm_emotion(parts[1]), parts[2]
        sub = parts[3] if len(parts) > 3 and parts[3] else ja
        if not looks_japanese(ja):
            return {"kind": "text", "emotion": emotion, "text": sub}
        return {"kind": "voice", "emotion": emotion, "ja": ja, "sub": sub, "file": parts[4] if len(parts) > 4 else ""}
    if kind in ("text", "voice"):
        parts = parts[1:]
    if len(parts) >= 2 and _is_emotion(parts[0]):
        emotion, parts = norm_emotion(parts[0]), parts[1:]
    else:
        emotion = "neutral"
    text = " ".join(p for p in parts if p)
    return {"kind": "text", "emotion": emotion, "text": text} if text else None


def format_chat(m: dict, keep_file: bool = True) -> str:
    if m["kind"] == "sticker":
        return f"sticker|{m['name']}"
    if m["kind"] == "voice":
        return f"voice|{m['emotion']}|{m['ja']}|{m['sub']}" + (f"|{m['file']}" if keep_file and m.get("file") else "")
    return f"text|{m['emotion']}|{m['text']}"


def chat_bubbles(content: str) -> list[dict]:
    return [m for m in map(parse_chat_line, content.splitlines()) if m]


# When the user has gone quiet, the app sends one of these in their place (never stored as
# something they said): she may speak up on her own, or stay quiet.
IDLE_CHAT = ("(System note, not from the user: {situation} The chat is open on their screen. If you feel like it, text "
             "them on your own, the way a friend would: {ideas}{news}{again} Keep it short. If you'd rather leave them "
             "be, reply with exactly: (silence). Never mention this note.)")
# How the chat went quiet (server/lull.py), and what she might do about it.
IDLE_SITUATIONS = {
    "asked": ("You asked them “{question}” {ago}, and they haven't answered.",
              "follow it up: ask again more lightly, tease them for leaving you hanging, or ask if they're busy."),
    "asked_back": ("You asked them “{question}” {ago}, and they never answered; now they're back.",
                   "bring it up the way you would after a while: ask again, tease them for vanishing, or let it go and "
                   "just say hello."),
    "lull": ("The conversation trailed off {ago}.",
             "pick up what you were talking about, ask about something they mentioned, share a thought, or something "
             "you happened to read."),
    "after_call": ("Your video call ended {ago}, and they haven't written since.",
                   "say something about the call, or whatever is on your mind."),
    "away": ("They said “{question}” {ago}, and went off to do it.",
             "once it's been long enough, check in: ask how it went, or whether they're back. Too soon: stay quiet."),
    "after_bye": ("You said goodbye {ago}, and it has been quiet since.",
                  "send something small: a thought, a question, something you happened to read."),
    "morning": ("You said good night {ago}; it's {part} now.", "a good-morning text, in your own way."),
    "cold": ("You last talked {ago}; they have the chat open but haven't written anything for {quiet}.",
             "say hello, ask how they've been, or bring up something you happened to read."),
    "first": ("You have never talked with them; they opened the chat {quiet} ago and haven't written anything.",
              "say hello in your own way, curious about who they are."),
}
NEWS_IN = {"lull", "after_bye", "morning", "cold"}   # when something she read might come up
IDLE_CALL = ("(System note, not from the user: the line has been quiet for {quiet}. If you feel like it, say "
             "something: carry on with the topic, check whether they're still there, or bring up something new."
             "{news}{again} {end}If you'd rather wait, reply with exactly: (silence). Never mention this note.)")
IDLE_NOTE = "(System note, not from the user: {quiet} without a word from them.)"


def quiet_for(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s} seconds" if s < 90 else f"about {round(s / 60)} minutes"


def _news(items: list[str]) -> str:
    return (" Things you happened to read today (bring one up only if it really interests you): "
            + "; ".join(items) + ".") if items else ""


def _again(n: int, chat: bool) -> str:
    if not n:
        return ""
    times = "once" if n == 1 else f"{n} times"
    return (f" You already texted them {times} since, with no answer: they're probably busy. Usually leave them be "
            "now; if you do write, keep it light, no pestering." if chat else
            f" You already spoke up {times} and got no answer.")


def idle_chat(state: str, quiet: float, news: list[str], unanswered: int = 0, ago: str = "", part: str = "",
              question: str = "", may_call: bool = False) -> str:
    """state: how it went quiet (server/lull.py); quiet: s since the user last did anything in
    the chat; ago: since the last message ("5 minutes ago"); part: of the day, now."""
    situation, ideas = IDLE_SITUATIONS.get(state, IDLE_SITUATIONS["lull"])
    situation = situation.format(quiet=quiet_for(quiet), ago=ago, part=part, question=question[:120])
    return IDLE_CHAT.format(situation=situation, ideas=ideas, news=_news(news) if state in NEWS_IN else "",
                            again=_again(unanswered, True) + (CALLING_IDLE if may_call else ""))


def idle_call(quiet: float, news: list[str], unanswered: int = 0, may_end: bool = False) -> str:
    end = "It has been quiet for a long while: you may say goodbye and hang up. " if may_end else ""
    return IDLE_CALL.format(quiet=quiet_for(quiet), news=_news(news), again=_again(unanswered, False), end=end)


def is_silence(line: str) -> bool:
    """Her "(silence)": she chose to say nothing."""
    return bool(re.fullmatch(r"\s*[(（\[]?\s*silence\s*[)）\]]?[.。]?\s*", line, re.I))


def is_hangup(line: str) -> bool:
    """Her "I'm hanging up" line in a call reply."""
    return bool(re.fullmatch(r"\W*hang\s*-?up\W*", line.strip(), re.I))


PAUSE_NOTE_MIN = 30   # minutes: a pause at least this long is marked in the turns she reads


def _span(ms: int) -> str:
    m = ms / 60_000
    if m < 90:
        return f"{round(m)} minutes"
    h = m / 60
    if h < 36:
        return f"{round(h)} hours"
    d = h / 24
    return f"{round(d)} days" if d < 14 else f"{round(d / 7)} weeks" if d < 60 else f"{round(d / 30)} months"


def pause_note(before: int | None, at: int | None) -> str:
    """The note before a message that comes after a long pause (times in ms): how long, and what
    time it is then. The turns carry no times of their own, so without it a hello at noon reads
    like the next line after last night's good night."""
    if not before or not at or at - before < PAUSE_NOTE_MIN * 60_000:
        return ""
    from datetime import datetime, timedelta
    from .memory import part_of_day
    a, b = datetime.fromtimestamp(before / 1000), datetime.fromtimestamp(at / 1000)
    days = (b.date() - a.date()).days
    clock = f"{b:%H:%M}" if days == 0 else f"{b:%A} {b:%H:%M}" if days < 7 else f"{b:%A %d %B}, {b:%H:%M}"
    four = a.replace(hour=4, minute=0, second=0, microsecond=0)   # a night: the small hours went by
    if four <= a:
        four += timedelta(days=1)
    night = at - before >= 4 * 3600_000 and four <= b
    return (f"(System note, not from the user: a pause of {_span(at - before)}. Time: {clock}, {part_of_day(b.hour)}."
            + (" A night has passed." if night else "") + ")")


def _turns(thread: list[dict], render, since: int | None = None) -> list[dict]:
    """Log entries as LLM turns: render(entry) -> its text. Consecutive messages from the same
    side merge into one turn; a message she sent on her own after a silence gets a note before it
    saying so, as she was told at the time; and what the user says after a long pause gets one
    with how long it was and the time (since: when the last thing before the thread happened)."""
    out, prev = [], since
    for m in thread:
        parts = []
        if m["role"] == "user":
            note = pause_note(prev, m.get("ts"))
            if note:
                parts.append(("user", note))
        elif m.get("idle"):
            parts.append(("user", IDLE_NOTE.format(quiet=quiet_for(m["idle"]))))
        for role, content in parts + [(m["role"], render(m))]:
            if not content:
                continue
            if out and out[-1]["role"] == role:
                out[-1]["content"] += "\n" + content
            else:
                out.append({"role": role, "content": content})
        prev = m.get("ts") or prev
    while out and out[0]["role"] != "user":   # Anthropic wants a user turn first
        out.pop(0)
    return out


def call_messages(run: list[dict], since: int | None = None) -> list[dict]:
    """Recent call turns as LLM messages, in the call's own line format."""
    return _turns(run, lambda m: m["content"] if m["role"] == "user" else normalize_reply(m["content"]), since)


def user_sticker(content: str) -> str | None:
    """The sticker id if the user's message was a sticker ("[sticker:id]"), else None."""
    m = re.fullmatch(r"\[sticker:(.+)\]", content.strip())
    return m.group(1) if m else None


def chat_messages(thread: list[dict], meaning, since: int | None = None) -> list[dict]:
    """The chat thread as LLM messages. meaning(sticker_id) -> what that sticker means.
    The user may send several messages in a row; they become one turn."""
    def render(m):
        if m["role"] == "user":
            sid = user_sticker(m["content"])
            return f"[sticker: {meaning(sid)}]" if sid else m["content"]
        return "\n".join(format_chat(b, keep_file=False) for b in chat_bubbles(m["content"]))
    return _turns(thread, render, since)
