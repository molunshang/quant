"""StrategyStore / ChatStore tests (SQLite, tmp_path isolated)."""
from __future__ import annotations

import pytest

from api.agent.store import StrategyStore, ChatStore, AgentSessionStore


def test_draft_publish_versions(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    r1 = s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    assert r1["version"] == 1
    assert r1["status"] == "draft"
    r2 = s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma v2")
    assert r2["version"] == 2

    g = s.get_strategy("ma")
    assert g["current_version"] == 2
    assert len(g["versions"]) == 2
    assert g["versions"][-1]["status"] == "draft"

    pub = s.publish_version("ma", 2, {"total_return": 0.2}, "年化>=10%")
    assert pub["status"] == "published"
    assert pub["metrics"]["total_return"] == 0.2

    g2 = s.get_strategy("ma")
    assert g2["status"] == "published"
    s.close()


def test_get_source_returns_draft_source(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    s.register_draft("ma", "def strategy(ctx, params):\n    pass v1", "sma v1")
    s.register_draft("ma", "def strategy(ctx, params):\n    pass v2", "sma v2")
    # no version -> latest (highest) version source
    assert s.get_source("ma") == "def strategy(ctx, params):\n    pass v2"
    assert s.get_source("ma", 1) == "def strategy(ctx, params):\n    pass v1"
    assert s.get_source("ma", 2) == "def strategy(ctx, params):\n    pass v2"
    # unknown version / unknown strategy -> None
    assert s.get_source("ma", 99) is None
    assert s.get_source("nope") is None
    s.close()


def test_publish_unknown_strategy_raises(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    with pytest.raises(KeyError):
        s.publish_version("nope", 1, {}, "")
    assert s.get_strategy("nope") is None
    s.close()


def test_chat_store_roundtrip(tmp_path):
    c = ChatStore(db_path=str(tmp_path / "t.db"))
    c.add_message("s1", "user", "目标")
    c.add_message("s1", "assistant", "报告")
    msgs = c.list_messages("s1")
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert "s1" in c.list_sessions()
    c.close()


def test_tool_call_roundtrip(tmp_path):
    c = ChatStore(db_path=str(tmp_path / "t.db"))
    mid = c.add_message("s1", "user", "在沪深300做到年化10%")
    c.add_tool_call("s1", mid, 1, "list_symbols", {"type": "stock"}, '{"symbols": []}', False)
    c.add_tool_call("s1", mid, 2, "register_strategy",
                    {"name": "ma", "source": "def strategy(ctx,p): pass"}, '{"version": 1}', True)
    calls = c.list_tool_calls("s1")
    assert len(calls) == 2
    assert calls[0]["message_id"] == mid
    assert calls[0]["name"] == "list_symbols"
    assert calls[0]["turn"] == 1
    assert calls[0]["input"] == '{"type": "stock"}'
    assert calls[0]["output"] == '{"symbols": []}'
    assert calls[0]["is_error"] == 0
    assert calls[1]["is_error"] == 1
    # 按会话隔离
    c.add_message("s2", "user", "其他")
    assert c.list_tool_calls("s2") == []
    c.close()


def test_link_session_strategy_roundtrip(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    s.publish_version("ma", 1, {"total_return": 0.2}, "年化>=10%")
    s.link_session_strategy("s1", "ma", 1)
    rows = s.list_session_strategies("s1")
    assert len(rows) == 1
    assert rows[0]["name"] == "ma"
    assert rows[0]["version"] == 1
    assert rows[0]["status"] == "published"
    assert "def strategy" in rows[0]["source"]
    assert rows[0]["metrics"]["total_return"] == 0.2
    assert rows[0]["goal"] == "年化>=10%"
    # 另一会话无关联
    assert s.list_session_strategies("s2") == []
    s.close()


def test_get_versions_includes_source(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    s.register_draft("ma", "def strategy(ctx, p):\n    pass v1", "sma")
    versions = s.get_versions("ma")
    assert versions[0]["source"] == "def strategy(ctx, p):\n    pass v1"
    s.close()


def test_register_draft_returns_strategy_id(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    r1 = s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    r2 = s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma v2")
    assert isinstance(r1["strategy_id"], int)
    assert r1["strategy_id"] == r2["strategy_id"]  # 同名策略复用同一 id
    s.close()


def test_session_summaries_sorted_and_truncated(tmp_path):
    c = ChatStore(db_path=str(tmp_path / "t.db"))
    c.add_message("s1", "user", "旧")          # 先插入 s1：MAX(id) 更小
    c.add_message("s2", "user", "新" * 40)     # 后插入 s2：MAX(id) 更大
    c.add_message("s2", "assistant", "完成")
    # 对齐 created_at：MAX(created_at) 完全相同
    c.conn.execute("UPDATE chat_messages SET created_at='2026-01-01T00:00:00.000000+00:00'")
    c.conn.commit()
    sums = c.session_summaries(0, 10)
    # 同秒：去掉 MAX(id) DESC 时按 session_id ASC 会得到 ["s1","s2"]（s1 更早），
    # 正是 MAX(id) DESC tie-breaker 让 id 更大的 s2 排前 —— 真正测试该 tie-breaker
    assert [s["session_id"] for s in sums] == ["s2", "s1"]
    assert sums[0]["message_count"] == 2
    assert sums[0]["title"].endswith("…")
    assert len(sums[0]["title"]) == 31
    assert sums[1]["title"] == "旧"


def test_session_summaries_pagination(tmp_path):
    c = ChatStore(db_path=str(tmp_path / "t.db"))
    for i in range(5):
        c.add_message(f"s{i}", "user", f"m{i}")
    # 用显式 created_at（随 id 递增）消除时间戳随机性，顺序完全确定
    c.conn.execute(
        "UPDATE chat_messages SET created_at = printf('2026-01-0%dT00:00:00.000000+00:00', id)"
    )
    c.conn.commit()
    page1 = c.session_summaries(0, 2)
    page2 = c.session_summaries(2, 2)
    page3 = c.session_summaries(4, 2)
    assert [s["session_id"] for s in page1] == ["s4", "s3"]
    assert [s["session_id"] for s in page2] == ["s2", "s1"]
    assert [s["session_id"] for s in page3] == ["s0"]
    assert len(page1) == 2 and len(page2) == 2 and len(page3) == 1


def test_list_session_strategy_names_batch(tmp_path):
    s = StrategyStore(db_path=str(tmp_path / "t.db"))
    s.register_draft("ma", "def strategy(ctx, p):\n    pass", "sma")
    s.register_draft("bb", "def strategy(ctx, p):\n    pass", "bb")
    s.link_session_strategy("s1", "ma", 1)
    s.link_session_strategy("s1", "bb", 1)
    s.link_session_strategy("s2", "ma", 1)
    got = s.list_session_strategy_names(["s1", "s2", "s3"])
    assert got["s1"] == ["bb", "ma"]  # 按名称排序
    assert got["s2"] == ["ma"]
    assert got["s3"] == []


def test_get_many(tmp_path):
    a = AgentSessionStore(db_path=str(tmp_path / "t.db"))
    a.set("s1", "running", goal_json='{"universe": ["沪深300"]}')
    a.set("s2", "done")
    got = a.get_many(["s1", "s2", "s3"])
    assert got["s1"]["status"] == "running"
    assert got["s2"]["status"] == "done"
    assert got["s3"] is None
