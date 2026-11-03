"""Her stickers: image files in web/assets/stickers/, each with a meaning for the model.

Any png, gif, webp or jpg dropped into that folder (or a folder inside it) becomes a sticker
named after its file: 无语.png is "无语". stickers.txt in the same folder can give a file a
better meaning, so the model knows when to send it, one line per file:

    hentai.webp = 骂对方变态，一脸嫌弃

Lines starting with # are comments. The folder is read again whenever something in it changes.
"""
from pathlib import Path

from .config import ROOT

DIR = ROOT / "web" / "assets" / "stickers"
INDEX = DIR / "stickers.txt"
IMAGES = {".png", ".gif", ".webp", ".jpg", ".jpeg"}
_cache: dict = {"key": None, "list": []}


def _meanings() -> dict[str, str]:
    """file name (lower case) -> meaning, in the order stickers.txt lists them."""
    out = {}
    try:
        text = INDEX.read_text(encoding="utf-8-sig")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, meaning = (s.strip() for s in line.split("=", 1))
        if name and meaning:
            out[name.lower()] = meaning
    return out


def load() -> list[dict]:
    """[{id, url, meaning}]: the ones stickers.txt lists first, in its order, then the rest."""
    if not DIR.exists():
        return []
    files = sorted(p for p in DIR.rglob("*") if p.is_file() and p.suffix.lower() in IMAGES)
    key = (INDEX.stat().st_mtime if INDEX.exists() else 0, tuple((str(p), p.stat().st_mtime) for p in files))
    if key == _cache["key"]:
        return _cache["list"]
    meanings = _meanings()
    rank = {name: i for i, name in enumerate(meanings)}
    out, seen = [], set()
    for p in files:
        rel = p.relative_to(DIR).as_posix()
        sid = p.stem if p.stem.lower() not in seen else rel.rsplit(".", 1)[0]  # same name in two folders
        seen.add(sid.lower())
        names = (rel.lower(), p.name.lower(), p.stem.lower())
        meaning = next((meanings[n] for n in names if n in meanings), p.stem)
        out.append({"id": sid, "url": "assets/stickers/" + rel, "meaning": meaning,
                    "_rank": min(rank.get(n, len(rank)) for n in names)})
    out.sort(key=lambda s: (s["_rank"], s["id"].lower()))
    for s in out:
        del s["_rank"]
    _cache.update(key=key, list=out)
    return out


def find(name: str) -> dict | None:
    """The sticker the model (or the user) meant: by name, or failing that by its meaning."""
    name = name.strip().strip("[]【】:：\"'“”").lower()
    if not name:
        return None
    stickers = load()
    for s in stickers:
        if s["id"].lower() == name:
            return s
    for s in stickers:
        if name in s["meaning"].lower() or s["id"].lower() in name:
            return s
    return None


def prompt_list() -> str:
    """The set as the model sees it: one '- name: meaning' line each."""
    return "\n".join(f"  - {s['id']}: {s['meaning']}" for s in load()) or "  (none yet)"
