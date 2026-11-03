"""Kurisu's voice from mio/amadeus (ESPnet VITS, CC-BY-4.0) as a tiny local HTTP service.

Runs in its own Python 3.12 venv (tools/voice/install_amadeus_vits.bat), because ESPnet
does not support the app's Python. server/tts.py starts it and talks to it on 127.0.0.1;
it exits when the app does (--parent).

    GET  /health                          200 once the model is loaded, else 503
    POST /tts {"text": "...", "speed": 1, "pause": 1}  -> audio/wav (> 1: slower / longer pauses)
"""
import argparse
import contextlib
import io
import json
import logging
import os
import re
import threading
import time
import warnings
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("HF_HOME", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Amadeus", "hf"))
os.environ["HF_ENDPOINT"] = "https://huggingface.co"  # a global hf-mirror endpoint only redirects
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

MODEL = "mio/amadeus"
tts = None
lock = threading.Lock()  # one synthesis at a time: it already uses all the CPU threads it gets


def load():
    global tts
    # ESPnet and friends print deprecation notes on load; they'd only clutter the app's window
    warnings.filterwarnings("ignore")
    logging.getLogger().setLevel(logging.ERROR)
    t = time.perf_counter()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import torch
        from espnet2.bin.tts_inference import Text2Speech
        torch.set_num_threads(max(1, (os.cpu_count() or 4) // 2))
        try:  # cached: don't wait on the network
            os.environ["HF_HUB_OFFLINE"] = "1"
            model = Text2Speech.from_pretrained(MODEL, device="cpu")
        except Exception:
            os.environ["HF_HUB_OFFLINE"] = "0"
            model = Text2Speech.from_pretrained(MODEL, device="cpu")
        with torch.no_grad():
            model("あ。")  # the first call is slow; pay for it now
    tts = model
    print(f"[voice] Amadeus voice ready ({time.perf_counter() - t:.1f}s)", flush=True)


# Pauses follow the meaning, not the model's dice. Left alone, VITS draws each pause's
# length at random (a comma could get 0.9 s, a sentence end no more than a comma). So a
# line is cut into units at sentence ends and at hesitations (…); each unit is synthesized
# on its own, and the pause after it depends on how it ends: a question waits, an
# exclamation snaps on, a hesitation is a thinking beat. The emotion scales all of them
# (the app sends it as `pause`) and a little randomness keeps them from sounding metronomic.
# Commas stay inside a unit, where VITS phrases them; only overlong ones are shortened.
DUR_NOISE = 0.6   # syllable-timing variety (ESPnet's default 0.8 made pauses erratic)
EDGE = 0.03       # s of silence kept at either end of a line
AFTER = {"…": 0.55, "？": 0.5, "?": 0.5, "！？": 0.4, "!?": 0.4, "！": 0.32, "!": 0.32}  # else 0.45 (。)
UNITS = re.compile(r"(?<=[。！？!?])(?![。！？!?」』）)])|(?<=…)(?!…)")  # after 。！？ runs (not inside quotes) and after …


def comma_pause(sec: float) -> float:
    """A pause VITS made inside a unit: keep its length up to 0.2 s, shrink what's beyond."""
    return sec if sec <= 0.2 else min(0.2 + (sec - 0.2) * 0.4, 0.42)


def tidy(wav, fs):
    import numpy as np
    hop = fs // 100
    n = len(wav) // hop
    if n < 3:
        return wav
    env = np.sqrt((wav[: n * hop].reshape(n, hop) ** 2).mean(1))
    voiced = env > max(env.max() * 0.03, 1e-4)
    idx = np.nonzero(voiced)[0]
    if not len(idx):
        return wav
    first, last = idx[0], idx[-1]
    pieces, cur = [], max(0, first * hop - int(EDGE * fs))
    f = first
    while f <= last:
        if voiced[f]:
            f += 1
            continue
        g = f
        while g <= last and not voiced[g]:
            g += 1
        sec = (g - f) / 100
        if comma_pause(sec) < sec:  # keep both ends of the pause, drop its middle
            keep = int(comma_pause(sec) * fs / 2)
            pieces.append(wav[cur: f * hop + keep])
            cur = g * hop - keep
        f = g
    pieces.append(wav[cur: min(len(wav), (last + 1) * hop + int(EDGE * fs))])
    fade = int(0.005 * fs)  # the cuts are in silence, but fade anyway so nothing clicks
    for p in pieces:
        if len(p) > 2 * fade:
            p[:fade] *= np.linspace(0, 1, fade)
            p[-fade:] *= np.linspace(1, 0, fade)
    return np.concatenate(pieces)


def split_units(text: str) -> list[str]:
    """Sentences and hesitations of a line; a fragment too short to say on its own (っ！, え？) joins the next."""
    out, carry = [], ""
    for s in (s for s in UNITS.split(text) if s.strip()):
        s = carry + s
        if len(re.sub(r"[。！？!?、…「」『』（）()\sっッ]", "", s)) < 2:
            carry = s
        else:
            out.append(s)
            carry = ""
    if carry:
        out[-1:] = [out[-1] + carry] if out else [carry]
    return out or [text]


def pause_after(unit: str) -> float:
    end = re.search(r"[。！？!?…]+$", unit)
    end = end.group() if end else "。"
    return AFTER.get("…" if end.endswith("…") else end[-2:], AFTER.get(end[-1], 0.45))


def synthesize(text: str, speed: float, pause: float = 1.0) -> bytes:
    import numpy as np
    import torch
    units = split_units(text)
    pieces = []
    with lock, torch.no_grad():
        for i, u in enumerate(units):
            if i:
                sec = pause_after(units[i - 1]) * pause * np.random.uniform(0.85, 1.2)
                pieces.append(np.zeros(int(sec * tts.fs), np.float32))
            say = u.rstrip("…")  # the … is the pause just added; VITS would add another
            if say.strip():
                out = tts(say, decode_conf={"alpha": float(speed), "noise_scale_dur": DUR_NOISE})
                pieces.append(tidy(out["wav"].numpy().astype(np.float32), tts.fs))
    wav = np.concatenate(pieces) if pieces else np.zeros(int(0.3 * tts.fs), np.float32)
    pcm = (np.clip(wav, -1, 1) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(tts.fs)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body=b"", mime="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self._send(200 if tts else 503, json.dumps({"ready": bool(tts)}).encode())
        else:
            self._send(404)

    def do_POST(self):
        if self.path != "/tts":
            return self._send(404)
        if not tts:
            return self._send(503, b'{"error": "loading"}')
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            text = str(body["text"]).strip()
            if not text:
                return self._send(400, b'{"error": "empty text"}')
            self._send(200, synthesize(text, body.get("speed", 1.0), body.get("pause", 1.0)), "audio/wav")
        except Exception as e:  # keep serving; report to the app
            self._send(500, json.dumps({"error": f"{type(e).__name__}: {e}"}).encode())

    def log_message(self, *args):  # quiet: the app logs requests
        pass


def alive(pid: int) -> bool:
    if os.name != "nt":
        return os.getppid() == pid
    import ctypes
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE; os.kill(pid, 0) would kill it on Windows
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        k32.CloseHandle(h)


def exit_with(pid: int):
    while alive(pid):
        time.sleep(2)
    os._exit(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9881)
    ap.add_argument("--parent", type=int, help="exit when this process does (the app)")
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)  # fails fast if one already runs
    if args.parent:
        threading.Thread(target=exit_with, args=(args.parent,), daemon=True).start()
    threading.Thread(target=load_or_quit, daemon=True).start()
    server.serve_forever()


def load_or_quit():
    try:
        load()
    except Exception as e:  # the app then falls back to its other voice at once
        print(f"[voice] Amadeus voice failed to load: {type(e).__name__}: {e}", flush=True)
        os._exit(1)


if __name__ == "__main__":
    main()
