"""Japanese voice.

engine = "auto"       -> GPT-SoVITS (your own Kurisu clone) when a reference clip is set up,
                         else the Amadeus VITS voice when installed, else edge-tts
engine = "gpt-sovits" -> GPT-SoVITS only
engine = "vits"       -> mio/amadeus, a ready-made Kurisu voice (ESPnet VITS, CC-BY-4.0, CPU)
engine = "edge"       -> Microsoft Edge neural voice only (free, online, not Kurisu's voice)
"""
import asyncio
import atexit
import hashlib
import os
import re
import subprocess
import time
from collections import OrderedDict
from pathlib import Path

import httpx

from .config import resolve_path


class TTSError(Exception):
    pass


_ELLIPSIS = re.compile(r"(?:…|‥|・{2,}|\.{2,})+")


def speakable(text: str) -> str:
    """What the voice should read: her line minus what makes TTS stall or stumble.

    Ellipses become one … mid-line, a hesitation the voice turns into a thinking pause;
    one that opens or ends a line goes, since the pause between lines covers it (the app
    makes that pause longer). Chinese-only characters (乐, 话 ... echoed from the user)
    have no Japanese reading and are dropped. The subtitle still shows the original line."""
    t = text.strip()
    t = _ELLIPSIS.sub("…", t)
    t = re.sub(r"^…+\s*", "", t)
    t = re.sub(r"…+(?=[！？!?。、」』）]|$)", "", t)
    t = "".join(c for c in t if not ("一" <= c <= "鿿") or _in_japanese(c))
    return re.sub(r"、{2,}", "、", t).strip("、 ") or text.strip()


def _in_japanese(ch: str) -> bool:
    try:
        ch.encode("cp932")  # Shift-JIS covers the kanji Japanese text uses
        return True
    except UnicodeEncodeError:
        return False


# Per-emotion (rate %, pitch Hz) on top of the base voice. Kept small on purpose:
# Nanami sits at ~239 Hz; +25..40 Hz pushed her to 280..300 Hz, which sounds like a
# child, while Kurisu's voice is calm and fairly low. Emotion mostly changes pace.
EDGE_PROSODY = {
    "neutral": (0, 0),
    "smile": (2, 3),
    "happy": (4, 5),
    "laugh": (6, 8),
    "annoyed": (8, 2),
    "angry": (10, 3),
    "furious": (14, 5),
    "pout": (-4, 2),
    "disgusted": (-4, -4),
    "embarrassed": (6, 6),
    "sad": (-8, -6),
    "surprised": (6, 8),
    "puzzled": (-3, 4),
    "thinking": (-6, -3),
    "smug": (-3, -2),
}


class GPTSoVITS:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.url = cfg.get("url", "http://127.0.0.1:9880").rstrip("/")
        self.refs = cfg.get("refs", {})
        self.default_ref = cfg.get("default_ref", "neutral")
        self._weights_loaded = False
        self._lock = asyncio.Lock()  # GPT-SoVITS handles one request at a time anyway

    def ref_for(self, emotion: str) -> dict:
        ref = self.refs.get(emotion) or self.refs.get(self.default_ref)
        if not ref or not ref.get("audio"):
            raise TTSError("No reference voice configured. Add [tts.refs.neutral] to config.toml.")
        path = resolve_path(ref["audio"])
        if not path.exists():
            raise TTSError(f"Reference audio not found: {path}")
        return {"audio": str(path), "text": ref.get("text", ""), "lang": ref.get("lang", "ja")}

    def has_ref(self) -> bool:
        try:
            self.ref_for(self.default_ref)
            return True
        except TTSError:
            return False

    async def _ensure_weights(self, client: httpx.AsyncClient):
        if self._weights_loaded:
            return
        for key, endpoint in (("gpt_weights", "set_gpt_weights"), ("sovits_weights", "set_sovits_weights")):
            if self.cfg.get(key):
                r = await client.get(f"{self.url}/{endpoint}", params={"weights_path": str(resolve_path(self.cfg[key]))})
                if r.status_code >= 400:
                    raise TTSError(f"{endpoint} failed: {r.text[:300]}")
        self._weights_loaded = True

    async def synthesize(self, text: str, emotion: str) -> bytes:
        ref = self.ref_for(emotion)
        body = {
            "text": text,
            "text_lang": "ja",
            "ref_audio_path": ref["audio"],
            "prompt_text": ref["text"],
            "prompt_lang": ref["lang"],
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": float(self.cfg.get("speed", 1.0)),
            "top_k": int(self.cfg.get("top_k", 15)),
            "top_p": float(self.cfg.get("top_p", 1.0)),
            "temperature": float(self.cfg.get("temperature", 1.0)),
            "media_type": "wav",
            "streaming_mode": False,
        }
        async with self._lock:
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=5)) as client:
                    await self._ensure_weights(client)
                    r = await client.post(f"{self.url}/tts", json=body)
            except httpx.ConnectError as e:
                raise TTSError(f"Cannot reach GPT-SoVITS at {self.url}. Is api_v2.py running?") from e
        if r.status_code >= 400:
            raise TTSError(f"GPT-SoVITS error {r.status_code}: {r.text[:300]}")
        return r.content

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                await client.get(self.url)
            return True
        except httpx.HTTPError:
            return False


