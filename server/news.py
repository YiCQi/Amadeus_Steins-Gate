"""A few headlines she might have read today, for when a conversation lulls.

Read from the RSS or Atom feeds in config.toml [news] (feeds = [...], enabled = true/false), at
most once an hour. Without internet, or with news off, she simply has none to mention.
"""
import asyncio
import random
import time
import xml.etree.ElementTree as ET

import httpx

DEFAULT_FEEDS = [
    "https://www.sciencedaily.com/rss/mind_brain.xml",   # neuroscience: her own field
    "https://www3.nhk.or.jp/rss/news/cat0.xml",          # NHK's main headlines
]
ATOM = "{http://www.w3.org/2005/Atom}"
_cache: dict = {"ts": 0.0, "items": []}


async def _feed(client: httpx.AsyncClient, url: str) -> list[str]:
    try:
        r = await client.get(url)
        if r.status_code != 200 or len(r.content) > 3_000_000:
            return []
        root = ET.fromstring(r.content)
    except Exception:  # offline, a moved feed, not XML: no headlines from it
        return []
    source = (root.findtext("channel/title") or root.findtext(f"{ATOM}title") or "").strip()
    entries = list(root.iter("item")) or list(root.iter(f"{ATOM}entry"))
    out = []
    for e in entries[:15]:
        title = " ".join((e.findtext("title") or e.findtext(f"{ATOM}title") or "").split())
        if title:
            out.append(f"{title} ({source})" if source else title)
    return out


async def headlines(cfg: dict, n: int = 3) -> list[str]:
    if not cfg.get("enabled", True):
        return []
    if time.time() - _cache["ts"] > 3600:
        _cache["ts"] = time.time()
        async with httpx.AsyncClient(timeout=6, follow_redirects=True) as client:
            found = await asyncio.gather(*(_feed(client, url) for url in cfg.get("feeds", DEFAULT_FEEDS)))
        items = [t for feed in found for t in feed]
        if items:
            _cache["items"] = items
    pool = _cache["items"]
    return random.sample(pool, min(n, len(pool)))
