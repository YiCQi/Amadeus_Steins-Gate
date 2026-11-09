"""Amadeus backend: serves the UI and proxies LLM / TTS / STT.

Calls and the text chat share one memory (memory.py): every message goes into one timeline,
and both see what was said in the other.
"""
import asyncio
import bisect
import io
import itertools
import json
import random
import re
import time
import uuid
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, embed, kana, lull, memory, news, notes, persona, stickers
from .llm import LLMError, stream_chat
from .stt import WhisperSTT
from .tts import TTSError, Voice, speakable

cfg = config.load_config()
tts = Voice(cfg.get("tts", {}))
tts.warm()
stt = WhisperSTT(cfg.get("stt", {}))
stt.warm()     # speech recognition loads in the background, ready for the first thing you say
kana.warm()
embed.warm()   # memory recall by meaning (server/embed.py); fetched on first run

app = FastAPI(title="Amadeus")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sticker_meaning(sid: str) -> str:
    """What a sticker conveys, its mood. The catalogue's notes in brackets on when to send it
    are for her choosing hers, not part of what one means when you send it."""
    s = stickers.find(sid or "")
    if not s:
        return sid or ""
    return re.sub(r"\s*[（(][^）)]*[）)]", "", s["meaning"]).strip() or s["meaning"]


CHAT_MAX = 3   # bubbles in one chat reply, at most (she's told one or two; this is the backstop)
CALL_MAX = 5   # lines in one spoken reply, at most (room for an explanation when asked)


def need_model() -> dict:
    settings = config.load_settings()
    if not settings.get("model"):
        raise HTTPException(400, "No model configured. Open settings and enter your API details.")
    return settings


_background: set[asyncio.Task] = set()


def remember_later(settings: dict, language: str):
    """After a reply: let her look after her long-term memory (memory.maintain), in the background."""
    async def summarize(system: str, text: str) -> str:
        out = ""
        async for chunk in stream_chat({**settings, "temperature": 0.3}, system, [{"role": "user", "content": text}]):
            out += chunk
        return out

    task = asyncio.create_task(memory.maintain(summarize, language))
    _background.add(task)
    task.add_done_callback(_background.discard)


# ---------- the video call ----------

async def idle_news(chance: float) -> list[str]:
    """Now and then, a few headlines she might bring up when it goes quiet (server/news.py)."""
    return await news.headlines(cfg.get("news", {})) if random.random() < chance else []


class ChatIn(BaseModel):
    message: str | None = None
    event: str | None = None  # "connect" when a new call starts; "idle" when the line has gone quiet
    idle: float = 0           # for how long (s)
    page: str = ""            # which open page this is: a new turn there cancels only that page's lines
    turn: str = ""            # the page's id for this turn, so it can be taken back ...
    retract: str = ""         # ... by the next one: the turn whose message this one repeats with the rest


# Turns taken back while their reply may still be running: whatever it was, it didn't happen.
retracted: deque[str] = deque(maxlen=50)


