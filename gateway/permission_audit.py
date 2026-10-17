"""
权限审计日志模块 - Permission Audit Logger

提供完整的权限决策审计功能：
- 记录所有权限检查请求
- 记录用户决策（允许/拒绝/超时）
- 支持查询和导出审计日志
- 持久化存储到SQLite数据库

设计原则：
- 不可篡改：日志一旦写入不可修改
- 完整性：记录所有权限相关事件
- 可追溯：支持按时间、会话、工具等维度查询
"""

import sqlite3
import json
import logging
import os
import re
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass
from enum import Enum
import hashlib

logger = logging.getLogger(__name__)

_DATA_HOME = os.environ.get("PEBBLE_HOME") or os.path.join(Path.home(), ".pebble")
os.makedirs(_DATA_HOME, exist_ok=True)
AUDIT_DB_PATH = Path(_DATA_HOME) / "permission_audit.db"


class AuditEventType(str, Enum):
    """审计事件类型"""
    PERMISSION_CHECK = "permission_check"      # 权限检查请求
    PERMISSION_ALLOWED = "permission_allowed"  # 权限允许
    PERMISSION_DENIED = "permission_denied"    # 权限拒绝
    PERMISSION_TIMEOUT = "permission_timeout"  # 权限超时
    PATH_BOUNDARY_VIOLATION = "path_violation" # 路径边界违规
    RULE_MATCHED = "rule_matched"              # 规则匹配
    MODE_CHANGED = "mode_changed"              # 权限模式变更


class AuditDecisionSource(str, Enum):
    """决策来源"""
    USER_APPROVE = "user_approve"        # 用户批准
    USER_DENY = "user_deny"              # 用户拒绝
    AUTO_ALLOW_RULE = "auto_allow_rule"  # 自动允许规则
    AUTO_DENY_RULE = "auto_deny_rule"    # 自动拒绝规则
    BYPASS_MODE = "bypass_mode"          # 绕过模式
    LOW_RISK = "low_risk"                # 低风险自动允许
    TIMEOUT = "timeout"                  # 超时
    PATH_VIOLATION = "path_violation"    # 路径违规


@dataclass
class AuditEvent:
    """审计事件"""
    id: Optional[int] = None
    timestamp: str = ""
    event_type: str = ""
    
    # 会话信息
    session_id: str = ""
    conversation_uuid: str = ""
    agent_name: str = ""
    
    # 工具信息
    tool_name: str = ""
    tool_args: str = ""  # JSON
    permission_type: str = ""
    permission_level: str = ""
    
    # 操作内容
    target: str = ""
    content: str = ""
    project_path: str = ""
    resolved_path: str = ""  # 解析后的实际路径
    
    # 决策信息
    decision_source: str = ""
    allowed: bool = False
    rule_matched: str = ""  # 匹配的规则
    user_response_time_ms: int = 0  # 用户响应时间
    
    # 附加信息
    reason: str = ""
    error_message: str = ""
    extra: str = ""  # JSON for additional data
    
    # 完整性校验
    checksum: str = ""


