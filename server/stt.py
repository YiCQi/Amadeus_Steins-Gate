"""Local speech-to-text (English or Chinese) with faster-whisper, loaded in the background at start.

On an NVIDIA GPU (needs the CUDA 12 cuBLAS and cuDNN 9 DLLs, see docs/using.md) a short sentence
takes about 0.1 s, so it can afford a 5-way beam search, which copes better with real mics and
accents. On the CPU it decodes greedily (beam 1): about 1 s a sentence instead of several with a
beam of 5, for much the same text. Which of the two is decided once, when the model loads."""
import asyncio
import io
import os
import re
import threading
import time

# Whisper sometimes writes a name the traditional or Japanese way; she reads simplified Chinese.
ZH_NAMES = {"岡部": "冈部", "紅莉栖": "红莉栖", "牧瀨": "牧濑", "牧瀬": "牧濑", "真由里": "真由理", "鳳凰院": "凤凰院",
            "橋田": "桥田", "比屋定": "比屋定"}


def tidy_zh(text: str) -> str:
    """Simplified names, and Chinese punctuation after Chinese characters (the GPU writes ',' where the CPU writes '，')."""
    for a, b in ZH_NAMES.items():
        text = text.replace(a, b)
    return re.sub(r"(?<=[一-鿿])[,?!]", lambda m: {",": "，", "?": "？", "!": "！"}[m.group()], text)


# Biases Whisper toward the names people will actually say to her.
# The Chinese prompt is in Simplified characters, which also nudges Whisper away from Traditional output.
NAMES_PROMPT = {
    "en": "Kurisu, Makise Kurisu, Amadeus, Okabe, Mayuri, Daru, Christina, Dr Pepper, El Psy Kongroo.",
    "zh": "红莉栖，牧濑红莉栖，Amadeus，冈部，真由理，桥田至，克里斯蒂娜，助手，胡椒博士。以下是普通话的句子。",
}


def decode_audio(data: bytes, sr: int = 16000):
    """Any container (webm/ogg/wav) -> 16 kHz mono float32.

    faster-whisper's own decoder passes av.open(metadata_errors=...), which
    newer PyAV releases no longer accept, so decode here instead.
    """
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=sr)
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def _cuda_dlls():
    """pip's nvidia-cublas-cu12 and nvidia-cudnn-cu12 put their DLLs in site-packages/nvidia/*/bin:
    make them findable, so the GPU works without touching the system PATH. Nothing if absent."""
    try:
        import nvidia
    except ImportError:
        return
    from pathlib import Path
    for base in getattr(nvidia, "__path__", []):
        for d in Path(base).glob("*/bin"):
            os.environ["PATH"] = f"{d}{os.pathsep}{os.environ.get('PATH', '')}"
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(d))


class WhisperSTT:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._model = None
        self.device = None
        self._load_lock = threading.Lock()

    def warm(self):
        """Load the model in the background, so the first thing you say doesn't wait for it."""
        if self.cfg.get("mode", "local") == "local":
            threading.Thread(target=self._load, daemon=True).start()

    def _load(self):
        with self._load_lock:
            if self._model is not None:
                return self._model
            import numpy as np
            from faster_whisper import WhisperModel

            size = self.cfg.get("model", "small")
            t = time.perf_counter()
            if self.cfg.get("device", "cuda") == "cuda":
                try:
                    _cuda_dlls()
                    model = WhisperModel(size, device="cuda", compute_type="float16")
                    # a missing cuBLAS / cuDNN only shows when it first runs: find out now, not on your first sentence
                    list(model.transcribe(np.zeros(8000, dtype=np.float32), beam_size=1)[0])
                    self._model, self.device = model, "cuda"
                except Exception as e:
                    print(f"[stt] no GPU for speech recognition ({str(e)[:80]}); using the CPU", flush=True)
            if self._model is None:
                threads = max(4, min(12, (os.cpu_count() or 8) // 2))
                self._model = WhisperModel(size, device="cpu", compute_type="int8", cpu_threads=threads)
                self.device = "cpu"
            print(f"[stt] speech recognition ready ({self.device}, {size}, {time.perf_counter() - t:.1f}s)", flush=True)
            return self._model

    def _transcribe(self, audio: bytes, language: str) -> str:
        if language not in NAMES_PROMPT:
            language = "en"
        model = self._load()
        samples = decode_audio(audio)
        t = time.perf_counter()
        segments, _ = model.transcribe(samples, language=language, vad_filter=True, beam_size=5 if self.device == "cuda" else 1,
                                       initial_prompt=NAMES_PROMPT[language])
        sep = "" if language == "zh" else " "
        text = sep.join(s.text.strip() for s in segments).strip()
        if language == "zh":
            text = tidy_zh(text)
        print(f"[stt] {time.perf_counter() - t:.2f}s for {len(samples) / 16000:.1f}s of audio ({self.device})", flush=True)
        return text

    async def transcribe(self, audio: bytes, language: str = "en") -> str:
        return await asyncio.to_thread(self._transcribe, audio, language)