@app.post("/api/chat")
async def chat(body: ChatIn):
    settings = need_model()
    language = settings.get("language", "en")
    connect, idle = body.event == "connect", body.event == "idle"
    if body.retract:
        # hands-free: they paused, then went on talking before she said a word; this message has it all
        retracted.append(body.retract)
        try:
            n = memory.retract("call", body.retract)
            print(f"[call] went on talking after a pause: the first part is sent again with the rest ({n} log entries taken back)", flush=True)
        except OSError as e:   # the log briefly locked (an antivirus scan, say): the first part just stays in it
            print(f"[call] couldn't take back the first part of what they said ({e}); it stays in the log", flush=True)
    if connect:
        user_text = persona.connect_event(language)
    elif idle:   # not something the user said: a note that lets her speak up, or not
        n = memory.unanswered("call")
        user_text = persona.idle_call(body.idle, await idle_news(0.3), n, may_end=n >= 2 or body.idle >= 150)
    else:
        user_text = (body.message or "").strip()
    if not user_text:
        raise HTTPException(400, "Empty message")

    mem, recent = memory.context("call", "" if connect or idle else user_text, sticker_meaning)
    said = {"role": "user", "content": user_text, **({} if idle else {"ts": memory.now_ms()})}   # idle notes say how long themselves
    messages = persona.call_messages(recent + [said], since=memory.last_before(recent))
    system = persona.build_system_prompt(settings.get("user_name", ""), language, context=mem)
    if connect:
        memory.add("call", "event", kind="call_start")
    elif not idle:
        memory.add("call", "user", user_text, **({"turn": body.turn} if body.turn else {}))

    # A new turn makes any voice still being synthesised for this page's previous one stale. Another
    # page's lines are left alone: a second tab (or browser) with the app open mustn't silence this one.
    mine = [vid for vid, (page, _) in voices.items() if not body.page or page in ("", body.page)]
    unfinished = sum(not voices[vid][1].done() for vid in mine)
    for vid in mine:
        voices.pop(vid)[1].cancel()
    if unfinished:
        print(f"[voice] {unfinished} unfinished line(s) dropped: a newer turn ({body.event or 'message'}) came in", flush=True)

    async def events():
        t0 = time.perf_counter()
        timing: dict[str, float] = {}
        mark = lambda key: timing.setdefault(key, round(time.perf_counter() - t0, 2))  # noqa: E731
        line, kept, feelings = "", [], []
        early = None  # voice id already started for the line being streamed

        def finish(text: str):
            """A whole line has arrived: make sure it is voiced, then send its subtitle."""
            nonlocal early
            if persona.is_hangup(text):
                yield sse("hangup", {})
                return
            seg = persona.parse_line(text)
            vid, early = early, None
            if not seg or persona.is_silence(seg["sub"]):   # "(silence)": she let the quiet be
                return
            if len(kept) >= CALL_MAX:   # enough said for one turn: the rest goes unsaid
                return
            mark("first_line")
            kept.append(persona.format_line(seg))
            feelings.append(seg["emotion"])
            if vid is None:
                # Chinese written where the Japanese goes: her voice would read it as gibberish
                say = seg["ja"] if persona.speakable_japanese(seg["ja"]) else ""
                vid = start_voice(say, seg["emotion"], body.page)
                yield sse("voice", {"id": vid, "emotion": seg["emotion"], "ja": say, "kana": kana.reading(speakable(say)) if say else ""})
            yield sse("segment", {"id": vid, "sub": seg["sub"]})

        try:
            async for chunk in stream_chat(settings, system, messages):
                mark("first_token")
                line += chunk
                while "\n" in line:
                    done, line = line.split("\n", 1)
                    for ev in finish(done):
                        yield ev
                # "emotion|Japanese|" is enough to start the voice; the subtitle can follow.
                if early is None and line.count("|") >= 2 and len(kept) < CALL_MAX:
                    emotion, ja = line.split("|")[:2]
                    ja = ja.strip()
                    if persona.looks_japanese(ja):
                        mark("first_voice_text")
                        early = start_voice(ja, persona.norm_emotion(emotion), body.page)
                        yield sse("voice", {"id": early, "emotion": persona.norm_emotion(emotion), "ja": ja,
                                            "kana": kana.reading(speakable(ja))})
            for ev in finish(line):
                yield ev
        except LLMError as e:
            yield sse("error", {"message": str(e)})
            return
        except Exception as e:  # network errors etc.
            yield sse("error", {"message": f"{type(e).__name__}: {e}"})
            return
        mark("total")
        timing.setdefault("first_voice_text", timing.get("first_line"))
        print("[timing]", "  ".join(f"{k}={v}s" for k, v in timing.items()), flush=True)
        if body.turn and body.turn in retracted:   # taken back while this was being written
            return
        if kept:
            memory.add("call", "assistant", "\n".join(kept), **({"idle": round(body.idle)} if idle else {}))
            yield sse("mood", {"value": memory.feel(feelings)})
        yield sse("done", {"timing": timing})
        if kept or not idle:
            remember_later(settings, language)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class CallEndIn(BaseModel):
    seconds: float = 0
    by: str = "user"   # who hung up: "user" or "kurisu"


@app.post("/api/call/end")
async def call_end(body: CallEndIn):
    """The call is over: it goes into her memory, and into the chat as a call record."""
    e = memory.add("call", "event", kind="call_end", seconds=round(body.seconds), by="kurisu" if body.by == "kurisu" else "user")
    return shown(e)[0]


