import logging
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DB_PATH = None
_local = threading.local()


def _get_db() -> sqlite3.Connection:
    if not hasattr(_local, 'conn') or _local.conn is None:
        global _DB_PATH
        if _DB_PATH is None:
            home = os.environ.get("PEBBLE_HOME") or os.path.join(Path.home(), ".pebble")
            os.makedirs(home, exist_ok=True)
            _DB_PATH = os.path.join(home, "sessions.db")
        _local.conn = sqlite3.connect(_DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
        _init_db(_local.conn)
    return _local.conn


def _init_db(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            preview TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            project_path TEXT NOT NULL DEFAULT '',
            message_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            reasoning TEXT,
            tool_name TEXT,
            tool_context TEXT,
            msg_uuid TEXT NOT NULL DEFAULT '',
            sort_order INTEGER NOT NULL DEFAULT 0,
            is_compacted INTEGER NOT NULL DEFAULT 0,
            is_compact_summary INTEGER NOT NULL DEFAULT 0,
            interrupted INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, sort_order);
        CREATE TABLE IF NOT EXISTS session_costs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            model TEXT NOT NULL DEFAULT '',
            input_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
            cost_cny REAL NOT NULL DEFAULT 0,
            input_cost REAL NOT NULL DEFAULT 0,
            output_cost REAL NOT NULL DEFAULT 0,
            cache_hit_cost REAL NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_costs_session ON session_costs(session_id, created_at);
    """)
    # 迁移：旧表缺少列时补充
    for col, col_type in [
        ("msg_uuid", "TEXT NOT NULL DEFAULT ''"),
        ("tool_name", "TEXT"),
        ("tool_context", "TEXT"),
        ("tool_call_id", "TEXT"),
        ("tool_calls", "TEXT"),
        ("sort_order", "INTEGER NOT NULL DEFAULT 0"),
        ("is_compacted", "INTEGER NOT NULL DEFAULT 0"),
        ("is_compact_summary", "INTEGER NOT NULL DEFAULT 0"),
        ("interrupted", "INTEGER NOT NULL DEFAULT 0"),
    ]:
        try:
            conn.execute(f"ALTER TABLE messages ADD COLUMN {col} {col_type}")
        except sqlite3.OperationalError:
            pass
    # sessions 表迁移：project_path 列（多项目归属）
    try:
        conn.execute("ALTER TABLE sessions ADD COLUMN project_path TEXT NOT NULL DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project_path, updated_at)")
    except sqlite3.OperationalError:
        pass


class SessionStore:
    def create(self, title: str = "New conversation", project_path: str = "") -> dict:
        sid = str(uuid.uuid4())
        now = time.time()
        conn = _get_db()
        try:
            conn.execute(
                "INSERT INTO sessions (id, title, created_at, updated_at, project_path) VALUES (?, ?, ?, ?, ?)",
                (sid, title, now, now, project_path),
            )
            conn.commit()
        except Exception:
            conn.rollback()  # 失败回滚：释放写锁，避免连锁 "database is locked"
            raise
        logger.info(f"[Session] Created session {sid} project={project_path or '(none)'}")
        return self._row_to_dict(conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone())

    def get(self, sid: str) -> Optional[dict]:
        conn = _get_db()
        row = conn.execute("SELECT * FROM sessions WHERE id=?", (sid,)).fetchone()
        return self._row_to_dict(row) if row else None

    def list(self, limit: int = 200, project_path: str | None = None) -> list:
        conn = _get_db()
        # last_message_at：会话内最新一条消息的创建时间（按 sort_order 取最后一条，走 idx_messages_session 索引），
        # 供前端会话列表显示"最近活动时间"（空会话为 NULL，前端回退 created_at）
        last_msg_sub = (
            "(SELECT m.created_at FROM messages m WHERE m.session_id = s.id "
            "ORDER BY m.sort_order DESC LIMIT 1) AS last_message_at"
        )
        if project_path:
            rows = conn.execute(
                f"SELECT s.*, {last_msg_sub} FROM sessions s WHERE s.project_path=? ORDER BY s.updated_at DESC LIMIT ?",
                (project_path, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT s.*, {last_msg_sub} FROM sessions s ORDER BY s.updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_list_item(r) for r in rows]

    def unassigned_count(self) -> int:
        conn = _get_db()
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE project_path='' OR project_path IS NULL"
        ).fetchone()
        return int(row["n"]) if row else 0

    def adopt_unassigned(self, project_path: str) -> int:
        """把未分配会话批量归入指定项目，返回归入数量"""
        conn = _get_db()
        cur = conn.execute(
            "UPDATE sessions SET project_path=? WHERE project_path='' OR project_path IS NULL",
            (project_path,),
        )
        conn.commit()
        return cur.rowcount

    def get_message_uuids(self, session_id: str) -> list:
        """获取指定 session 的所有消息的 msg_uuid"""
        conn = _get_db()
        rows = conn.execute(
            "SELECT msg_uuid FROM messages WHERE session_id=? AND msg_uuid!=''",
            (session_id,),
        ).fetchall()
        return [r["msg_uuid"] for r in rows]

    def delete(self, sid: str) -> bool:
        conn = _get_db()
        try:
            cur = conn.execute("DELETE FROM sessions WHERE id=?", (sid,))
            conn.commit()
        except Exception:
            conn.rollback()  # 失败回滚：释放写锁，避免连锁 "database is locked"
            raise
        deleted = cur.rowcount > 0
        if deleted:
            logger.info(f"[Session] Deleted session {sid}")
        return deleted

    def update_preview(self, sid: str, preview: str):
        conn = _get_db()
        try:
            conn.execute(
                "UPDATE sessions SET preview=?, updated_at=? WHERE id=?",
                (preview, time.time(), sid),
            )
            conn.commit()
        except Exception:
            conn.rollback()  # 失败回滚：释放写锁，避免连锁 "database is locked"
            raise

    def update_title(self, sid: str, title: str, preview: str = ""):
        conn = _get_db()
        now = time.time()
        try:
            if preview:
                conn.execute(
                    "UPDATE sessions SET title=?, preview=?, updated_at=? WHERE id=?",
                    (title, preview, now, sid),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
                    (title, now, sid),
                )
            conn.commit()
        except Exception:
            conn.rollback()  # 失败回滚：释放写锁，避免连锁 "database is locked"
            raise

    def increment_messages(self, sid: str):
        conn = _get_db()
        now = time.time()
        try:
            conn.execute(
                "UPDATE sessions SET message_count = message_count + 1, updated_at = ? WHERE id=?",
                (now, sid),
            )
            conn.commit()
        except Exception:
            conn.rollback()  # 失败回滚：释放写锁，避免连锁 "database is locked"
            raise

    def most_recent(self) -> Optional[dict]:
        conn = _get_db()
        row = conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT 1"
        ).fetchone()
        if row:
            return {
                "session_id": row["id"],
                "title": row["title"],
                "started_at": int(row["created_at"]),
            }
        return None

    def _next_order(self, session_id: str) -> int:
        conn = _get_db()
        row = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) + 1 AS n FROM messages WHERE session_id=?",
            (session_id,),
        ).fetchone()
        return row["n"] if row else 0

    def add_user_message(self, session_id: str, content: str, msg_uuid: str = "") -> tuple:
        conn = _get_db()
        now = time.time()
        order = self._next_order(session_id)
        if not msg_uuid:
            msg_uuid = str(uuid.uuid4())
        try:
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, msg_uuid, sort_order, created_at) VALUES (?, 'user', ?, ?, ?, ?)",
                (session_id, content, msg_uuid, order, now),
            )
            conn.commit()
        except Exception:
            conn.rollback()  # 失败回滚：释放写锁，避免连锁 "database is locked"
            raise
        return (cur.lastrowid, msg_uuid)

    def add_tool_message(self, session_id: str, tool_name: str, tool_context: str, tool_call_id: str = "", msg_uuid: str = "") -> tuple:
        conn = _get_db()
        now = time.time()
        order = self._next_order(session_id)
        if not msg_uuid:
            msg_uuid = str(uuid.uuid4())
        try:
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_name, tool_context, tool_call_id, msg_uuid, sort_order, created_at) VALUES (?, 'tool', '', ?, ?, ?, ?, ?, ?)",
                (session_id, tool_name, tool_context, tool_call_id, msg_uuid, order, now),
            )
            conn.commit()
        except Exception:
            conn.rollback()  # 失败回滚：释放写锁，避免连锁 "database is locked"
            raise
        return (cur.lastrowid, msg_uuid)

    def add_assistant_message(self, session_id: str, content: str, reasoning: Optional[str] = None, tool_calls: Optional[str] = None, msg_uuid: str = "", interrupted: bool = False) -> tuple:
        # [DEBUG-SESSION] 若有中断消息持久化问题，取消注释
        # print(f"[DEBUG-SESSION] add_assistant_message: interrupted={interrupted}, ...")
        conn = _get_db()
        now = time.time()
        order = self._next_order(session_id)
        if not msg_uuid:
            msg_uuid = str(uuid.uuid4())
        try:
            cur = conn.execute(
                "INSERT INTO messages (session_id, role, content, reasoning, tool_calls, msg_uuid, sort_order, interrupted, created_at) VALUES (?, 'assistant', ?, ?, ?, ?, ?, ?, ?)",
                (session_id, content, reasoning, tool_calls, msg_uuid, order, 1 if interrupted else 0, now),
            )
            conn.commit()
        except Exception:
            conn.rollback()  # 失败回滚：释放写锁，避免连锁 "database is locked"
            raise
        logger.debug(f"[DEBUG-INTERRUPT] Session DB add_assistant_message: msg_id={cur.lastrowid} content_len={len(content or '')} reasoning_len={len(reasoning or '')} interrupted={interrupted}")
        return (cur.lastrowid, msg_uuid)

    def delete_message(self, session_id: str, message_id: int) -> dict:
        """删除指定消息，返回 {deleted: bool, msg_uuid: str}"""
        conn = _get_db()
        # 先查出 msg_uuid
        row = conn.execute(
            "SELECT msg_uuid FROM messages WHERE id=? AND session_id=?",
            (message_id, session_id),
        ).fetchone()
        msg_uuid = row["msg_uuid"] if row else ""
        try:
            cur = conn.execute(
                "DELETE FROM messages WHERE id=? AND session_id=?",
                (message_id, session_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()  # 失败回滚：释放写锁，避免连锁 "database is locked"
            raise
        return {"deleted": cur.rowcount > 0, "msg_uuid": msg_uuid}

    def replace_messages(self, session_id: str, new_messages: list) -> bool:
        """
        原子性地替换会话的所有消息（用于压缩后更新）
        
        Args:
            session_id: 会话 ID
            new_messages: 新消息列表，每条包含 role, content 等字段
        
        Returns:
            是否成功
        """
        conn = _get_db()
        try:
            conn.execute("BEGIN")
            # 删除所有旧消息
            conn.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            # 插入新消息
            now = time.time()
            for i, msg in enumerate(new_messages):
                role = msg.get("role", "user")
                content = msg.get("content", "")
                reasoning = msg.get("reasoning") or msg.get("reasoning_content")
                tool_name = msg.get("name") if role == "tool" else None
                tool_context = msg.get("context") if role == "tool" else None
                if role == "tool":
                    conn.execute(
                        "INSERT INTO messages (session_id, role, content, tool_name, tool_context, sort_order, created_at) VALUES (?, 'tool', '', ?, ?, ?, ?)",
                        (session_id, tool_name, tool_context, i, now),
                    )
                elif role == "assistant":
                    conn.execute(
                        "INSERT INTO messages (session_id, role, content, reasoning, sort_order, created_at) VALUES (?, 'assistant', ?, ?, ?, ?)",
                        (session_id, content, reasoning, i, now),
                    )
                else:
                    conn.execute(
                        "INSERT INTO messages (session_id, role, content, sort_order, created_at) VALUES (?, ?, ?, ?, ?)",
                        (session_id, role, content, i, now),
                    )
            # 更新会话的 updated_at
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"[Session] replace_messages failed: {e}")
            return False

    def insert_compaction_result(self, session_id: str, boundary_msg: dict, summary_msg: dict, kept_count: int) -> bool:
        """
        插入压缩标记和摘要消息，不删除旧消息（压缩标记机制）
        
        Args:
            session_id: 会话 ID
            boundary_msg: 边界标记消息 dict (role, content)
            summary_msg: 摘要消息 dict (role, content, is_compact_summary)
            kept_count: 保留的最近消息数（已在 DB 中，不需重新插入）
        """
        conn = _get_db()
        try:
            conn.execute("BEGIN")
            # 获取当前最大 sort_order
            row = conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) FROM messages WHERE session_id=?",
                (session_id,)
            ).fetchone()
            max_order = row[0] if row else 0
            cutoff = max(0, max_order - kept_count)

            # Mark every message covered by the new summary. Messages stay in
            # storage for UI/export/search, but context builders skip them.
            conn.execute(
                "UPDATE messages SET is_compacted = 1 WHERE session_id=? AND sort_order <= ?",
                (session_id, cutoff)
            )
            
            now = time.time()
            # 将 cutoff 及之后的消息后移 2 位
            conn.execute(
                "UPDATE messages SET sort_order = sort_order + 2 WHERE session_id=? AND sort_order > ?",
                (session_id, cutoff)
            )
            # 插入边界标记 (system)
            conn.execute(
                "INSERT INTO messages (session_id, role, content, sort_order, is_compacted, created_at) VALUES (?, 'system', ?, ?, 0, ?)",
                (session_id, boundary_msg.get("content", ""), cutoff + 1, now)
            )
            # 插入摘要 (user, is_compact_summary)
            conn.execute(
                "INSERT INTO messages (session_id, role, content, sort_order, is_compacted, is_compact_summary, created_at) VALUES (?, 'user', ?, ?, 0, 1, ?)",
                (session_id, summary_msg.get("content", ""), cutoff + 2, now)
            )
            conn.execute("UPDATE sessions SET updated_at=? WHERE id=?", (now, session_id))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"[Session] insert_compaction_result failed: {e}")
            return False

    def get_messages(self, session_id: str, limit: int = 200) -> list:
        conn = _get_db()
        rows = conn.execute(
            "SELECT id, role, content, reasoning, tool_name, tool_context, tool_call_id, tool_calls, created_at, is_compacted, is_compact_summary, interrupted FROM messages WHERE session_id=? ORDER BY sort_order ASC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        result = []
        for r in rows:
            role = r["role"]
            created = r["created_at"]
            msg_id = r["id"]
            is_compacted = bool(r["is_compacted"])
            is_compact_summary = bool(r["is_compact_summary"])
            interrupted = bool(r["interrupted"])
            if role == "assistant":
                # [DEBUG-SESSION] print(f"[DEBUG-SESSION] get_messages: msg_id={msg_id}, role=assistant, interrupted_col={r['interrupted']}, ...")
                entry = {"role": "assistant", "text": r["content"], "created_at": created, "message_id": msg_id, "is_compacted": is_compacted, "is_compact_summary": is_compact_summary, "interrupted": interrupted}
                if r["reasoning"]:
                    entry["reasoning"] = r["reasoning"]
                if r["tool_calls"]:
                    entry["tool_calls"] = r["tool_calls"]
                result.append(entry)
            elif role == "tool":
                ctx = r["tool_context"] or ""
                name = r["tool_name"] or "tool"
                result.append({"role": "tool", "name": name, "context": ctx, "tool_call_id": r["tool_call_id"] or "", "created_at": created, "message_id": msg_id, "is_compacted": is_compacted, "is_compact_summary": is_compact_summary})
            elif role == "assistant":
                entry = {"role": "assistant", "text": r["content"], "created_at": created, "message_id": msg_id, "is_compacted": is_compacted, "is_compact_summary": is_compact_summary, "interrupted": interrupted}
                if r["reasoning"]:
                    entry["reasoning"] = r["reasoning"]
                if r["tool_calls"]:
                    entry["tool_calls"] = r["tool_calls"]
                result.append(entry)
            elif role in ("user", "system"):
                result.append({"role": role, "text": r["content"], "created_at": created, "message_id": msg_id, "is_compacted": is_compacted, "is_compact_summary": is_compact_summary})
        return result

    def get_messages_for_context(self, session_id: str, limit: int = 999999) -> list:
        """Return only messages that should be visible to the next LLM turn."""
        messages = self.get_messages(session_id, limit=limit)
        visible = []
        for msg in messages:
            content = msg.get("text") or msg.get("content") or ""
            if msg.get("is_compacted"):
                continue
            if msg.get("role") == "system" and content.startswith("[COMPACT_BOUNDARY:"):
                continue
            visible.append(msg)
        return visible

    def search_messages(self, query: str, limit: int = 20) -> list:
        """
        全文搜索所有会话的消息内容
        
        Args:
            query: 搜索关键词
            limit: 返回条数上限
        
        Returns:
            匹配的消息列表，含会话标题和上下文片段
        """
        conn = _get_db()
        like_query = f"%{query}%"
        rows = conn.execute(
            """SELECT m.id, m.role, m.content, m.created_at,
                      COALESCE(s.id, '') as session_id,
                      COALESCE(s.title, 'Deleted conversation') as session_title
               FROM messages m
               LEFT JOIN sessions s ON m.session_id = s.id
               WHERE m.content LIKE ?
               ORDER BY m.created_at DESC
               LIMIT ?""",
            (like_query, limit),
        ).fetchall()

        results = []
        for row in rows:
            content = row["content"] or ""
            # 截取上下文片段（关键词前后各 60 字）
            idx = content.lower().find(query.lower())
            if idx != -1:
                start = max(0, idx - 60)
                end = min(len(content), idx + len(query) + 60)
                snippet = content[start:end]
                if start > 0:
                    snippet = "..." + snippet
                if end < len(content):
                    snippet = snippet + "..."
            else:
                snippet = content[:150]
                if len(content) > 150:
                    snippet += "..."

            results.append({
                "message_id": row["id"],
                "role": row["role"],
                "snippet": snippet,
                "created_at": row["created_at"],
                "session_id": row["session_id"],
                "session_title": row["session_title"] or "New conversation",
            })

        return results

    def add_cost_entry(self, session_id: str, cost_data: dict):
        """
        保存一次 API 调用的费用记录
        
        Args:
            session_id: 会话 ID
            cost_data: 费用数据字典，包含 model, input_tokens, output_tokens,
                      cache_hit_tokens, cost_cny, input_cost, output_cost, cache_hit_cost
        """
        conn = _get_db()
        now = time.time()
        conn.execute(
            """INSERT INTO session_costs
               (session_id, model, input_tokens, output_tokens, cache_hit_tokens,
                cost_cny, input_cost, output_cost, cache_hit_cost, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id,
                cost_data.get("model", ""),
                cost_data.get("input_tokens", 0),
                cost_data.get("output_tokens", 0),
                cost_data.get("cache_hit_tokens", 0),
                cost_data.get("cost_cny", 0),
                cost_data.get("input_cost", 0),
                cost_data.get("output_cost", 0),
                cost_data.get("cache_hit_cost", 0),
                now,
            ),
        )
        conn.commit()

    def get_cost_summary(self, session_id: str) -> dict:
        """
        获取某个会话的费用汇总
        
        Returns:
            {
                "total_cost_cny": float,
                "total_calls": int,
                "total_input_tokens": int,
                "total_output_tokens": int,
                "total_cache_hit_tokens": int,
                "by_model": [
                    {"model": str, "calls": int, "input_tokens": int,
                     "output_tokens": int, "cost_cny": float}
                ]
            }
        """
        conn = _get_db()
        # 汇总
        row = conn.execute(
            """SELECT COUNT(*) as calls,
                      COALESCE(SUM(input_tokens + cache_hit_tokens), 0) as in_tok,
                      COALESCE(SUM(output_tokens), 0) as out_tok,
                      COALESCE(SUM(cache_hit_tokens), 0) as cache_tok,
                      COALESCE(SUM(cost_cny), 0) as total_cost
               FROM session_costs WHERE session_id=?""",
            (session_id,),
        ).fetchone()

        # 按模型分组
        model_rows = conn.execute(
            """SELECT model,
                      COUNT(*) as calls,
                      COALESCE(SUM(input_tokens + cache_hit_tokens), 0) as in_tok,
                      COALESCE(SUM(output_tokens), 0) as out_tok,
                      COALESCE(SUM(cache_hit_tokens), 0) as cache_tok,
                      COALESCE(SUM(cost_cny), 0) as cost
               FROM session_costs WHERE session_id=?
               GROUP BY model ORDER BY cost DESC""",
            (session_id,),
        ).fetchall()

        by_model = []
        for r in model_rows:
            by_model.append({
                "model": r["model"],
                "calls": r["calls"],
                "input_tokens": r["in_tok"],
                "output_tokens": r["out_tok"],
                "cache_hit_tokens": r["cache_tok"],
                "cost_cny": round(r["cost"], 6),
            })

        return {
            "total_cost_cny": round(row["total_cost"], 6),
            "total_calls": row["calls"],
            "total_input_tokens": row["in_tok"],
            "total_output_tokens": row["out_tok"],
            "total_cache_hit_tokens": row["cache_tok"],
            "by_model": by_model,
        }

    def message_count(self, session_id: str) -> int:
        conn = _get_db()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE session_id=?", (session_id,)
        ).fetchone()
        return row["cnt"] if row else 0

    @staticmethod
    def _row_to_dict(row) -> dict:
        return {
            "session_id": row["id"],
            "title": row["title"],
            "preview": row["preview"],
            "message_count": row["message_count"],
            "project_path": row["project_path"] if "project_path" in row.keys() else "",
            "created_at": datetime.fromtimestamp(row["created_at"]).isoformat(),
        }

    @staticmethod
    def _row_to_list_item(row) -> dict:
        keys = row.keys()
        last_msg_at = row["last_message_at"] if "last_message_at" in keys else None
        return {
            "id": row["id"],
            "title": row["title"],
            "preview": row["preview"],
            "message_count": row["message_count"],
            "project_path": row["project_path"] if "project_path" in keys else "",
            "started_at": int(row["created_at"]),
            "last_message_at": int(last_msg_at) if last_msg_at else None,
        }

    def export_markdown(self, session_id: str) -> Optional[str]:
        """将对话导出为 Markdown 格式"""
        session = self.get(session_id)
        if not session:
            return None
        title = session.get("title") or "New conversation"
        messages = self.get_messages(session_id, limit=999999)
        if not messages:
            return None

        from datetime import datetime
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"# {title}",
            "",
            f"导出时间: {now_str}",
            "",
        ]

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("text") or msg.get("content") or ""
            created_raw = msg.get("created_at", "")
            created_str = ""
            if created_raw:
                try:
                    created_str = datetime.fromtimestamp(float(created_raw)).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                except (ValueError, TypeError):
                    created_str = str(created_raw)

            if role == "user":
                lines.append(f"## \U0001f9d1 \u7528\u6237  ({created_str})")
                lines.append("")
                lines.append(content)
                lines.append("")

            elif role == "assistant":
                lines.append(f"## \U0001f916 Agent  ({created_str})")
                lines.append("")

                # 思考内容
                reasoning = msg.get("reasoning") or ""
                if reasoning:
                    lines.append("> \U0001f4a1 **\u601d\u8003\u8fc7\u7a0b**")
                    lines.append(">")
                    for rl in reasoning.split("\n"):
                        lines.append(f"> {rl}")
                    lines.append("")

                lines.append(content)
                lines.append("")

            elif role == "tool":
                tool_name = msg.get("name") or msg.get("tool_name") or ""
                tool_ctx = msg.get("context") or msg.get("tool_context") or ""
                lines.append(f"> **\U0001f527 \u5de5\u5177 [{tool_name}]**")
                if tool_ctx:
                    lines.append(f"> {tool_ctx}")
                lines.append("")
                continue

        return "\n".join(lines)
