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
    assistant_blocks: list[dict] | None = None


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


def _anthropic_messages(messages: list[dict]) -> list[dict]:
    """Translate the provider-neutral message list to Anthropic's wire format.

    Neutral shapes translated here:
      - {"role": "assistant", "tool_calls": [{"id", "name", "input"}]}
          -> {"role": "assistant", "content": [{"type": "tool_use", "id", "name", "input"}]}
      - {"role": "assistant", "assistant_blocks": [{"type": "thinking", ...}, ...]}
          -> assistant content = assistant_blocks verbatim (preserves thinking
             blocks that reasoning models require to be passed back)
      - {"role": "user", "tool_results": [{"tool_use_id", "content", "is_error"}]}
          -> {"role": "user", "content": [{"type": "tool_result", "tool_use_id", "content", "is_error"}]}
    Plain-text messages pass through unchanged.
    """
    out = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("assistant_blocks"):
            out.append({"role": "assistant", "content": list(m["assistant_blocks"])})
        elif m.get("role") == "assistant" and m.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc.get("input", {})}
                    for tc in m["tool_calls"]
                ],
            })
        elif m.get("role") == "user" and m.get("tool_results"):
            out.append({
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tr["tool_use_id"],
                        "content": tr.get("content", ""),
                        "is_error": tr.get("is_error", False),
                    }
                    for tr in m["tool_results"]
                ],
            })
        else:
            out.append(dict(m))
    return out


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
            "messages": _anthropic_messages(messages),
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
        # Keep the full assistant content blocks (incl. thinking) so the agent
        # loop can pass them back verbatim — reasoning models require it.
        # Real anthropic blocks expose model_dump(); dict-style blocks (tests)
        # carry the fields directly.
        blocks = []
        for b in resp.content:
            dump = getattr(b, "model_dump", None)
            blocks.append(dump() if callable(dump) else dict(vars(b)))
        if resp.content and not blocks:
            blocks = None
        return LLMResponse(text=text, tool_uses=tool_uses, assistant_blocks=blocks)


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


def _openai_messages(messages: list[dict]) -> list[dict]:
    """Translate the provider-neutral message list to OpenAI's wire format.

    Neutral shapes translated here:
      - {"role": "assistant", "tool_calls": [{"id", "name", "input"}]}
          -> {"role": "assistant", "content": None,
              "tool_calls": [{"id", "type": "function",
                              "function": {"name", "arguments": "<json string>"}}]}
      - {"role": "user", "tool_results": [{"tool_use_id", "content", "is_error"}]}
          -> one {"role": "tool", "tool_call_id", "content"} message per result
             ("Error: " prefixed when is_error)
    Plain-text messages pass through unchanged.
    """
    out = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            out.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("input", {}), ensure_ascii=False),
                        },
                    }
                    for tc in m["tool_calls"]
                ],
            })
        elif m.get("role") == "user" and m.get("tool_results"):
            for tr in m["tool_results"]:
                content = tr.get("content", "")
                if tr.get("is_error"):
                    content = f"Error: {content}"
                out.append({
                    "role": "tool",
                    "tool_call_id": tr["tool_use_id"],
                    "content": content,
                })
        else:
            out.append(dict(m))
    return out


class OpenAICompatProvider(LLMProvider):
    def __init__(self, api_key: str, base_url: str, model: str):
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def complete(self, *, system, messages, tools, model=None, max_tokens=4096) -> LLMResponse:
        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.extend(_openai_messages(messages))
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
