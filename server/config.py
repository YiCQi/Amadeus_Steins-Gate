"""Configuration: config.toml (hand-edited) + data/settings.json (written by the UI)."""
import json
import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.toml"
EXAMPLE_PATH = ROOT / "config.example.toml"
DATA_DIR = Path(os.environ.get("AMADEUS_DATA", ROOT / "data"))  # override for a separate profile/test run
SETTINGS_PATH = DATA_DIR / "settings.json"
HISTORY_PATH = DATA_DIR / "history.json"   # the video call
CHAT_PATH = DATA_DIR / "chat.json"         # the text chat
VOICE_DIR = DATA_DIR / "voice"             # her voice messages in the text chat

# Keys the UI may change. Everything else lives in config.toml.
UI_KEYS = {
    "provider", "base_url", "api_key", "model", "temperature", "reasoning_effort",
    "user_name", "stt_mode", "show_japanese", "language", "idle_talk",
}


def load_config() -> dict:
    path = CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_PATH
    with open(path, "rb") as f:
        return tomllib.load(f)


def load_settings() -> dict:
    """Effective UI-level settings: config.toml defaults overlaid with settings.json."""
    cfg = load_config()
    llm = cfg.get("llm", {})
    s = {
        "provider": llm.get("provider", "openai"),
        "base_url": llm.get("base_url", ""),
        "api_key": llm.get("api_key", ""),
        "model": llm.get("model", ""),
        "temperature": llm.get("temperature", 0.8),
        "reasoning_effort": llm.get("reasoning_effort", ""),  # "" = don't send
        "user_name": cfg.get("user", {}).get("name", ""),
        "stt_mode": cfg.get("stt", {}).get("mode", "local"),
        "show_japanese": False,
        "idle_talk": True,   # when it goes quiet, she may speak up on her own
        "language": cfg.get("user", {}).get("language", "en"),  # subtitle language: "en" or "zh"
    }
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
            s.update({k: v for k, v in saved.items() if k in UI_KEYS})
        except (json.JSONDecodeError, OSError):
            pass
    return s


def save_settings(update: dict) -> dict:
    DATA_DIR.mkdir(exist_ok=True)
    current = {}
    if SETTINGS_PATH.exists():
        try:
            current = json.loads(SETTINGS_PATH.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            current = {}
    for k, v in update.items():
        if k not in UI_KEYS:
            continue
        if k == "api_key" and (v is None or "•" in str(v)):
            continue  # masked value echoed back from the UI: keep existing key
        current[k] = v
    SETTINGS_PATH.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")
    return load_settings()


def public_settings(s: dict) -> dict:
    out = dict(s)
    key = out.get("api_key") or ""
    out["api_key"] = ("••••••••" + key[-4:]) if key else ""
    out["has_api_key"] = bool(key)
    return out


def resolve_path(p: str) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (ROOT / path)