# ---------- text chat: text bubbles, her stickers, the odd Japanese voice message ----------

class TextIn(BaseModel):
    message: str | None = None
    messages: list[str] | None = None   # several sent while she was still replying: one turn
    sticker: str | None = None          # the user sent one of her stickers (its id)
    event: str | None = None            # "idle": the user has been quiet a while, she may text first
    idle: float = 0                     # for how long (s)


def wav_seconds(audio: bytes) -> float | None:
    try:
        with wave.open(io.BytesIO(audio)) as w:
            return round(w.getnframes() / w.getframerate(), 2)
    except (wave.Error, EOFError):
        return None


async def record_voice(ja: str, emotion: str) -> tuple[str | None, float | None]:
    """Synthesise a voice message and keep it (data/voice/), so it can be replayed later."""
    if not ja or not tts.enabled:
        return None, None
    try:
        audio, mime, _ = await asyncio.wait_for(tts.synthesize(ja, emotion), 45)
    except (TTSError, asyncio.TimeoutError):
        return None, None
    config.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex[:12]}.{'wav' if mime == 'audio/wav' else 'mp3'}"
    (config.VOICE_DIR / name).write_bytes(audio)
    # keep the newest few hundred; older messages scroll out of the chat's memory anyway
    for old in sorted(config.VOICE_DIR.iterdir(), key=lambda p: p.stat().st_mtime)[:-300]:
        old.unlink(missing_ok=True)
    return name, wav_seconds(audio) if name.endswith(".wav") else None


def bubble_out(b: dict) -> dict | None:
    """A bubble as the page shows it; None for a sticker that doesn't exist."""
    if b["kind"] == "sticker":
        s = stickers.find(b["name"])
        return {"kind": "sticker", "id": s["id"], "url": s["url"], "meaning": s["meaning"]} if s else None
    if b["kind"] == "voice":
        return {"kind": "voice", "emotion": b["emotion"], "ja": b["ja"], "sub": b["sub"], "file": b.get("file", "")}
    return {"kind": "text", "emotion": b["emotion"], "text": b["text"]}


_nudge = {"at": 0.0}   # when she last began texting first: one at a time, even with the chat open twice