class PermissionAuditLogger:
    """
    权限审计日志记录器
    
    功能：
    - 记录权限检查事件
    - 记录用户决策
    - 支持查询和导出
    - 数据完整性校验
    """
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(AUDIT_DB_PATH)
        self._write_lock = threading.Lock()
        self._init_db()
    
    def _init_db(self):
        """初始化审计数据库"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # 创建审计事件表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS permission_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                event_type TEXT NOT NULL,
                
                -- 会话信息
                session_id TEXT,
                conversation_uuid TEXT,
                agent_name TEXT,
                
                -- 工具信息
                tool_name TEXT,
                tool_args TEXT,
                permission_type TEXT,
                permission_level TEXT,
                
                -- 操作内容
                target TEXT,
                content TEXT,
                project_path TEXT,
                resolved_path TEXT,
                
                -- 决策信息
                decision_source TEXT,
                allowed INTEGER,
                rule_matched TEXT,
                user_response_time_ms INTEGER,
                
                -- 附加信息
                reason TEXT,
                error_message TEXT,
                extra TEXT,
                
                -- 完整性校验
                checksum TEXT,
                
                -- 索引字段
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建索引
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON permission_audit_log(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_session ON permission_audit_log(session_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_conversation ON permission_audit_log(conversation_uuid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_tool ON permission_audit_log(tool_name)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_event_type ON permission_audit_log(event_type)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_audit_allowed ON permission_audit_log(allowed)")
        
        conn.commit()
        conn.close()
        
        logger.info(f"[PermissionAudit] Database initialized: {self.db_path}")
    
    # ===== 完整性校验（v2：覆盖全部安全字段；旧记录无版本前缀，仅提示不计失败）=====
    _CHECKSUM_FIELDS = (
        "timestamp", "event_type", "session_id", "conversation_uuid", "agent_name",
        "tool_name", "tool_args", "permission_type", "permission_level",
        "target", "content", "project_path", "resolved_path",
        "decision_source", "allowed", "rule_matched", "user_response_time_ms",
        "reason", "error_message", "extra",
    )

    @staticmethod
    def _normalize_bool_str(value) -> str:
        """统一 bool 表示：内存中的 True/False 与 DB 中的 1/0 归一为 '1'/'0'"""
        try:
            return "1" if bool(int(value)) else "0"
        except (TypeError, ValueError):
            return "1" if value else "0"

    # ===== 敏感信息脱敏 =====
    # 审计库中 target/content/tool_args 等字段会记录命令与代码原文；
    # 含 API key / token / 密码的内容在写入前统一掩码。
    _SENSITIVE_PATTERNS = [
        (re.compile(r'sk-[A-Za-z0-9_\-]{8,}'), 'sk-***'),
        (re.compile(r'ghp_[A-Za-z0-9]{20,}'), 'ghp_***'),
        (re.compile(r'gho_[A-Za-z0-9]{20,}'), 'gho_***'),
        (re.compile(r'github_pat_[A-Za-z0-9_]{20,}'), 'github_pat_***'),
        (re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._\-]{8,}'), r'\1***'),
        (re.compile(
            r'(?i)((?:api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|password|passwd|secret)'
            r'["\']?\s*[=:]\s*["\']?)([^\s"\',;}{]{6,})'
        ), r'\1***'),
    ]

    @classmethod
    def _mask_sensitive(cls, text: str) -> str:
        """掩码敏感信息（API key / token / 密码等），防止明文落盘"""
        if not text or not isinstance(text, str):
            return text
        masked = text
        for pattern, repl in cls._SENSITIVE_PATTERNS:
            masked = pattern.sub(repl, masked)
        return masked

    def _checksum_payload(self, mapping: dict) -> str:
        """规范化校验字段负载（v2/v3 共用）"""
        payload = {f: mapping.get(f, "") for f in self._CHECKSUM_FIELDS}
        payload["allowed"] = self._normalize_bool_str(payload["allowed"])
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)

    def _compute_checksum(self, event: AuditEvent, prev_checksum: str = "") -> str:
        """
        计算事件校验和（v3 链式）：
        checksum = sha256(prev_checksum | payload)。链上任一记录被修改（含去前缀降级）
        都会导致后续重算失配，无法通过单点重算掩盖。
        """
        data = self._checksum_payload({f: getattr(event, f, "") for f in self._CHECKSUM_FIELDS})
        return "v3:" + hashlib.sha256(f"{prev_checksum}|{data}".encode("utf-8")).hexdigest()[:32]
    
    def log_event(self, event: AuditEvent) -> int:
        """
        记录审计事件
        
        Args:
            event: 审计事件对象
            
        Returns:
            事件ID
        """
        if not event.timestamp:
            event.timestamp = datetime.utcnow().isoformat()
        
        # 脱敏：防止 API key / 密码等明文落盘
        # （在 checksum 计算之前执行，校验和覆盖脱敏后的值）
        event.target = self._mask_sensitive(event.target)
        event.content = self._mask_sensitive(event.content)
        event.tool_args = self._mask_sensitive(event.tool_args)
        event.reason = self._mask_sensitive(event.reason)
        event.error_message = self._mask_sensitive(event.error_message)
        event.extra = self._mask_sensitive(event.extra)
        
        with self._write_lock:
            conn = sqlite3.connect(self.db_path)
            try:
                cursor = conn.cursor()
                # v3 链式校验和：取链上最后一条的 checksum 作为前缀（写锁保证链连续）
                cursor.execute("SELECT checksum FROM permission_audit_log ORDER BY id DESC LIMIT 1")
                row = cursor.fetchone()
                prev_checksum = (row[0] if row and row[0] else "GENESIS")
                event.checksum = self._compute_checksum(event, prev_checksum)

                cursor.execute("""
                    INSERT INTO permission_audit_log (
                        timestamp, event_type, session_id, conversation_uuid, agent_name,
                        tool_name, tool_args, permission_type, permission_level,
                        target, content, project_path, resolved_path,
                        decision_source, allowed, rule_matched, user_response_time_ms,
                        reason, error_message, extra, checksum
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    event.timestamp, event.event_type, event.session_id, event.conversation_uuid, event.agent_name,
                    event.tool_name, event.tool_args, event.permission_type, event.permission_level,
                    event.target, event.content, event.project_path, event.resolved_path,
                    event.decision_source, int(event.allowed), event.rule_matched, event.user_response_time_ms,
                    event.reason, event.error_message, event.extra, event.checksum
                ))
                
                event_id = cursor.lastrowid
                conn.commit()
            finally:
                conn.close()
        
        logger.debug(f"[PermissionAudit] Logged event {event_id}: {event.event_type} - {event.tool_name}")
        return event_id
    
    def log_permission_check(
        self,
        tool_name: str,
        tool_args: Dict[str, Any],
        permission_type: str,
        permission_level: str,
        target: str,
        content: str,
        project_path: str,
        session_id: str = "",
        conversation_uuid: str = "",
        agent_name: str = ""
    ) -> AuditEvent:
        """记录权限检查请求"""
        event = AuditEvent(
            event_type=AuditEventType.PERMISSION_CHECK.value,
            session_id=session_id,
            conversation_uuid=conversation_uuid,
            agent_name=agent_name,
            tool_name=tool_name,
            tool_args=json.dumps(tool_args, ensure_ascii=False),
            permission_type=permission_type,
            permission_level=permission_level,
            target=target,
            content=content,
            project_path=project_path
        )
        self.log_event(event)
        return event
    
    def log_permission_decision(
        self,
        tool_name: str,
        allowed: bool,
        decision_source: str,
        rule_matched: str = "",
        user_response_time_ms: int = 0,
        session_id: str = "",
        conversation_uuid: str = "",
        agent_name: str = "",
        target: str = "",
        reason: str = ""
    ) -> int:
        """记录权限决策"""
        event_type = AuditEventType.PERMISSION_ALLOWED.value if allowed else AuditEventType.PERMISSION_DENIED.value
        
        event = AuditEvent(
            event_type=event_type,
            session_id=session_id,
            conversation_uuid=conversation_uuid,
            agent_name=agent_name,
            tool_name=tool_name,
            target=target,
            allowed=allowed,
            decision_source=decision_source,
            rule_matched=rule_matched,
            user_response_time_ms=user_response_time_ms,
            reason=reason
        )
        return self.log_event(event)
    
    def log_path_violation(
        self,
        tool_name: str,
        requested_path: str,
        resolved_path: str,
        project_path: str,
        session_id: str = "",
        conversation_uuid: str = "",
        agent_name: str = ""
    ) -> int:
        """记录路径边界违规"""
        event = AuditEvent(
            event_type=AuditEventType.PATH_BOUNDARY_VIOLATION.value,
            session_id=session_id,
            conversation_uuid=conversation_uuid,
            agent_name=agent_name,
            tool_name=tool_name,
            target=requested_path,
            resolved_path=resolved_path,
            project_path=project_path,
            allowed=False,
            decision_source=AuditDecisionSource.PATH_VIOLATION.value,
            reason=f"Path '{resolved_path}' is outside project boundary '{project_path}'"
        )
        return self.log_event(event)
    
    def query(
        self,
        start_time: str = None,
        end_time: str = None,
        session_id: str = None,
        conversation_uuid: str = None,
        tool_name: str = None,
        event_type: str = None,
        allowed: bool = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        查询审计日志
        
        Args:
            start_time: 开始时间 (ISO格式)
            end_time: 结束时间 (ISO格式)
            session_id: 会话ID
            conversation_uuid: 对话UUID
            tool_name: 工具名称
            event_type: 事件类型
            allowed: 是否允许
            limit: 最大返回数量
            
        Returns:
            审计事件列表
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        conditions = []
        params = []
        
        if start_time:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time:
            conditions.append("timestamp <= ?")
            params.append(end_time)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if conversation_uuid:
            conditions.append("conversation_uuid = ?")
            params.append(conversation_uuid)
        if tool_name:
            conditions.append("tool_name = ?")
            params.append(tool_name)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)
        if allowed is not None:
            conditions.append("allowed = ?")
            params.append(int(allowed))
        
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        
        query = f"""
            SELECT * FROM permission_audit_log
            WHERE {where_clause}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        conn.close()
        
        return [dict(zip(columns, row)) for row in rows]
    
    def get_statistics(
        self,
        start_time: str = None,
        end_time: str = None
    ) -> Dict[str, Any]:
        """
        获取审计统计信息
        
        Returns:
            统计数据字典
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        time_filter = ""
        params = []
        if start_time and end_time:
            time_filter = "WHERE timestamp BETWEEN ? AND ?"
            params = [start_time, end_time]
        elif start_time:
            time_filter = "WHERE timestamp >= ?"
            params = [start_time]
        elif end_time:
            time_filter = "WHERE timestamp <= ?"
            params = [end_time]
        
        # 总事件数
        cursor.execute(f"SELECT COUNT(*) FROM permission_audit_log {time_filter}", params)
        total_events = cursor.fetchone()[0]
        
        # 允许/拒绝统计
        cursor.execute(f"SELECT COUNT(*) FROM permission_audit_log {time_filter} {'AND' if time_filter else 'WHERE'} allowed = 1", params)
        allowed_count = cursor.fetchone()[0]
        
        cursor.execute(f"SELECT COUNT(*) FROM permission_audit_log {time_filter} {'AND' if time_filter else 'WHERE'} allowed = 0", params)
        denied_count = cursor.fetchone()[0]
        
        # 按工具统计
        cursor.execute(f"""
            SELECT tool_name, COUNT(*) as count, SUM(allowed) as allowed_count
            FROM permission_audit_log {time_filter}
            GROUP BY tool_name
            ORDER BY count DESC
            LIMIT 20
        """, params)
        tool_stats = [{"tool": row[0], "total": row[1], "allowed": row[2]} for row in cursor.fetchall()]
        
        # 按决策来源统计
        cursor.execute(f"""
            SELECT decision_source, COUNT(*) as count
            FROM permission_audit_log {time_filter}
            WHERE decision_source IS NOT NULL
            GROUP BY decision_source
            ORDER BY count DESC
        """, params)
        decision_stats = [{"source": row[0], "count": row[1]} for row in cursor.fetchall()]
        
        # 路径违规统计
        cursor.execute(f"""
            SELECT COUNT(*) FROM permission_audit_log {time_filter}
            {'AND' if time_filter else 'WHERE'} event_type = ?
        """, params + [AuditEventType.PATH_BOUNDARY_VIOLATION.value])
        path_violations = cursor.fetchone()[0]
        
        conn.close()
        
        return {
            "total_events": total_events,
            "allowed_count": allowed_count,
            "denied_count": denied_count,
            "allow_rate": round(allowed_count / total_events * 100, 2) if total_events > 0 else 0,
            "tool_statistics": tool_stats,
            "decision_statistics": decision_stats,
            "path_violations": path_violations
        }
    
    def export_to_json(
        self,
        output_path: str,
        start_time: str = None,
        end_time: str = None
    ) -> int:
        """
        导出审计日志到JSON文件
        
        Args:
            output_path: 输出文件路径
            start_time: 开始时间
            end_time: 结束时间
            
        Returns:
            导出的事件数量
        """
        events = self.query(start_time=start_time, end_time=end_time, limit=10000)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        
        logger.info(f"[PermissionAudit] Exported {len(events)} events to {output_path}")
        return len(events)
    
    def verify_integrity(self, event_id: int = None) -> bool:
        """
        验证日志完整性（链式校验）

        - v3 记录：链式重算 sha256(prev_checksum|payload)；链上任一环被修改
          或去前缀降级都会失配
        - v2 记录（历史）：按 v2 规则（无链）重算校验
        - 无版本前缀的 legacy 记录：跳过并计数

        Args:
            event_id: 特定事件ID，None则验证所有

        Returns:
            是否通过验证（legacy 记录不导致失败）
        """
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        if event_id:
            cursor.execute("SELECT * FROM permission_audit_log WHERE id = ?", (event_id,))
        else:
            cursor.execute("SELECT * FROM permission_audit_log ORDER BY id")
        
        rows = cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        # 全表链（按 id 升序）——v3 校验需要每条记录前一条的 checksum
        cursor.execute("SELECT id, checksum FROM permission_audit_log ORDER BY id")
        chain = [(rid, cs or "") for rid, cs in cursor.fetchall()]
        conn.close()
        pos = {rid: i for i, (rid, _cs) in enumerate(chain)}

        checked_v3 = 0
        checked_v2 = 0
        legacy = 0
        for row in rows:
            event_dict = dict(zip(columns, row))
            stored = event_dict.get("checksum", "") or ""
            eid = event_dict.get("id")
            data = self._checksum_payload(event_dict)
            if stored.startswith("v3:"):
                idx = pos.get(eid)
                prev = chain[idx - 1][1] if idx is not None and idx > 0 else "GENESIS"
                computed = "v3:" + hashlib.sha256(f"{prev}|{data}".encode("utf-8")).hexdigest()[:32]
                if stored != computed:
                    logger.error(f"[PermissionAudit] Integrity check failed (v3 chain) for event {eid}")
                    return False
                checked_v3 += 1
            elif stored.startswith("v2:"):
                computed = "v2:" + hashlib.sha256(data.encode("utf-8")).hexdigest()[:32]
                if stored != computed:
                    logger.error(f"[PermissionAudit] Integrity check failed (v2) for event {eid}")
                    return False
                checked_v2 += 1
            else:
                legacy += 1

        logger.info(
            f"[PermissionAudit] Integrity check passed (v3 chain: {checked_v3}, v2: {checked_v2}, legacy skipped: {legacy})"
        )
        return True


# 全局单例
_audit_logger: Optional[PermissionAuditLogger] = None


def get_audit_logger() -> PermissionAuditLogger:
    """获取审计日志记录器单例"""
    global _audit_logger
    if _audit_logger is None:
        _audit_logger = PermissionAuditLogger()
    return _audit_logger
