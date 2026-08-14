"""LLMAgent: state-machine tool loop driving an LLM to meet a user goal."""
from __future__ import annotations

import json
import threading
import uuid
from collections import deque
from typing import Generator

from strategies.manager import StrategyManager

from .gate import format_goal_text
from .provider import LLMProvider, LLMResponse, ToolCall
from .tools import TOOLS, AgentToolContext


def build_system_prompt(goal: str | dict | None = None) -> str:
    lines = [
        "你是A股量化策略优化助手。用户给出投资目标（如年化收益、超额收益、最大回撤）。",
        "你的工作流程：",
        "1. 用 register_strategy 编写/修改组合策略草稿。源码定义 def initialize(ctx)（可选，一次初始化）+ def handle_data(ctx)（每个交易日调用一次）。用 ctx.history(symbol, lookback) 读取各标的截至当前 bar 的历史、ctx.price(symbol)、ctx.positions、ctx.cash、ctx.total_value 分析行情与持仓；用 ctx.buy(symbol, pct) / ctx.sell(symbol, pct) 下单（pct 相对组合净值 0~1，返回 bool；sell 默认清仓）。标的由策略自己在 ctx.universe 里选。引擎按 A 股规则成交：T+1、涨跌停、100 股整手。指标助手 sma/ema/rsi/macd 已内置可直接调用（无需 import）；也可 import math/numpy/pandas（常用别名 math/np/pd）。",
        "2. 用 run_backtest 提交组合回测（strategy_ref 用当前草稿名），可传 universe 限制标的池（缺省=已缓存标的集），可一次提交多个并行。回测只能跑训练段区间（即目标区间的 start~end）；验证段由系统在发布时自动验收，禁止（也无法）直接回测验证段。",
        "3. 查看回测指标，用 check_goal 校验是否达标；未达标可先用 diagnose_backtest(job_id) 深挖原因（月度收益、回撤起止、标的盈亏归因），再修改草稿重跑。",
        "4. 达标后必须用 publish_strategy（goal_met=true）发布。发布时系统自动在未见过的验证段上做期末考，任一验证段不达标都会拒绝发布并反馈差距，Agent 回到训练段继续调优。",
        "工具结果会被合并到下一轮。达成目标后给出简短中文汇报。",
    ]
    if goal:
        if isinstance(goal, dict):
            lines.append(f"\n用户目标：{format_goal_text(goal)}")
            c = goal.get("constraints") or {}
            if c:
                lines.append(f"目标约束（必须满足，check_goal 用这些阈值校验）：{json.dumps(c, ensure_ascii=False)}")
            if goal.get("universe"):
                lines.append(f"标的池范围（策略在 universe 内自选标的）：{'、'.join(goal['universe'])}"
                             "（指数/行业名可用 list_symbols(index=...) 或 list_symbols(industry=...) 展开成具体标的；list_industries 可查行业）")
            p = goal.get("period")
            if p:
                lines.append(f"回测时间区间：{p.get('start')} 至 {p.get('end')}（run_backtest 的 start/end 使用此区间）")
            if goal.get("benchmark"):
                lines.append(f"基准：{goal['benchmark']}")
        else:
            lines.append(f"\n用户目标：{goal}")
    return "\n".join(lines)


def _build_state_snapshot(ctx: AgentToolContext, goal: str | None) -> dict:
    # never leak validation-period dates to the LLM — strip them from the
    # goal snapshot (they are enforced internally by publish_strategy only)
    if isinstance(goal, dict):
        goal = {k: v for k, v in goal.items() if k != "validation_periods"}
    drafts = []
    for s in ctx.store.list_strategies():
        g = ctx.store.get_strategy(s["name"])
        if g is None:
            continue
        drafts.append({
            "name": g["name"],
            "status": g["status"],
            "current_version": g["current_version"],
        })
    return {"goal": goal, "strategies": drafts}


