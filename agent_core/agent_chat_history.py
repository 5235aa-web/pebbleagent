"""
Agent Chat History - Agent聊天历史管理

提供独立的聊天历史存储，与主聊天隔离。
使用SQLite数据库存储。
"""

import sqlite3
import json
import os
import uuid
import re
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from contextlib import contextmanager
import logging

from .safe_json import safe_json_loads, safe_json_dumps

logger = logging.getLogger(__name__)

_DATA_HOME = os.environ.get("PEBBLE_HOME") or os.path.join(Path.home(), ".pebble")
os.makedirs(_DATA_HOME, exist_ok=True)
DB_PATH = os.path.join(_DATA_HOME, "agent_chat.db")


def _connect() -> sqlite3.Connection:
    """统一连接工厂：WAL + 外键约束 + 写锁等待（对齐 sessions.db 策略）。

    - WAL: 多读者/单写者，降低并发锁冲突（此前默认 DELETE 模式，写锁全库）
    - foreign_keys=ON: 阻止"会话行不存在时写入消息"产生孤儿数据
      （此前 FK 未启用，曾产生 conversation_id=70 孤儿消息并引发上层 FK 崩溃）
    - timeout=10: 写锁等待 10 秒（默认 5 秒，多会话并发下更宽容）
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

# ============================================================================
# 上下文展开限长常量
# 工具输出全文（execution_order.tool_info.output）可能达百万字符（历史 bug：
# 单条 execution_order 141 万字符），展开进 LLM 上下文后远超模型窗口。
# 展开时统一限长，超长部分截断并加省略标记。
# ============================================================================
MAX_TOOL_OUTPUT_CONTEXT_CHARS = 2000   # 单条工具输出进入 LLM 上下文的最大字符数
MAX_TOOL_INPUT_CONTEXT_CHARS = 1000    # 单条工具参数的最大字符数
MAX_REASONING_CONTEXT_CHARS = 2000     # 单条 reasoning 内容的最大字符数
MAX_TEXT_CONTEXT_CHARS = 4000          # 单条 assistant 文本的最大字符数


def _truncate_context_text(text: Optional[str], limit: int) -> Optional[str]:
    """截断进入 LLM 上下文的文本，超长加省略标记（保留开头 + 结尾摘要）"""
    if not text:
        return text
    if len(text) <= limit:
        return text
    # 保留开头 limit * 3/4 + 结尾 limit/4，中间省略（长工具输出开头是结论、结尾是错误信息，两端都有价值）
    # 省略标记计入预算，保证总长不超过 limit
    marker = f"\n[... 已截断 {len(text) - limit} 字符，完整内容见会话记录 ...]\n"
    usable = limit - len(marker)
    if usable < 100:
        usable = max(limit // 2, 10)
    head_len = int(usable * 0.75)
    tail_len = usable - head_len
    return text[:head_len] + marker + text[-tail_len:]


@contextmanager
def _get_db():
    """数据库连接上下文管理器，确保连接正确关闭"""
    conn = _connect()
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_agent_chat_db():
    """初始化Agent聊天数据库表"""
    conn = _connect()
    cursor = conn.cursor()
    
    # 创建Agent聊天对话表
    cursor.execute("""CREATE TABLE IF NOT EXISTS agent_chat_conversations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uuid TEXT UNIQUE NOT NULL,
        user_id TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        title TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")
    
    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_conv_user ON agent_chat_conversations(user_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_conv_agent ON agent_chat_conversations(agent_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_conv_updated ON agent_chat_conversations(updated_at DESC)")
    
    # 创建Agent聊天消息表
    cursor.execute("""CREATE TABLE IF NOT EXISTS agent_chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        thinking TEXT,
        reasoning_content TEXT,
        token_count INTEGER DEFAULT 0,
        agent_name TEXT,
        msg_uuid TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0,
        is_compacted INTEGER NOT NULL DEFAULT 0,
        is_compact_summary INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (conversation_id) REFERENCES agent_chat_conversations(id) ON DELETE CASCADE
    )""")
    
    # 创建索引
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_msg_conv ON agent_chat_messages(conversation_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_agent_msg_created ON agent_chat_messages(created_at)")
    
    # 迁移：为现有表添加 agent_name 列（如果不存在）
    _safe_add_column(cursor, "agent_chat_messages", "agent_name", "TEXT")
    
    # 迁移：为现有表添加 msg_uuid 列
    _safe_add_column(cursor, "agent_chat_messages", "msg_uuid", "TEXT NOT NULL DEFAULT ''")
    
    # 迁移：为现有表添加 reasoning_content 列（如果不存在）
    _safe_add_column(cursor, "agent_chat_messages", "reasoning_content", "TEXT")
    
    # 迁移：为现有表添加 execution_order 列（如果不存在）
    _safe_add_column(cursor, "agent_chat_messages", "execution_order", "TEXT")
    
    # 迁移：为现有表添加 is_pinned 列
    _safe_add_column(cursor, "agent_chat_conversations", "is_pinned", "INTEGER DEFAULT 0")
    
    # 迁移：为 agent_chat_messages 添加 sort_order 列（用于压缩 marker 排序）
    _safe_add_column(cursor, "agent_chat_messages", "sort_order", "INTEGER NOT NULL DEFAULT 0")
    # 迁移：标记已被压缩摘要覆盖的消息。旧消息保留在库里，但 LLM 上下文跳过。
    _safe_add_column(cursor, "agent_chat_messages", "is_compacted", "INTEGER NOT NULL DEFAULT 0")
    # 迁移：摘要消息标记（前端按字段识别，替代文本前缀强依赖）
    _safe_add_column(cursor, "agent_chat_messages", "is_compact_summary", "INTEGER NOT NULL DEFAULT 0")
    # 为已有数据填充递增 sort_order
    cursor.execute("""
        UPDATE agent_chat_messages SET sort_order = (
            SELECT COUNT(*) FROM agent_chat_messages AS t2 
            WHERE t2.conversation_id = agent_chat_messages.conversation_id 
              AND t2.id <= agent_chat_messages.id
        )
    """)
    
    # 创建 FTS5 全文搜索虚拟表
    cursor.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS agent_chat_messages_fts USING fts5(
            content, role, conversation_id UNINDEXED,
            tokenize='unicode61'
        )
    """)
    # 迁移：为已有消息重建 FTS 索引
    cursor.execute("SELECT COUNT(*) FROM agent_chat_messages_fts")
    fts_count = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM agent_chat_messages")
    msg_count = cursor.fetchone()[0]
    if fts_count < msg_count:
        cursor.execute("INSERT OR REPLACE INTO agent_chat_messages_fts(rowid, content, role, conversation_id) SELECT id, content, role, conversation_id FROM agent_chat_messages")
    
    # ==================== 计费持久化表 ====================
    cursor.execute("""CREATE TABLE IF NOT EXISTS agent_chat_costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id INTEGER NOT NULL,
        model_name TEXT NOT NULL,
        api_call_index INTEGER NOT NULL DEFAULT 0,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        cache_hit_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        input_cost REAL NOT NULL DEFAULT 0.0,
        cache_hit_cost REAL NOT NULL DEFAULT 0.0,
        output_cost REAL NOT NULL DEFAULT 0.0,
        total_cost REAL NOT NULL DEFAULT 0.0,
        currency TEXT NOT NULL DEFAULT 'CNY',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (conversation_id) REFERENCES agent_chat_conversations(id) ON DELETE CASCADE
    )""")
    
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cost_conv ON agent_chat_costs(conversation_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cost_conv_model ON agent_chat_costs(conversation_id, model_name)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cost_created ON agent_chat_costs(created_at)")
    
    conn.commit()
    conn.close()
    logger.info("[AgentChatHistory] Database initialized")


def _safe_add_column(cursor, table: str, column: str, col_type: str):
    """安全添加列——仅在列不存在时添加"""
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


# 初始化数据库
init_agent_chat_db()


def create_agent_conversation(user_id: str, agent_name: str, title: str = None, uuid: str = None) -> Dict[str, Any]:
    """
    创建新的Agent聊天对话
    
    Args:
        user_id: 用户ID
        agent_name: Agent名称
        title: 对话标题（可选）
        uuid: 指定 UUID（不指定则自动生成），用于 TUI 保持对话连续性
    
    Returns:
        包含 uuid 和 conversation_id 的字典
    """
    conn = _connect()
    cursor = conn.cursor()
    
    conv_uuid = uuid or str(uuid.uuid4())
    if not title:
        title = f"与 {agent_name} 的对话"
    
    cursor.execute("""
        INSERT INTO agent_chat_conversations (uuid, user_id, agent_name, title)
        VALUES (?, ?, ?, ?)
    """, (conv_uuid, user_id, agent_name, title))
    
    conversation_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    logger.info(f"[AgentChatHistory] Created conversation: {conv_uuid} for agent: {agent_name}")
    
    return {
        "uuid": conv_uuid,
        "conversation_id": conversation_id,
        "agent_name": agent_name,
        "title": title
    }


def get_agent_conversations(user_id: str, agent_name: str = None) -> List[Dict[str, Any]]:
    """
    获取用户的Agent聊天对话列表
    
    Args:
        user_id: 用户ID
        agent_name: 可选，筛选特定Agent的对话
    
    Returns:
        对话列表
    """
    conn = _connect()
    cursor = conn.cursor()
    
    if agent_name:
        cursor.execute("""
            SELECT c.id, c.uuid, c.agent_name, c.title, c.created_at, c.updated_at, c.is_pinned,
                   (SELECT COUNT(*) FROM agent_chat_messages m WHERE m.conversation_id = c.id AND m.role = 'user') as msg_count
            FROM agent_chat_conversations c
            WHERE c.user_id = ? AND c.agent_name = ?
            ORDER BY c.is_pinned DESC, c.updated_at DESC
        """, (user_id, agent_name))
    else:
        cursor.execute("""
            SELECT c.id, c.uuid, c.agent_name, c.title, c.created_at, c.updated_at, c.is_pinned,
                   (SELECT COUNT(*) FROM agent_chat_messages m WHERE m.conversation_id = c.id AND m.role = 'user') as msg_count
            FROM agent_chat_conversations c
            WHERE c.user_id = ?
            ORDER BY c.is_pinned DESC, c.updated_at DESC
        """, (user_id,))
    
    rows = cursor.fetchall()
    conn.close()
    
    conversations = []
    for row in rows:
        conversations.append({
            "id": row[0],
            "uuid": row[1],
            "agent_name": row[2],
            "title": row[3],
            "created_at": row[4],
            "updated_at": row[5],
            "is_pinned": bool(row[6]) if len(row) > 6 else False,
            "message_count": row[7] if len(row) > 7 else 0
        })
    
    return conversations


def get_agent_conversation_by_uuid(conv_uuid: str) -> Optional[Dict[str, Any]]:
    """通过UUID获取对话"""
    conn = _connect()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, uuid, user_id, agent_name, title, created_at, updated_at
        FROM agent_chat_conversations
        WHERE uuid = ?
    """, (conv_uuid,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        conv_id = row[0]
        result = {
            "id": conv_id,
            "uuid": row[1],
            "user_id": row[2],
            "agent_name": row[3],
            "title": row[4],
            "created_at": row[5],
            "updated_at": row[6],
            "cost_summary": get_conversation_cost_summary(conv_id)
        }
        return result
    return None


def update_agent_conversation_title(conv_uuid: str, title: str) -> bool:
    """更新对话标题"""
    conn = _connect()
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE agent_chat_conversations
        SET title = ?, updated_at = CURRENT_TIMESTAMP
        WHERE uuid = ?
    """, (title, conv_uuid))
    
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    
    return success


def toggle_pin_conversation(conv_uuid: str) -> bool:
    """切换对话置顶状态"""
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute("UPDATE agent_chat_conversations SET is_pinned = CASE WHEN is_pinned THEN 0 ELSE 1 END, updated_at = CURRENT_TIMESTAMP WHERE uuid = ?", (conv_uuid,))
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success


def delete_agent_conversation(conv_uuid: str, user_id: str = None) -> bool:
    """删除对话（及其所有消息）"""
    conn = _connect()
    cursor = conn.cursor()
    
    if user_id:
        # 安全删除：验证用户ID
        cursor.execute("""
            DELETE FROM agent_chat_conversations
            WHERE uuid = ? AND user_id = ?
        """, (conv_uuid, user_id))
    else:
        cursor.execute("DELETE FROM agent_chat_conversations WHERE uuid = ?", (conv_uuid,))
    
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    
    if success:
        logger.info(f"[AgentChatHistory] Deleted conversation: {conv_uuid}")
    
    return success


def delete_agent_conversation_full(conv_uuid: str) -> bool:
    """彻底删除对话：消息（含无 msg_uuid 的）+ 成本记录 + 会话行。

    供 gateway session.delete 跨库同步调用；旧实现只删有 uuid 的消息，
    导致 conversations 行、无 uuid 消息、agent_chat_costs 永久残留。
    """
    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN")
        cursor.execute("SELECT id FROM agent_chat_conversations WHERE uuid = ?", (conv_uuid,))
        row = cursor.fetchone()
        if row is None:
            conn.commit()
            return False
        conv_id = row[0]
        cursor.execute("DELETE FROM agent_chat_messages WHERE conversation_id = ?", (conv_id,))
        cursor.execute("DELETE FROM agent_chat_costs WHERE conversation_id = ?", (conv_id,))
        cursor.execute("DELETE FROM agent_chat_conversations WHERE id = ?", (conv_id,))
        conn.commit()
        logger.info(f"[AgentChatHistory] Deleted conversation fully: {conv_uuid} (conv_id={conv_id})")
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"[AgentChatHistory] Failed to delete conversation {conv_uuid}: {e}")
        return False
    finally:
        conn.close()


def add_agent_chat_message(
    conversation_id: int,
    role: str,
    content: str,
    thinking: Optional[str] = None,
    reasoning_content: Optional[str] = None,
    token_count: int = 0,
    agent_name: Optional[str] = None,
    execution_order: Optional[str] = None,
    msg_uuid: Optional[str] = None
) -> int:
    """
    添加消息到对话
    
    Args:
        conversation_id: 对话ID
        role: 角色 ('user' 或 'assistant')
        content: 消息内容
        thinking: 思考内容（可选）
        reasoning_content: 推理内容（思考模式下的思维链）
        token_count: Token数量
        agent_name: Agent名称（用于正确显示 AI 名称）
        execution_order: 执行顺序（JSON字符串，包含reasoning/text/tool的穿插顺序）
        msg_uuid: 跨库消息UUID（用于同步删除），不传则自动生成
    
    Returns:
        新消息的ID
    """
    conn = _connect()
    cursor = conn.cursor()
    if not msg_uuid:
        msg_uuid = str(uuid.uuid4())
    
    # 计算下一条消息的 sort_order
    cursor.execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 FROM agent_chat_messages WHERE conversation_id = ?",
        (conversation_id,)
    )
    sort_order = cursor.fetchone()[0]
    
    cursor.execute("""
        INSERT INTO agent_chat_messages (conversation_id, role, content, thinking, reasoning_content, token_count, agent_name, execution_order, msg_uuid, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (conversation_id, role, content, thinking, reasoning_content, token_count, agent_name, execution_order, msg_uuid, sort_order))
    
    message_id = cursor.lastrowid
    
    # 同步更新 FTS 索引
    if message_id and content:
        try:
            cursor.execute("INSERT OR REPLACE INTO agent_chat_messages_fts(rowid, content, role, conversation_id) VALUES (?, ?, ?, ?)",
                           (message_id, content, role, conversation_id))
        except Exception:
            pass
    
    # 更新对话的 updated_at
    cursor.execute("""
        UPDATE agent_chat_conversations
        SET updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (conversation_id,))
    
    conn.commit()
    conn.close()
    
    return message_id


def get_agent_chat_messages(conversation_id: int, limit: int = 999999) -> List[Dict[str, Any]]:
    """
    获取对话的消息列表
    
    Args:
        conversation_id: 对话ID
        limit: 最大消息数量
    
    Returns:
        消息列表
    """
    conn = _connect()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT id, role, content, thinking, reasoning_content, token_count, created_at, agent_name, execution_order, sort_order, is_compacted, is_compact_summary
        FROM agent_chat_messages
        WHERE conversation_id = ?
        ORDER BY sort_order ASC
        LIMIT ?
    """, (conversation_id, limit))
    
    rows = cursor.fetchall()
    conn.close()
    
    messages = []
    for row in rows:
        messages.append({
            "id": row[0],
            "role": row[1],
            "content": row[2],
            "thinking": row[3],
            "reasoning_content": row[4],
            "token_count": row[5],
            "created_at": row[6],
            "agent_name": row[7],
            "execution_order": row[8],
            "sort_order": row[9],
            "is_compacted": bool(row[10]),
            "is_compact_summary": bool(row[11])
        })
    
    return messages


def replace_agent_chat_messages(conversation_id: int, new_messages: List[Dict[str, Any]]) -> bool:
    """
    原子性地替换对话的所有消息（用于压缩后同步 agent_chat.db）
    
    Args:
        conversation_id: 对话ID
        new_messages: 新消息列表，每条包含 role, content, reasoning_content 等字段
    
    Returns:
        是否成功
    """
    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN")
        cursor.execute("DELETE FROM agent_chat_messages WHERE conversation_id = ?", (conversation_id,))
        for i, msg in enumerate(new_messages):
            role = msg.get("role", "user")
            content = msg.get("content", "") or ""
            reasoning = msg.get("reasoning_content")
            cursor.execute(
                """INSERT INTO agent_chat_messages (conversation_id, role, content, reasoning_content, token_count, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (conversation_id, role, content, reasoning, 0, i + 1)
            )
        cursor.execute(
            "UPDATE agent_chat_conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,)
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"[AgentChatHistory] replace_agent_chat_messages failed: {e}")
        return False
    finally:
        conn.close()


def insert_compaction_result(conversation_id: int, boundary_msg: dict, summary_msg: dict) -> bool:
    """
    插入压缩标记和摘要消息，不删除旧消息（压缩标记机制）

    压缩语义（与 sessions.db 版本一致）：boundary 之前的全部旧消息已被压缩，
    标记 is_compacted=1（上下文构建跳过）；boundary/summary 追加到消息末尾
    （is_compacted=0，保留在上下文中）。

    注意：agent_chat.db 是精简库（只保存 user/assistant 消息），与 sessions.db
    的完整消息集（含 tool）粒度不同，因此不能使用 sessions 的 kept_count 推算
    cutoff——那会导致标记范围错误（历史 bug：cutoff 被推成 1，实际标记 0 条，
    压缩后 LLM 上下文完全不缩小）。本实现直接标记"boundary 之前的所有旧消息"，
    语义天然正确；多次压缩时旧的 boundary/summary 也会被标记跳过，上下文只
    保留最新一份摘要。
    """
    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN")
        # 1) 压缩边界之前的所有旧消息标记为已压缩（上下文构建跳过）
        cursor.execute(
            "UPDATE agent_chat_messages SET is_compacted = 1 WHERE conversation_id = ? AND is_compacted = 0",
            (conversation_id,)
        )
        # 2) boundary/summary 追加到消息末尾
        cursor.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM agent_chat_messages WHERE conversation_id = ?",
            (conversation_id,)
        )
        max_order = cursor.fetchone()[0]
        # 3) 插入边界标记（system）
        cursor.execute(
            "INSERT INTO agent_chat_messages (conversation_id, role, content, sort_order, is_compacted) VALUES (?, 'system', ?, ?, 0)",
            (conversation_id, boundary_msg.get("content", ""), max_order + 1)
        )
        # 4) 插入摘要（user, is_compact_summary）
        cursor.execute(
            "INSERT INTO agent_chat_messages (conversation_id, role, content, sort_order, is_compacted, is_compact_summary) VALUES (?, 'user', ?, ?, 0, 1)",
            (conversation_id, summary_msg.get("content", ""), max_order + 2)
        )
        cursor.execute(
            "UPDATE agent_chat_conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (conversation_id,)
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        logger.error(f"[AgentChatHistory] insert_compaction_result failed: {e}")
        return False
    finally:
        conn.close()


def _parse_execution_order(raw_execution_order: Any) -> List[Dict[str, Any]]:
    """解析 execution_order 字段，自动处理非法 JSON 转义"""
    if not raw_execution_order:
        return []
    try:
        if isinstance(raw_execution_order, str):
            parsed = safe_json_loads(raw_execution_order, default=[])
            return parsed if isinstance(parsed, list) else []
        if isinstance(raw_execution_order, list):
            return raw_execution_order
    except Exception:
        pass
    return []


def _recover_tool_id(tool_info: Dict[str, Any], idx: int, 
                      assistant_tool_calls: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    恢复丢失的 tool_id
    
    优先从 tool_info 中读取。如果缺失，尝试通过 tool_name + input 签名匹配
    assistant 消息中 tool_calls 数组的 id。如果仍无法匹配，生成带 idx 的后备 id
    并记录警告。
    """
    tool_id = tool_info.get("id")
    if tool_id:
        return tool_id
    
    tool_name = tool_info.get("name") or tool_info.get("toolName") or "unknown"
    tool_input = tool_info.get("input") or "{}"
    
    # 尝试通过 tool_name + input 签名匹配 assistant 的 tool_calls
    if assistant_tool_calls:
        for tc in assistant_tool_calls:
            tc_func = tc.get("function", {})
            tc_name = tc_func.get("name", "")
            tc_args = tc_func.get("arguments", "")
            if tc_name == tool_name and tc_args == tool_input:
                return tc.get("id", f"recovered_tool_{idx}")
    
    # 终极后备：记录警告并生成后备 id
    logger.warning(
        f"[AgentChatHistory] Recovered missing tool_id for '{tool_name}' "
        f"at index {idx} — this indicates corrupted execution_order data"
    )
    return f"recovered_tool_{idx}_{tool_name}"


def _strip_legacy_tool_blocks(content: str) -> str:
    """剥离旧版嵌入 content 的工具执行块"""
    if not content or "【工具执行】" not in content:
        return content
    stripped = re.sub(r"【工具执行】.*?(?=(\n\s*\n【工具执行】|\Z))", "", content, flags=re.S)
    return stripped.strip()


def _looks_like_unbacked_tool_claim(content: str) -> bool:
    """识别没有真实 tool_calls 支撑的高风险成功宣称"""
    if not content:
        return False
    success_markers = [
        "创建成功并已验证",
        "真正调用工具",
        "独立验证",
        "write_file",
        "Get-ChildItem",
        "PowerShell",
        "已成功创建",
        "LSP 验证通过",
        "构建成功",
        "重新构建",
    ]
    hit_count = sum(1 for marker in success_markers if marker in content)
    return hit_count >= 2


def _expand_assistant_context_message(
    msg: Dict[str, Any],
    include_reasoning: bool = True
) -> List[Dict[str, Any]]:
    """将一条 assistant 历史消息恢复为 API 所需的 assistant/tool 序列"""
    execution_order = _parse_execution_order(msg.get("execution_order"))
    has_tool_items = any(
        item.get("type") == "tool"
        and (lambda tool_info: bool(
            (tool_info.get("name") or tool_info.get("toolName"))
            and (tool_info.get("name") or tool_info.get("toolName")) != "unknown"
            and tool_info.get("status") != "streaming"
        ))(item.get("tool_info") or item.get("toolInfo") or {})
        for item in execution_order
    )

    if not has_tool_items:
        clean_content = _strip_legacy_tool_blocks(msg.get("content", ""))
        if _looks_like_unbacked_tool_claim(clean_content):
            logger.warning(
                "[AgentChatHistory] Skipping suspicious assistant message without tool trace: id=%s",
                msg.get("id")
            )
            return []

        result_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": clean_content
        }
        if include_reasoning:
            result_msg["reasoning_content"] = msg.get("reasoning_content") or None
        return [result_msg]

    # 修复：execution_order 有工具调用但缺失 text 项时，从 DB content 字段补入
    has_text_items = any(
        item.get("type") == "text" and item.get("content")
        for item in execution_order
    )
    if not has_text_items:
        db_content = _strip_legacy_tool_blocks(msg.get("content", ""))
        if db_content and not _looks_like_unbacked_tool_claim(db_content):
            execution_order.insert(0, {
                "type": "text",
                "content": db_content,
                "order": -1,
            })
            logger.info(
                "[AgentChatHistory] Recovered %d chars of text content from DB for msg %s",
                len(db_content), msg.get("id")
            )

    api_messages: List[Dict[str, Any]] = []
    pending_text_parts: List[str] = []
    pending_reasoning_parts: List[str] = []

    def flush_assistant(tool_calls: Optional[List[Dict[str, Any]]] = None) -> None:
        content = "\n\n".join(part for part in pending_text_parts if part).strip()
        reasoning = "\n\n".join(part for part in pending_reasoning_parts if part).strip()

        if not content and not reasoning and not tool_calls:
            return

        assistant_msg: Dict[str, Any] = {
            "role": "assistant",
            "content": content or ""
        }
        if include_reasoning:
            assistant_msg["reasoning_content"] = reasoning or None
        if tool_calls:
            assistant_msg["tool_calls"] = tool_calls

        api_messages.append(assistant_msg)
        pending_text_parts.clear()
        pending_reasoning_parts.clear()

    idx = 0
    while idx < len(execution_order):
        item = execution_order[idx]
        item_type = item.get("type")

        if item_type == "reasoning":
            if item.get("content"):
                pending_reasoning_parts.append(_truncate_context_text(item["content"], MAX_REASONING_CONTEXT_CHARS))
            idx += 1
            continue

        if item_type == "text":
            if item.get("content"):
                pending_text_parts.append(_truncate_context_text(item["content"], MAX_TEXT_CONTEXT_CHARS))
            idx += 1
            continue

        if item_type == "tool":
            tool_calls: List[Dict[str, Any]] = []
            tool_results: List[Dict[str, Any]] = []

            while idx < len(execution_order) and execution_order[idx].get("type") == "tool":
                tool_item = execution_order[idx]
                tool_info = tool_item.get("tool_info") or tool_item.get("toolInfo") or {}
                tool_name = tool_info.get("name") or tool_info.get("toolName") or ""
                if not tool_name or tool_name == "unknown":
                    logger.warning(
                        "[AgentChatHistory] Skipping invalid tool item in execution_order at idx=%s",
                        idx
                    )
                    idx += 1
                    continue
                tool_id = tool_info.get("id")
                # 如果 tool_id 缺失，生成稳定的后备 id（包含 tool_name 以区别于简单序号）
                if not tool_id:
                    logger.warning(
                        f"[AgentChatHistory] Missing tool_id for '{tool_name}' "
                        f"at execution_order idx={idx} — using fallback"
                    )
                    tool_id = f"recovered_{idx}_{tool_name.replace('-', '_').replace(' ', '_')}"
                tool_input = tool_info.get("input") or "{}"
                tool_output = tool_info.get("output")
                tool_error = tool_info.get("error")

                if not isinstance(tool_input, str):
                    try:
                        tool_input = json.dumps(tool_input, ensure_ascii=False)
                    except Exception:
                        tool_input = "{}"
                # 工具参数限长，避免巨型 JSON 进入上下文
                tool_input = _truncate_context_text(tool_input, MAX_TOOL_INPUT_CONTEXT_CHARS) or "{}"

                tool_calls.append({
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": tool_input
                    }
                })

                # 工具输出限长，根治 execution_order 膨胀（历史 bug：单条输出 141 万字符）
                tool_result_content = _truncate_context_text(
                    tool_output or tool_error or "Tool completed with no output.",
                    MAX_TOOL_OUTPUT_CONTEXT_CHARS
                )
                tool_results.append({
                    "role": "tool",
                    "tool_call_id": tool_id,
                    "content": tool_result_content
                })
                idx += 1

            flush_assistant(tool_calls=tool_calls)
            api_messages.extend(tool_results)
            continue

        idx += 1

    flush_assistant()
    return api_messages


def get_agent_chat_messages_for_context(conversation_id: int, limit: int = 50, include_reasoning: bool = True) -> List[Dict[str, Any]]:
    """
    获取用于上下文的消息列表（简化格式）
    
    Args:
        conversation_id: 对话ID
        limit: 最大消息数量
        include_reasoning: 是否包含 reasoning_content（用于思考模式多轮对话）
    
    Returns:
        OpenAI格式的消息列表 [{"role": "...", "content": "..."}]
    """
    messages = get_agent_chat_messages(conversation_id, limit)
    
    result: List[Dict[str, Any]] = []
    for msg in messages:
        if msg.get("is_compacted"):
            continue

        content = msg.get("content", "") or ""
        if msg["role"] == "system" and content.startswith("[COMPACT_BOUNDARY:"):
            continue

        if msg["role"] == "assistant":
            result.extend(_expand_assistant_context_message(msg, include_reasoning=include_reasoning))
            continue
        
        # 处理 tool 消息：必须有 tool_call_id
        if msg["role"] == "tool":
            tool_call_id = msg.get("tool_call_id")
            if not tool_call_id:
                # 如果缺少 tool_call_id，跳过这条消息（避免 API 错误）
                logger.warning(f"[AgentChatHistory] Skipping tool message without tool_call_id: id={msg.get('id')}")
                continue
            result.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": msg.get("content", "")
            })
            continue
        
        # 处理 user/system 消息
        result.append({"role": msg["role"], "content": content})
    
    return result


