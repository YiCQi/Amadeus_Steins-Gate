# Using Amadeus

[← Back to the README](../README.md)

**Language.** Her voice is always Japanese. Everything else follows **Settings → Subtitle language**: English or 简体中文. In Chinese mode the interface, her subtitles and texts, the conversation log and mic recognition all switch to Chinese.

## Text chat and calls

The app opens on the chat. She texts in your subtitle language, sends stickers, and now and then a short Japanese voice message (tap it to play; the translation is under it). The camera button calls her; the red button hangs up, and she can hang up on you too: after saying goodbye, or when she's had enough. Each call ends up in the chat as a record with its length.

**Cutting her off.** You can talk over her, press the mic, send a message or hang up while she's still talking. Her reply then stops there, and she remembers only what she got to say, so later she won't think she told you the rest. A line she was in the middle of is remembered as broken off ("こんばんは——"), so she knows you interrupted her.

**She speaks up on her own.** While the chat is open (in a background tab too; its title then counts her unread messages), she goes by how the conversation went quiet, the way a person would (`server/lull.py`):

- she asked you something and you haven't answered: she follows up after a minute or two (or, if you'd gone, a few minutes after you're back);
- the chat just trailed off, or a call just ended: she often picks it up after a few minutes; when it came to a close by itself ("好", "嗯"), later and less often;
- you said you'd be off doing something ("我去洗澡", "brb"): she checks in after half an hour or so;
- you said goodbye: she leaves you be for hours; after a good night, until the next morning;
- you haven't talked for a long time: now and then a hello, some minutes after you open the chat.

**She may also call you.** Now and then, instead of texting back, she starts the video call herself: your phone rings over whatever is on screen, and you answer or turn it down. She does it when the feeling has got past what typing carries — not to be nice and not because it has been a while — and she knows what she rang about when you pick up. Rules decide only whether the option is open to her at all: never in the first few messages of a conversation, never out of a flat stretch where nothing is happening, at most twice a day, not for a couple of hours after any call, and nothing out of the blue between midnight and 8am. Whether to actually ring is hers. A call you miss or turn down stays in the chat as a missed call, and in her memory as one she made, so she may bring it up later. Turn it off in **Settings → She may call you, when a text won't carry it**.

She texts at most twice in a row without an answer, and can always choose to stay quiet; once she has, she isn't asked again until one of you writes. Deciding when costs nothing; each time she might write is one ordinary request to your AI API, about the size of a reply. If you start writing while she's still thinking about it, your message goes first. On a call she checks whether you're still there after half a minute or so of silence, and after a long silence she says goodbye and hangs up. Turn all this off in **Settings → When it goes quiet, she may speak up first**. "Something she read today" comes from a few headlines in the RSS feeds under `[news]` in `config.toml` (neuroscience news and NHK by default; `enabled = false` for none).

**History.** The chat loads a page at a time: scroll up for older messages, and the arrow button takes you back to the latest. The magnifier searches the whole history by words (all of them must match), or lists her voice messages, stickers or calls; tap a result to open the chat there. **By date** opens the chat at that day's first message.

**Stickers** are the images in `web/assets/stickers/`. To add your own, drop a png, gif, webp or jpg there and add a line to `stickers.txt` with what it means and, in brackets, when to send it (`无语.png = 无语……（对方说了很蠢的话）`). Without a line, the file name is its meaning. She picks stickers by those lines, and you can send any of them with the 😊 button. When you send one, she gets only its mood (the part before the brackets) and takes it the way a face pulled is meant: it colours her reply, and she doesn't pick it apart. Her own 21 animated stickers were rendered from her rig (11 are in the chat, the rest wait in `unused/`). The chat also has the 40 stickers of the game's Rine chat app, in `sg0/`: a Chinese localization under CC BY-NC-SA 4.0, for non-commercial use (see [NOTICE.md](../NOTICE.md)).