def _result_to_text(res: dict) -> str:
    """Compact backtest result for LLM context."""
    r = res.get("result")
    if r is None:
        return f"回测 job #{res.get('job_id')} 失败: {res.get('error')}"
    m = r.get("metrics", {})
    keep = {k: m.get(k) for k in
            ("total_return", "annual_return", "max_drawdown", "sharpe", "volatility",
             "win_rate", "n_trades", "excess_return", "calmar", "sortino", "turnover",
             "avg_holdings", "max_concentration", "monthly_win_rate")}
    return json.dumps({
        "job_id": res.get("job_id"),
        "symbol": r.get("symbol"),
        "symbol_name": r.get("symbol_name"),
        "metrics": keep,
    }, ensure_ascii=False)


class EventBus:
    """In-memory per-session event queue (drained on read)."""
    def __init__(self):
        self._sessions: dict[str, deque] = {}
        self._cond = threading.Condition()

    def create_session(self) -> str:
        sid = uuid.uuid4().hex[:12]
        with self._cond:
            self._sessions[sid] = deque()
        return sid

    def publish(self, session_id: str, event: dict):
        with self._cond:
            self._sessions.setdefault(session_id, deque()).append(event)
            self._cond.notify_all()

    def stream(self, session_id: str) -> Generator[dict, None, None]:
        """Drain queued events, then wait for new ones (caller handles heartbeat/timeout)."""
        while True:
            with self._cond:
                q = self._sessions.get(session_id)
                if q:
                    yield q.popleft()
                    continue
                if self._sessions.get(session_id) is None or getattr(self, "_stopped", False):
                    return
                self._cond.wait(timeout=15.0)
            yield {"type": "heartbeat"}  # signal caller to send SSE :ping


