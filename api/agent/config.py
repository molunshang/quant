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
            except Exception as e:  # noqa: BLE001 - any load failure degrades to {} + surfaced error
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

    # ---- validation ----
    def _validate(self, p: dict, existing: set[str]) -> None:
        name = p.get("name", "")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError("名称不能为空")
        if name in existing:
            raise ConfigError(f"provider 名称已存在: {name}")
        typ = p.get("type")
        if typ not in VALID_TYPES:
            raise ConfigError(f"类型必须为 {'/'.join(VALID_TYPES)}，当前: {typ}")
        if not isinstance(p.get("model"), str) or not p["model"].strip():
            raise ConfigError("模型名称不能为空")
        api_key = p.get("api_key", "")
        if not isinstance(api_key, str) or not api_key.strip():
            raise ConfigError("API Key 不能为空")
        base_url = p.get("base_url", "")
        if typ == "openai_compat" and not (isinstance(base_url, str) and base_url.strip()):
            raise ConfigError("openai_compat 类型必须填写 base_url")
        if typ == "openai_compat" and not base_url.startswith(("http://", "https://")):
            raise ConfigError("base_url 必须以 http:// 或 https:// 开头")

    # ---- mutations (each validates, writes, reloads) ----
    def add(self, p: dict) -> dict:
        with self._lock:
            cfg = self._read()
            self._validate(p, {x["name"] for x in cfg["providers"]})
            cfg["providers"].append(p)
            self._write(cfg)
            self._reload()
            return self.list()

    def update(self, name: str, p: dict) -> dict:
        with self._lock:
            cfg = self._read()
            names = [x["name"] for x in cfg["providers"]]
            if name not in names:
                raise ConfigError(f"provider 不存在: {name}")
            p = dict(p)
            p["name"] = name  # name is identity, not editable
            existing = set(names) - {name}
            self._validate(p, existing)
            cfg["providers"] = [p if x["name"] == name else x for x in cfg["providers"]]
            self._write(cfg)
            self._reload()
            return self.list()

    def delete(self, name: str) -> dict:
        with self._lock:
            cfg = self._read()
            kept = [x for x in cfg["providers"] if x["name"] != name]
            if len(kept) == len(cfg["providers"]):
                raise ConfigError(f"provider 不存在: {name}")
            cfg["providers"] = kept
            if cfg.get("default") == name:
                cfg["default"] = None
            self._write(cfg)
            self._reload()
            return self.list()

    def set_default(self, name: str) -> dict:
        with self._lock:
            cfg = self._read()
            if not any(x["name"] == name for x in cfg["providers"]):
                raise ConfigError(f"provider 不存在: {name}")
            cfg["default"] = name
            self._write(cfg)
            self._reload()
            return self.list()

    # ---- test connectivity ----
    def test(self, p: dict) -> dict:
        api_key = p.get("api_key", "")
        if isinstance(api_key, str) and api_key.startswith("env:"):
            var = api_key[4:]
            if not os.environ.get(var):
                return {"ok": False, "error": f"环境变量 {var} 未设置"}
        try:
            self._validate(dict(p, name=p.get("name") or "_test"), set())
        except ConfigError as e:
            return {"ok": False, "error": str(e)}
        base_url = p.get("base_url") or "(使用默认地址)"
        try:
            self._ping(self._build(p))
        except Exception as e:  # noqa: BLE001 - surface underlying error for diagnosis
            status = getattr(e, "status_code", None)
            detail = f"HTTP {status}: {e}" if status else (str(e) or type(e).__name__)
            return {"ok": False, "error": f"{base_url} → {detail}"}
        return {"ok": True, "error": None}

    def _build(self, p: dict):
        typ = p["type"]
        api_key = _expand_env(p.get("api_key", ""))
        model = p.get("model", "claude-opus-5")
        if typ == "anthropic":
            return AnthropicProvider(api_key=api_key, base_url=p.get("base_url"), model=model)
        return OpenAICompatProvider(api_key=api_key, base_url=p["base_url"], model=model)

    def _ping(self, provider) -> None:
        provider.complete(
            system="ping",
            messages=[{"role": "user", "content": "ping"}],
            tools=[],
            max_tokens=1,
        )
