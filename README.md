# Amadeus

A personal recreation of **Amadeus** from *Steins;Gate 0*: the AI built from Makise Kurisu's memories, which you talk to through a phone-style video call.

- **Brain**: your own AI API (any OpenAI-compatible API or Anthropic)
- **Voice**: Japanese, cloned locally with [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) on your GPU
- **Subtitles / conversation**: English or 简体中文 (Settings → Subtitle language). Her voice stays Japanese either way. In Chinese mode, the interface, subtitles, the conversation log, and mic recognition all switch to Chinese.
- **Ears**: type, or press the mic button (local Whisper, or the browser's speech recognition)
- **Face**: a 2D mesh rig built from the portrait (Live2D-style: head turns with depth, eyelids, gaze, brows, drawn mouth for lip-sync, blush, swinging hair, breathing, 15 expressions in several strengths), with the original layered sprites as a fallback
- **Text chat**: the home screen, a messaging app in the Amadeus terminal's style: she texts in your language, sends her own stickers, and now and then a Japanese voice message. Video calls start from there, and end up in the chat as call records
- **Memory**: one memory for calls and chats, kept like a person's: it knows the time, fades, comes back when something reminds her, and forgets only what you ask it to (see below)

```
browser (web/)                   Amadeus server (server/, :8710)          external
──────────────                   ───────────────────────────────          ────────
text / mic ──► /api/stt ───────► faster-whisper (English)
           ──► /api/chat ──────► persona + history ──stream──────────────► your LLM API
   ◄── SSE "segment" {emotion, en, ja} per sentence
           ──► /api/tts (ja) ──► GPT-SoVITS client ──────────────────────► GPT-SoVITS api_v2 (:9880)
   ◄── wav ─► Web Audio ─► analyser ─► mouth sprites
                                     subtitles (en)
```

The model replies one sentence per line as `emotion|English|Japanese`. Each sentence is voiced as soon as it arrives, so she starts talking before the whole reply has finished generating.

---

## 1. Quick start

1. Install **Python 3.11+** (3.14 works).
2. Double-click **`start.bat`**. The first run creates a virtual environment in `%LOCALAPPDATA%\Amadeus\venv` and installs the dependencies. (It lives outside this folder because the `;` in `Amadeus_Steins;Gate` breaks Python venvs.)
3. Your browser opens the chat at <http://127.0.0.1:8710>. Fill in **Settings** (⚙, top left), then text her, or press the camera button for a video call:

| Provider | Provider setting | Base URL | Example model |
|---|---|---|---|
| OpenAI | OpenAI-compatible | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | OpenAI-compatible | `https://api.deepseek.com/v1` | `deepseek-chat` |
| OpenRouter | OpenAI-compatible | `https://openrouter.ai/api/v1` | `anthropic/claude-sonnet-5.5` |
| Ollama (local) | OpenAI-compatible | `http://127.0.0.1:11434/v1` | `qwen2.5:14b` (no key needed) |
| Anthropic | Anthropic | `https://api.anthropic.com` | `claude-sonnet-5-5` |

**Her voice:** run `tools\voice\install_amadeus_vits.bat` once (about 0.8 GB: CPU PyTorch, ESPnet and the [mio/amadeus](https://huggingface.co/mio/amadeus) model). After that she speaks in her Amadeus voice, synthesized on your CPU in about 0.3–0.8 s a line, with no GPU or internet needed. The app starts the voice with itself and stops it on exit. Without it, she uses a free Microsoft Japanese voice (edge-tts, needs internet). For the most faithful voice, train your own GPT-SoVITS clone (section 2).

### Speed

- **"Thinking" (reasoning) models** can think for 10–30 s before saying a word. Set **Settings → Reasoning effort → low**, or pick a non-reasoning model.
- Her first line is kept short, and synthesis of each line starts as soon as its Japanese text is written, before its subtitle arrives.
- After each reply, the log shows a ⏱ line: how long until she started speaking, how long the model took to write her first line, and how long synthesis took. The server terminal prints the same numbers as `[timing]`.
- Local voices are faster: the Amadeus voice takes about 0.3–0.8 s a line on the CPU and GPT-SoVITS about 0.3–0.5 s, versus about 1–1.4 s for the online fallback.

> The model has to write natural Japanese **and** follow the line format. Stronger models (GPT-4o class, Claude Sonnet, DeepSeek-V3, Qwen2.5-14B+) are noticeably better at this than small ones.

Your API key is stored in plain text in `data/settings.json` on this PC.

### Text chat and calls

The app opens on the chat. She texts in your subtitle language, sends stickers, and now and then a short Japanese voice message (tap it to play; the translation is under it). The camera button calls her; the red button hangs up, and she can hang up on you too: after saying goodbye, or when she's had enough. Each call ends up in the chat as a record with its length.

**Stickers** are the images in `web/assets/stickers/`. To add your own, drop a png, gif, webp or jpg there and add a line to `stickers.txt` saying when to send it (`无语.png = 无语，对方说了很蠢的话`). Without a line, the file name is its meaning. She picks stickers by those meanings, and you can send any of them with the 😊 button. Her starter set of 21 animated stickers was rendered from her own rig.

### Her memory

Calls and chats share one memory (`server/memory.py`, kept in `data/memory/`), modelled on a person's rather than a chat log:

- **Everything is kept, with times** (`log.jsonl`, the archive). She knows what time it is and how long it has been since you last talked, so she can tell a late-night call from a morning one.
- **Recent conversation** (since she last sorted her memories) is what she reads word for word, calls and chats together: when you call, she knows what you texted.
- **Older conversation becomes memories.** When the recent part grows long, or a conversation has been over for an hour, she sorts its oldest stretch into an episode: a dated gist in her own words, how much it mattered, how she felt, and the facts she learned about you. This is one extra request to your AI API, in the background.
- **Memories fade, and come back.** Each one weakens over days to weeks (important ones last longer), and every time it comes up again it lasts longer still. Faded memories are deep memories: she only gets them back when something strongly reminds her of them, and only vaguely. She is shown just what the moment reminds her of, so long histories never overflow the model.
- **Mood.** She carries a mood from 0 (bad) to 1 (great) from one conversation to the next. It moves with how each exchange felt to her, drifts back to her usual self over a few hours, colours what she says, and shows on her face (a good mood lifts the corners of her mouth).
- **Forgetting is selective.** The brain button in the chat (or **Settings → Her memory…**) shows what she knows about you, what she remembers and how clearly, and her mood. You can make her forget a single memory, a single fact, or the conversation not yet sorted. Nothing is destroyed: forgotten things are sealed and can be restored from the same panel. There is no "erase everything" button.

---

## 2. Her voice (GPT-SoVITS)

### 2.1 Install GPT-SoVITS

1. Download the **Windows integrated package** from the [GPT-SoVITS releases](https://github.com/RVC-Boss/GPT-SoVITS/releases). For an **RTX 50-series** GPU, pick the build marked for 50-series / CUDA 12.8; older builds don't support Blackwell GPUs.
2. Extract it to a simple path such as `D:\GPT-SoVITS`. **Not inside this folder**: the `;` in the folder name breaks it.

### 2.2 Collect Kurisu's voice clips

Take them from **your own copy** of the anime or the games. Clean clips matter much more than the amount of audio:

- Speech only: no background music, sound effects, or other characters talking over her. The GPT-SoVITS WebUI includes **UVR5** (vocal separation) to strip music.
- Calm, clear lines make the best neutral reference.

### 2.3 Option A: zero-shot (5 minutes, decent)

1. Pick one **3–10 second** clip, save it as `voice/refs/neutral.wav`, and write down **exactly** what she says (Japanese).
2. In `config.toml`:
   ```toml
   [tts.refs.neutral]
   audio = "voice/refs/neutral.wav"
   text  = "まったく、何を言ってるのよ。"   # exact transcript of the clip
   ```

### 2.4 Option B: fine-tune (about 1 hour, sounds like Kurisu)

With **1–10+ minutes** of clean clips (more is better), in the GPT-SoVITS WebUI (`go-webui.bat`):

1. **0-Fetch dataset**: put your clips in one folder → *Audio slicer* → *ASR* (choose **Faster Whisper**, language **ja**) → *Proofreading* (fix the transcripts; this matters).
2. **1-GPT-SoVITS-TTS → 1A**: experiment name `kurisu`, point it at the sliced audio + `.list` file, and run *one-click formatting*.
3. **1B fine-tuning**: train **SoVITS** (≈8–10 epochs), then **GPT** (≈10–15 epochs). The defaults are fine on a 16 GB GPU.
4. The weights appear in `SoVITS_weights_v2Pro\` and `GPT_weights_v2Pro\` (the folder names depend on the version you train). Point `config.toml` at them:
   ```toml
   gpt_weights    = "D:/GPT-SoVITS/GPT_weights_v2Pro/kurisu-e15.ckpt"
   sovits_weights = "D:/GPT-SoVITS/SoVITS_weights_v2Pro/kurisu_e8_s400.pth"
   ```
5. You still need a reference clip (Option A). The fine-tuned model adds her timbre on top of it.

### 2.5 Run the voice server

Each time, before (or after) `start.bat`, open a terminal in the GPT-SoVITS folder:

```bash
runtime\python.exe api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

Then restart Amadeus and press **Settings → Test voice**.

### 2.6 Emotional voices (optional)

The model tags every sentence with an emotion. Add a clip per emotion and GPT-SoVITS copies its tone:

```toml
[tts.refs.annoyed]
audio = "voice/refs/annoyed.wav"
text  = "クリスティーナって呼ぶな！"

[tts.refs.embarrassed]
audio = "voice/refs/embarrassed.wav"
text  = "べ、別にそういうわけじゃ…"
```

Emotions: `neutral smile happy laugh smug annoyed angry furious pout disgusted embarrassed sad surprised puzzled thinking`. Any emotion without a clip falls back to `neutral`.

> **Please keep it private.** Kurisu's voice belongs to her voice actress, Asami Imai, and the rights holders. Use it for personal use only, and don't share the trained model, the clips, or recordings of the clone.

---

## 3. Microphone

- **Local Whisper** (default): audio is transcribed on your PC with faster-whisper. It runs on the CPU unless the CUDA 12 cuBLAS + cuDNN 9 DLLs are on your `PATH` (it falls back to CPU automatically). A short sentence takes about 1 s on the CPU with the `small` model. You can change `model` under `[stt]`.
- **Browser**: Chrome or Edge built-in speech recognition, with no setup (audio goes to Google or Microsoft).
- Click the mic once to start and again to stop.
- **Hands-free call** (the waveform button next to the mic): the mic stays open, she replies as soon as you pause, and talking over her interrupts her. It detects speech by loudness against the room's noise, so a quiet room and headphones work best (through speakers, her own voice raises the bar for interrupting).
- On a phone: set `host = "0.0.0.0"` in `config.toml` and open `http://<your-pc-ip>:8710`. Browsers only allow the mic on HTTPS or localhost, so on a phone use text input or put it behind HTTPS.

---

## 4. Her face

The page uses the **mesh rig** in `web/assets/kurisu/rig/` when it exists (and WebGL works), and the **sprites** otherwise. Open `/?avatar=sprites` to compare, or `/?rigdebug` for sliders and expression buttons.

### 4.1 Mesh rig

[See-through](https://github.com/shitagaki-lab/see-through) splits the portrait into parts (front hair, back hair, face, eye whites, irises, lashes, brows, nose, mouth, neck, clothes) and repaints what each part hides, plus a depth map per part. `tools/psd_to_rig.py` puts each part on a deformable grid, and `web/rig.js` drives it in the browser:

- **Head**: yaw, pitch and roll. Every vertex has a depth, so the nose and bangs move more than the cheeks, and back hair moves the other way. The face's outline sits further back than its middle, so a turned face keeps its shape instead of sliding sideways.
- **Eyes**: the eyelids close along the shape of each eye, the eye smile (^^) raises the lower lid, and the irises look around (clipped to the eye white). The converter paints in the top of each iris that the lid hides, so a wide-open, surprised eye shows a whole iris.
- **Brows, mouth, blush**: the mouth is drawn per frame at the portrait's own resolution, with soft lines that thin toward the corners like the art (closed line ↔ open with teeth and tongue, smile/frown, smirk). Lip-sync takes each mora's shape (a/i/u/e/o, ん, っ) from the line's kana reading, spread over the voiced parts of the audio; loudness only decides how far it opens.
- **Expressions**: 15, some in strengths (smile < happy < laugh, annoyed < angry < furious), plus sulking with puffed cheeks and mumbling, puzzled with one brow up and a squint, the half-lidded disgusted stare. Some move on their own: laughing shakes her, fury trembles.
- **Body**: the two side locks and the bangs swing on their own springs behind head turns and in a light breeze, breathing, idle sway.

Only building a rig needs See-through; the rig itself is a few images and a JSON file that run in any browser, phones included.

**Build it** (Windows, NVIDIA GPU with 12 GB+):

```bash
python tools/clean_scanlines.py web/assets/kurisu/neutral.webp art/kurisu/kurisu_clean.png
```

```bash
tools\seethrough\install.bat
```

```bash
tools\make_rig.bat art\kurisu\kurisu_clean.png
```

1. `clean_scanlines.py` removes the CRT scanlines of Amadeus screenshots, which would otherwise be baked into every part (the app draws its own). Skip it for clean images.
2. `install.bat` installs See-through into `\SeeThrough` on the same drive. That's a download of about 17.5 GB (PyTorch and the model weights).
3. `make_rig.bat` decomposes the image (a few minutes) and builds the rig. Check `web/assets/kurisu/rig/preview.png`, then reload the page.

**No GPU?** Upload the image to the See-through online demo ([ModelScope](https://modelscope.cn/studios/ljsabc/See-Through), [Hugging Face](https://huggingface.co/spaces/24yearsold/see-through-demo)), download the PSD, and run `tools\make_rig.bat IMAGE PSD`. The demos return only the PSD, so a head-shaped depth model stands in for the depth maps.

### 4.2 Sprites (fallback)

`web/assets/kurisu/sprites.json` defines the expressions. Each one has a `base` image plus optional **full-canvas transparent overlays** that are stacked on top:

```json
"annoyed": {
  "base": "annoyed.webp",
  "eyes_closed": "annoyed_eyes_closed.png",
  "mouth_closed": "annoyed_mouth_closed.png",
  "mouth_open": "annoyed_mouth_open.png"
}
```

Overlay keys: `eyes_half`, `eyes_closed`, `mouth_closed`, `mouth_half`, `mouth_open`. A missing key means "the base already looks like that". Expressions you haven't drawn go through `aliases` to `neutral`, and CSS effects still react (blush, huff, hop).

The included closed-eyes and closed-mouth overlays were generated from the portrait by `tools/make_overlays.py`. It works by pulling skin texture in step with the 4-px scanlines, so you can adapt it for a new portrait by editing the box coordinates.

---

## 5. Files

```
start.bat / run.py        launcher
config.example.toml       copy → config.toml (start.bat does this)
server/
  main.py                 FastAPI routes, SSE streaming (call and text chat)
  memory.py               her memory: timeline, consolidation, recall, fading, forgetting, mood
  persona.py              Kurisu system prompts (call, chat) + reply parsers
  stickers.py             her sticker set: web/assets/stickers/ + stickers.txt
  llm.py                  OpenAI-compatible & Anthropic streaming
  tts.py                  voices: GPT-SoVITS, Amadeus VITS, edge-tts (+ cache)
  vits_server.py          the Amadeus voice service (runs in its own Python 3.12 venv)
  kana.py                 kana readings of her lines (pykakasi), for lip-sync
  stt.py                  faster-whisper
web/                      UI (no build step)
  app.js style.css index.html
  chat.js                 the text chat (the home screen)
  memory.js               the memory panel
  rig.js                  mesh rig renderer (WebGL) and motion
  lipsync.js              kana reading + audio -> mouth shape timeline
  call.js                 hands-free call: voice activity detection, barge-in
  avatar.js               sprite avatar (fallback)
  assets/kurisu/          sprites; rig/ = the built rig
  assets/stickers/        her stickers (add your own here) + stickers.txt
art/kurisu/               rig sources: cleaned portrait, See-through PSDs
voice/refs/               your reference clips
data/                     settings.json; memory/ (her memory: log, episodes, facts, mood); voice/ (her voice messages)
tools/
  make_rig.bat            portrait -> See-through -> rig
  psd_to_rig.py           See-through PSD -> web/assets/kurisu/rig/
  clean_scanlines.py      removes CRT scanlines from a screenshot
  seethrough/             See-through installer
  voice/                  installer for the Amadeus voice (mio/amadeus, ESPnet VITS)
  make_overlays.py        blink / lip-sync overlays for the sprites
```

To give her a completely fresh start, close the app and delete `data/memory/` (her voice messages are in `data/voice/`).

## Troubleshooting

- **"Voice unavailable: Cannot reach GPT-SoVITS"**: `api_v2.py` isn't running, or it's on a different port than `[tts] url`.
- **She sounds like someone else**: check that the reference transcript matches the clip exactly, use a cleaner clip, or fine-tune (Option B).
- **Subtitles show but there's no Japanese or voice**: the model ignored the line format. Use a stronger model.
- **`start.bat` fails at pip**: check that `python` is on your PATH (`python --version`).
- **"The Amadeus voice stopped"**: run `tools\voice\install_amadeus_vits.bat` again; the server window shows `[voice] Amadeus voice ready` once it works. Set `engine = "edge"` in `config.toml` to skip it.

## Credits

- Voice model: [mio/amadeus](https://huggingface.co/mio/amadeus) by mio, licensed [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) (ESPnet VITS). The installer downloads it from Hugging Face; it is not bundled with this project.
- Layer decomposition: [See-through](https://github.com/shitagaki-lab/see-through) (Apache-2.0). Kana readings: [pykakasi](https://codeberg.org/miurahr/pykakasi).
- Steins;Gate, Makise Kurisu, and her voice (Asami Imai) belong to their rights holders. This is a non-commercial fan project; don't sell it or redistribute recordings of her voice.