def count_conversation_tokens(conversation_id: int) -> int:
    """统计对话的总Token数"""
    conn = _connect()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT SUM(token_count) FROM agent_chat_messages
        WHERE conversation_id = ?
    """, (conversation_id,))
    
    result = cursor.fetchone()
    conn.close()
    
    return result[0] if result[0] else 0


# ==================== 计费持久化 ====================

def save_api_call_cost(
    conversation_id: int,
    model_name: str,
    api_call_index: int,
    input_tokens: int,
    cache_hit_tokens: int,
    output_tokens: int,
    input_cost: float,
    cache_hit_cost: float,
    output_cost: float,
    total_cost: float,
    currency: str = "CNY"
) -> int:
    """
    保存单次 API 调用的费用明细到 agent_chat_costs 表
    
    Returns:
        新记录的 ID
    """
    conn = _connect()
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT INTO agent_chat_costs (
            conversation_id, model_name, api_call_index,
            input_tokens, cache_hit_tokens, output_tokens,
            input_cost, cache_hit_cost, output_cost, total_cost, currency
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        conversation_id, model_name, api_call_index,
        input_tokens, cache_hit_tokens, output_tokens,
        input_cost, cache_hit_cost, output_cost, total_cost, currency
    ))
    
    cost_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    logger.debug(
        f"[Cost] Saved API call cost: conv={conversation_id}, "
        f"model={model_name}, index={api_call_index}, total=¥{total_cost:.6f}"
    )
    return cost_id


def get_conversation_cost_summary(conversation_id: int) -> dict:
    """
    获取对话累计费用汇总
    
    Returns:
        {
            "total_cost": float,           # 总费用
            "total_input_tokens": int,     # 总输入 token
            "total_cache_hit_tokens": int, # 总缓存 token
            "total_output_tokens": int,    # 总输出 token
            "api_calls": int,              # 总 API 调用次数
            "currency": str,               # 货币
            "by_model": {                  # 按模型分组
                "deepseek-v4-flash": {
                    "cost": 0.001,
                    "calls": 1,
                    "input_tokens": 16640,
                    "output_tokens": 271
                }
            }
        }
    """
    conn = _connect()
    cursor = conn.cursor()
    
    # 汇总查询
    cursor.execute("""
        SELECT 
            COALESCE(SUM(total_cost), 0.0) as total_cost,
            COALESCE(SUM(input_tokens + cache_hit_tokens), 0) as total_input_tokens,
            COALESCE(SUM(cache_hit_tokens), 0) as total_cache_hit_tokens,
            COALESCE(SUM(output_tokens), 0) as total_output_tokens,
            COUNT(*) as api_calls,
            COALESCE(MAX(currency), 'CNY') as currency
        FROM agent_chat_costs
        WHERE conversation_id = ?
    """, (conversation_id,))
    
    row = cursor.fetchone()
    
    summary = {
        "total_cost": round(row[0], 6) if row else 0.0,
        "total_input_tokens": row[1] if row else 0,
        "total_cache_hit_tokens": row[2] if row else 0,
        "total_output_tokens": row[3] if row else 0,
        "api_calls": row[4] if row else 0,
        "currency": row[5] if row else "CNY",
        "by_model": {}
    }
    
    # 按模型分组查询
    cursor.execute("""
        SELECT 
            model_name,
            COALESCE(SUM(total_cost), 0.0) as cost,
            COUNT(*) as calls,
            COALESCE(SUM(input_tokens), 0) as input_tokens,
            COALESCE(SUM(output_tokens), 0) as output_tokens
        FROM agent_chat_costs
        WHERE conversation_id = ?
        GROUP BY model_name
        ORDER BY cost DESC
    """, (conversation_id,))
    
    for r in cursor.fetchall():
        summary["by_model"][r[0]] = {
            "cost": round(r[1], 6),
            "calls": r[2],
            "input_tokens": r[3],
            "output_tokens": r[4]
        }
    
    conn.close()
    return summary


def get_conversation_cost_history(conversation_id: int, limit: int = 50) -> list:
    """
    获取对话的费用历史明细（每次 API 调用的详细记录）
    
    Returns:
        [{model_name, api_call_index, input_tokens, output_tokens, total_cost, created_at}, ...]
    """
    conn = _connect()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT model_name, api_call_index, input_tokens, cache_hit_tokens,
               output_tokens, input_cost, cache_hit_cost, output_cost,
               total_cost, currency, created_at
        FROM agent_chat_costs
        WHERE conversation_id = ?
        ORDER BY api_call_index ASC
        LIMIT ?
    """, (conversation_id, limit))
    
    history = []
    for row in cursor.fetchall():
        history.append({
            "model_name": row[0],
            "api_call_index": row[1],
            "input_tokens": row[2],
            "cache_hit_tokens": row[3],
            "output_tokens": row[4],
            "input_cost": round(row[5], 6),
            "cache_hit_cost": round(row[6], 6),
            "output_cost": round(row[7], 6),
            "total_cost": round(row[8], 6),
            "currency": row[9],
            "created_at": row[10]
        })
    
    conn.close()
    return history


