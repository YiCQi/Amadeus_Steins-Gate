# Troubleshooting

[← Back to the README](../README.md)

- **"Voice unavailable: Cannot reach GPT-SoVITS"**: `api_v2.py` isn't running, or it's on a different port than `[tts] url`.
- **"The Amadeus voice stopped"**: run `tools\voice\install_amadeus_vits.bat` again; the server window shows `[voice] Amadeus voice ready` once it works. Set `engine = "edge"` in `config.toml` to skip it.
- **"Voice unavailable: …" on a call**: the server window says which line failed and why (`[voice] line … failed: …`). The page has already asked for it a second time; if that failed too, the reason is in the message.
- **She sounds like someone else**: check that the reference transcript matches the clip exactly, use a cleaner clip, or fine-tune (see [Her voice](voice.md)).
- **Subtitles show but there's no Japanese or voice**: the model ignored the line format. Use a stronger model.
- **She takes long to answer**: reasoning ("thinking") models can think for 10–30 s first; set **Settings → Reasoning effort → low**, or pick another model (see [Speed](using.md#speed)).
- **`start.bat` fails at pip**: check that `python` is on your PATH (`python --version`).
