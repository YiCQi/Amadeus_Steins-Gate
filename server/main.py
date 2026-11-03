"""Amadeus backend: serves the UI and proxies LLM / TTS / STT.

Calls and the text chat share one memory (memory.py): every message goes into one timeline,
and both see what was said in the other.
"""
import asyncio
import io
import itertools
import json
import time
import uuid
import wave
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, kana, memory, persona, stickers
from .llm import LLMError, stream_chat
from .stt import WhisperSTT
from .tts import TTSError, Voice, speakable

cfg = config.load_config()
tts = Voice(cfg.get("tts", {}))
tts.warm()
stt = WhisperSTT(cfg.get("stt", {}))
kana.warm()

app = FastAPI(title="Amadeus")


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def sticker_meaning(sid: str) -> str:
    s = stickers.find(sid or "")
    return s["meaning"] if s else (sid or "")


def need_model() -> dict:
    settings = config.load_settings()
    if not settings.get("model"):
        raise HTTPException(400, "No model configured. Open settings and enter your API details.")
    return settings


_background: set[asyncio.Task] = set()


def remember_later(settings: dict, language: str):
    """After a reply: let her consolidate older conversation into long-term memory, in the background."""
    async def summarize(system: str, text: str) -> str:
        out = ""
        async for chunk in stream_chat({**settings, "temperature": 0.3}, system, [{"role": "user", "content": text}]):
            out += chunk
        return out

    task = asyncio.create_task(memory.consolidate(summarize, language))
    _background.add(task)
    task.add_done_callback(_background.discard)


# ---------- the video call ----------

class ChatIn(BaseModel):
    message: str | None = None
    event: str | None = None  # "connect" when a new call starts


@app.post("/api/chat")
async def chat(body: ChatIn):
    settings = need_model()
    language = settings.get("language", "en")
    connect = body.event == "connect"
    user_text = persona.connect_event(language) if connect else (body.message or "").strip()
    if not user_text:
        raise HTTPException(400, "Empty message")

    mem, recent = memory.context("call", "" if connect else user_text, sticker_meaning)
    messages = persona.call_messages(recent + [{"role": "user", "content": user_text}])
    system = persona.build_system_prompt(settings.get("user_name", ""), language, context=mem)
    if connect:
        memory.add("call", "event", kind="call_start")
    else:
        memory.add("call", "user", user_text)

    # A new turn makes any voice still being synthesised for the previous one stale.
    for task in voices.values():
        task.cancel()
    voices.clear()

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
            if not seg:
                return
            mark("first_line")
            kept.append(persona.format_line(seg))
            feelings.append(seg["emotion"])
            if vid is None:
                vid = start_voice(seg["ja"], seg["emotion"])
                yield sse("voice", {"id": vid, "emotion": seg["emotion"], "ja": seg["ja"], "kana": kana.reading(speakable(seg["ja"]))})
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
                if early is None and line.count("|") >= 2:
                    emotion, ja = line.split("|")[:2]
                    ja = ja.strip()
                    if persona.looks_japanese(ja):
                        mark("first_voice_text")
                        early = start_voice(ja, persona.norm_emotion(emotion))
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
        if kept:
            memory.add("call", "assistant", "\n".join(kept))
        yield sse("mood", {"value": memory.feel(feelings)})
        yield sse("done", {"timing": timing})
        remember_later(settings, language)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class CallEndIn(BaseModel):
    seconds: float = 0
    by: str = "user"   # who hung up: "user" or "kurisu"


@app.post("/api/call/end")
async def call_end(body: CallEndIn):
    """The call is over: it goes into her memory, and into the chat as a call record."""
    e = memory.add("call", "event", kind="call_end", seconds=round(body.seconds), by="kurisu" if body.by == "kurisu" else "user")
    return {"role": "event", "kind": "call", "seconds": e["seconds"], "by": e["by"], "ts": e["ts"]}


# ---------- text chat: text bubbles, her stickers, the odd Japanese voice message ----------

class TextIn(BaseModel):
    message: str | None = None
    messages: list[str] | None = None   # several sent while she was still replying: one turn
    sticker: str | None = None          # the user sent one of her stickers (its id)


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


