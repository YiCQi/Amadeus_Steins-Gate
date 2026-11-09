# Notices

[← Back to the README](README.md)

## This project's license

The code and documentation written for this project are under the [MIT license](LICENSE), © 2026 YiCQi. That covers `server/`, `web/` (but not `web/assets/`), `tools/`, `docs/` (but not `docs/media/`), and the files at the top of the repository.

## Steins;Gate material: not covered

Steins;Gate, Steins;Gate 0, Makise Kurisu, her likeness and her voice (Asami Imai) belong to their rights holders. They aren't this project's to license, and the MIT license doesn't cover them. In this repository, that means her portrait and everything made from it:

- `art/kurisu/kurisu_clean.png`: the portrait, a cleaned screenshot of her Amadeus form;
- `web/assets/kurisu/`: her rig and sprites;
- `web/assets/stickers/`: her stickers, rendered from the rig;
- `docs/media/`: pictures and clips of the app, which show her.

This is a non-commercial fan project, not affiliated with or endorsed by the rights holders. Don't sell this material or use it commercially, and don't redistribute recordings of her voice. To build something of your own on this code, rig a portrait you have the rights to ([docs/face.md](docs/face.md) shows how).

## Third-party software and models

None of these are stored in this repository. Setup installs or downloads them, and each keeps its own license.

| | Used for | License |
|---|---|---|
| [mio/amadeus](https://huggingface.co/mio/amadeus) by mio, an ESPnet VITS model | her voice | CC BY 4.0 |
| [ESPnet](https://github.com/espnet/espnet), [PyTorch](https://pytorch.org), [pyopenjtalk-plus](https://github.com/tsukumijima/pyopenjtalk-plus), [jaconv](https://github.com/ikegami-yukino/jaconv) | running her voice | Apache-2.0, BSD-3-Clause, MIT, MIT |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper), [CTranslate2](https://github.com/OpenNMT/CTranslate2) and the Whisper `small` model | speech recognition | MIT |
| [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) (its [ONNX build](https://huggingface.co/Xenova/multilingual-e5-small)), [ONNX Runtime](https://github.com/microsoft/onnxruntime) | recalling memories by meaning | MIT |
| [pykakasi](https://codeberg.org/miurahr/pykakasi) | kana readings, for lip-sync | GPL-3.0-or-later |
| [edge-tts](https://github.com/rany2/edge-tts) | the online fallback voice | LGPL-3.0 |
| [See-through](https://github.com/shitagaki-lab/see-through) | splitting a portrait into rig layers (only to build a rig) | Apache-2.0 |
| FastAPI, Uvicorn, httpx, python-multipart, PyAV, Pillow, psd-tools, NumPy | the server and tools | MIT, BSD-3-Clause, Apache-2.0 |
| Cormorant Garamond, Share Tech Mono, Noto Sans, Noto Sans JP and Noto Sans SC, loaded from Google Fonts | the interface's type | SIL Open Font License 1.1 |
| NVIDIA cuBLAS and cuDNN (optional) | speech recognition on an NVIDIA GPU | NVIDIA's own license |

pykakasi (GPL) and edge-tts (LGPL) are installed separately and never bundled, so they don't change this project's license. Anyone who ships a bundle that includes them, such as a frozen .exe, must follow their terms for that bundle.
