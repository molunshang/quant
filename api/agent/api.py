"""FastAPI routes for the LLM agent (chat, SSE, providers, published strategies)."""
from __future__ import annotations

import json
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .agent import EventBus, LLMAgent
from .executor import BacktestExecutor
from .config import ConfigError, ProviderConfigStore
from .store import ChatStore, StrategyStore


def register_agent_routes(app: FastAPI) -> None:
    bus = EventBus()
    store = StrategyStore()
    chat_store = ChatStore()
    executor = BacktestExecutor()
    config = ProviderConfigStore()  # {name: LLMProvider} cache, reloaded on every write

    @app.post("/api/chat")
    def chat(body: dict):
        message = body.get("message", "")
        if not message:
            raise HTTPException(status_code=400, detail="message required")
        session_id = body.get("session_id") or bus.create_session()
        goal = body.get("goal")
        chat_store.add_message(session_id, "user", message)

        provider_name = body.get("provider")
        providers = config.providers()
        if not providers:
            raise HTTPException(status_code=400, detail="no provider configured")
        if provider_name:
            provider = providers.get(provider_name)
            if provider is None:
                raise HTTPException(status_code=400, detail=f"未知 provider: {provider_name}")
        else:
            default = config.get_default()
            provider = providers.get(default) if default else next(iter(providers.values()))

        agent = LLMAgent(provider=provider, store=store, executor=executor)
        chat_store.add_message(session_id, "system", f"目标: {goal or message}")

        def _run():
            try:
                report = agent.run(session_id, message, goal=goal, bus=bus)
                chat_store.add_message(session_id, "assistant", report.get("report", ""))
            except Exception as e:  # noqa: BLE001 - surface errors to the user, no eternal spinner
                bus.publish(session_id, {"type": "error", "error": str(e)})
                chat_store.add_message(session_id, "assistant", f"出错了: {e}")

        threading.Thread(target=_run, daemon=True).start()
        return {"session_id": session_id}

    @app.get("/api/chat/events")
    def chat_events(session_id: str):
        def gen():
            yield "retry: 3000\n\n"  # SSE reconnect hint
            for event in bus.stream(session_id):
                if event.get("type") == "heartbeat":
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/chat/sessions")
    def chat_sessions():
        return {"sessions": chat_store.list_sessions()}

    @app.get("/api/providers")
    def providers_list():
        return {"providers": list(config.providers().keys())}

    @app.get("/api/llm/providers/list")
    def llm_providers_list():
        try:
            return config.list()
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/llm/providers/add")
    def llm_providers_add(body: dict):
        try:
            return config.add(body)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/llm/providers/update")
    def llm_providers_update(body: dict):
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="缺少 name")
        try:
            return config.update(name, body)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/llm/providers/delete")
    def llm_providers_delete(body: dict):
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="缺少 name")
        try:
            return config.delete(name)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/api/llm/providers/test")
    def llm_providers_test(body: dict):
        return config.test(body)

    @app.post("/api/llm/default/set")
    def llm_default_set(body: dict):
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=400, detail="缺少 name")
        try:
            return config.set_default(name)
        except ConfigError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.get("/api/strategies/published")
    def published():
        return {"strategies": store.list_strategies(include_drafts=False)}

    @app.get("/api/strategies/{name}/versions")
    def strategy_versions(name: str):
        g = store.get_strategy(name)
        if g is None:
            raise HTTPException(status_code=404, detail="unknown strategy")
        return {"name": name, "versions": g["versions"]}
