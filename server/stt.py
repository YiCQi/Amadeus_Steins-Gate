"""Local speech-to-text (English or Chinese) with faster-whisper (loaded on first use)."""
import asyncio
import io
import threading


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


class WhisperSTT:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._model = None
        self._load_lock = threading.Lock()

    def _load(self):
        with self._load_lock:
            if self._model is not None:
                return self._model
            from faster_whisper import WhisperModel

            size = self.cfg.get("model", "small")
            device = self.cfg.get("device", "cuda")
            try:
                self._model = WhisperModel(size, device=device, compute_type="float16" if device == "cuda" else "int8")
            except Exception:
                # CUDA libraries (cuBLAS / cuDNN) missing -> fall back to CPU
                self._model = WhisperModel(size, device="cpu", compute_type="int8")
            return self._model

    def _transcribe(self, audio: bytes, language: str) -> str:
        if language not in NAMES_PROMPT:
            language = "en"
        model = self._load()
        try:
            segments, _ = model.transcribe(decode_audio(audio), language=language, vad_filter=True, beam_size=5,
                                           initial_prompt=NAMES_PROMPT[language])
            sep = "" if language == "zh" else " "
            return sep.join(s.text.strip() for s in segments).strip()
        except RuntimeError:
            # cuBLAS/cuDNN errors often only surface at inference time
            if self.cfg.get("device", "cuda") == "cpu":
                raise
            self.cfg = {**self.cfg, "device": "cpu"}
            self._model = None
            return self._transcribe(audio, language)

    async def transcribe(self, audio: bytes, language: str = "en") -> str:
        return await asyncio.to_thread(self._transcribe, audio, language)
