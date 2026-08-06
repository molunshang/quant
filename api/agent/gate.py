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
