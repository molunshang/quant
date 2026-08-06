"""SQLite persistence: strategy drafts/versions/published snapshots + chat history."""
from __future__ import annotations

import functools
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

DB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
DB_PATH = os.path.join(DB_DIR, "agent.db")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _synchronized(method):
    """Serialize access to the shared sqlite connection via a per-instance lock.

    check_same_thread=False allows cross-thread use, but the connection must be
    used by one thread at a time; RLock keeps nested public calls (e.g.
    list_strategies -> get_strategy) from deadlocking.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class StrategyStore:
    def __init__(self, db_path: str | None = None):
        os.makedirs(os.path.dirname(db_path or DB_PATH), exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS strategy_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy_id INTEGER NOT NULL REFERENCES strategies(id),
            version INTEGER NOT NULL,
            source TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft',
            metrics_json TEXT,
            goal TEXT,
            published_at TEXT,
            created_at TEXT,
            UNIQUE(strategy_id, version)
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT
        );
        """)
        self.conn.commit()

    # ---- strategies ----
    @_synchronized
    def _strategy_id(self, name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            cur = self.conn.execute(
                "INSERT INTO strategies (name, status, created_at, updated_at) VALUES (?, 'draft', ?, ?)",
                (name, _now(), _now()),
            )
            return cur.lastrowid
        return row["id"]

    @_synchronized
    def register_draft(self, name: str, source: str, description: str = "") -> dict:
        sid = self._strategy_id(name)
        row = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) AS v FROM strategy_versions WHERE strategy_id = ?",
            (sid,),
        ).fetchone()
        version = int(row["v"]) + 1
        self.conn.execute(
            "INSERT INTO strategy_versions (strategy_id, version, source, description, status, created_at) VALUES (?,?,?,?,'draft',?)",
            (sid, version, source, description, _now()),
        )
        self.conn.execute(
            "UPDATE strategies SET updated_at = ? WHERE id = ?", (_now(), sid)
        )
        self.conn.commit()
        return {"name": name, "version": version, "status": "draft"}

    @_synchronized
    def get_strategy(self, name: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            return None
        versions = self.get_versions(name)
        published = [v for v in versions if v["status"] == "published"]
        return {
            "name": row["name"],
            "description": row["description"],
            "status": "published" if published else "draft",
            "current_version": max((v["version"] for v in versions), default=0),
            "versions": versions,
        }

    @_synchronized
    def list_strategies(self, include_drafts: bool = True) -> list[dict]:
        rows = self.conn.execute(
            "SELECT name, status FROM strategies ORDER BY name"
        ).fetchall()
        out = []
        for r in rows:
            if r["status"] == "draft" and not include_drafts:
                continue
            g = self.get_strategy(r["name"])
            out.append({
                "name": g["name"],
                "status": g["status"],
                "current_version": g["current_version"],
            })
        return out

    @_synchronized
    def publish_version(self, name: str, version: int, metrics: dict, goal: str) -> dict:
        sid_row = self.conn.execute(
            "SELECT id FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if sid_row is None:
            raise KeyError(f"strategy {name!r} has no version {version}")
        sid = sid_row["id"]
        row = self.conn.execute(
            "SELECT id FROM strategy_versions WHERE strategy_id = ? AND version = ?",
            (sid, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"strategy {name!r} has no version {version}")
        now = _now()
        self.conn.execute(
            "UPDATE strategy_versions SET status='published', metrics_json=?, goal=?, published_at=? WHERE id=?",
            (json.dumps(metrics), goal, now, row["id"]),
        )
        self.conn.execute(
            "UPDATE strategies SET status='published', updated_at=? WHERE id=?",
            (now, sid),
        )
        self.conn.commit()
        return {"name": name, "version": version, "status": "published", "metrics": metrics}

    @_synchronized
    def get_source(self, name: str, version: int | None = None) -> str | None:
        """Return the `source` of a strategy version, or the latest version's
        source if `version` is None. Returns None if the strategy or version
        does not exist."""
        sid_row = self.conn.execute(
            "SELECT id FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if sid_row is None:
            return None
        if version is None:
            row = self.conn.execute(
                "SELECT source FROM strategy_versions WHERE strategy_id = ? "
                "ORDER BY version DESC LIMIT 1",
                (sid_row["id"],),
            ).fetchone()
        else:
            row = self.conn.execute(
                "SELECT source FROM strategy_versions WHERE strategy_id = ? AND version = ?",
                (sid_row["id"], version),
            ).fetchone()
        if row is None:
            return None
        return row["source"]

    @_synchronized
    def get_versions(self, name: str) -> list[dict]:
        sid_row = self.conn.execute(
            "SELECT id FROM strategies WHERE name = ?", (name,)
        ).fetchone()
        if sid_row is None:
            return []
        rows = self.conn.execute(
            "SELECT version, source, description, status, metrics_json, goal, published_at, created_at "
            "FROM strategy_versions WHERE strategy_id = ? ORDER BY version",
            (sid_row["id"],),
        ).fetchall()
        return [
            {
                "version": r["version"],
                "status": r["status"],
                "description": r["description"],
                "metrics": json.loads(r["metrics_json"]) if r["metrics_json"] else None,
                "goal": r["goal"],
                "published_at": r["published_at"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    @_synchronized
    def close(self):
        self.conn.close()


class ChatStore:
    def __init__(self, db_path: str | None = None):
        os.makedirs(os.path.dirname(db_path or DB_PATH), exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT
        );
        """)
        self.conn.commit()

    @_synchronized
    def add_message(self, session_id: str, role: str, content: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO chat_messages (session_id, role, content, created_at) VALUES (?,?,?,?)",
            (session_id, role, content, _now()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    @_synchronized
    def list_messages(self, session_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, role, content, created_at FROM chat_messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def list_sessions(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT session_id FROM chat_messages ORDER BY session_id"
        ).fetchall()
        return [r["session_id"] for r in rows]

    @_synchronized
    def close(self):
        self.conn.close()


class AgentSessionStore:
    """SQLite persistence for agent session state (goal-gate state machine).

    A missing row means the session is idle. set() upserts and only updates
    the columns explicitly passed; other columns keep their previous values.
    """
    def __init__(self, db_path: str | None = None):
        os.makedirs(os.path.dirname(db_path or DB_PATH), exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(db_path or DB_PATH, check_same_thread=False, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS agent_sessions (
            session_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            goal_json TEXT,
            questions_json TEXT,
            confirm_summary_json TEXT,
            updated_at TEXT
        );
        """)
        self.conn.commit()

    @_synchronized
    def get(self, session_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT session_id, status, goal_json, questions_json, confirm_summary_json, updated_at "
            "FROM agent_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None

    @_synchronized
    def set(self, session_id: str, status: str, *, goal_json: str | None = None,
            questions_json: str | None = None, confirm_summary_json: str | None = None) -> None:
        now = _now()
        passed = {"goal_json": goal_json, "questions_json": questions_json,
                  "confirm_summary_json": confirm_summary_json}
        existing = self.get(session_id)
        if existing is None:
            self.conn.execute(
                "INSERT INTO agent_sessions "
                "(session_id, status, goal_json, questions_json, confirm_summary_json, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (session_id, status, passed["goal_json"], passed["questions_json"],
                 passed["confirm_summary_json"], now),
            )
        else:
            # 只更新显式传入的字段（非 None）；其余保留旧值
            merged = dict(existing)
            for k, v in passed.items():
                if v is not None:
                    merged[k] = v
            self.conn.execute(
                "UPDATE agent_sessions SET status=?, goal_json=?, questions_json=?, "
                "confirm_summary_json=?, updated_at=? WHERE session_id=?",
                (status, merged["goal_json"], merged["questions_json"],
                 merged["confirm_summary_json"], now, session_id),
            )
        self.conn.commit()

    @_synchronized
    def close(self):
        self.conn.close()
