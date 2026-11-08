"""What a text means, as a vector: multilingual-e5-small (MIT; Chinese, English, Japanese and 90
more), run locally on the CPU with onnxruntime, so recall can match what was meant and not only
the words that were used.

E5's recipe: "query: " in front of what is being looked up, "passage: " in front of what is
stored ("query: " on both sides when comparing two texts of the same kind), mean pooling over
the real tokens, and unit-length vectors, so a dot product is the cosine.

The model (about 135 MB) lives outside the project, in %LOCALAPPDATA%\\Amadeus\\models, and is
fetched on first use. Without it (offline, a failed download) available() is False and recall
falls back to matching words.
"""
import hashlib
import os
import threading
import urllib.request
from pathlib import Path

import numpy as np

REPO = "https://huggingface.co/Xenova/multilingual-e5-small/resolve/main"
FILES = {   # local name: (path in the repo, sha256, size)
    "model_quantized.onnx": ("onnx/model_quantized.onnx",
                             "f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193", 118308185),
    "tokenizer.json": ("tokenizer.json", "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39", 17082730),
}
DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Amadeus" / "models" / "multilingual-e5-small"
DIM, MAX_TOKENS, PAD = 384, 512, 1
PREFIX = {"query": "query: ", "passage": "passage: "}

_lock = threading.Lock()
_model: dict = {"session": None, "tok": None, "failed": None}
_recent: dict = {}   # (kind, text) -> vector: a reply looks up the same message several times
RECENT_MAX = 128


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _fetch():
    """Download whatever is missing, checked against its known checksum."""
    DIR.mkdir(parents=True, exist_ok=True)
    for name, (remote, sha, size) in FILES.items():
        dest = DIR / name
        if dest.exists() and dest.stat().st_size == size:
            continue
        part = dest.with_suffix(dest.suffix + ".part")
        print(f"[embed] downloading {name} ({size / 1e6:.0f} MB) for memory recall ...", flush=True)
        with urllib.request.urlopen(f"{REPO}/{remote}", timeout=60) as r, part.open("wb") as f:
            while block := r.read(1 << 20):
                f.write(block)
        if _sha256(part) != sha:
            part.unlink(missing_ok=True)
            raise OSError(f"{name}: checksum mismatch")
        part.replace(dest)


def _load():
    with _lock:
        if _model["session"] is not None or _model["failed"]:
            return
        try:
            import onnxruntime as ort
            from tokenizers import Tokenizer
            _fetch()
            # a good neighbour to her voice, which also runs on the CPU: two threads, and none
            # left spinning after each use (onnxruntime's default keeps cores busy-waiting)
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 2
            opts.inter_op_num_threads = 1
            opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
            opts.add_session_config_entry("session.inter_op.allow_spinning", "0")
            _model["session"] = ort.InferenceSession(str(DIR / "model_quantized.onnx"), opts,
                                                     providers=["CPUExecutionProvider"])
            tok = Tokenizer.from_file(str(DIR / "tokenizer.json"))
            tok.enable_truncation(MAX_TOKENS)
            tok.enable_padding(pad_id=PAD, pad_token="<pad>")
            _model["tok"] = tok
        except Exception as e:   # no model: recall matches words only
            _model["failed"] = f"{type(e).__name__}: {e}"
            print(f"[embed] not available ({_model['failed']}); memory recall will match words only", flush=True)


def warm():
    """Load (and if need be download) the model in the background, at startup."""
    threading.Thread(target=_load, daemon=True).start()


def available() -> bool:
    _load()
    return _model["session"] is not None


def encode(texts: list[str], kind: str = "passage", batch: int = 32) -> np.ndarray:
    """(len(texts), 384) unit vectors. kind: "query" or "passage"."""
    if not texts:
        return np.zeros((0, DIM), np.float32)
    if len(texts) == 1 and (kind, texts[0]) in _recent:
        return _recent[(kind, texts[0])][None, :]
    if not available():
        raise RuntimeError(_model["failed"] or "embedding model not loaded")
    if len(texts) == 1:
        v = encode_batch(texts, kind)
        if len(_recent) >= RECENT_MAX:
            _recent.pop(next(iter(_recent)))
        _recent[(kind, texts[0])] = v[0]
        return v
    return encode_batch(texts, kind, batch)


def encode_batch(texts: list[str], kind: str, batch: int = 32) -> np.ndarray:
    """The model itself, a batch at a time (encode() remembers recent single texts)."""
    out = []
    for i in range(0, len(texts), batch):
        enc = _model["tok"].encode_batch([PREFIX[kind] + t for t in texts[i:i + batch]])
        ids = np.array([e.ids for e in enc], np.int64)
        mask = np.array([e.attention_mask for e in enc], np.int64)
        hidden = _model["session"].run(None, {"input_ids": ids, "attention_mask": mask,
                                              "token_type_ids": np.zeros_like(ids)})[0]
        m = mask[..., None].astype(np.float32)
        pooled = (hidden * m).sum(1) / np.maximum(m.sum(1), 1e-9)          # mean over real tokens
        out.append(pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12))
    return np.concatenate(out).astype(np.float32)