def quiet_stream(state: str) -> StreamingResponse:
    """She didn't text first after all (not due yet, or already doing it): nothing to show."""
    return StreamingResponse(iter([sse("done", {"skipped": state})]), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/api/text/lull")
async def text_lull():
    """Whether, and in how many seconds of quiet, she might text first (server/lull.py)."""
    p = lull.plan()
    return {"state": p["state"], "wait": p["wait"]}


@app.post("/api/text")
async def text_chat(body: TextIn):
    settings = need_model()
    language = settings.get("language", "en")
    idle = body.event == "idle"
    if idle:
        plan = lull.plan()
        if time.monotonic() - _nudge["at"] < 120 or not lull.due_now(plan):
            return quiet_stream(plan["state"])
        _nudge["at"] = time.monotonic()
        sent = []
    elif body.sticker:
        s = stickers.find(body.sticker)
        if not s:
            raise HTTPException(400, "Unknown sticker")
        sent = [f"[sticker:{s['id']}]"]
    else:
        sent = [m.strip() for m in (body.messages or [body.message or ""]) if m and m.strip()]
        if not sent:
            raise HTTPException(400, "Empty message")

    # What to recall by: what they wrote. A sticker is a tone, not a topic: recalling by what it
    # shows (a Dr Pepper one, say) would hand her memories about that and invite her to dwell on it.
    query = " ".join(m for m in sent if not persona.user_sticker(m))
    mem, recent = memory.context("chat", query, sticker_meaning)
    if idle:   # not something the user said: a note that lets her text first, or not
        headlines = await idle_news(0.5) if plan["state"] in persona.NEWS_IN else []
        note = persona.idle_chat(plan["state"], body.idle, headlines, memory.unanswered("chat"),
                                 ago=memory.ago(plan["since"]) if plan.get("since") else "",
                                 part=memory.part_of_day(datetime.now().hour), question=plan.get("question", ""))
        turn = [{"role": "user", "content": note}]
    else:   # with the time, so a long pause before it can be marked
        turn = [{"role": "user", "content": m, "ts": memory.now_ms()} for m in sent]
    messages = persona.chat_messages(recent + turn, sticker_meaning, since=memory.last_before(recent))
    system = persona.build_chat_prompt(settings.get("user_name", ""), language, stickers.prompt_list(), context=mem)
    lids = [memory.add("chat", "user", m)["id"] for m in sent]

    async def events():
        line, kept, feelings = "", [], []
        if lids:   # where your messages are in the timeline, for the page's paging
            yield sse("sent", {"lids": lids})

        async def bubble(raw: str):
            b = persona.parse_chat_line(raw)
            if not b or (b["kind"] == "text" and persona.is_silence(b["text"])):
                return None
            if b["kind"] == "voice":
                b["file"], secs = await record_voice(b["ja"], b["emotion"])
                if not b["file"]:   # no voice to be had: say it in writing instead
                    b = {"kind": "text", "emotion": b["emotion"], "text": b["sub"]}
            out = bubble_out(b)
            if out is None:
                return None
            if out["kind"] == "voice":
                out["seconds"] = secs
            if b["kind"] != "sticker":
                feelings.append(b["emotion"])
            kept.append(persona.format_chat(b if b["kind"] != "sticker" else {**b, "name": out["id"]}))
            return sse("message", out)

        try:
            chunks = stream_chat(settings, system, messages)
            try:
                async for chunk in chunks:
                    line += chunk
                    while "\n" in line and len(kept) < CHAT_MAX:
                        done, line = line.split("\n", 1)
                        ev = await bubble(done)
                        if ev:
                            yield ev
                    if len(kept) >= CHAT_MAX:   # enough said: the model is stopped, the rest neither shown nor paid for
                        line = ""
                        break
            finally:
                await chunks.aclose()
            ev = await bubble(line) if len(kept) < CHAT_MAX else None
            if ev:
                yield ev
        except LLMError as e:
            yield sse("error", {"message": str(e)})
            return
        except Exception as e:  # network errors etc.
            yield sse("error", {"message": f"{type(e).__name__}: {e}"})
            return
        if kept:
            e = memory.add("chat", "assistant", "\n".join(kept), **({"idle": round(body.idle)} if idle else {}))
            yield sse("saved", {"lid": e["id"]})
            yield sse("mood", {"value": memory.feel(feelings)})
        elif idle:   # she chose to stay quiet: that holds until one of you writes, rather than asking her again
            lull.decline()
        yield sse("done", {})
        if kept or not idle:
            remember_later(settings, language)

    async def released(stream):
        try:
            async for ev in stream:
                yield ev
        finally:          # done, or the page gave up on it (you wrote first)
            _nudge["at"] = 0.0

    return StreamingResponse(released(events()) if idle else events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# ---------- the chat's history: a page at a time, and search ----------

def chat_entries() -> list[dict]:
    """What the chat shows, oldest first: its messages and the records of finished calls."""
    return [m for m in memory.log() if (m["ch"] == "chat" and m["role"] in ("user", "assistant"))
            or (m["role"] == "event" and m.get("kind") == "call_end")]


def shown(m: dict) -> list[dict]:
    """One timeline entry as the chat shows it (one reply of hers can be several bubbles).
    lid: the entry's id, which the page pages and jumps by."""
    base = {"lid": m["id"], "ts": m.get("ts")}
    if m["role"] == "event":
        return [{**base, "role": "event", "kind": "call", "seconds": m.get("seconds", 0), "by": m.get("by")}]
    if m["role"] == "user":
        sid = persona.user_sticker(m["content"])
        s = stickers.find(sid) if sid else None
        return [{**base, "role": "user", **({"kind": "sticker", "id": s["id"], "url": s["url"], "meaning": s["meaning"]}
                                            if s else {"kind": "text", "text": m["content"]})}]
    bubbles = [b for b in map(bubble_out, persona.chat_bubbles(m["content"])) if b]
    return [{**base, "role": "assistant", "bi": i, **b} for i, b in enumerate(bubbles)]   # bi: which bubble


def day_start(date: str) -> int:
    """"2026-11-03" -> that day's first millisecond, local time."""
    try:
        return int(datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)
    except ValueError:
        raise HTTPException(400, "Dates look like 2026-11-03")


@app.get("/api/text/history")
async def text_history(before: int | None = None, after: int | None = None, around: int | None = None,
                       date: str | None = None, limit: int = 40):
    """A page of the chat, `limit` entries: the newest; those before or after an entry id (scrolling);
    those around one (a search result); or from the first message of a day on."""
    entries = chat_entries()
    ids, n, limit = [m["id"] for m in entries], len(entries), max(1, min(limit, 200))
    target = None
    if date:
        start = day_start(date)
        i = next((i for i, m in enumerate(entries) if m["ts"] >= start), n - 1)   # nothing since: the newest
        lo = max(0, min(i, n - limit))
        hi = min(n, lo + limit)
        target = ids[i] if entries else None
    elif around is not None:
        i = bisect.bisect_left(ids, around)
        lo = max(0, min(i - limit // 3, n - limit))
        hi = min(n, lo + limit)
        target = around
    elif before is not None:
        hi = bisect.bisect_left(ids, before)
        lo = max(0, hi - limit)
    elif after is not None:
        lo = bisect.bisect_right(ids, after)
        hi = min(n, lo + limit)
    else:
        lo, hi = max(0, n - limit), n
    return {"items": [it for m in entries[lo:hi] for it in shown(m)], "more_before": lo > 0,
            "more_after": hi < n, "target": target}


def searchable(it: dict) -> str:
    return " ".join(filter(None, [it.get("text"), it.get("sub"), it.get("ja"), it.get("meaning")]))


@app.get("/api/text/search")
async def text_search(q: str = "", kind: str = "", date: str = "", limit: int = 80):
    """Chat messages with all of q's words, newest first; kind: only "voice" messages,
    "sticker"s or "call" records; date: only that day. Each with the entry id to jump to."""
    words = q.lower().split()
    if not (words or kind or date):
        return {"items": [], "total": 0}
    span = (day_start(date), day_start(date) + 86400_000) if date else None
    items, total = [], 0
    for m in reversed(chat_entries()):
        if span and not span[0] <= m["ts"] < span[1]:
            continue
        for it in reversed(shown(m)):
            if kind and it.get("kind") != kind:
                continue
            text = searchable(it)
            if words and not all(w in text.lower() for w in words):
                continue
            total += 1
            if len(items) < limit:
                items.append(it)
    return {"items": items, "total": total}


@app.get("/api/stickers")
async def sticker_list():
    return stickers.load()


@app.get("/api/voicemsg/{name}")
async def voice_message(name: str):
    path = config.VOICE_DIR / Path(name).name   # a bare file name, nothing else
    if not path.is_file():
        raise HTTPException(404, "No such voice message")
    return FileResponse(path, media_type="audio/wav" if path.suffix == ".wav" else "audio/mpeg")


# ---------- her memory: what she remembers, and forgetting (sealing) things ----------

class MemoryIn(BaseModel):
    kind: str        # "note", "episode", "fact" (before notes), or "recent" (the conversation not yet sorted)
    id: int | None = None


@app.get("/api/memory")
async def memory_overview():
    return memory.overview()


@app.post("/api/memory/forget")
async def memory_forget(body: MemoryIn):
    if not memory.forget(body.kind, body.id):
        raise HTTPException(404, "Nothing to forget there")
    settings = config.load_settings()
    if body.kind == "note" and settings.get("model"):   # rewrite older memories without it now, not after the next reply
        remember_later(settings, settings.get("language", "en"))
    return memory.overview()


@app.post("/api/memory/outdated")
async def memory_outdated(body: MemoryIn):
    """A note that isn't true any more: retired (unlike forgetting, nothing is blocked)."""
    if not notes.no_longer_true(body.id):
        raise HTTPException(404, "No such note")
    return memory.overview()


class UndoIn(BaseModel):
    change: int      # the number of a change in what she knows (notes.py changes.jsonl)


@app.post("/api/memory/undo")
async def memory_undo(body: UndoIn):
    if not notes.undo(body.change):
        raise HTTPException(404, "That change can't be undone (forgetting is undone by restoring)")
    return memory.overview()


@app.post("/api/memory/restore")
async def memory_restore(body: MemoryIn):
    if not memory.restore(body.kind, body.id):
        raise HTTPException(404, "Nothing sealed there")
    return memory.overview()


# ---------- voice jobs: started from the call stream, fetched by id ----------

voices: dict[str, tuple[str, asyncio.Task]] = {}   # line id -> (the page that asked, its synthesis)
_voice_ids = itertools.count(1)


async def _timed_synthesis(text: str, emotion: str):
    t = time.perf_counter()
    audio, media_type, engine = await tts.synthesize(text, emotion)
    return audio, media_type, engine, time.perf_counter() - t


def start_voice(ja: str, emotion: str, page: str = "") -> str:
    vid = f"v{next(_voice_ids)}"
    if ja and tts.enabled:
        voices[vid] = (page, asyncio.create_task(_timed_synthesis(ja, emotion)))
    return vid


@app.get("/api/voice/{vid}")
async def voice(vid: str):
    entry = voices.get(vid)
    if entry is None:
        raise HTTPException(404, "No voice for this line (expired, or TTS disabled)")
    task = entry[1]
    try:
        # shield: if the browser goes away, let synthesis finish instead of cancelling it
        audio, media_type, engine, secs = await asyncio.shield(task)
    except asyncio.CancelledError:
        raise HTTPException(410, "Voice cancelled by a newer message")
    except Exception as e:   # say why in the window: the page only shows it once
        print(f"[voice] line {vid} failed: {type(e).__name__}: {e}", flush=True)
        raise HTTPException(502, str(e) if isinstance(e, TTSError) else f"{type(e).__name__}: {e}")
    finally:
        if task.done():
            voices.pop(vid, None)
    return Response(audio, media_type=media_type,
                    headers={"X-Voice-Engine": engine, "X-TTS-Seconds": f"{secs:.2f}"})


class TTSIn(BaseModel):
    text: str
    emotion: str = "neutral"


@app.post("/api/tts")
async def synthesize(body: TTSIn):
    if not tts.enabled:
        raise HTTPException(503, "TTS disabled in config.toml")
    try:
        audio, media_type, engine = await tts.synthesize(body.text, body.emotion)
    except TTSError as e:
        print(f"[voice] a line asked for again failed too: {e}", flush=True)
        raise HTTPException(502, str(e))
    headers = {"X-Voice-Engine": engine, "X-Kana": quote(kana.reading(speakable(body.text)) or "")}  # headers are ASCII
    return Response(audio, media_type=media_type, headers=headers)


@app.post("/api/stt")
async def transcribe(audio: UploadFile = File(...)):
    data = await audio.read()
    try:
        text = await stt.transcribe(data, config.load_settings().get("language", "en"))
    except ImportError:
        raise HTTPException(501, "faster-whisper is not installed")
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")
    return {"text": text}


@app.get("/api/settings")
async def get_settings():
    return config.public_settings(config.load_settings())


@app.post("/api/settings")
async def set_settings(update: dict):
    return config.public_settings(config.save_settings(update))


@app.get("/api/history")
async def get_history():
    """What was said on calls, for the call's log panel."""
    out = []
    for m in memory.log():
        if m["ch"] != "call" or m["role"] == "event":
            continue
        if m["role"] == "user":
            out.append({"role": "user", "sub": m["content"]})
        else:
            out += [{"role": "assistant", **seg} for seg in map(persona.parse_line, m["content"].splitlines()) if seg]
    return out[-300:]


@app.get("/api/status")
async def status():
    s = config.load_settings()
    return {
        "llm_ready": bool(s.get("model")) and (bool(s.get("api_key")) or s.get("provider") == "openai"),
        "tts_enabled": tts.enabled,
        "mood": round(memory.mood(), 3),
        **(await tts.status() if tts.enabled else {"engine": None, "sovits_ready": False}),
    }


WEB = config.ROOT / "web"


@app.get("/")
async def index():
    return FileResponse(WEB / "index.html")


@app.middleware("http")
async def revalidate_static(request, call_next):
    # Without this, browsers keep running cached JS for a while after an update.
    # Revalidating costs a 304 per file when nothing changed.
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


app.mount("/", StaticFiles(directory=WEB), name="web")
