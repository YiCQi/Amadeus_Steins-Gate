"""Amadeus backend: serves the UI and proxies LLM / TTS / STT."""
import asyncio
import itertools
import json
import time
from urllib.parse import quote

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, kana, persona
from .llm import LLMError, stream_chat
from .stt import WhisperSTT
from .tts import TTSError, Voice, speakable

cfg = config.load_config()
tts = Voice(cfg.get("tts", {}))
tts.warm()
stt = WhisperSTT(cfg.get("stt", {}))
MAX_HISTORY = int(cfg.get("llm", {}).get("max_history", 30))
kana.warm()

app = FastAPI(title="Amadeus")


# ---------- conversation history ----------

def load_history() -> list[dict]:
    try:
        return json.loads(config.HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def save_history(history: list[dict]):
    config.DATA_DIR.mkdir(exist_ok=True)
    config.HISTORY_PATH.write_text(json.dumps(history[-MAX_HISTORY * 2:], ensure_ascii=False, indent=1), encoding="utf-8")


def history_for_display(history: list[dict]) -> list[dict]:
    out = []
    for m in history:
        if m["role"] == "user":
            if not persona.is_connect_event(m["content"]):
                out.append({"role": "user", "sub": m["content"]})
        else:
            for line in m["content"].splitlines():
                seg = persona.parse_line(line)
                if seg:
                    out.append({"role": "assistant", **seg})
    return out


# ---------- API ----------

class ChatIn(BaseModel):
    message: str | None = None
    event: str | None = None  # "connect" when a new call starts


@app.post("/api/chat")
async def chat(body: ChatIn):
    settings = config.load_settings()
    if not settings.get("model"):
        raise HTTPException(400, "No model configured. Open settings and enter your API details.")

    language = settings.get("language", "en")
    user_text = persona.connect_event(language) if body.event == "connect" else (body.message or "").strip()
    if not user_text:
        raise HTTPException(400, "Empty message")

    history = load_history()
    # Keep user/assistant strictly alternating (Anthropic requires it).
    if history and history[-1]["role"] == "user":
        history.pop()
    history = [m if m["role"] == "user" else {**m, "content": persona.normalize_reply(m["content"])}
               for m in history[-MAX_HISTORY * 2:]]
    messages = history + [{"role": "user", "content": user_text}]
    system = persona.build_system_prompt(settings.get("user_name", ""), language)

    # A new turn makes any voice still being synthesised for the previous one stale.
    for task in voices.values():
        task.cancel()
    voices.clear()

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def events():
        t0 = time.perf_counter()
        timing: dict[str, float] = {}
        mark = lambda key: timing.setdefault(key, round(time.perf_counter() - t0, 2))  # noqa: E731
        line, full = "", ""
        early = None  # voice id already started for the line being streamed

        def finish(text: str):
            """A whole line has arrived: make sure it is voiced, then send its subtitle."""
            nonlocal early
            seg = persona.parse_line(text)
            vid, early = early, None
            if not seg:
                return
            mark("first_line")
            if vid is None:
                vid = start_voice(seg["ja"], seg["emotion"])
                yield sse("voice", {"id": vid, "emotion": seg["emotion"], "ja": seg["ja"], "kana": kana.reading(speakable(seg["ja"]))})
            yield sse("segment", {"id": vid, "sub": seg["sub"]})

        try:
            async for chunk in stream_chat(settings, system, messages):
                mark("first_token")
                line += chunk
                full += chunk
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
        save_history(messages + [{"role": "assistant", "content": persona.normalize_reply(full)}])
        yield sse("done", {"timing": timing})

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---------- voice jobs: started from the chat stream, fetched by id ----------

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
    return history_for_display(load_history())


@app.post("/api/reset")
async def reset():
    save_history([])
    return {"ok": True}


@app.get("/api/status")
async def status():
    s = config.load_settings()
    return {
        "llm_ready": bool(s.get("model")) and (bool(s.get("api_key")) or s.get("provider") == "openai"),
        "tts_enabled": tts.enabled,
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
