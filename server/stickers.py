"""Her stickers: image files in web/assets/stickers/, each with a meaning for the model.

Any png, gif, webp or jpg dropped into that folder (or a folder inside it) becomes a sticker
named after its file: 无语.png is "无语". stickers.txt in the same folder can give a file a
better meaning, so the model knows when to send it, one line per file:

    hentai.webp = 骂对方变态，一脸嫌弃

Lines starting with # are comments. The folder is read again whenever something in it changes.
Two folders are special: private/ is yours alone (never committed to git, so images you
collected yourself are never published), and anything in unused/ stays in the library but
isn't used in the chat.
"""
import time
from pathlib import Path

from .config import ROOT

DIR = ROOT / "web" / "assets" / "stickers"
INDEX = "stickers.txt"
IMAGES = {".png", ".gif", ".webp", ".jpg", ".jpeg"}
SKIP = {"unused"}   # kept, but not in the chat
_cache: dict = {"key": None, "list": [], "checked": 0.0}


def _in_use(p: Path) -> bool:
    return not any(part.lower() in SKIP for part in p.relative_to(DIR).parts[:-1])


def _indexes() -> list[Path]:
    """Every stickers.txt in use: the main one first, then those in folders."""
    return sorted((p for p in DIR.rglob(INDEX) if _in_use(p)), key=lambda p: (len(p.relative_to(DIR).parts), str(p)))


def _meanings() -> dict[str, str]:
    """path from the stickers folder (lower case) -> meaning, in the order the stickers.txt files list them.
    A line in a folder's own stickers.txt is about that folder's file."""
    out = {}
    for index in _indexes():
        folder = index.parent.relative_to(DIR).as_posix()
        try:
            text = index.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, meaning = (s.strip() for s in line.split("=", 1))
            if name and meaning:
                out[(name if folder == "." else f"{folder}/{name}").lower()] = meaning
    return out


def load() -> list[dict]:
    """[{id, url, meaning}]: the ones stickers.txt lists first, in its order, then the rest."""
    if _cache["key"] is not None and time.monotonic() - _cache["checked"] < 2:
        return _cache["list"]   # looked at the folder a moment ago (a search can ask thousands of times)
    if not DIR.exists():
        return []
    _cache["checked"] = time.monotonic()
    files = sorted(p for p in DIR.rglob("*") if p.is_file() and p.suffix.lower() in IMAGES and _in_use(p))
    key = (tuple((str(i), i.stat().st_mtime) for i in _indexes()), tuple((str(p), p.stat().st_mtime) for p in files))
    if key == _cache["key"]:
        return _cache["list"]
    meanings = _meanings()
    rank = {name: i for i, name in enumerate(meanings)}
    out, seen = [], set()
    for p in files:
        rel = p.relative_to(DIR).as_posix()
        sid = p.stem if p.stem.lower() not in seen else rel.rsplit(".", 1)[0]  # same name in two folders
        seen.add(sid.lower())
        names = (rel.lower(), rel.rsplit(".", 1)[0].lower(), p.name.lower(), p.stem.lower())
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
