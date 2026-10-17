"""
snapshot_types.py — 快照系统全部数据结构定义

包含:
- FileSnapshotRecord: 一条文件快照记录
- RevertOperation: 一次完整的回退操作
- FileConflict: 单个文件的冲突信息
- SQLite 表定义 DDL
- 枚举常量
"""

from __future__ import annotations

import uuid
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

# ============================================================================
# 枚举常量
# ============================================================================

OPERATION_TYPE_VALUES = (
    "create",       # 新建文件
    "modify",       # 修改文件
    "delete",       # 删除文件
    "move_source",  # 移动操作的源
    "move_dest",    # 移动操作的目标
)

REVERT_STATUS_VALUES = (
    "active",       # 活跃，未被回退
    "reverting",    # 正在回退中
    "reverted",     # 已回退
    "unreverted",   # 已撤销回退
)

CONFLICT_TYPE_VALUES = (
    "sha256_mismatch",          # SHA256 不匹配
    "file_missing",             # 文件应该存在但不存在
    "unexpected_file_exists",   # 文件不应该存在但存在
    "move_integrity_broken",    # move 操作完整性被破坏
    "external_modification",    # 外部修改
)

REVERT_STRATEGY_VALUES = (
    "abort",            # 遇到冲突中止
    "skip_conflicts",   # 跳过冲突文件
    "overwrite_all",    # 覆盖所有冲突文件
)

# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class FileSnapshotRecord:
    """一条文件快照记录（对应 SQLite 中的一行）"""

    # 标识
    record_id: str                  # UUID 主键
    snapshot_id: str                # 所属快照点 ID，格式 "{message_id}_{tool_call_id}"
    session_uuid: str               # 所属会话 UUID

    # 触发上下文
    message_id: int                 # 触发快照的消息 ID
    tool_call_id: str               # 触发快照的工具调用 ID
    tool_name: str                  # write_file / edit_file / delete_file / move_file
    tool_input_json: str            # 工具调用参数的完整 JSON

    # 文件定位
    absolute_path: str              # 绝对路径
    relative_path: str              # 项目根目录下的相对路径

    # 备份文件
    backup_path: str = ""           # 备份文件的绝对路径
    backup_sha256: str = ""         # 备份文件的 SHA256 哈希
    backup_size: int = 0            # 备份文件大小（字节）
    backup_mtime: float = 0.0       # 备份文件的原始修改时间

    # 写入结果（工具执行后回填；用于回退时的外部修改检测）
    write_result_sha256: str = ""   # 该次工具调用结束后文件的 SHA256（不存在则为空）

    # 文件状态
    file_existed_before: bool = True    # 快照前此文件是否存在
    file_existed_after: bool = True     # 快照后此文件是否仍存在

    # 操作类型
    operation_type: str = "modify"      # create / modify / delete / move_source / move_dest
    move_counterpart_path: Optional[str] = None  # move 操作时的配对路径

    # 时间戳
    created_at: float = field(default_factory=time.time)

    # 回退状态
    revert_status: str = "active"       # active / reverting / reverted / unreverted
    reverted_at: Optional[float] = None
    revert_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result.pop('move_counterpart_path', None)
        if self.move_counterpart_path:
            result['move_counterpart_path'] = self.move_counterpart_path
        return result

    def to_db_row(self) -> Dict[str, Any]:
        """转换为 SQLite 行"""
        return {
            'record_id': self.record_id,
            'snapshot_id': self.snapshot_id,
            'session_uuid': self.session_uuid,
            'message_id': self.message_id,
            'tool_call_id': self.tool_call_id,
            'tool_name': self.tool_name,
            'tool_input_json': self.tool_input_json,
            'absolute_path': self.absolute_path,
            'relative_path': self.relative_path,
            'backup_path': self.backup_path,
            'backup_sha256': self.backup_sha256,
            'backup_size': self.backup_size,
            'backup_mtime': self.backup_mtime,
            'write_result_sha256': self.write_result_sha256,
            'file_existed_before': int(self.file_existed_before),
            'file_existed_after': int(self.file_existed_after),
            'operation_type': self.operation_type,
            'move_counterpart_path': self.move_counterpart_path,
            'created_at': self.created_at,
            'revert_status': self.revert_status,
            'reverted_at': self.reverted_at,
            'revert_id': self.revert_id,
        }

    @classmethod
    def from_db_row(cls, row: Dict[str, Any]) -> 'FileSnapshotRecord':
        """从 SQLite 行还原"""
        return cls(
            record_id=row['record_id'],
            snapshot_id=row['snapshot_id'],
            session_uuid=row['session_uuid'],
            message_id=row['message_id'],
            tool_call_id=row['tool_call_id'],
            tool_name=row['tool_name'],
            tool_input_json=row['tool_input_json'],
            absolute_path=row['absolute_path'],
            relative_path=row['relative_path'],
            backup_path=row.get('backup_path', ''),
            backup_sha256=row.get('backup_sha256', ''),
            backup_size=row.get('backup_size', 0),
            backup_mtime=row.get('backup_mtime', 0.0),
            write_result_sha256=row.get('write_result_sha256', '') or '',
            file_existed_before=bool(row.get('file_existed_before', True)),
            file_existed_after=bool(row.get('file_existed_after', True)),
            operation_type=row.get('operation_type', 'modify'),
            move_counterpart_path=row.get('move_counterpart_path'),
            created_at=row.get('created_at', 0.0),
            revert_status=row.get('revert_status', 'active'),
            reverted_at=row.get('reverted_at'),
            revert_id=row.get('revert_id'),
        )


