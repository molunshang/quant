"""Goal gate: structured goal extraction + clarify/confirm before execution.

Pure logic — no I/O except the provider call in gate_extract (Task 3). This
module must not import api.agent.agent (avoids a circular import: agent.py
imports format_goal_text from here).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

DEFAULT_PERIOD = {"start": "2020-01-01", "end": "2024-12-31"}
DEFAULT_BENCHMARK = "沪深300 绝对收益"

_CONFIRM_WORDS = ("确认", "没问题", "可以", "开始", "好", "OK", "对", "是的", "就这样", "同意", "行")
_SUFFIXES = ("吧", "的", "了", "呢", "啊", "呀", "嘛")


@dataclass
class GoalExtraction:
    universe: list[str] | None = None
    constraints: dict[str, float] | None = None
    period: dict[str, str] | None = None
    benchmark: str | None = None
    followup_question: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def missing_fields(extraction: GoalExtraction) -> list[str]:
    """Key fields that must be present before execution.

    period/benchmark have defaults and are NOT key fields; they surface in the
    confirmation summary as editable defaults instead.
    """
    missing = []
    if not extraction.universe:
        missing.append("universe")
    if not extraction.constraints:
        missing.append("constraints")
    return missing


def build_questions(missing: list[str], extraction: GoalExtraction) -> list[str]:
    questions = []
    if "universe" in missing:
        questions.append("想在哪些标的/范围上做回测？例如：沪深300成分、某行业、或具体股票代码。")
    if "constraints" in missing:
        questions.append("量化目标是什么？例如：年化收益≥10%、最大回撤≤15%，请给出具体数值。")
    if extraction.followup_question:
        questions.append(extraction.followup_question)
    return questions


def build_confirmation_summary(extraction: GoalExtraction) -> dict:
    period = extraction.period or dict(DEFAULT_PERIOD)
    benchmark = extraction.benchmark or DEFAULT_BENCHMARK
    return {
        "universe": extraction.universe or ["（未指定，Agent 将自行选择）"],
        "constraints": extraction.constraints or {},
        "period": period,
        "benchmark": benchmark,
        "defaults_noted": {
            "period": extraction.period is None,
            "benchmark": extraction.benchmark is None,
        },
    }


def is_confirmed(text: str) -> bool:
    t = re.sub(r"[\s！!。，,？?：:；;]", "", text.strip())
    if not t:
        return False
    if t in _CONFIRM_WORDS:
        return True
    return any(t in (w + s) for w in _CONFIRM_WORDS for s in _SUFFIXES)


def format_confirmation_text(summary: dict) -> str:
    lines = ["回测目标确认单："]
    lines.append(f"• 标的范围：{'、'.join(summary['universe'])}")
    c = summary["constraints"]
    lines.append(f"• 量化约束：{json.dumps(c, ensure_ascii=False) if c else '（未指定具体数值）'}")
    p = summary["period"]
    d_p = "（默认，可修改）" if summary["defaults_noted"]["period"] else ""
    lines.append(f"• 回测区间：{p['start']} 至 {p['end']}{d_p}")
    d_b = "（默认，可修改）" if summary["defaults_noted"]["benchmark"] else ""
    lines.append(f"• 基准：{summary['benchmark']}{d_b}")
    lines.append("回复「确认」开始执行，或告诉我需要修改的地方。")
    return "\n".join(lines)


def format_goal_text(goal_dict: dict) -> str:
    parts = []
    u = goal_dict.get("universe")
    if u:
        parts.append(f"标的范围：{'、'.join(u)}")
    c = goal_dict.get("constraints")
    if c:
        parts.append(f"量化约束：{json.dumps(c, ensure_ascii=False)}")
    p = goal_dict.get("period") or DEFAULT_PERIOD
    parts.append(f"回测区间：{p.get('start')} 至 {p.get('end')}")
    parts.append(f"基准：{goal_dict.get('benchmark') or DEFAULT_BENCHMARK}")
    return "；".join(parts)


def gate_step(extraction: GoalExtraction) -> tuple[str, object]:
    """Return ("clarify", questions) or ("confirm", confirmation_summary)."""
    missing = missing_fields(extraction)
    if missing or extraction.followup_question:
        return "clarify", build_questions(missing, extraction)
    return "confirm", build_confirmation_summary(extraction)


_GATE_SYSTEM = """你是A股量化回测目标提取器。从用户描述中提取结构化JSON，仅输出JSON对象，不要任何解释。

输出结构：
{
  "universe": ["标的范围，如'沪深300成分'、'白酒行业'、'510300'"],
  "constraints": {"annual_return": 0.10, "max_drawdown": -0.15, "total_return": 0.5, "sharpe": 1.0, "win_rate": 0.6},
  "period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
  "benchmark": "基准，如'沪深300'或'绝对收益'",
  "followup_question": "若目标仍有歧义需要追问用户，写一个问题；否则省略该字段"
}
规则：数值一律用小数（10%→0.10；回撤15%→-0.15）。无法从描述确定的字段省略。"""


def gate_extract(message: str, history: list[str], provider, goal: str | None = None) -> GoalExtraction:
    user_text = "\n".join([goal or "", *history, message])
    resp = provider.complete(
        system=_GATE_SYSTEM,
        messages=[{"role": "user", "content": user_text}],
        tools=[],
    )
    data = _parse_json(resp.text or "")
    return GoalExtraction(
        universe=_coerce_strings(data.get("universe")),
        constraints=_coerce_constraints(data.get("constraints")),
        period=_coerce_period(data.get("period")),
        benchmark=data.get("benchmark") if isinstance(data.get("benchmark"), str) else None,
        followup_question=data.get("followup_question") if isinstance(data.get("followup_question"), str) else None,
    )


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _coerce_strings(value) -> list[str] | None:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if isinstance(value, list):
        items = [v.strip() for v in value if isinstance(v, str) and v.strip()]
        return items or None
    return None


_DRAWDOWN_KEYS = ("max_drawdown",)


def _coerce_constraints(value) -> dict[str, float] | None:
    if not isinstance(value, dict) or not value:
        return None
    out = {}
    for k, v in value.items():
        if isinstance(v, (int, float)):
            num = float(v)
        elif isinstance(v, str):
            m = re.search(r"(\d+(?:\.\d+)?)", v)
            if not m:
                continue
            num = float(m.group(1))
            if "%" in v and abs(num) > 1:
                num /= 100.0
        else:
            continue
        if k in _DRAWDOWN_KEYS and num > 0:
            num = -num  # 回撤是亏损指标，正值归一为负（spec 全局约束）
        out[k] = num
    return out or None


def _coerce_period(value) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    start = value.get("start")
    end = value.get("end")
    if isinstance(start, str) and isinstance(end, str) and start and end:
        return {"start": start, "end": end}
    return None