Two folders in there are special. **`private/`** is yours alone: what you put there is used in the chat but never committed to git, so images you collected yourself (fan art, screenshots: their rights aren't yours) are never published. It has its own `stickers.txt` for their meanings. **`unused/`** keeps stickers in the library without using them in the chat; move one back out to use it again.

## Speed

- **"Thinking" (reasoning) models** can think for tens of seconds before saying a word: on one service we measured 15–120 s at its default effort, against about 1.2 s at low. Set **Settings → Reasoning effort → low**, or pick a non-reasoning model. A value the service doesn't support may be treated as its default (there, "minimal" and "none" were as slow as the default).
- **Where the time goes on a call**, measured with a reasoning model at low effort and an NVIDIA GPU, counted from your last word:

  | Step | Time |
  |---|---|
  | Hands-free hears that you've finished | about 0.5 s |
  | Speech recognition | 0.15 s |
  | The model's first word | about 1.2 s (median; 1.05–2.6 s) |
  | Her first line's Japanese is written | about 0.3 s more |
  | Her voice for it | about 0.12 s (0.4 s on a CPU) |
  | **In all** | **about 2.3 s** |

  Most of the model's 1.2 s is the service's own time: it answered a two-line prompt in about 1.1 s too. The app's part (building the prompt with her memory) takes 0.02 s.
- Her first line is kept short, and synthesis of each line starts as soon as its Japanese text is written, before its subtitle arrives.
- After each reply, the log shows a ⏱ line: how long until she started speaking, how long the model took to write her first line, and how long synthesis took. The server terminal prints the same numbers as `[timing]`.
- Local voices are faster: the Amadeus voice takes about 0.3–0.8 s a line on the CPU (well under 0.1 s on an NVIDIA GPU) and GPT-SoVITS about 0.3–0.5 s, versus about 1–1.4 s for the online fallback.

> The model has to write natural Japanese **and** follow the line format. Stronger models (GPT-4o class, Claude Sonnet, DeepSeek-V3, Qwen2.5-14B+) are noticeably better at this than small ones.

Your API key is stored in plain text in `data/settings.json` on this PC.

## Microphone

- **Local Whisper** (default): audio is transcribed on your PC with faster-whisper, loaded in the background when the app starts. On the CPU a short sentence takes about 1–2 s with the `small` model (greedy decoding); on an NVIDIA GPU about 0.15 s, with a more careful 5-way search, once NVIDIA's CUDA 12 libraries are in the app's venv (a 1.3 GB download, about 2 GB on disk, into that venv only): `%LOCALAPPDATA%\Amadeus\venv\Scripts\python.exe -m pip install --no-cache-dir nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"`. Measured on an RTX 5080 laptop: 0.10–0.15 s a sentence against 1.06 s on the CPU, the same accuracy. The server window says which it uses (`[stt] speech recognition ready (cuda, …)`) and how long each sentence took. You can change `model` under `[stt]`.
- **Browser**: Chrome or Edge built-in speech recognition, with no setup (audio goes to Google or Microsoft).
- Click the mic once to start and again to stop.
- **Hands-free call** (the waveform button next to the mic): the mic stays open, she replies as soon as you pause, and talking over her interrupts her. It detects speech by loudness against the room's noise, so a quiet room and headphones work best (through speakers, her own voice raises the bar for interrupting).
  - A pause of 0.55 s ends what you said, and she starts on her reply at once. If you go on talking before she has said a word, as after a thinking pause ("嗯……"), her reply is dropped and both parts go to her as one message ("我觉得……这个实验的结果还是有问题。"). Nothing of the dropped reply is kept. Once she has started speaking, going on interrupts her instead.
  - Measured on recorded sentences, a pause in mid-sentence of up to 0.4–0.5 s isn't taken as an end at all. Your words go to speech recognition about 0.5 s after your last word.
- On a phone: set `host = "0.0.0.0"` in `config.toml` and open `http://<your-pc-ip>:8710`. Browsers only allow the mic on HTTPS or localhost, so on a phone use text input or put it behind HTTPS.