@dataclass
class FileConflict:
    """单个文件的冲突信息"""

    relative_path: str              # 文件路径
    absolute_path: str              # 绝对路径
    snapshot_record_id: str         # 关联的快照记录 ID
    snapshot_sha256: str            # 快照记录的 SHA256
    current_sha256: str             # 当前磁盘上的 SHA256
    conflict_type: str              # sha256_mismatch / file_missing / unexpected_file_exists
    expectation: str                # 预期状态描述
    actual: str                     # 实际状态描述
    last_modified_at: Optional[float] = None
    resolution: Optional[str] = None    # skip / overwrite / abort

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RevertFileResult:
    """单个文件的回退执行结果"""

    relative_path: str
    absolute_path: str
    operation_type: str
    status: str                     # success / skipped / failed
    message: str                    # 详情信息
    conflict: Optional[FileConflict] = None
    restored_from: str = ""         # 恢复来源路径


@dataclass
class RevertOperation:
    """一次完整的回退操作"""

    # 标识
    revert_id: str                  # UUID
    session_uuid: str               # 所属会话
    triggered_at: float             # 触发时间
    completed_at: Optional[float] = None

    # 参数
    target_message_id: int = 0
    conflict_strategy: str = "abort"    # abort / skip_conflicts / overwrite_all

    # 执行状态
    status: str = "pending"             # pending / running / completed / failed / cancelled
    error_message: Optional[str] = None

    # 统计
    total_files: int = 0
    restored_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0

    # 冲突
    conflicts: List[Dict[str, Any]] = field(default_factory=list)

    # 逐文件结果
    file_results: List[Dict[str, Any]] = field(default_factory=list)

    # Undo 支持
    undo_snapshot_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def create(cls, session_uuid: str, target_message_id: int,
               conflict_strategy: str = "abort") -> 'RevertOperation':
        return cls(
            revert_id=str(uuid.uuid4()),
            session_uuid=session_uuid,
            triggered_at=time.time(),
            target_message_id=target_message_id,
            conflict_strategy=conflict_strategy,
            status="pending",
        )


# ============================================================================
# SQLite 表定义
# ============================================================================

FILE_SNAPSHOTS_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS file_snapshots (
    record_id            TEXT PRIMARY KEY NOT NULL,
    snapshot_id          TEXT NOT NULL,
    session_uuid         TEXT NOT NULL,
    message_id           INTEGER NOT NULL,
    tool_call_id         TEXT NOT NULL,
    tool_name            TEXT NOT NULL,
    tool_input_json      TEXT NOT NULL DEFAULT '{}',
    absolute_path        TEXT NOT NULL,
    relative_path        TEXT NOT NULL,
    backup_path          TEXT NOT NULL DEFAULT '',
    backup_sha256        TEXT NOT NULL DEFAULT '',
    backup_size          INTEGER NOT NULL DEFAULT 0,
    backup_mtime         REAL NOT NULL DEFAULT 0.0,
    write_result_sha256  TEXT NOT NULL DEFAULT '',
    file_existed_before  INTEGER NOT NULL DEFAULT 1,
    file_existed_after   INTEGER NOT NULL DEFAULT 1,
    operation_type       TEXT NOT NULL DEFAULT 'modify',
    move_counterpart_path TEXT,
    created_at           REAL NOT NULL,
    revert_status        TEXT NOT NULL DEFAULT 'active',
    reverted_at          REAL,
    revert_id            TEXT
)
"""

FILE_SNAPSHOTS_INDEXES_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_fs_session ON file_snapshots(session_uuid)",
    "CREATE INDEX IF NOT EXISTS idx_fs_msg ON file_snapshots(session_uuid, message_id)",
    "CREATE INDEX IF NOT EXISTS idx_fs_file ON file_snapshots(session_uuid, relative_path)",
    "CREATE INDEX IF NOT EXISTS idx_fs_status ON file_snapshots(session_uuid, revert_status)",
    "CREATE INDEX IF NOT EXISTS idx_fs_revert_id ON file_snapshots(revert_id)",
    "CREATE INDEX IF NOT EXISTS idx_fs_time ON file_snapshots(session_uuid, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_fs_snapshot ON file_snapshots(snapshot_id)",
]