@app.post("/api/text")
async def text_chat(body: TextIn):
    settings = need_model()
    language = settings.get("language", "en")
    if body.sticker:
        s = stickers.find(body.sticker)
        if not s:
            raise HTTPException(400, "Unknown sticker")
        sent = [f"[sticker:{s['id']}]"]
    else:
        sent = [m.strip() for m in (body.messages or [body.message or ""]) if m and m.strip()]
        if not sent:
            raise HTTPException(400, "Empty message")

    query = " ".join(sticker_meaning(persona.user_sticker(m)) if persona.user_sticker(m) else m for m in sent)
    mem, recent = memory.context("chat", query, sticker_meaning)
    turn = [{"role": "user", "content": m} for m in sent]
    messages = persona.chat_messages(recent + turn, sticker_meaning)
    system = persona.build_chat_prompt(settings.get("user_name", ""), language, stickers.prompt_list(), context=mem)
    for m in sent:
        memory.add("chat", "user", m)

    async def events():
        line, kept, feelings = "", [], []

        async def bubble(raw: str):
            b = persona.parse_chat_line(raw)
            if not b:
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
            async for chunk in stream_chat(settings, system, messages):
                line += chunk
                while "\n" in line:
                    done, line = line.split("\n", 1)
                    ev = await bubble(done)
                    if ev:
                        yield ev
            ev = await bubble(line)
            if ev:
                yield ev
        except LLMError as e:
            yield sse("error", {"message": str(e)})
            return
        except Exception as e:  # network errors etc.
            yield sse("error", {"message": f"{type(e).__name__}: {e}"})
            return
        if kept:
            memory.add("chat", "assistant", "\n".join(kept))
        yield sse("mood", {"value": memory.feel(feelings)})
        yield sse("done", {})
        remember_later(settings, language)

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/text/history")
async def text_history():
    """The chat as the page shows it: chat messages and call records, from the shared timeline."""
    out = []
    for m in memory.log():
        ts = m.get("ts")
        if m["role"] == "event":
            if m.get("kind") == "call_end":
                out.append({"role": "event", "kind": "call", "seconds": m.get("seconds", 0), "by": m.get("by"), "ts": ts})
            continue
        if m["ch"] != "chat":
            continue
        if m["role"] == "user":
            sid = persona.user_sticker(m["content"])
            s = stickers.find(sid) if sid else None
            out.append({"role": "user", "ts": ts, **({"kind": "sticker", "id": s["id"], "url": s["url"]} if s else
                                                      {"kind": "text", "text": m["content"]})})
        else:
            for b in persona.chat_bubbles(m["content"]):
                shown = bubble_out(b)
                if shown:
                    out.append({"role": "assistant", "ts": ts, **shown})
    return out[-600:]


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
    kind: str        # "episode", "fact", or "recent" (the conversation not yet sorted into memories)
    id: int | None = None


@app.get("/api/memory")
async def memory_overview():
    return memory.overview()


@app.post("/api/memory/forget")
async def memory_forget(body: MemoryIn):
    if not memory.forget(body.kind, body.id):
        raise HTTPException(404, "Nothing to forget there")
    return memory.overview()


@app.post("/api/memory/restore")
async def memory_restore(body: MemoryIn):
    if not memory.restore(body.kind, body.id):
        raise HTTPException(404, "Nothing sealed there")
    return memory.overview()


# ---------- voice jobs: started from the call stream, fetched by id ----------

voices: dict[str, asyncio.Task] = {}
_voice_ids = itertools.count(1)


async def _timed_synthesis(text: str, emotion: str):
    t = time.perf_counter()
    audio, media_type, engine = await tts.synthesize(text, emotion)
    return audio, media_type, engine, time.perf_counter() - t


def start_voice(ja: str, emotion: str) -> str:
    vid = f"v{next(_voice_ids)}"
    if ja and tts.enabled:
        voices[vid] = asyncio.create_task(_timed_synthesis(ja, emotion))
    return vid


@app.get("/api/voice/{vid}")
async def voice(vid: str):
    task = voices.get(vid)
    if task is None:
        raise HTTPException(404, "No voice for this line (expired, or TTS disabled)")
    try:
        # shield: if the browser goes away, let synthesis finish instead of cancelling it
        audio, media_type, engine, secs = await asyncio.shield(task)
    except asyncio.CancelledError:
        raise HTTPException(410, "Voice cancelled by a newer message")
    except TTSError as e:
        raise HTTPException(502, str(e))
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
