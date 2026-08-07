"""一次性清理事实库中的测试垃圾（Option B：保留真实会话/策略）。

保留：会话 474dee0be1a6、43eff297c199；策略 etf_ma_cross、etf_trend。
删除：其余全部会话数据 + smoke_strat 策略。
修正：43eff297c199 的 agent_sessions.status='running' -> 'done'。
"""
import os
import sqlite3
import sys

KEEP_SESSIONS = {"474dee0be1a6", "43eff297c199"}
KEEP_STRATEGIES = {"etf_ma_cross", "etf_trend"}

DB = os.environ.get("QUANT_AGENT_DB") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data", "agent.db"
)


def main():
    if "--commit" not in sys.argv:
        print("DRY-RUN：仅打印将删除的行数。加 --commit 真正执行。")
        commit = False
    else:
        commit = True
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # 会话相关：删除非保留会话
    keep_sql = ",".join("?" * len(KEEP_SESSIONS))
    for table in ("chat_messages", "tool_calls", "session_strategies"):
        rows = cur.execute(
            f"SELECT COUNT(*) n FROM {table} WHERE session_id NOT IN ({keep_sql})",
            tuple(KEEP_SESSIONS),
        ).fetchone()
        print(f"  {table}: 将删除 {rows['n']} 行")
    # 会话无关的 agent_sessions 删除同规则（保留会话之外的行）
    rows = cur.execute(
        f"SELECT COUNT(*) n FROM agent_sessions WHERE session_id NOT IN ({keep_sql})",
        tuple(KEEP_SESSIONS),
    ).fetchone()
    print(f"  agent_sessions: 将删除 {rows['n']} 行")

    # 策略：删除 smoke_strat
    st_rows = cur.execute(
        "SELECT id, name FROM strategies WHERE name NOT IN (?,?)", tuple(KEEP_STRATEGIES)
    ).fetchall()
    for r in st_rows:
        print(f"  strategy {r['name']} (id={r['id']}): 删除策略及其版本")

    if not commit:
        conn.close()
        return

    for table in ("chat_messages", "tool_calls", "session_strategies"):
        cur.execute(
            f"DELETE FROM {table} WHERE session_id NOT IN ({keep_sql})",
            tuple(KEEP_SESSIONS),
        )
    cur.execute(
        f"DELETE FROM agent_sessions WHERE session_id NOT IN ({keep_sql})",
        tuple(KEEP_SESSIONS),
    )
    for r in st_rows:
        cur.execute("DELETE FROM strategy_versions WHERE strategy_id=?", (r["id"],))
        cur.execute("DELETE FROM strategies WHERE id=?", (r["id"],))
    # 修正卡死状态
    cur.execute(
        "UPDATE agent_sessions SET status='done' WHERE session_id='43eff297c199' AND status='running'"
    )
    conn.commit()
    conn.close()
    print("完成。")


if __name__ == "__main__":
    main()
