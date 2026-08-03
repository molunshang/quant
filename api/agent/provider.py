"""LLM provider abstraction: multi-backend support (Anthropic + OpenAI-compatible)."""
from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import anthropic
from openai import OpenAI


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict = field(default_factory=dict)


@dataclass
class LLMResponse:
    text: str | None = None
    tool_uses: list[ToolCall] = field(default_factory=list)


class LLMProvider(ABC):
    @abstractmethod
    def complete(
        self,
        *,
        system: str,
        messages: list[dict],
        tools: list[dict],
        model: str | None = None,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """One LLM call. Returns text + tool-use list (both may be present)."""


def _anthropic_tools(tools: list[dict]) -> list[dict]:
    """Convert generic {name, description, parameters} to Anthropic schema."""
    return [
        {
            "name": t["name"],
            "description": t.get("description", ""),
            "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


class AnthropicProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str | None = None, model: str = "claude-opus-5"):
        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = anthropic.Anthropic(**kwargs)
        self.model = model

    def complete(self, *, system, messages, tools, model=None, max_tokens=4096) -> LLMResponse:
        kwargs: dict = {
            "model": model or self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = _anthropic_tools(tools)
        resp = self._client.messages.create(**kwargs)
        text = "".join(b.text for b in resp.content if b.type == "text") or None
        tool_uses = [
            ToolCall(id=b.id, name=b.name, input=b.input)
            for b in resp.content
            if b.type == "tool_use"
        ]
        return LLMResponse(text=text, tool_uses=tool_uses)


def _openai_tools(tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


class OpenAICompatProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def complete(self, *, system, messages, tools, model=None, max_tokens=4096) -> LLMResponse:
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(messages)
        kwargs: dict = {
            "model": model or self.model,
            "messages": msgs,
            "max_tokens": max_tokens,
        }
        kwargs["tools"] = _openai_tools(tools)
        raw = self._client.chat.completions.create(**kwargs)
        resp = raw if isinstance(raw, dict) else raw.model_dump()
        message = resp["choices"][0]["message"]
        text = message.get("content") or None
        tool_uses = []
        for tc in message.get("tool_calls") or []:
            try:
                input_ = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                input_ = {}
            tool_uses.append(ToolCall(id=tc["id"], name=tc["function"]["name"], input=input_))
        return LLMResponse(text=text, tool_uses=tool_uses)


def _expand_env(value: str) -> str:
    if value.startswith("env:"):
        return os.environ.get(value[4:], "")
    return value


def load_providers(path: str | None = None) -> dict[str, LLMProvider]:
    """Load providers from config/llm.json -> {name: provider}."""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "llm.json")
    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    providers: dict[str, LLMProvider] = {}
    for p in cfg.get("providers", []):
        name = p["name"]
        typ = p["type"]
        api_key = _expand_env(p.get("api_key", ""))
        if typ == "anthropic":
            providers[name] = AnthropicProvider(
                api_key=api_key, base_url=p.get("base_url"), model=p.get("model", "claude-opus-5")
            )
        elif typ == "openai_compat":
            providers[name] = OpenAICompatProvider(
                api_key=api_key, base_url=p["base_url"], model=p.get("model", "gpt-4o")
            )
        else:
            raise ValueError(f"unknown provider type: {typ}")
    return providers