# Per-emotion speaking speed for the VITS voice (>1 is slower); it has no pitch control.
# Barely-there on purpose: a reply is 2-3 lines with different emotions, and bigger
# differences made her speed jump from one line to the next.
VITS_SPEED = {
    "annoyed": 0.98,
    "angry": 0.97,
    "furious": 0.95,
    "laugh": 0.98,
    "surprised": 0.98,
    "sad": 1.04,
    "thinking": 1.03,
    "pout": 1.03,
    "puzzled": 1.02,
    "disgusted": 1.02,
}
# How long she pauses inside a line, by emotion (the app uses the same between lines)
PAUSE_MOOD = {"thinking": 1.3, "sad": 1.25, "puzzled": 1.2, "pout": 1.15, "smug": 1.1, "disgusted": 1.05,
              "laugh": 0.85, "surprised": 0.85, "annoyed": 0.8, "angry": 0.75, "furious": 0.65}


class AmadeusVITS:
    """mio/amadeus, served by server/vits_server.py from its own Python 3.12 venv
    (tools/voice/install_amadeus_vits.bat). Started with the app; stops with it."""

    def __init__(self, cfg: dict):
        default = Path(os.environ.get("LOCALAPPDATA", "")) / "Amadeus" / "vits-venv" / "Scripts" / "python.exe"
        self.python = Path(os.path.expandvars(cfg.get("vits_python") or str(default)))
        self.port = int(cfg.get("vits_port", 9881))
        self.url = f"http://127.0.0.1:{self.port}"
        self.speed = float(cfg.get("vits_speed", 1.0))
        self.proc = None

    @property
    def installed(self) -> bool:
        return self.python.exists()

    def start(self):
        """Launch the service in the background; loading the model takes ~5-10 s."""
        if not self.installed or (self.proc and self.proc.poll() is None):
            return
        script = Path(__file__).with_name("vits_server.py")
        # --parent: the service quits by itself if the app dies without stopping it
        self.proc = subprocess.Popen([str(self.python), str(script), "--port", str(self.port), "--parent", str(os.getpid())],
                                     stdin=subprocess.DEVNULL)
        atexit.register(self.stop)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    async def _ready(self, client: httpx.AsyncClient, timeout: float = 45):
        deadline = time.monotonic() + timeout
        while True:
            try:
                if (await client.get(f"{self.url}/health")).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self.proc is not None and self.proc.poll() is not None:
                raise TTSError("The Amadeus voice stopped; run tools\\voice\\install_amadeus_vits.bat again")
            if time.monotonic() > deadline:
                raise TTSError("The Amadeus voice is still loading")
            await asyncio.sleep(0.25)

    async def synthesize(self, text: str, emotion: str) -> bytes:
        self.start()
        speed = self.speed * VITS_SPEED.get(emotion, 1.0)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=3)) as client:
                await self._ready(client)
                r = await client.post(f"{self.url}/tts", json={"text": text, "speed": speed, "pause": PAUSE_MOOD.get(emotion, 1.0)})
        except httpx.HTTPError as e:
            raise TTSError(f"Amadeus voice unreachable: {e}") from e
        if r.status_code >= 400:
            raise TTSError(f"Amadeus voice error {r.status_code}: {r.text[:300]}")
        return r.content

    async def ping(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                return (await client.get(f"{self.url}/health")).status_code == 200
        except httpx.HTTPError:
            return False


class EdgeVoice:
    def __init__(self, cfg: dict):
        self.voice = cfg.get("edge_voice", "ja-JP-NanamiNeural")
        self.rate = int(cfg.get("edge_rate", 3))     # percent
        self.pitch = int(cfg.get("edge_pitch", -6))  # Hz; -6 puts her around 228 Hz

    @property
    def available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    async def synthesize(self, text: str, emotion: str) -> bytes:
        try:
            import edge_tts
        except ImportError as e:
            raise TTSError("edge-tts is not installed (run start.bat again to install it)") from e
        dr, dp = EDGE_PROSODY.get(emotion, (0, 0))
        comm = edge_tts.Communicate(text, self.voice, rate=f"{self.rate + dr:+d}%", pitch=f"{self.pitch + dp:+d}Hz")
        audio = b""
        try:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    audio += chunk["data"]
        except Exception as e:
            raise TTSError(f"Fallback voice failed (needs internet): {e}") from e
        if not audio:
            raise TTSError("Fallback voice returned no audio")
        return audio


class Voice:
    """Picks the engine per request and caches results."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.engine = cfg.get("engine", "auto")
        self.sovits = GPTSoVITS(cfg)
        self.vits = AmadeusVITS(cfg)
        self.edge = EdgeVoice(cfg)
        self._cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True))

    def pick(self) -> str:
        if self.engine != "auto":
            return self.engine
        if self.sovits.has_ref():
            return "gpt-sovits"
        return "vits" if self.vits.installed else "edge"

    def warm(self):
        """Start the VITS voice with the app, so it has loaded before her first line."""
        if self.enabled and self.pick() == "vits":
            self.vits.start()

    async def synthesize(self, text: str, emotion: str = "neutral") -> tuple[bytes, str, str]:
        """Returns (audio bytes, media type, engine name)."""
        text = speakable(text)
        engine = self.pick()
        key = hashlib.sha1(f"{text}\0{emotion}\0{engine}".encode()).hexdigest()
        if key in self._cache:
            self._cache.move_to_end(key)
            audio, mime = self._cache[key]
            return audio, mime, "cache"

        engines = {"gpt-sovits": (self.sovits, "audio/wav"), "vits": (self.vits, "audio/wav"), "edge": (self.edge, "audio/mpeg")}
        if engine not in engines:
            raise TTSError(f"Unknown TTS engine: {engine}")
        voice, mime = engines[engine]
        try:
            audio, name = await voice.synthesize(text, emotion), engine
        except TTSError:
            if self.engine != "auto" or engine == "edge":
                raise
            audio, mime, name = await self.edge.synthesize(text, emotion), "audio/mpeg", "edge"

        self._cache[key] = (audio, mime)
        if len(self._cache) > 64:
            self._cache.popitem(last=False)
        return audio, mime, name

    async def status(self) -> dict:
        engine = self.pick()
        sovits_ready = engine == "gpt-sovits" and await self.sovits.ping()
        if sovits_ready:
            active = "gpt-sovits"
        elif engine == "vits":
            active = "vits"  # may still be loading; the first line waits for it
        elif self.engine in ("auto", "edge") and self.edge.available:
            active = "edge"
        else:
            active = None
        return {"engine": active, "sovits_ready": sovits_ready, "vits_ready": engine == "vits" and await self.vits.ping()}
