# How it works

[← Back to the README](../README.md)

```
browser (web/)                          Amadeus server (server/, :8710)                 external
──────────────                          ───────────────────────────────                 ────────
text / mic ──► /api/stt ──────────────► faster-whisper (local)
           ──► /api/chat (call) ──────► persona + memory ──stream──────────────────────► your AI API
           ──► /api/text (chat)
   ◄── SSE "voice" {ja} as soon as a line's Japanese is written, then "segment" {subtitle}
           ──► /api/voice/{id} ───────► Amadeus voice (ESPnet VITS, :9881)
                                        or GPT-SoVITS api_v2 (:9880), or edge-tts ─────► Microsoft
   ◄── wav ─► Web Audio ─► kana timeline ─► mesh rig (WebGL): lip-sync, expressions
                           subtitles in your language
```

On a call the model replies one sentence per line as `emotion|Japanese|subtitle`; in the chat, one bubble per line as `text|emotion|message`, `sticker|name` or `voice|emotion|Japanese|translation`. Each spoken line is voiced as soon as its Japanese arrives, so she starts talking before the whole reply has finished generating.

Her reply goes into her memory once it has been written, unless she is cut off first: then the page stops the request (so the model stops writing) and tells `/api/call/said` how many of her lines she had started on, and her reply is kept only that far, whether it was still being written or already recorded. A line she was in the middle of ends in `——`, so she knows she was interrupted. In a hands-free call, going on talking before she has said a word drops her reply instead, and the next message takes the place of the unanswered one (`retract`).

## Files

```
start.bat / run.py        launcher
config.example.toml       copy → config.toml (start.bat does this)
LICENSE / NOTICE.md       MIT for the code; what it doesn't cover (her likeness), third-party licenses
server/
  main.py                 FastAPI routes, SSE streaming (call and text chat)
  memory.py               her memory: timeline, consolidation, merging, recall, fading, forgetting, mood
  notes.py                what she knows: core and archive, versions, recall ranking, strict forgetting
  embed.py                recall by meaning: multilingual-e5-small, local (downloaded on first run)
  persona.py              Kurisu system prompts (call, chat, when it goes quiet) + reply parsers
  stickers.py             her sticker set: web/assets/stickers/ + stickers.txt
  lull.py                 when the chat goes quiet: whether and when she texts first
  calling.py              when she rings you herself, and how often she may
  verbatim.py             the words themselves, searchable: a local index of every message
  news.py                 a few headlines she may bring up (config.toml [news])
  llm.py                  OpenAI-compatible & Anthropic streaming
  tts.py                  voices: GPT-SoVITS, Amadeus VITS, edge-tts (+ cache)
  vits_server.py          the Amadeus voice service (runs in its own Python 3.12 venv)
  kana.py                 kana readings of her lines (pykakasi), for lip-sync
  stt.py                  faster-whisper
web/                      UI (no build step)
  app.js style.css index.html
  chat.js                 the text chat (the home screen), a page of history at a time
  search.js               searching the chat history
  memory.js               the memory panel
  rig.js                  mesh rig renderer (WebGL) and motion
  lipsync.js              kana reading + audio -> mouth shape timeline
  call.js                 hands-free call: voice activity detection, barge-in (app.js joins up a pause that wasn't the end)
  avatar.js               sprite avatar (fallback)
  assets/kurisu/          sprites; rig/ = the built rig
  assets/stickers/        her stickers (add your own here) + stickers.txt
art/kurisu/               rig sources: cleaned portrait, See-through PSDs
voice/refs/               your reference clips
data/                     yours alone, never committed: settings.json (your API key);
                          memory/ (her memory: log, episodes, notes and their changes, mood);
                          memory-backups/ (full backups); voice/ (her voice messages)
docs/                     these pages; media/ = the README's pictures
tools/
  memory_notes.py         her notes: status, migrate, rollback (off / restore), recall check
  make_rig.bat            portrait -> See-through -> rig
  psd_to_rig.py           See-through PSD -> web/assets/kurisu/rig/
  clean_scanlines.py      removes CRT scanlines from a screenshot
  seethrough/             See-through installer
  voice/                  installer for the Amadeus voice (mio/amadeus, ESPnet VITS)
  make_overlays.py        blink / lip-sync overlays for the sprites
```
