# Using Amadeus

[← Back to the README](../README.md)

**Language.** Her voice is always Japanese. Everything else follows **Settings → Subtitle language**: English or 简体中文. In Chinese mode the interface, her subtitles and texts, the conversation log and mic recognition all switch to Chinese.

## Text chat and calls

The app opens on the chat. She texts in your subtitle language, sends stickers, and now and then a short Japanese voice message (tap it to play; the translation is under it). The camera button calls her; the red button hangs up, and she can hang up on you too: after saying goodbye, or when she's had enough. Each call ends up in the chat as a record with its length.

**She speaks up on her own.** While the chat is open (in a background tab too; its title then counts her unread messages), she goes by how the conversation went quiet, the way a person would (`server/lull.py`):

- she asked you something and you haven't answered: she follows up after a few minutes (or, if you'd gone, a few minutes after you're back);
- the chat just trailed off, or a call just ended: now and then she picks it up after a while;
- you said goodbye: she leaves you be for hours; after a good night, until the next morning;
- you haven't talked for a long time: now and then a hello, some minutes after you open the chat.

She texts at most twice in a row without an answer, and can always choose to stay quiet. Deciding when costs nothing; each time she might write is one ordinary request to your AI API, about the size of a reply. If you start writing while she's still thinking about it, your message goes first. On a call she checks whether you're still there after half a minute or so of silence, and after a long silence she says goodbye and hangs up. Turn all this off in **Settings → When it goes quiet, she may speak up first**. "Something she read today" comes from a few headlines in the RSS feeds under `[news]` in `config.toml` (neuroscience news and NHK by default; `enabled = false` for none).

**History.** The chat loads a page at a time: scroll up for older messages, and the arrow button takes you back to the latest. The magnifier searches the whole history by words (all of them must match), or lists her voice messages, stickers or calls; tap a result to open the chat there. **By date** opens the chat at that day's first message.

**Stickers** are the images in `web/assets/stickers/`. To add your own, drop a png, gif, webp or jpg there and add a line to `stickers.txt` with what it means and, in brackets, when to send it (`无语.png = 无语……（对方说了很蠢的话）`). Without a line, the file name is its meaning. She picks stickers by those lines, and you can send any of them with the 😊 button. When you send one, she gets only its mood (the part before the brackets) and takes it the way a face pulled is meant: it colours her reply, and she doesn't pick it apart. Her starter set of 21 animated stickers was rendered from her own rig.

## Speed

- **"Thinking" (reasoning) models** can think for 10–30 s before saying a word. Set **Settings → Reasoning effort → low**, or pick a non-reasoning model.
- Her first line is kept short, and synthesis of each line starts as soon as its Japanese text is written, before its subtitle arrives.
- After each reply, the log shows a ⏱ line: how long until she started speaking, how long the model took to write her first line, and how long synthesis took. The server terminal prints the same numbers as `[timing]`.
- Local voices are faster: the Amadeus voice takes about 0.3–0.8 s a line on the CPU (well under 0.1 s on an NVIDIA GPU) and GPT-SoVITS about 0.3–0.5 s, versus about 1–1.4 s for the online fallback.

> The model has to write natural Japanese **and** follow the line format. Stronger models (GPT-4o class, Claude Sonnet, DeepSeek-V3, Qwen2.5-14B+) are noticeably better at this than small ones.

Your API key is stored in plain text in `data/settings.json` on this PC.

## Microphone

- **Local Whisper** (default): audio is transcribed on your PC with faster-whisper. It runs on the CPU unless the CUDA 12 cuBLAS + cuDNN 9 DLLs are on your `PATH` (it falls back to CPU automatically). A short sentence takes about 1 s on the CPU with the `small` model. You can change `model` under `[stt]`.
- **Browser**: Chrome or Edge built-in speech recognition, with no setup (audio goes to Google or Microsoft).
- Click the mic once to start and again to stop.
- **Hands-free call** (the waveform button next to the mic): the mic stays open, she replies as soon as you pause, and talking over her interrupts her. It detects speech by loudness against the room's noise, so a quiet room and headphones work best (through speakers, her own voice raises the bar for interrupting).
- On a phone: set `host = "0.0.0.0"` in `config.toml` and open `http://<your-pc-ip>:8710`. Browsers only allow the mic on HTTPS or localhost, so on a phone use text input or put it behind HTTPS.
