"""Streaming chat against the user's own API.

provider = "openai"    -> any OpenAI-compatible /chat/completions endpoint
                          (OpenAI, DeepSeek, OpenRouter, Groq, Ollama, LM Studio, ...)
provider = "anthropic" -> Anthropic Messages API
"""
import json
from typing import AsyncIterator

import httpx


class LLMError(Exception):
    pass


# One client for the whole process: keeps the TLS connection to the API alive
# between turns instead of re-handshaking every message.
_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15))
    return _client


async def stream_chat(settings: dict, system: str, messages: list[dict]) -> AsyncIterator[str]:
    provider = settings.get("provider", "openai")
    if provider == "anthropic":
        gen = _stream_anthropic(settings, system, messages)
    else:
        gen = _stream_openai(settings, system, messages)
    async for chunk in gen:
        yield chunk


async def _stream_openai(s: dict, system: str, messages: list[dict]) -> AsyncIterator[str]:
    base = (s.get("base_url") or "https://api.openai.com/v1").rstrip("/")
    headers = {"Content-Type": "application/json"}
    if s.get("api_key"):
        headers["Authorization"] = f"Bearer {s['api_key']}"
    body = {
        "model": s.get("model"),
        "messages": [{"role": "system", "content": system}] + messages,
        "temperature": float(s.get("temperature", 0.8)),
        "stream": True,
    }
    # Reasoning ("thinking") models can think for tens of seconds before saying anything.
    # Only sent when set: non-reasoning models reject the parameter.
    if s.get("reasoning_effort"):
        body["reasoning_effort"] = s["reasoning_effort"]
    async with client().stream("POST", f"{base}/chat/completions", headers=headers, json=body) as r:
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code}: {(await r.aread()).decode(errors='replace')[:500]}")
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {})
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if delta.get("content"):
                yield delta["content"]


async def _stream_anthropic(s: dict, system: str, messages: list[dict]) -> AsyncIterator[str]:
    base = (s.get("base_url") or "https://api.anthropic.com").rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    headers = {
        "Content-Type": "application/json",
        "x-api-key": s.get("api_key", ""),
        "anthropic-version": "2023-06-01",
    }
    body = {
        "model": s.get("model"),
        "system": system,
        "messages": messages,
        "max_tokens": 1024,
        "temperature": float(s.get("temperature", 0.8)),
        "stream": True,
    }
    async with client().stream("POST", f"{base}/v1/messages", headers=headers, json=body) as r:
        if r.status_code >= 400:
            raise LLMError(f"{r.status_code}: {(await r.aread()).decode(errors='replace')[:500]}")
        async for line in r.aiter_lines():
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except json.JSONDecodeError:
                continue
            if ev.get("type") == "content_block_delta" and ev["delta"].get("type") == "text_delta":
                yield ev["delta"]["text"]
            elif ev.get("type") == "error":
                raise LLMError(str(ev.get("error")))
