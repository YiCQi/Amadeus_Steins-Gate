"""Kana readings of her Japanese lines, so the browser can shape the mouth per mora.

Kanji have to be read (誰 -> だれ); pykakasi does that offline in well under a
millisecond per line once its dictionary is loaded (~0.7 s, done at startup).
"""
import threading

_kks = None
_lock = threading.Lock()


def _load():
    global _kks
    with _lock:
        if _kks is None:
            try:
                import pykakasi
                _kks = pykakasi.kakasi()
            except ImportError:
                _kks = False  # not installed: the browser falls back to the kana it can see
    return _kks


def warm():
    threading.Thread(target=_load, daemon=True).start()


def reading(text: str) -> str | None:
    """Hiragana reading with punctuation kept (it marks the pauses), or None."""
    kks = _kks if _kks is not None else _load()
    if not kks:
        return None
    return "".join(item["hira"] for item in kks.convert(text))
