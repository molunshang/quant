"""FastAPI routes for the LLM agent (chat, SSE, providers, published strategies)."""
from __future__ import annotations

import json
import logging
import threading

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from .agent import EventBus, LLMAgent
from .executor import BacktestExecutor
from .config import ConfigError, ProviderConfigStore
from .gate import (
    format_confirmation_text, format_goal_text, gate_extract, gate_step, is_confirmed,
)
from .store import AgentSessionStore, ChatStore, StrategyStore


def handle_chat(session_id, message, goal, provider, bus, session_store, chat_store, store, executor,
                message_id=None) -> dict:
    """Goal-gate state machine dispatch for one user message.

    Returns an outcome dict: {"outcome": "clarify"|"confirm"|"running"|"error", ...}.
    """
    row = session_store.get(session_id)
    status = row["status"] if row else "idle"

    if status == "running":
        bus.publish(session_id, {"type": "error", "error": "正在运行中，请稍候再试"})
        return {"outcome": "error", "reason": "running"}

    msgs = [m["content"] for m in chat_store.list_messages(session_id)
            if m["role"] in ("user", "assistant")]
    if msgs and msgs[-1] == message:
        msgs = msgs[:-1]  # current message passed separately to gate_extract
    history = msgs

    if status == "pending_confirm" and row and is_confirmed(message):
        # 用户确认 -> 注入确认后的结构化目标，启动现有 agent 循环
        goal_dict = json.loads(row["goal_json"]) if row.get("goal_json") else {}
        session_store.set(session_id, "running")
        bus.publish(session_id, {"type": "running"})
        try:
            agent = LLMAgent(provider=provider, store=store, executor=executor, chat_store=chat_store)
            report = agent.run(session_id, format_goal_text(goal_dict), goal=goal_dict, bus=bus,
                               message_id=message_id)
            chat_store.add_message(session_id, "assistant", report.get("report", ""))
            session_store.set(session_id, "done")
            return {"outcome": "running", "report": report}
        except Exception:
            # 运行异常 -> 回到 pending_confirm，避免会话永久卡在 running
            session_store.set(session_id, "pending_confirm",
                              confirm_summary_json=row.get("confirm_summary_json"))
            raise

    if status in ("idle", "done", "pending_clarify", "pending_confirm"):
        # 新目标 / 澄清答复 / 确认单上的修改意见 -> 走提取+step+挂起
        return _extract_and_advance(session_id, message, history, goal, provider, bus, session_store)

    bus.publish(session_id, {"type": "error", "error": f"未知会话状态: {status}"})
    return {"outcome": "error", "reason": status}


def _extract_and_advance(session_id, message, history, goal, provider, bus, session_store) -> dict:
    extraction = gate_extract(message, history, provider, goal=goal)
    step_name, payload = gate_step(extraction)
    if step_name == "clarify":
        session_store.set(session_id, "pending_clarify",
                          goal_json=json.dumps(extraction.to_dict(), ensure_ascii=False),
                          questions_json=json.dumps(payload, ensure_ascii=False))
        bus.publish(session_id, {"type": "clarify", "questions": payload})
        return {"outcome": "clarify", "questions": payload}
    session_store.set(session_id, "pending_confirm",
                      goal_json=json.dumps(extraction.to_dict(), ensure_ascii=False),
                      confirm_summary_json=json.dumps(payload, ensure_ascii=False))
    text = format_confirmation_text(payload)
    bus.publish(session_id, {"type": "confirm", "summary": payload, "text": text})
    return {"outcome": "confirm", "summary": payload}


def register_agent_routes(app: FastAPI, bus=None, store=None, chat_store=None, executor=None,
                          config=None, session_store=None) -> None:
    bus = bus or EventBus()
    store = store or StrategyStore()
    chat_store = chat_store or ChatStore()
    executor = executor or BacktestExecutor()
    config = config or ProviderConfigStore()  # {name: LLMProvider} cache, reloaded on every write
    session_store = session_store or AgentSessionStore()

    @app.post("/api/chat")
    def chat(body: dict):
        message = body.get("message", "")
        if not message:
            raise HTTPException(status_code=400, detail="message required")
        session_id = body.get("session_id") or bus.create_session()
        goal = body.get("goal")
        msg_id = chat_store.add_message(session_id, "user", message)

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

        chat_store.add_message(session_id, "system", f"目标: {goal or message}")

        def _run():
            try:
                handle_chat(session_id, message, goal, provider, bus,
                            session_store, chat_store, store, executor, message_id=msg_id)
            except Exception as e:  # noqa: BLE001 - surface errors, no eternal spinner
                logging.exception("chat run failed for session %s", session_id)
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

    @app.get("/api/chat/history")
    def chat_history():
        sessions = []
        for sid in chat_store.list_sessions():
            msgs = chat_store.list_messages(sid)
            if not msgs:
                continue
            first_user = next((m["content"] for m in msgs if m["role"] == "user"), "")
            title = first_user[:30] + ("…" if len(first_user) > 30 else "")
            row = session_store.get(sid)
            sessions.append({
                "session_id": sid,
                "title": title,
                "created_at": msgs[0]["created_at"],
                "updated_at": msgs[-1]["created_at"],
                "message_count": len(msgs),
                "strategy_names": [s["name"] for s in store.list_session_strategies(sid)],
                "status": row["status"] if row else "done",
            })
        sessions.sort(key=lambda s: s["updated_at"] or "", reverse=True)
        return {"sessions": sessions}

    @app.get("/api/chat/sessions/{sid}")
    def chat_session_detail(sid: str):
        msgs = chat_store.list_messages(sid)
        if not msgs:
            raise HTTPException(status_code=404, detail="会话不存在")
        calls = chat_store.list_tool_calls(sid)
        by_msg: dict[int, list] = {}
        for c in calls:
            by_msg.setdefault(c["message_id"], []).append({
                "name": c["name"], "input": c["input"], "output": c["output"],
                "is_error": c["is_error"], "turn": c["turn"],
            })
        messages = [
            {"id": m["id"], "role": m["role"], "content": m["content"], "created_at": m["created_at"],
             "tool_calls": by_msg.get(m["id"], [])}
            for m in msgs
        ]
        return {"session_id": sid, "messages": messages, "strategies": store.list_session_strategies(sid)}

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