class LLMAgent:
    def __init__(self, provider, store, executor, chat_store=None, max_turns=10, max_tools_per_turn=5):
        self.provider = provider
        self.store = store
        self.executor = executor
        self.chat_store = chat_store
        self.max_turns = max_turns
        self.max_tools_per_turn = max_tools_per_turn
        self._manager = StrategyManager()
        self._ctx = AgentToolContext(store=store, executor=executor, strategy_manager=self._manager)
        # Bridge store drafts -> run_backtest: the executor hands this shared,
        # per-turn hydrated manager to api.runner.run_backtest on every submit.
        if hasattr(executor, "_strategy_manager"):
            executor._strategy_manager = self._manager

    def _hydrate_manager(self):
        """Load the current store drafts into the shared StrategyManager so
        run_backtest(strategy_ref=<name>) resolves the draft source."""
        for s in self._ctx.store.list_strategies():
            src = self._ctx.store.get_source(s["name"])
            if src is not None:
                self._ctx.strategy_manager.register(s["name"], src)

    def _tool_name_to_fn(self, name: str):
        import api.agent.tools as T
        return {
            "list_symbols": T.list_symbols,
            "run_backtest": T.run_backtest,
            "register_strategy": T.register_strategy,
            "list_strategies": T.list_strategies,
            "publish_strategy": T.publish_strategy,
            "check_goal": T.check_goal,
            "list_industries": T.list_industries,
            "query_sector_perf": T.query_sector_perf,
            "diagnose_backtest": T.diagnose_backtest,
        }[name]

    def run(self, session_id: str, user_message: str, goal: str | None = None,
            bus: EventBus | None = None, message_id: int | None = None) -> dict:
        self._ctx.session_id = session_id
        self._ctx.goal = goal if isinstance(goal, dict) else {}
        self._ctx.training_period = goal.get("period") if isinstance(goal, dict) else None
        system = build_system_prompt(goal or user_message)
        # messages: user goal first, then alternating assistant tool_calls / user
        # tool_results as the loop runs. These are PROVIDER-NEUTRAL shapes; each
        # LLMProvider translates them to its own wire format inside complete().
        messages = [{"role": "user", "content": user_message}]
        final_text = ""
        for turn in range(self.max_turns):
            if bus:
                bus.publish(session_id, {"type": "turn", "turn": turn + 1})
            # Hydrate BEFORE tool execution so strategies registered in previous
            # turns resolve deterministically when run_backtest submits a job.
            self._hydrate_manager()
            try:
                resp = self.provider.complete(system=system, messages=messages, tools=TOOLS)
            except Exception as e:  # noqa: BLE001 - bad key / down proxy -> surface to user
                if bus:
                    bus.publish(session_id, {"type": "error", "error": str(e)})
                return {"session_id": session_id, "report": f"出错了: {e}", "turns": turn + 1, "error": str(e)}
            if resp.text:
                final_text = resp.text
            if not resp.tool_uses:
                break  # LLM done (report)
            # execute tools (bounded concurrency), collect tool_results
            tool_results = []
            for tc in resp.tool_uses[: self.max_tools_per_turn]:
                try:
                    fn = self._tool_name_to_fn(tc.name)
                    out = fn(tc.input, self._ctx)
                    tool_results.append({
                        "tool_use_id": tc.id, "content": out, "is_error": False,
                    })
                    if bus:
                        bus.publish(session_id, {"type": "tool", "name": tc.name, "output": out, "input": tc.input})
                    if self.chat_store is not None and message_id is not None:
                        try:
                            self.chat_store.add_tool_call(session_id, message_id, turn, tc.name, tc.input, out, is_error=False)
                        except Exception:  # noqa: BLE001 - 记录失败不影响工具结果与运行
                            pass
                except Exception as e:  # noqa: BLE001 - tool error surfaced to LLM
                    tool_results.append({
                        "tool_use_id": tc.id, "content": f"ERROR: {e}", "is_error": True,
                    })
                    if bus:
                        bus.publish(session_id, {"type": "tool_error", "name": tc.name, "error": str(e), "input": tc.input})
                    if self.chat_store is not None and message_id is not None:
                        try:
                            self.chat_store.add_tool_call(session_id, message_id, turn, tc.name, tc.input, str(e), is_error=True)
                        except Exception:  # noqa: BLE001 - 记录失败不影响工具结果与运行
                            pass
            # Hydrate AFTER tool execution and BEFORE wait_all: a strategy
            # registered in THIS turn (register_strategy) must resolve when its
            # run_backtest jobs hit the executor's worker threads.
            self._hydrate_manager()
            # backtests submitted this turn: wait for the batch, attach results.
            # The summary+state snapshot is emitted as a separate plain-text user
            # message AFTER the tool_results user message, not as an extra tool_result
            # (a tool_result's tool_use_id must match a tool_use block in the preceding
            # assistant message; an orphan id like "__state__" is rejected with HTTP 400
            # by the Messages API). Consecutive user messages are valid and are
            # combined into a single turn by the API.
            state_text = None
            if any(tc.name == "run_backtest" for tc in resp.tool_uses):
                try:
                    results = self.executor.wait_all(timeout=300)
                except Exception as e:  # noqa: BLE001 - executor failure -> surface to user
                    if bus:
                        bus.publish(session_id, {"type": "error", "error": str(e)})
                    return {"session_id": session_id, "report": f"出错了: {e}", "turns": turn + 1, "error": str(e)}
                self.executor.reset_batch()
                snapshot = _build_state_snapshot(self._ctx, goal or user_message)
                backtest_summary = "\n".join(_result_to_text(r) for r in results)
                state_text = f"回测结果汇总：\n{backtest_summary}\n当前状态：{json.dumps(snapshot, ensure_ascii=False)}"
                if bus:
                    bus.publish(session_id, {"type": "backtest_results", "results": results})
            messages.append({
                "role": "assistant",
                "tool_calls": [{"id": tc.id, "name": tc.name, "input": tc.input} for tc in resp.tool_uses],
                "assistant_blocks": resp.assistant_blocks,
            })
            messages.append({"role": "user", "tool_results": [{"tool_use_id": tr["tool_use_id"], "content": tr["content"], "is_error": tr["is_error"]} for tr in tool_results]})
            if state_text is not None:
                messages.append({"role": "user", "content": state_text})
        if bus:
            bus.publish(session_id, {"type": "done", "report": final_text})
        return {"session_id": session_id, "report": final_text, "turns": turn + 1}