def search_agent_messages(
    query: str,
    limit: int = 20,
    agent_name: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    全文搜索 agent 聊天消息
    
    Args:
        query: 搜索关键词
        limit: 返回条数上限
        agent_name: 可选的 agent 过滤
    
    Returns:
        匹配的消息列表，含会话标题和上下文片段
    """
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 用 FTS5 搜索
    like_query = f"%{query}%"
    sql = """
        SELECT m.id, m.role, m.content, m.created_at,
               c.uuid as conv_uuid, c.title as conv_title, c.agent_name
        FROM agent_chat_messages m
        JOIN agent_chat_conversations c ON m.conversation_id = c.id
        WHERE m.content LIKE ?
    """
    params: List[Any] = [like_query]
    
    if agent_name:
        sql += " AND c.agent_name = ?"
        params.append(agent_name)
    
    sql += " ORDER BY m.created_at DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(sql, params)
    rows = cursor.fetchall()
    
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
            "conversation_uuid": row["conv_uuid"],
            "conversation_title": row["conv_title"] or "未命名对话",
            "agent_name": row["agent_name"],
        })
    
    conn.close()
    return results


def delete_agent_chat_message(message_id: int, conversation_id: int) -> bool:
    """删除单条消息"""
    conn = _connect()
    cursor = conn.cursor()
    
    cursor.execute("""
        DELETE FROM agent_chat_messages
        WHERE id = ? AND conversation_id = ?
    """, (message_id, conversation_id))
    
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    
    return success


def delete_agent_chat_message_by_uuid(msg_uuid: str) -> bool:
    """通过 msg_uuid 删除消息（用于跨库同步删除）"""
    if not msg_uuid:
        return False
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM agent_chat_messages WHERE msg_uuid = ?",
        (msg_uuid,)
    )
    success = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return success
