"""
Snapshot System — 文件级快照与回退系统

完整实现 Session Revert 功能：
- 写操作前自动创建文件快照
- 精确回退文件状态到任意历史消息点
- 三阶段冲突检测与决策
- SSE 流式回退进度
- 撤销回退支持
- 磁盘空间管理

快照范围: write_file / edit_file / delete_file / move_file
消息原则: 回退只恢复文件，不修改任何消息数据
"""

from .snapshot_types import (
    FileSnapshotRecord,
    RevertOperation,
    FileConflict,
    OPERATION_TYPE_VALUES,
    REVERT_STATUS_VALUES,
    CONFLICT_TYPE_VALUES,
)

from .snapshot_manager import SnapshotManager, get_snapshot_manager

from .snapshot_executor import RevertExecutor

from .snapshot_diff import DiffEngine

from .snapshot_conflict import ConflictDetector

from .snapshot_undo import UndoEngine

from .snapshot_cleanup import (
    enforce_disk_limits,
    cleanup_session_snapshots,
)

__all__ = [
    # 类型
    'FileSnapshotRecord',
    'RevertOperation',
    'FileConflict',
    'OPERATION_TYPE_VALUES',
    'REVERT_STATUS_VALUES',
    'CONFLICT_TYPE_VALUES',
    # 核心
    'SnapshotManager',
    'get_snapshot_manager',
    # 执行
    'RevertExecutor',
    # Diff
    'DiffEngine',
    # 冲突
    'ConflictDetector',
    # Undo
    'UndoEngine',
    # 清理
    'enforce_disk_limits',
    'cleanup_session_snapshots',
]
