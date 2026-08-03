"""StrategyStore / ChatStore tests (SQLite, tmp_path isolated)."""
from __future__ import annotations

import pytest

from api.agent.store import StrategyStore, ChatStore


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
