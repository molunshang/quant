"""LLM provider configuration management: validated, atomic read/write of config/llm.json."""
from __future__ import annotations

import json
import os
import threading

from .provider import AnthropicProvider, OpenAICompatProvider, _expand_env, load_providers

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "llm.json"
)
VALID_TYPES = ("anthropic", "openai_compat")


class ConfigError(Exception):
    """Invalid provider config. Message is user-facing Chinese."""


def _default_path() -> str:
    return os.environ.get("QUANT_LLM_CONFIG") or DEFAULT_PATH


class ProviderConfigStore:
    def __init__(self, path: str | None = None):
        self.path = path or _default_path()
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._lock = threading.RLock()
        self._default: str | None = None
        self._providers: dict[str, object] = {}
        self._load_error: str | None = None
        if os.path.exists(self.path):
            try:
                self._reload()
            except ConfigError as e:
                self._load_error = str(e)

    # ---- file I/O ----
    def _read(self) -> dict:
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except FileNotFoundError:
            return {"default": None, "providers": []}
        except json.JSONDecodeError as e:
            raise ConfigError(f"配置文件格式错误: {e}") from e
        if not isinstance(cfg, dict):
            raise ConfigError("配置文件格式错误: 顶层必须是 JSON 对象")
        providers = cfg.get("providers", [])
        if not isinstance(providers, list):
            raise ConfigError("配置文件格式错误: providers 必须是数组")
        return {"default": cfg.get("default"), "providers": providers}

    def _write(self, cfg: dict) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def _reload(self) -> None:
        with self._lock:
            cfg = self._read()
            self._default = cfg["default"]
            self._load_error = None
            try:
                self._providers = load_providers(self.path)
            except (ValueError, KeyError) as e:
                self._providers = {}
                self._load_error = f"配置解析失败: {e}"

    # ---- reads ----
    def list(self) -> dict:
        cfg = self._read()
        return {"default": cfg["default"], "providers": cfg["providers"], "error": self._load_error}

    def get(self, name: str) -> dict | None:
        for p in self.list()["providers"]:
            if p.get("name") == name:
                return p
        return None

    def get_default(self) -> str | None:
        with self._lock:
            return self._default

    def providers(self) -> dict[str, object]:
        with self._lock:
            return dict(self._providers)
