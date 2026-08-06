"""Goal gate tests (pure logic + state machine, fake provider, no network)."""
from __future__ import annotations

from api.agent.store import AgentSessionStore


def test_agent_session_store_roundtrip(tmp_path):
    store = AgentSessionStore(str(tmp_path / "t.db"))
    assert store.get("s1") is None  # idle = no row

    store.set("s1", "pending_confirm", goal_json='{"universe": ["沪深300"]}')
    row = store.get("s1")
    assert row["status"] == "pending_confirm"
    assert "沪深300" in row["goal_json"]

    # set without goal_json must preserve the stored value
    store.set("s1", "running")
    row = store.get("s1")
    assert row["status"] == "running"
    assert "沪深300" in row["goal_json"]  # preserved


def test_agent_session_store_status_transition(tmp_path):
    store = AgentSessionStore(str(tmp_path / "t.db"))
    store.set("s1", "pending_clarify", questions_json='["问题A"]')
    store.set("s1", "pending_confirm", confirm_summary_json='{"universe": ["沪深300"]}')
    row = store.get("s1")
    assert row["status"] == "pending_confirm"
    assert row["questions_json"] == '["问题A"]'  # preserved — set() only updates passed fields
    assert "沪深300" in row["confirm_summary_json"]
